import json
import hashlib
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from utils.historical_player_props import (
    flatten_historical_date_blob,
    flatten_historical_record,
    merge_rows_by_precedence,
)
from utils.logging_utils import log_status
from utils.supabase_client import get_supabase_client

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logger = logging.getLogger(__name__)

UPSERT_BATCH_SIZE = max(1, int(os.getenv("SUPABASE_UPSERT_BATCH_SIZE", "50")))
UPSERT_MAX_RETRIES = max(1, int(os.getenv("SUPABASE_UPSERT_MAX_RETRIES", "4")))
LINE_MOVEMENT_MAX_SNAPSHOTS = max(0, int(os.getenv("SUPABASE_LINE_MOVEMENT_MAX_SNAPSHOTS", "32")))
CURRENT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "current",
)
LINE_MOVEMENT_SYNC_STATE_PATH = os.path.join(CURRENT_DATA_DIR, "line_movements_sync_state.json")
ARCHIVE_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "archive",
)
DEFAULT_HISTORICAL_ODDS_PATH = os.path.join(ARCHIVE_DATA_DIR, "historical_odds.json")
HISTORICAL_PLAYER_PROPS_TABLE = "historical_player_props"


def _chunk_rows(rows, chunk_size):
    for start in range(0, len(rows), chunk_size):
        yield rows[start:start + chunk_size]


def _resolve_historical_odds_path(path: str) -> str:
    if path and os.path.exists(path):
        return path
    if os.path.exists(DEFAULT_HISTORICAL_ODDS_PATH):
        return DEFAULT_HISTORICAL_ODDS_PATH
    return path


def _is_retryable_upsert_error(error):
    message = str(error).lower()
    return (
        "statement timeout" in message
        or "canceling statement due to statement timeout" in message
        or "timed out" in message
        or "connection" in message
        or "temporarily unavailable" in message
        or "429" in message
    )


def _is_missing_relation_error(error, table_name: str):
    message = str(error).lower()
    table_token = str(table_name).lower()
    return (
        ("relation" in message and "does not exist" in message and table_token in message)
        or ("could not find the table" in message and table_token in message)
        or ("schema cache" in message and table_token in message)
    )


def _load_sync_state(path: str):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not read sync state %s: %s", path, exc)
        return {}


