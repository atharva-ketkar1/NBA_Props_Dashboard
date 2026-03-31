"""
upsert_props.py
Loads raw DK/FD CSVs + season stats, resolves player IDs via PlayerMatcher,
and batch-upserts into the player_props table.

Called by:
  - run_pipeline.py  (after the daily aggregation)
  - cron_line_movement.py  (after each intraday scrape)
  - cron_closing_lines.py  (after each closing-line capture)

All callers pass file paths explicitly — no hardcoded paths, no shared
constants imported from run_pipeline.py.
"""

import os
import ast
import hashlib
import json
import logging
import math
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.logging_utils import log_status
from utils.player_matcher import PlayerMatcher
from utils.prop_date_resolver import load_schedule_rows, resolve_prop_game_date
from utils.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

ET_ZONE = ZoneInfo("America/New_York")
PP_ARCHIVE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "archive",
    "prizepicks",
)
SYNC_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "current",
    "player_props_sync_state.json",
)

# Mirrors PROP_MAP in aggregator.py — kept in sync manually.
# Maps raw prop_type strings from scrapers → internal stat keys.
PROP_MAP = {
    'points':   'PTS',
    'rebounds': 'REB',
    'assists':  'AST',
    'threes':   'FG3M',
    'blocks':   'BLK',
    'steals':   'STL',
    'pra':      'PTS+REB+AST',
    'pr':       'PTS+REB',
    'pa':       'PTS+AST',
    'ra':       'REB+AST',
    'stocks':   'STL+BLK',
}


def _load_sync_state():
    if not os.path.exists(SYNC_STATE_PATH):
        return {}
    try:
        with open(SYNC_STATE_PATH, "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not read player_props sync state: %s", exc)
        return {}


