import json
import hashlib
import logging
import os
import time
from datetime import date, datetime, timedelta

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
# The rolling window master_cron purges line_movements on. Imported there so the
# DELETE cutoff and the sync-state prune below are the same number.
LINE_MOVEMENT_RETENTION_DAYS = max(1, int(os.getenv("SUPABASE_LINE_MOVEMENT_RETENTION_DAYS", "3")))
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

# Distinguishes "this key has never been seen" from "this key's line is None",
# which is a real state a book can be in (posted, then pulled).
_UNSEEN = object()


def _chunk_rows(rows, chunk_size):
    for start in range(0, len(rows), chunk_size):
        yield rows[start:start + chunk_size]


def _resolve_historical_odds_path(path: str) -> str:
    if path and os.path.exists(path):
        return path
    if os.path.exists(DEFAULT_HISTORICAL_ODDS_PATH):
        return DEFAULT_HISTORICAL_ODDS_PATH
    return path


def historical_odds_legacy_fallback_enabled():
    raw_value = str(os.getenv("HISTORICAL_ODDS_LEGACY_FALLBACK", "true")).strip().lower()
    if raw_value in {"0", "false", "no", "off"}:
        return False
    return True


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


def line_movement_purge_cutoff(today=None) -> str:
    """The ISO date `line_movements` rows are deleted below.

    One callable rather than two copies of `today - RETENTION_DAYS`, because a
    test asserting the two agree can only do so by calling this. The previous
    guard grepped master_cron.py for the constant's name, which a literal date
    in the DELETE would satisfy while deleting nothing.
    """
    return ((today or date.today()) - timedelta(days=LINE_MOVEMENT_RETENTION_DAYS)).isoformat()


def purge_stale_props_history(today=None):
    """Delete player_props and line_movements rows outside the rolling window.

    Lifted out of master_cron so it can be tested. As six inline lines in the
    scheduler, the only available guard was grepping the source for the
    constant's name -- which a hardcoded date in the DELETE satisfied while
    deleting nothing, leaving the stated "one number" invariant silently false.

    `historical_odds` is deliberately NOT purged; it is the full-season archive.
    The durable copy of the intraday series is the disk archive written by
    SnapshotManager._archive_snapshot, which is why these two tables can be
    treated as an ephemeral cache at all.
    """
    cutoff = line_movement_purge_cutoff(today)
    client = get_supabase_client()
    for table in ("player_props", "line_movements"):
        client.table(table).delete().lt("game_date", cutoff).execute()
    return cutoff


def _prune_line_movement_sync_state(state, today=None):
    """Forget the dates the rolling purge has already deleted from Supabase.

    This state file used to carry every date it had ever synced, while
    `master_cron` deletes `line_movements` rows older than
    `LINE_MOVEMENT_RETENTION_DAYS`. A retained fingerprint for a deleted row
    claims that row is present, so the date could never be rewritten -- the
    state and the table disagreed about what existed. Pruning on the same
    window keeps the pair coherent; a date whose fingerprint is dropped simply
    gets re-upserted, which is the safe direction to fail.
    """
    cutoff = date.fromisoformat(line_movement_purge_cutoff(today))
    pruned = {}
    for game_date, fingerprint in state.items():
        try:
            parsed = date.fromisoformat(str(game_date))
        except ValueError:
            continue
        if parsed >= cutoff:
            pruned[game_date] = fingerprint
    return pruned


def _line_fingerprint(line_payload):
    """The posted line as a comparable scalar, ignoring the juice.

    Only the line decides whether a snapshot is worth storing. Odds jitter by a
    cent or two on most ticks and nobody reads a sparkline to learn that -106
    became -104, so comparing on the full payload would keep ~5x the entries
    for no information anyone uses. The odds still ride along on the entries
    that are kept, which is where they are worth seeing.
    """
    if isinstance(line_payload, dict):
        raw_line = line_payload.get("line")
        if raw_line in (None, ""):
            return ("line", None)
        try:
            return ("line", float(raw_line))
        except (TypeError, ValueError):
            return ("line", str(raw_line))
    # Not a dict, so there is no line to compare. Fall back to the whole value
    # rather than dropping it, so an unexpected shape is stored on change
    # instead of disappearing.
    return ("raw", json.dumps(line_payload, sort_keys=True, default=str))