def _save_sync_state(path: str, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(temp_path, path)


def _fingerprint_payload(payload) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def _compact_snapshots_for_supabase(snapshots):
    if LINE_MOVEMENT_MAX_SNAPSHOTS <= 0 or len(snapshots) <= LINE_MOVEMENT_MAX_SNAPSHOTS:
        return snapshots

    keep = []
    if snapshots:
        keep.append(snapshots[0])

    keep.extend(snapshot for snapshot in snapshots if snapshot.get("label") == "pre_game")
    keep.extend(snapshots[-LINE_MOVEMENT_MAX_SNAPSHOTS:])

    compacted = []
    seen = set()
    for snapshot in keep:
        dedupe_key = f"{snapshot.get('timestamp', '')}|{snapshot.get('label', '')}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        compacted.append(snapshot)

    compacted.sort(key=lambda snapshot: str(snapshot.get("timestamp") or ""))
    return compacted


def _batched_upsert(table_name: str, rows, on_conflict: str, context: str, raise_on_failure: bool = False):
    if not rows:
        return True

    client = get_supabase_client()

    for chunk_index, chunk in enumerate(_chunk_rows(rows, UPSERT_BATCH_SIZE), start=1):
        last_error = None

        for attempt in range(1, UPSERT_MAX_RETRIES + 1):
            try:
                client.table(table_name).upsert(
                    chunk,
                    on_conflict=on_conflict,
                ).execute()
                last_error = None
                break
            except Exception as error:
                last_error = error
                if attempt >= UPSERT_MAX_RETRIES or not _is_retryable_upsert_error(error):
                    break
                time.sleep(1.5 * attempt)

        if last_error is not None:
            logger.warning(
                "%s upsert failed on chunk %d/%d: %s",
                context,
                chunk_index,
                max(1, (len(rows) + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE),
                last_error,
            )
            if raise_on_failure:
                raise last_error
            return False

    return True


def _load_historical_archive(historical_odds_path: str):
    historical_odds_path = _resolve_historical_odds_path(historical_odds_path)
    if not os.path.exists(historical_odds_path):
        return historical_odds_path, None

    with open(historical_odds_path, "r") as f:
        return historical_odds_path, json.load(f)


def _prepare_legacy_historical_rows_from_blob(game_date: str, date_blob):
    rows = []
    skipped_keys = []

    for player_id, record in (date_blob or {}).items():
        if not isinstance(record, dict):
            continue
        try:
            normalized_player_id = int(player_id)
        except (TypeError, ValueError):
            skipped_keys.append(str(player_id))
            continue
        rows.append(
            {
                "player_id": normalized_player_id,
                "player_name": record.get("name"),
                "team": record.get("team"),
                "game_date": game_date,
                "props": record.get("props", {}),
                "source": record.get("source"),
                "captured_at": record.get("captured_at"),
            }
        )

    return rows, skipped_keys


def _prepare_normalized_historical_rows_from_blob(game_date: str, date_blob, include_pp=False):
    rows = flatten_historical_date_blob(game_date, date_blob, include_pp=include_pp)
    return merge_rows_by_precedence(rows)


def upsert_line_movements_from_file(line_movements_path: str):
    """Mirror the current line movement snapshot blob into Supabase."""
    if not os.path.exists(line_movements_path):
        logger.warning("line movements file not found: %s", line_movements_path)
        return False

    with open(line_movements_path, "r") as f:
        lm_data = json.load(f)

    default_date = lm_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    snapshots_by_date = {}

    for snapshot in lm_data.get("snapshots", []):
        players = snapshot.get("players", {}) if isinstance(snapshot, dict) else {}
        dated_players = {}

        for player_id, pdata in players.items():
            game_date = (pdata or {}).get("game_date") or default_date
            dated_players.setdefault(game_date, {})[player_id] = pdata

        for game_date, players_blob in dated_players.items():
            snapshots_by_date.setdefault(game_date, []).append({
                "timestamp": snapshot.get("timestamp"),
                "label": snapshot.get("label"),
                "players": players_blob,
            })

    rows = [
        {"game_date": game_date, "snapshots": _compact_snapshots_for_supabase(snapshots)}
        for game_date, snapshots in snapshots_by_date.items()
    ]

    if not rows:
        rows = [{
            "game_date": default_date,
            "snapshots": _compact_snapshots_for_supabase(lm_data.get("snapshots", [])),
        }]

    sync_state = _load_sync_state(LINE_MOVEMENT_SYNC_STATE_PATH)
    next_sync_state = dict(sync_state)
    changed_rows = []
    for row in rows:
        game_date = str(row.get("game_date", ""))
        fingerprint = _fingerprint_payload(row.get("snapshots", []))
        next_sync_state[game_date] = fingerprint
        if sync_state.get(game_date) != fingerprint:
            changed_rows.append(row)

    if not changed_rows:
        log_status(logger, "SKIP", "line_movements sync unchanged", dates=len(rows))
        try:
            _save_sync_state(LINE_MOVEMENT_SYNC_STATE_PATH, next_sync_state)
        except Exception as exc:
            logger.warning("Could not persist line_movements sync state: %s", exc)
        return True

    success = _batched_upsert(
        "line_movements",
        changed_rows,
        on_conflict="game_date",
        context="line_movements",
    )
    if success:
        try:
            _save_sync_state(LINE_MOVEMENT_SYNC_STATE_PATH, next_sync_state)
        except Exception as exc:
            logger.warning("Could not persist line_movements sync state: %s", exc)
        log_status(logger, "OK", "line_movements sync complete", dates=len(changed_rows))
    return success


def upsert_historical_odds_from_file(historical_odds_path: str, game_date: str):
    """Mirror one game date from the local historical archive into Supabase rows."""
    historical_odds_path, historical_odds = _load_historical_archive(historical_odds_path)
    if historical_odds is None:
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return False

    date_blob = historical_odds.get(game_date, {})
    if not isinstance(date_blob, dict) or not date_blob:
        logger.info("No historical odds found for %s", game_date)
        return False

    rows, skipped_keys = _prepare_legacy_historical_rows_from_blob(game_date, date_blob)
    if not rows:
        log_status(logger, "SKIP", "No historical odds rows prepared", date=game_date)
        return False

    if skipped_keys:
        log_status(
            logger,
            "WARN",
            "Skipped historical odds rows with unresolved player ids",
            skipped=len(skipped_keys),
            date=game_date,
        )

    success = _batched_upsert(
        "historical_odds",
        rows,
        on_conflict="player_id,game_date",
        context=f"historical_odds[{game_date}]",
    )
    if success:
        log_status(logger, "OK", "historical_odds sync complete", date=game_date, rows=len(rows))
    return success


def upsert_historical_player_props_rows(rows, context="historical_player_props", skip_if_table_missing=True):
    normalized_rows = merge_rows_by_precedence(rows)
    if not normalized_rows:
        log_status(logger, "SKIP", "No normalized historical rows prepared", context=context)
        return True

    try:
        success = _batched_upsert(
            HISTORICAL_PLAYER_PROPS_TABLE,
            normalized_rows,
            on_conflict="player_id,game_date,sportsbook,stat_type",
            context=context,
            raise_on_failure=True,
        )
    except Exception as exc:
        if skip_if_table_missing and _is_missing_relation_error(exc, HISTORICAL_PLAYER_PROPS_TABLE):
            log_status(
                logger,
                "WARN",
                "Normalized historical sync skipped; table missing",
                table=HISTORICAL_PLAYER_PROPS_TABLE,
            )
            return True
        raise

    if success:
        log_status(logger, "OK", "historical_player_props sync complete", context=context, rows=len(normalized_rows))
    return success


def upsert_historical_player_props_from_file(historical_odds_path: str, game_date: str, include_pp: bool = False):
    historical_odds_path, historical_odds = _load_historical_archive(historical_odds_path)
    if historical_odds is None:
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return False

    date_blob = historical_odds.get(game_date, {})
    if not isinstance(date_blob, dict) or not date_blob:
        logger.info("No normalized historical odds found for %s", game_date)
        return False

    rows = _prepare_normalized_historical_rows_from_blob(game_date, date_blob, include_pp=include_pp)
    if not rows:
        log_status(logger, "SKIP", "No normalized historical rows prepared", date=game_date)
        return False

    return upsert_historical_player_props_rows(
        rows,
        context=f"historical_player_props[{game_date}]",
    )


def upsert_live_historical_player_props(records, include_pp: bool = False):
    rows = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        rows.extend(
            flatten_historical_record(
                player_id=record.get("player_id"),
                game_date=record.get("game_date"),
                game_id=record.get("game_id"),
                record=record.get("record"),
                include_pp=include_pp,
            )
        )

    return upsert_historical_player_props_rows(rows, context="historical_player_props[live]")


def backfill_historical_player_props_from_file(historical_odds_path: str, game_dates=None, include_pp: bool = False):
    historical_odds_path, historical_odds = _load_historical_archive(historical_odds_path)
    if historical_odds is None:
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return False

    if game_dates:
        target_dates = [game_date for game_date in game_dates if isinstance(game_date, str) and historical_odds.get(game_date)]
    else:
        target_dates = sorted(
            [
                game_date for game_date, payload in (historical_odds or {}).items()
                if isinstance(game_date, str) and isinstance(payload, dict) and payload
            ]
        )

    if not target_dates:
        log_status(logger, "SKIP", "No historical dates available for normalized backfill")
        return False

    success = True
    for game_date in target_dates:
        success = upsert_historical_player_props_from_file(
            historical_odds_path,
            game_date,
            include_pp=include_pp,
        ) and success

    if success:
        log_status(logger, "OK", "Normalized historical backfill complete", dates=len(target_dates))
    return success


def sync_recent_historical_odds_from_file(historical_odds_path: str, max_days: int = 5):
    historical_odds_path, historical_odds = _load_historical_archive(historical_odds_path)
    if historical_odds is None:
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return True

    valid_dates = sorted(
        [
            game_date for game_date, payload in (historical_odds or {}).items()
            if isinstance(game_date, str) and isinstance(payload, dict) and payload
        ],
        reverse=True,
    )[:max(1, max_days)]

    if not valid_dates:
        return True

    success = True
    for game_date in valid_dates:
        legacy_ok = upsert_historical_odds_from_file(historical_odds_path, game_date)
        normalized_ok = upsert_historical_player_props_from_file(historical_odds_path, game_date)
        success = legacy_ok and normalized_ok and success
    return success