def _save_sync_state(state):
    os.makedirs(os.path.dirname(SYNC_STATE_PATH), exist_ok=True)
    temp_path = f"{SYNC_STATE_PATH}.tmp"
    with open(temp_path, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(temp_path, SYNC_STATE_PATH)


def _row_sync_key(row):
    return "|".join(
        [
            str(row.get("player_id", "")),
            str(row.get("stat_type", "")),
            str(row.get("sportsbook", "")),
            str(row.get("game_date", "")),
        ]
    )


def _row_fingerprint(row):
    payload = {
        "line": _normalize_float(row.get("line")),
        "over_odds": _normalize_int(row.get("over_odds")),
        "under_odds": _normalize_int(row.get("under_odds")),
        "implied": _normalize_float(row.get("implied"), default=0.0),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def _is_missing(value):
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _normalize_float(value, default=None):
    if _is_missing(value):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _normalize_int(value, default=None):
    number = _normalize_float(value, default=None)
    if number is None:
        return default
    return int(number)


def _build_schedule_lookup(schedule_rows):
    lookup = {}
    for game in schedule_rows or []:
        game_date = str(game.get("game_date") or "").strip()
        if not game_date:
            continue
        for team_key in ("home_team_tricode", "away_team_tricode"):
            team = str(game.get(team_key) or "").strip()
            if team:
                lookup[(team, game_date)] = game
    return lookup


def _is_live_or_final_schedule_game(game):
    return bool((game or {}).get("is_live")) or bool((game or {}).get("is_final"))


def _load_archived_live_prizepicks_rows(schedule_rows):
    live_games_by_date = {}
    for game in schedule_rows or []:
        if not _is_live_or_final_schedule_game(game):
            continue
        game_date = str(game.get("game_date") or "").strip()
        game_id = str(game.get("game_id") or "").strip()
        if not game_date or not game_id:
            continue
        live_games_by_date.setdefault(game_date, set()).add(game_id)

    archived_rows = []
    loaded_dates = 0
    for game_date, allowed_game_ids in live_games_by_date.items():
        archive_path = os.path.join(PP_ARCHIVE_DIR, f"{game_date}.json")
        if not os.path.exists(archive_path):
            continue

        try:
            with open(archive_path, "r") as handle:
                archive_data = json.load(handle)
        except Exception as exc:
            logger.warning("Could not read PrizePicks archive %s: %s", archive_path, exc)
            continue

        if not isinstance(archive_data, dict):
            continue

        loaded_dates += 1
        for player_key, record in archive_data.items():
            if not isinstance(record, dict):
                continue

            archived_game_id = str(record.get("game_id") or "").strip()
            if archived_game_id not in allowed_game_ids:
                continue

            props = record.get("props")
            if not isinstance(props, dict):
                continue

            for stat_key, book_map in props.items():
                if not isinstance(book_map, dict):
                    continue

                pp_line = book_map.get("pp")
                if not isinstance(pp_line, dict):
                    continue

                line = _normalize_float(pp_line.get("line"))
                if line is None:
                    continue

                try:
                    player_id = int(player_key)
                except (TypeError, ValueError):
                    player_id = player_key

                archived_rows.append({
                    "player_id": player_id,
                    "stat_type": str(stat_key or "").strip().upper(),
                    "sportsbook": "pp",
                    "line": line,
                    "over_odds": _normalize_int(pp_line.get("over")),
                    "under_odds": _normalize_int(pp_line.get("under")),
                    "implied": 0.0,
                    "game_date": game_date,
                })

    return archived_rows, loaded_dates


def run_odds_update(
    dk_path: str,
    fd_path: str,
    stats_path: str,
    game_date: str = None,
    pp_path: str = None,
):
    """
    Load raw sportsbook CSV files and season stats, resolve player IDs via
    PlayerMatcher, then batch-upsert into the player_props table.

    Parameters
    ----------
    dk_path : str
        Absolute or relative path to draftkings.csv
    fd_path : str
        Absolute or relative path to fanduel.csv
    pp_path : str, optional
        Absolute or relative path to prizepicks.csv
    stats_path : str
        Absolute or relative path to season_stats.csv
        (needed to build the PlayerMatcher roster)
    game_date : str, optional
        ISO date string 'YYYY-MM-DD'. Defaults to today.
    """
    if game_date is None:
        game_date = datetime.now(ET_ZONE).date().isoformat()

    def normalize_row_game_date(raw_value):
        if not raw_value:
            return game_date
        if isinstance(raw_value, str):
            stripped = raw_value.strip()
            if len(stripped) >= 10 and stripped[4] == "-" and stripped[7] == "-":
                if len(stripped) == 10:
                    return stripped
                try:
                    dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                    return dt.astimezone(ET_ZONE).strftime("%Y-%m-%d")
                except ValueError:
                    return stripped[:10]
        return game_date

    # --- Load CSVs ---
    try:
        df_stats = pd.read_csv(stats_path)
    except Exception as e:
        logger.error("Failed to load season_stats: %s", e)
        return False

    df_dk = pd.DataFrame()
    df_fd = pd.DataFrame()
    df_pp = pd.DataFrame()

    try:
        df_dk = pd.read_csv(dk_path)
    except pd.errors.EmptyDataError:
        df_dk = pd.DataFrame()
    except Exception as e:
        logger.warning("Failed to load DraftKings CSV (%s): %s", dk_path, e)

    try:
        df_fd = pd.read_csv(fd_path)
    except pd.errors.EmptyDataError:
        df_fd = pd.DataFrame()
    except Exception as e:
        logger.warning("Failed to load FanDuel CSV (%s): %s", fd_path, e)

    if pp_path:
        try:
            df_pp = pd.read_csv(pp_path)
        except pd.errors.EmptyDataError:
            df_pp = pd.DataFrame()
        except Exception as e:
            logger.warning("Failed to load PrizePicks CSV (%s): %s", pp_path, e)

    # --- Build PlayerMatcher from season stats roster ---
    stats_records = df_stats[['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION']].to_dict('records')
    matcher = PlayerMatcher(stats_records)
    team_by_pid = {
        int(row['PLAYER_ID']): str(row.get('TEAM_ABBREVIATION', '') or '').strip()
        for _, row in df_stats.iterrows()
    }
    schedule_rows = load_schedule_rows(os.path.join(os.path.dirname(__file__), '..', 'data', 'current'))
    schedule_lookup = _build_schedule_lookup(schedule_rows)
    archived_pp_rows, archived_pp_dates = _load_archived_live_prizepicks_rows(schedule_rows)

    if df_dk.empty and df_fd.empty and df_pp.empty and not archived_pp_rows:
        logger.warning("All sportsbook CSVs are empty — nothing to upsert.")
        return False

    # --- Build upsert rows ---
    rows = []
    resolved_from_schedule = 0
    unresolved_missing_dates = 0
    skipped_missing_line = 0
    skipped_live_game = 0
    for df, book in [(df_dk, 'dk'), (df_fd, 'fd'), (df_pp, 'pp')]:
        if df.empty:
            continue
        for _, row in df.iterrows():
            # --- Replicate team_options handling from aggregator.process_odds() ---
            # DK uses this to disambiguate players on two-way contracts / traded players.
            team_opts = []
            raw_opts = row.get('team_options')
            if isinstance(raw_opts, str) and '[' in raw_opts:
                try:
                    team_opts = ast.literal_eval(raw_opts)
                except Exception:
                    pass

            pid = matcher.match_player(
                row.get('player', ''),
                row.get('team', 'UNK'),
                team_opts
            )
            if not pid:
                continue

            canonical_team = team_by_pid.get(int(pid), row.get('team', 'UNK'))
            resolved_game_date, date_source = resolve_prop_game_date(
                row.get('game_date'),
                canonical_team=canonical_team,
                game_label=row.get('game', ''),
                schedule_rows=schedule_rows,
            )
            if date_source.startswith('schedule'):
                resolved_from_schedule += 1
            elif not resolved_game_date:
                unresolved_missing_dates += 1

            schedule_game = schedule_lookup.get((canonical_team, normalize_row_game_date(resolved_game_date)))
            if _is_live_or_final_schedule_game(schedule_game):
                skipped_live_game += 1
                continue

            raw_prop = row.get('prop_type', '')
            stat_key = PROP_MAP.get(raw_prop, raw_prop).upper()
            line = _normalize_float(row.get('line'))
            if line is None:
                skipped_missing_line += 1
                continue

            rows.append({
                'player_id':  pid,
                'stat_type':  stat_key,
                'sportsbook': book,
                'line':       line,
                'over_odds':  _normalize_int(row.get('over_odds')),
                'under_odds': _normalize_int(row.get('under_odds')),
                'implied':    _normalize_float(row.get('implied_prob'), default=0.0),
                'game_date':  normalize_row_game_date(resolved_game_date),
            })

    if archived_pp_rows:
        rows.extend(archived_pp_rows)
        log_status(
            logger,
            "INFO",
            "Loaded archived PrizePicks pregame rows for live/final games",
            rows=len(archived_pp_rows),
            dates=archived_pp_dates,
        )

    if rows:
        deduped_rows = {}
        for row in rows:
            deduped_rows[_row_sync_key(row)] = row
        rows = list(deduped_rows.values())

    if not rows:
        logger.warning("No rows resolved — check matcher and CSV column names.")
        return False

    if resolved_from_schedule:
        log_status(logger, "INFO", "Resolved prop rows via schedule fallback", rows=resolved_from_schedule)
    if unresolved_missing_dates:
        log_status(
            logger,
            "WARN",
            "Prop rows fell back to the run date",
            rows=unresolved_missing_dates,
        )
    if skipped_missing_line:
        log_status(
            logger,
            "WARN",
            "Skipped prop rows missing a usable line",
            rows=skipped_missing_line,
        )
    if skipped_live_game:
        log_status(
            logger,
            "SKIP",
            "Skipped live/final player prop rows",
            rows=skipped_live_game,
        )

    sync_state = _load_sync_state()
    next_sync_state = {}
    changed_rows = []
    for row in rows:
        sync_key = _row_sync_key(row)
        fingerprint = _row_fingerprint(row)
        next_sync_state[sync_key] = fingerprint
        if sync_state.get(sync_key) != fingerprint:
            changed_rows.append(row)

    if not changed_rows:
        log_status(logger, "SKIP", "player_props sync unchanged", date=game_date, rows=len(rows))
        try:
            _save_sync_state(next_sync_state)
        except Exception as exc:
            logger.warning("Could not persist player_props sync state: %s", exc)
        return True

    log_status(
        logger,
        "RUN",
        "player_props sync",
        changed=len(changed_rows),
        prepared=len(rows),
        date=game_date,
    )

    # --- Batch upsert in chunks of 100 to avoid request size limits ---
    sb = get_supabase_client()
    all_chunks_ok = True
    for i in range(0, len(changed_rows), 100):
        chunk = changed_rows[i:i + 100]
        try:
            sb.table('player_props').upsert(
                chunk,
                on_conflict='player_id,stat_type,sportsbook,game_date'
            ).execute()
        except Exception as e:
            logger.error("Upsert failed for chunk %d-%d: %s", i, i + len(chunk), e)
            all_chunks_ok = False

    if all_chunks_ok:
        try:
            _save_sync_state(next_sync_state)
        except Exception as exc:
            logger.warning("Could not persist player_props sync state: %s", exc)
        log_status(
            logger,
            "OK",
            "player_props sync complete",
            changed=len(changed_rows),
            prepared=len(rows),
            date=game_date,
        )
    return all_chunks_ok


if __name__ == "__main__":
    # Quick smoke test — run from the backend/ directory with env vars set
    import sys
    BASE = os.path.join(os.path.dirname(__file__), '..', 'data', 'current')
    run_odds_update(
        dk_path=os.path.join(BASE, 'draftkings.csv'),
        fd_path=os.path.join(BASE, 'fanduel.csv'),
        stats_path=os.path.join(BASE, 'season_stats.csv'),
    )
