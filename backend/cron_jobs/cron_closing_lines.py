import os
import json
import logging
import argparse
import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sys
import concurrent.futures
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scrapers'))
from utils.logging_utils import configure_logging, log_section, log_status
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
from utils.player_matcher import PlayerMatcher
from utils.prop_date_resolver import resolve_prop_game_date
from utils.snapshot_manager import SnapshotManager
from utils.odds_csv import write_odds_csv
from utils.prizepicks_archive import archive_prizepicks_rows
from utils.upsert_market_history import (
    historical_odds_legacy_fallback_enabled,
    upsert_historical_odds_from_file,
    upsert_live_historical_player_props,
    upsert_line_movements_from_file,
)

configure_logging()
logger = logging.getLogger("CronPreTipRefresh")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
MASTER_PATH = os.path.join(DATA_DIR, "master_feed.json")
SCHEDULE_PATH = os.path.join(DATA_DIR, "today_schedule.json")
# We will use this file to track which games we have already scraped today
# so we don't double-scrape a game if the cron checks multiple times in a window.
STATE_PATH = os.path.join(BASE_DIR, "logs", "cron_state_today.json")

# Map betting props to internal names to match aggregator
PROP_MAP = {
    'points': 'PTS', 'rebounds': 'REB', 'assists': 'AST',
    'threes': 'FG3M', 'blocks': 'BLK', 'steals': 'STL',
    'pra': 'PTS+REB+AST', 'pr': 'PTS+REB', 'pa': 'PTS+AST', 'ra': 'REB+AST', 'stocks': 'STL+BLK'
}


def normalize_lookup_name(name):
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
    name = name.replace(".", "").replace("'", "")
    name = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', name).strip()
    return " ".join(name.split())

def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))


def normalize_game_date(raw_value):
    """Normalize scraper or schedule timestamps to an ET YYYY-MM-DD string."""
    if not raw_value:
        return ""
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
            return stripped
        try:
            dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except ValueError:
            return stripped[:10]
    return str(raw_value)


def build_schedule_maps(schedule):
    games = schedule.get("games", []) if isinstance(schedule, dict) else []
    now = get_et_now()
    sorted_games = sorted(
        games,
        key=lambda g: (
            normalize_game_date(g.get("game_date")),
            g.get("game_time_utc") or "",
        ),
    )

    team_date_to_game = {}
    active_game_by_team = {}
    fallback_game_by_team = {}

    for game in sorted_games:
        game_date = normalize_game_date(game.get("game_date"))
        game_id = game.get("game_id")
        if not game_id or not game_date:
            continue

        teams = [game.get("home_team_tricode"), game.get("away_team_tricode")]
        deadline_dt = None
        deadline_str = game.get("closing_scrape_deadline")
        if deadline_str:
            try:
                deadline_dt = datetime.fromisoformat(deadline_str)
            except ValueError:
                deadline_dt = None

        for team in filter(None, teams):
            team_date_to_game[(team, game_date)] = game
            fallback_game_by_team.setdefault(team, game)
            if (
                team not in active_game_by_team
                and deadline_dt is not None
                and deadline_dt >= (now - timedelta(minutes=15))
            ):
                active_game_by_team[team] = game

    for team, game in fallback_game_by_team.items():
        active_game_by_team.setdefault(team, game)

    return {
        "team_date_to_game": team_date_to_game,
        "active_game_by_team": active_game_by_team,
    }

def load_master_feed_maps():
    """Load player/team lookups and a robust matcher from master_feed.json."""
    players_metadata = []
    id_to_team = {}
    if os.path.exists(MASTER_PATH):
        try:
            with open(MASTER_PATH, 'r') as f:
                master_feed = json.load(f)
                for p in master_feed:
                    pid = str(p.get("id", ""))
                    team = p.get("team", "")
                    name = p.get("name", "")
                    if pid and name:
                        players_metadata.append({
                            "PLAYER_ID": pid,
                            "PLAYER_NAME": name,
                            "TEAM_ABBREVIATION": team,
                        })
                    if pid and team:
                        id_to_team[pid] = team
        except Exception as e:
            log_status(logger, "FAIL", "Could not load master feed IDs", error=e)
    return PlayerMatcher(players_metadata), id_to_team


