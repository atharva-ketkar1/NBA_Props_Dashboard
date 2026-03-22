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
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
from utils.player_matcher import PlayerMatcher
from utils.prop_date_resolver import resolve_prop_game_date
from utils.snapshot_manager import SnapshotManager
from utils.upsert_market_history import (
    upsert_historical_odds_from_file,
    upsert_line_movements_from_file,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CronClosingLines")

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
            logger.error(f"Error loading master feed for IDs: {e}")
    return PlayerMatcher(players_metadata), id_to_team


def persist_raw_odds_csvs(dk_data, fd_data):
    """Persist the latest raw scraper output so downstream upserts use fresh data."""
    try:
        pd.DataFrame(dk_data or []).to_csv(os.path.join(DATA_DIR, "draftkings.csv"), index=False)
        pd.DataFrame(fd_data or []).to_csv(os.path.join(DATA_DIR, "fanduel.csv"), index=False)
    except Exception as e:
        logger.warning(f"Unable to persist raw odds CSVs: {e}")

def scrape_and_shape_odds(is_closing=False, allowed_game_ids=None):
    """Run scrapers, map names to IDs, align data for SnapshotManager."""
    logger.info("Executing odds scrapers...")
    
    dk_data = []
    fd_data = []
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        f1 = executor.submit(draftkings.fetch_dk_odds)
        f2 = executor.submit(fanduel.fetch_odds)
        
        # --- DraftKings Safeguard ---
        try:
            dk_data = f1.result(timeout=120) 
        except concurrent.futures.TimeoutError:
            logger.error("DraftKings scraper timed out (Infinite block prevented).")
        except Exception as e:
            logger.error(f"DraftKings scraper crashed: {e}")
            
        # --- FanDuel Safeguard ---
        try:
            fd_data = f2.result(timeout=120)
        except concurrent.futures.TimeoutError:
            logger.error("FanDuel scraper timed out (Infinite block prevented).")
        except Exception as e:
            logger.error(f"FanDuel scraper crashed: {e}")
            
    if not dk_data: dk_data = []
    if not fd_data: fd_data = []

    persist_raw_odds_csvs(dk_data, fd_data)
        
    matcher, id_to_team = load_master_feed_maps()
    schedule = {}
    if os.path.exists(SCHEDULE_PATH):
        try:
            with open(SCHEDULE_PATH, "r") as f:
                schedule = json.load(f)
        except Exception as e:
            logger.warning(f"Unable to load schedule context while shaping odds: {e}")

    schedule_maps = build_schedule_maps(schedule)
    allowed_game_ids = {str(gid) for gid in (allowed_game_ids or []) if gid}
    players_dict = {}
    skipped_schedule_mismatch = 0
    skipped_non_target_game = 0
    
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
        
    if skipped_schedule_mismatch:
        logger.info(
            "Skipped %d odds rows because they matched a non-active scheduled game for that team.",
            skipped_schedule_mismatch,
        )
    if skipped_non_target_game:
        logger.info(
            "Skipped %d odds rows because they were outside the targeted closing-line games.",
            skipped_non_target_game,
        )

    logger.info(
        "Odds scrape complete: DK=%d rows, FD=%d rows, mapped_players=%d",
        len(dk_data),
        len(fd_data),
        len(players_dict),
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

def main(dry_run=False):
    logger.info("Initializing Cron Closing Lines Check...")
    now = get_et_now()
    today_date = now.strftime("%Y-%m-%d")
    
    # Load state
    state = load_cron_state()
    # Reset state if it's a new day
    if state.get("date") != today_date:
        state = {"date": today_date, "scraped_games": []}
    
    # Read schedule
    if not os.path.exists(SCHEDULE_PATH):
        logger.error(f"Schedule file not found: {SCHEDULE_PATH}")
        return
        
    try:
        with open(SCHEDULE_PATH, 'r') as f:
            sched = json.load(f)
    except Exception as e:
        logger.error(f"Could not load schedule JSON: {e}")
        return
        
    games_to_scrape = []
    
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
        logger.info("No games within the 10-minute closing window.")
        return False
        
    logger.info(f"Triggering closing lines scrape for games: {[g['matchup'] for g in games_to_scrape]}")
    
    if dry_run:
        logger.info("DRY RUN: Exiting before actual scrape.")
        return True
        
    # Run the expensive logic
    try:
        players_data = scrape_and_shape_odds(
            is_closing=True,
            allowed_game_ids=[g["game_id"] for g in games_to_scrape],
        )
        if not players_data:
            logger.warning("Closing lines scrape produced no mapped players for the targeted games.")
            return False
        sm = SnapshotManager()
                
        # Send to SnapshotManager
        sm.process_closing_lines(players_data)
        # SnapshotManager ALSO writes a final intraday snapshot for "pre_game"
        sm.write_snapshot("pre_game", players_data, bypass_dedupe=True, filter_to_active_schedule=True)
        
        # Upsert fresh props to Supabase (non-fatal)
        # DATA_DIR is defined at module level in this file
        try:
            from utils.upsert_props import run_odds_update
            props_ok = run_odds_update(
                dk_path=os.path.join(DATA_DIR, "draftkings.csv"),
                fd_path=os.path.join(DATA_DIR, "fanduel.csv"),
                stats_path=os.path.join(DATA_DIR, "season_stats.csv"),
            )
            line_movements_ok = upsert_line_movements_from_file(os.path.join(DATA_DIR, "line_movements_today.json"))
            historical_path = os.path.join(BASE_DIR, "data", "archive", "historical_odds.json")
            archive_dates = sorted({pdata.get("game_date") or get_et_now().strftime("%Y-%m-%d") for pdata in players_data.values()})
            historical_ok = True
            for archive_date in archive_dates:
                historical_ok = upsert_historical_odds_from_file(historical_path, archive_date) and historical_ok
            if not props_ok or not line_movements_ok or not historical_ok:
                logger.warning(
                    "Closing lines DB sync was incomplete "
                    f"(props_ok={props_ok}, line_movements_ok={line_movements_ok}, historical_ok={historical_ok})."
                )
                return False
        except Exception as e:
            logger.warning(f"Supabase market upsert failed (non-fatal): {e}")
            return False

        for g in games_to_scrape:
            if g["game_id"] not in state["scraped_games"]:
                state["scraped_games"].append(g["game_id"])

        save_cron_state(state)
        logger.info("Closing sweeps complete!")
        return True

    except Exception as e:
        logger.error(f"Failed to execute scrape: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron script for closing lines")
    parser.add_argument("--dry-run", action="store_true", help="Run checks without actually scraping")
    args = parser.parse_args()
    
    main(dry_run=args.dry_run)