def _compact_snapshots_for_supabase(snapshots):
    """Reduce the day's snapshots to the moments a line actually moved.

    `line_movements` is one jsonb row per game_date, and Postgres cannot patch
    a jsonb value -- it rewrites and WAL-logs the whole TOASTed thing on every
    upsert. At ~57 upserts a day that made the row's size the pipeline's
    dominant write cost, which is what took the database down on 2026-11-23.

    The previous version controlled that size by keeping a trailing window of
    whole snapshots (`SUPABASE_LINE_MOVEMENT_MAX_SNAPSHOTS`), so the cost grew
    with the square of the day and the price of keeping more history was paid
    57 times over. That is backwards: measured across the 18 days in
    `data/archive/line_movements`, a day holds ~1,900 distinct
    (player, stat, book) keys over ~56 snapshots -- ~106,000 stored values --
    of which only ~3,500 are a line that differs from the one before it. The
    window was discarding the morning to make room for copies of the afternoon.

    So store transitions, not snapshots: an entry's `players` carries only the
    props whose line changed since the last entry that mentioned them. The
    first sighting of a key always counts as a change, which is how the opening
    line survives to the end of the day. A whole day now costs ~0.5MB instead
    of 1.3MB for a two-hour tail, which is ~17MB of jsonb a day instead of
    ~85MB.

    `pre_game` captures are kept whole. They are the authoritative pre-tip read
    for the games in that window, and keeping them guarantees every prop that
    was live at tip has a point there -- so a line that never moved all day
    still draws as a flat series rather than a single point. Measured at ~6%
    more jsonb, which is worth it.

    Entries keep the `{timestamp, label, players}` shape of a real snapshot, so
    `flattenIntradayMovements` on the frontend and anything else reading the
    row needs no change. Note that the series is now sparse: a reader that
    wants a prop's value at an arbitrary moment has to carry the last entry
    forward, rather than expecting every prop in every entry.

    This only governs what Supabase holds for the dashboard's sparkline. The
    durable, full-fidelity series is still every snapshot verbatim in
    `data/archive/line_movements/<date>.json`, which is what closing-line value
    is computed from.
    """
    if not isinstance(snapshots, list) or not snapshots:
        return []

    ordered = sorted(
        (snapshot for snapshot in snapshots if isinstance(snapshot, dict)),
        key=lambda snapshot: str(snapshot.get("timestamp") or ""),
    )

    last_line_by_key = {}
    compacted = []

    for snapshot in ordered:
        players = snapshot.get("players")
        if not isinstance(players, dict):
            continue

        # The pre-tip read is stored in full, so every prop still quoted at tip
        # has a point there even if its line never moved.
        keep_whole = snapshot.get("label") == "pre_game"
        entry_players = {}

        for player_id, player_data in players.items():
            if not isinstance(player_data, dict):
                continue
            props = player_data.get("props")
            if not isinstance(props, dict):
                continue

            moved_props = {}
            for stat_type, sportsbook_map in props.items():
                if not isinstance(sportsbook_map, dict):
                    continue

                moved_books = {}
                for sportsbook, line_payload in sportsbook_map.items():
                    key = (str(player_id), str(stat_type), str(sportsbook))
                    fingerprint = _line_fingerprint(line_payload)
                    # `_UNSEEN` rather than None: a book posting no line is a
                    # real, comparable state, and must not read as never seen.
                    if keep_whole or last_line_by_key.get(key, _UNSEEN) != fingerprint:
                        moved_books[sportsbook] = line_payload
                    last_line_by_key[key] = fingerprint

                if moved_books:
                    moved_props[stat_type] = moved_books

            if moved_props:
                # The player's scalars (name, team, game_id, game_date) ride on
                # every entry that mentions them. game_date in particular is
                # what the sparkline filters on, so an entry without it would
                # be silently dropped from the chart.
                entry_players[player_id] = {
                    **{key: value for key, value in player_data.items() if key != "props"},
                    "props": moved_props,
                }

        if entry_players:
            compacted.append({
                "timestamp": snapshot.get("timestamp"),
                "label": snapshot.get("label"),
                "players": entry_players,
            })

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

    next_sync_state = _prune_line_movement_sync_state(next_sync_state)

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


def _get_recent_historical_dates(historical_odds, max_days: int):
    return sorted(
        [
            game_date for game_date, payload in (historical_odds or {}).items()
            if isinstance(game_date, str) and isinstance(payload, dict) and payload
        ],
        reverse=True,
    )[:max(1, max_days)]


def sync_recent_historical_odds_from_file(historical_odds_path: str, max_days: int = 5):
    historical_odds_path, historical_odds = _load_historical_archive(historical_odds_path)
    if historical_odds is None:
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return True

    valid_dates = _get_recent_historical_dates(historical_odds, max_days)

    if not valid_dates:
        return True

    success = True
    for game_date in valid_dates:
        success = upsert_historical_odds_from_file(historical_odds_path, game_date) and success
    return success


def sync_recent_historical_player_props_from_file(historical_odds_path: str, max_days: int = 5, include_pp: bool = False):
    historical_odds_path, historical_odds = _load_historical_archive(historical_odds_path)
    if historical_odds is None:
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return True

    valid_dates = _get_recent_historical_dates(historical_odds, max_days)

    if not valid_dates:
        return True

    success = True
    for game_date in valid_dates:
        success = upsert_historical_player_props_from_file(
            historical_odds_path,
            game_date,
            include_pp=include_pp,
        ) and success
    return success