def persist_raw_odds_csvs(dk_data, fd_data):
    """Persist the latest raw scraper output so downstream upserts use fresh data."""
    try:
        target_game_date = get_et_now().date().isoformat()
        write_odds_csv(
            os.path.join(DATA_DIR, "draftkings.csv"),
            dk_data,
            preserve_on_empty=True,
            target_game_date=target_game_date,
            sportsbook_label="DraftKings",
        )
        write_odds_csv(
            os.path.join(DATA_DIR, "fanduel.csv"),
            fd_data,
            preserve_on_empty=True,
            target_game_date=target_game_date,
            sportsbook_label="FanDuel",
        )
    except Exception as e:
        log_status(logger, "WARN", "Unable to persist raw odds CSVs", error=e)


def is_live_or_final_game(game):
    return bool((game or {}).get("is_live")) or bool((game or {}).get("is_final"))

def scrape_and_shape_odds(is_closing=False, allowed_game_ids=None):
    """Run scrapers, map names to IDs, align data for SnapshotManager."""
    log_status(logger, "RUN", "Odds scrapers")
    
    dk_data = []
    fd_data = []
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        f1 = executor.submit(draftkings.fetch_dk_odds)
        f2 = executor.submit(fanduel.fetch_odds)
        
        # --- DraftKings Safeguard ---
        try:
            dk_data = f1.result(timeout=120) 
        except concurrent.futures.TimeoutError:
            log_status(logger, "FAIL", "DraftKings scraper timed out")
        except Exception as e:
            log_status(logger, "FAIL", "DraftKings scraper crashed", error=e)
            
        # --- FanDuel Safeguard ---
        try:
            fd_data = f2.result(timeout=120)
        except concurrent.futures.TimeoutError:
            log_status(logger, "FAIL", "FanDuel scraper timed out")
        except Exception as e:
            log_status(logger, "FAIL", "FanDuel scraper crashed", error=e)
            
    if not dk_data: dk_data = []
    if not fd_data: fd_data = []

    matcher, id_to_team = load_master_feed_maps()
    schedule = {}
    if os.path.exists(SCHEDULE_PATH):
        try:
            with open(SCHEDULE_PATH, "r") as f:
                schedule = json.load(f)
        except Exception as e:
            log_status(logger, "WARN", "Unable to load schedule context while shaping odds", error=e)

    schedule_maps = build_schedule_maps(schedule)
    allowed_game_ids = {str(gid) for gid in (allowed_game_ids or []) if gid}
    players_dict = {}
    skipped_schedule_mismatch = 0
    skipped_non_target_game = 0
    skipped_live_game = 0
    filtered_fd_data = []
    filtered_dk_data = []
    
    # Process FanDuel first
    for row in fd_data:
        pid = matcher.match_player(
            row.get("player", ""),
            row.get("team", "UNK"),
        )
        pid = str(pid) if pid else normalize_lookup_name(row.get("player", ""))
        
        prop = PROP_MAP.get(row.get("prop_type", ""), row.get("prop_type", "")).upper()
        if not prop: continue

        canonical_team = id_to_team.get(str(pid), row.get("team", ""))
        row_game_date, _ = resolve_prop_game_date(
            row.get("game_date"),
            canonical_team=canonical_team,
            game_label=row.get("game", ""),
            schedule_rows=schedule.get("games", []) if isinstance(schedule, dict) else [],
            now_et=get_et_now(),
        )
        schedule_game = schedule_maps["team_date_to_game"].get((canonical_team, row_game_date))
        active_team_game = schedule_maps["active_game_by_team"].get(canonical_team)
        schedule_context_game = schedule_game or active_team_game

        if is_live_or_final_game(schedule_context_game):
            skipped_live_game += 1
            continue

        if allowed_game_ids:
            if not schedule_game or str(schedule_game.get("game_id")) not in allowed_game_ids:
                skipped_non_target_game += 1
                continue
        elif is_closing and active_team_game and schedule_game:
            if str(schedule_game.get("game_id")) != str(active_team_game.get("game_id")):
                skipped_schedule_mismatch += 1
                continue
        elif is_closing and active_team_game and row_game_date:
            skipped_schedule_mismatch += 1
            continue

        if pid not in players_dict:
            players_dict[pid] = {
                "name": row.get("player", ""),
                "team": canonical_team,
                "game_id": schedule_game.get("game_id") if schedule_game else None,
                "game_date": row_game_date,
                "props": {},
                "fanduel_inPlay": row.get("inPlay", False),
                "fanduel_available": True
            }
        
        if prop not in players_dict[pid]["props"]:
            players_dict[pid]["props"][prop] = {}
            
        players_dict[pid]["props"][prop]["fanduel"] = {
            "line": row.get("line"),
            "over": row.get("over_odds"),
            "under": row.get("under_odds")
        }
        filtered_fd_data.append(row)

    # Process DraftKings
    for row in dk_data:
        pid = matcher.match_player(
            row.get("player", ""),
            row.get("team", "UNK"),
            row.get("team_options"),
        )
        pid = str(pid) if pid else normalize_lookup_name(row.get("player", ""))
        
        prop = PROP_MAP.get(row.get("prop_type", ""), row.get("prop_type", "")).upper()
        if not prop: continue

        canonical_team = id_to_team.get(str(pid), row.get("team", ""))
        row_game_date, _ = resolve_prop_game_date(
            row.get("game_date"),
            canonical_team=canonical_team,
            game_label=row.get("game", ""),
            schedule_rows=schedule.get("games", []) if isinstance(schedule, dict) else [],
            now_et=get_et_now(),
        )
        schedule_game = schedule_maps["team_date_to_game"].get((canonical_team, row_game_date))
        active_team_game = schedule_maps["active_game_by_team"].get(canonical_team)
        schedule_context_game = schedule_game or active_team_game

        if is_live_or_final_game(schedule_context_game):
            skipped_live_game += 1
            continue

        if allowed_game_ids:
            if not schedule_game or str(schedule_game.get("game_id")) not in allowed_game_ids:
                skipped_non_target_game += 1
                continue
        elif is_closing and active_team_game and schedule_game:
            if str(schedule_game.get("game_id")) != str(active_team_game.get("game_id")):
                skipped_schedule_mismatch += 1
                continue
        elif is_closing and active_team_game and row_game_date:
            skipped_schedule_mismatch += 1
            continue

        if pid not in players_dict:
            players_dict[pid] = {
                "name": row.get("player", ""),
                "team": canonical_team,
                "game_id": schedule_game.get("game_id") if schedule_game else None,
                "game_date": row_game_date,
                "props": {},
                "draftkings_available": True
            }
        else:
            players_dict[pid]["draftkings_available"] = True
            if players_dict[pid]["team"] == "":
                players_dict[pid]["team"] = canonical_team
            if not players_dict[pid].get("game_id") and schedule_game:
                players_dict[pid]["game_id"] = schedule_game.get("game_id")
            if not players_dict[pid].get("game_date"):
                players_dict[pid]["game_date"] = row_game_date
                
        if prop not in players_dict[pid]["props"]:
            players_dict[pid]["props"][prop] = {}
            
        players_dict[pid]["props"][prop]["draftkings"] = {
            "line": row.get("line"),
            "over": row.get("over_odds"),
            "under": row.get("under_odds")
        }
        filtered_dk_data.append(row)

    if skipped_schedule_mismatch:
        log_status(
            logger,
            "SKIP",
            "Skipped non-active scheduled-game odds rows",
            skipped=skipped_schedule_mismatch,
        )
    if skipped_non_target_game:
        log_status(
            logger,
            "SKIP",
            "Skipped non-target closing-game odds rows",
            skipped=skipped_non_target_game,
        )
    if skipped_live_game:
        log_status(
            logger,
            "SKIP",
            "Skipped live/final odds rows",
            skipped=skipped_live_game,
        )

    persist_raw_odds_csvs(filtered_dk_data, filtered_fd_data)

    log_status(
        logger,
        "OK",
        "Odds scrape complete",
        dk_rows=len(dk_data),
        fd_rows=len(fd_data),
        mapped_players=len(players_dict),
    )

    return players_dict

