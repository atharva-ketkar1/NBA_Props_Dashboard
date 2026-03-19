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
import logging
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.player_matcher import PlayerMatcher
from utils.prop_date_resolver import load_schedule_rows, resolve_prop_game_date
from utils.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

ET_ZONE = ZoneInfo("America/New_York")

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


def run_odds_update(dk_path: str, fd_path: str, stats_path: str, game_date: str = None):
    """
    Load raw DK/FD CSV files and season stats, resolve player IDs via
    PlayerMatcher, then batch-upsert into the player_props table.

    Parameters
    ----------
    dk_path : str
        Absolute or relative path to draftkings.csv
    fd_path : str
        Absolute or relative path to fanduel.csv
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

    try:
        df_dk = pd.read_csv(dk_path)
    except Exception as e:
        logger.warning("Failed to load DraftKings CSV (%s): %s", dk_path, e)

    try:
        df_fd = pd.read_csv(fd_path)
    except Exception as e:
        logger.warning("Failed to load FanDuel CSV (%s): %s", fd_path, e)

    if df_dk.empty and df_fd.empty:
        logger.warning("Both DK and FD CSVs are empty — nothing to upsert.")
        return False

    # --- Build PlayerMatcher from season stats roster ---
    stats_records = df_stats[['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION']].to_dict('records')
    matcher = PlayerMatcher(stats_records)
    team_by_pid = {
        int(row['PLAYER_ID']): str(row.get('TEAM_ABBREVIATION', '') or '').strip()
        for _, row in df_stats.iterrows()
    }
    schedule_rows = load_schedule_rows(os.path.join(os.path.dirname(__file__), '..', 'data', 'current'))

    # --- Build upsert rows ---
    rows = []
    resolved_from_schedule = 0
    unresolved_missing_dates = 0
    for df, book in [(df_dk, 'dk'), (df_fd, 'fd')]:
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

            raw_prop = row.get('prop_type', '')
            stat_key = PROP_MAP.get(raw_prop, raw_prop).upper()

            rows.append({
                'player_id':  pid,
                'stat_type':  stat_key,
                'sportsbook': book,
                'line':       row.get('line'),
                'over_odds':  row.get('over_odds'),
                'under_odds': row.get('under_odds'),
                'implied':    row.get('implied_prob', 0) or 0,
                'game_date':  normalize_row_game_date(resolved_game_date),
            })

    if not rows:
        logger.warning("No rows resolved — check matcher and CSV column names.")
        return False

    if resolved_from_schedule:
        logger.info("Resolved %d prop rows to a game_date via schedule fallback.", resolved_from_schedule)
    if unresolved_missing_dates:
        logger.warning(
            "%d prop rows were still missing a resolvable game_date and fell back to the run date.",
            unresolved_missing_dates,
        )

    logger.info("Upserting %d player_props rows for %s...", len(rows), game_date)

    # --- Batch upsert in chunks of 100 to avoid request size limits ---
    sb = get_supabase_client()
    all_chunks_ok = True
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        try:
            sb.table('player_props').upsert(
                chunk,
                on_conflict='player_id,stat_type,sportsbook,game_date'
            ).execute()
        except Exception as e:
            logger.error("Upsert failed for chunk %d-%d: %s", i, i + len(chunk), e)
            all_chunks_ok = False

    if all_chunks_ok:
        logger.info("player_props upsert complete.")
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