def load_cron_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cron_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)

def main(dry_run=False, preselected_games=None):
    now = get_et_now()
    today_date = now.strftime("%Y-%m-%d")
    log_section(logger, "Closing line sweep", date=today_date, dry_run=dry_run)
    
    # Load state
    state = load_cron_state()
    # Reset state if it's a new day
    if state.get("date") != today_date:
        state = {"date": today_date, "scraped_games": []}
    
    # Read schedule
    if not os.path.exists(SCHEDULE_PATH):
        log_status(logger, "FAIL", "Schedule file not found", path=SCHEDULE_PATH)
        return
        
    try:
        with open(SCHEDULE_PATH, 'r') as f:
            sched = json.load(f)
    except Exception as e:
        log_status(logger, "FAIL", "Could not load schedule JSON", error=e)
        return
        
    games_to_scrape = []

    if preselected_games is not None:
        schedule_by_id = {
            str(game.get("game_id")): game
            for game in sched.get("games", [])
            if isinstance(game, dict) and game.get("game_id")
        }
        for selected in preselected_games:
            if not isinstance(selected, dict):
                continue
            game_id = str(selected.get("game_id") or "")
            if not game_id or game_id in state["scraped_games"]:
                continue
            selected_deadline = selected.get("deadline")
            if isinstance(selected_deadline, str):
                try:
                    selected_deadline = datetime.fromisoformat(selected_deadline)
                except ValueError:
                    selected_deadline = None
            games_to_scrape.append({
                "game_id": game_id,
                "deadline": selected_deadline,
                "matchup": selected.get("matchup")
                or schedule_by_id.get(game_id, {}).get("matchup", "Unknown"),
            })
    else:
        for game in sched.get("games", []):
            game_id = game.get("game_id")
            deadline_str = game.get("closing_scrape_deadline")
            if not deadline_str or not game_id:
                continue
                
            # If we already scraped this game today, skip
            if game_id in state["scraped_games"]:
                continue
                
            try:
                deadline_dt = datetime.fromisoformat(deadline_str)
                # The target logic: Scrape if we are within 12 minutes before the deadline
                # If cron runs every 5 mins (e.g. at XX:15, XX:20) and game is at XX:30
                # At XX:15: deadline is 15 mins away (delta=15)
                # At XX:20: deadline is 10 mins away (delta=10) -> TRIGGER!
                
                delta = deadline_dt - now
                delta_minutes = delta.total_seconds() / 60.0
                
                # If the game is starting in <= 12 minutes, and it hasn't started yet
                # (or it started max 5 mins ago and we missed it)
                if -5 <= delta_minutes <= 12:
                    games_to_scrape.append({
                        "game_id": game_id,
                        "deadline": deadline_dt,
                        "matchup": game.get("matchup", "Unknown")
                    })
            except ValueError:
                pass

    if not games_to_scrape:
        log_status(logger, "SKIP", "No games within the closing window")
        return False
        
    log_status(
        logger,
        "RUN",
        "Triggering pre-tip props refresh",
        games=len(games_to_scrape),
        matchups=[g["matchup"] for g in games_to_scrape],
    )
    
    if dry_run:
        log_status(logger, "OK", "Dry run complete")
        return True
        
    # Run the expensive logic
    try:
        players_data = scrape_and_shape_odds(
            is_closing=True,
            allowed_game_ids=[g["game_id"] for g in games_to_scrape],
        )
        if not players_data:
            log_status(logger, "WARN", "Pre-tip props refresh produced no mapped players")
            return False
        sm = SnapshotManager()
                
        # Send to SnapshotManager
        closing_summary = sm.process_closing_lines(players_data)
        # SnapshotManager ALSO writes a final intraday snapshot for "pre_game"
        sm.write_snapshot("pre_game", players_data, bypass_dedupe=True, filter_to_active_schedule=True)
        
        # Upsert fresh props to Supabase (non-fatal)
        # DATA_DIR is defined at module level in this file
        props_ok = True
        line_movements_ok = True
        normalized_historical_ok = True
        legacy_historical_ok = True
        legacy_historical_fallback = historical_odds_legacy_fallback_enabled()
        pp_rows = []

        try:
            from utils.upsert_props import run_odds_update
            from scrapers import fetch_odds_prizepicks as prizepicks
            pp_path = None
            if prizepicks.prizepicks_enabled():
                try:
                    pp_rows, _pp_diagnostics = prizepicks.fetch_and_write_rows(output_path=prizepicks.DEFAULT_OUTPUT_PATH)
                    pp_path = str(prizepicks.DEFAULT_OUTPUT_PATH)
                    pp_archive = archive_prizepicks_rows(
                        pp_rows,
                        allowed_game_ids=[g["game_id"] for g in games_to_scrape],
                    )
                    if pp_archive.get("rows_archived", 0):
                        log_status(
                            logger,
                            "OK",
                            "PrizePicks local archive updated",
                            rows=pp_archive.get("rows_archived", 0),
                            dates=pp_archive.get("dates_written", 0),
                        )
                    else:
                        log_status(
                            logger,
                            "SKIP",
                            "PrizePicks local archive had no closing-window rows",
                            seen=pp_archive.get("rows_seen", 0),
                            skipped_not_target_game=pp_archive.get("skipped_not_target_game", 0),
                        )
                except Exception as error:
                    cached_pp_path = prizepicks.cached_output_path_if_recent(prizepicks.DEFAULT_OUTPUT_PATH)
                    if cached_pp_path:
                        pp_path = str(cached_pp_path)
                        log_status(
                            logger,
                            "WARN",
                            "PrizePicks refresh failed; using recent cached CSV",
                            error=error,
                            cached_path=pp_path,
                        )
                    else:
                        log_status(logger, "WARN", "PrizePicks refresh failed", error=error)
            props_ok = run_odds_update(
                dk_path=os.path.join(DATA_DIR, "draftkings.csv"),
                fd_path=os.path.join(DATA_DIR, "fanduel.csv"),
                stats_path=os.path.join(DATA_DIR, "season_stats.csv"),
                pp_path=pp_path,
            )
        except Exception as e:
            log_status(logger, "WARN", "Props upsert failed", error=e)
            props_ok = False

        try:
            line_movements_ok = upsert_line_movements_from_file(os.path.join(DATA_DIR, "line_movements_today.json"))
        except Exception as e:
            log_status(logger, "WARN", "Line movements upsert failed", error=e)
            line_movements_ok = False

        try:
            normalized_historical_ok = upsert_live_historical_player_props(closing_summary.get("records", []))
        except Exception as e:
            log_status(logger, "WARN", "Normalized historical odds upsert failed", error=e)
            normalized_historical_ok = False

        if legacy_historical_fallback:
            try:
                historical_path = os.path.join(BASE_DIR, "data", "archive", "historical_odds.json")
                archive_dates = sorted({pdata.get("game_date") or get_et_now().strftime("%Y-%m-%d") for pdata in players_data.values()})
                for archive_date in archive_dates:
                    legacy_historical_ok = upsert_historical_odds_from_file(historical_path, archive_date) and legacy_historical_ok
            except Exception as e:
                log_status(logger, "WARN", "Historical odds upsert failed", error=e)
                legacy_historical_ok = False

        historical_ok = normalized_historical_ok and legacy_historical_ok

        if not props_ok or not line_movements_ok or not historical_ok:
            log_status(
                logger,
                "WARN",
                "Pre-tip refresh DB sync incomplete",
                props_ok=props_ok,
                line_movements_ok=line_movements_ok,
                historical_ok=historical_ok,
                normalized_historical_ok=normalized_historical_ok,
                legacy_historical_ok=legacy_historical_ok,
                legacy_historical_fallback_enabled=legacy_historical_fallback,
            )
            return False

        for g in games_to_scrape:
            if g["game_id"] not in state["scraped_games"]:
                state["scraped_games"].append(g["game_id"])

        save_cron_state(state)
        log_status(logger, "OK", "Pre-tip props refresh complete", games=len(games_to_scrape))
        return True

    except Exception as e:
        log_status(logger, "FAIL", "Failed to execute pre-tip props refresh", error=e)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron script for pre-tip props refresh and local closing archive")
    parser.add_argument("--dry-run", action="store_true", help="Run checks without actually scraping")
    args = parser.parse_args()
    
    main(dry_run=args.dry_run)
