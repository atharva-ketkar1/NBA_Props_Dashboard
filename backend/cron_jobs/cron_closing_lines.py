import os
import json
import logging
import argparse
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

import sys
import concurrent.futures

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scrapers'))
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
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
    return name.replace(".", "").replace("'", "")

def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))

def load_master_feed_maps():
    """Load player name/id/team mappings from master_feed.json."""
    name_to_id = {}
    id_to_team = {}
    if os.path.exists(MASTER_PATH):
        try:
            with open(MASTER_PATH, 'r') as f:
                master_feed = json.load(f)
                for p in master_feed:
                    name = normalize_lookup_name(p.get("name", ""))
                    pid = str(p.get("id", ""))
                    team = p.get("team", "")
                    if name and pid:
                        name_to_id[name] = pid
                        if team:
                            id_to_team[pid] = team
        except Exception as e:
            logger.error(f"Error loading master feed for IDs: {e}")
    return name_to_id, id_to_team

def scrape_and_shape_odds(is_closing=False):
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
        
    name_to_id, id_to_team = load_master_feed_maps()
    players_dict = {}
    
    # Process FanDuel first
    for row in fd_data:
        name = normalize_lookup_name(row.get("player", ""))
        pid = name_to_id.get(name, name)
        
        prop = PROP_MAP.get(row.get("prop_type", ""), row.get("prop_type", "")).upper()
        if not prop: continue
        
        canonical_team = id_to_team.get(str(pid), row.get("team", ""))

        if pid not in players_dict:
            players_dict[pid] = {
                "name": row.get("player", ""),
                "team": canonical_team,
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
        name = normalize_lookup_name(row.get("player", ""))
        pid = name_to_id.get(name, name)
        
        prop = PROP_MAP.get(row.get("prop_type", ""), row.get("prop_type", "")).upper()
        if not prop: continue
        
        canonical_team = id_to_team.get(str(pid), row.get("team", ""))

        if pid not in players_dict:
            players_dict[pid] = {
                "name": row.get("player", ""),
                "team": canonical_team,
                "props": {},
                "draftkings_available": True
            }
        else:
            players_dict[pid]["draftkings_available"] = True
            if players_dict[pid]["team"] == "":
                players_dict[pid]["team"] = canonical_team
                
        if prop not in players_dict[pid]["props"]:
            players_dict[pid]["props"][prop] = {}
            
        players_dict[pid]["props"][prop]["draftkings"] = {
            "line": row.get("line"),
            "over": row.get("over_odds"),
            "under": row.get("under_odds")
        }
        
    return players_dict
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
# so we don't double-scrape a game if the cron checks multiple times in a window.
STATE_PATH = os.path.join(BASE_DIR, "logs", "cron_state_today.json")

def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))

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
        players_data = scrape_and_shape_odds(is_closing=True)
        sm = SnapshotManager()
        
        # Attach game_ids logic (taken from scheduler.py)
        team_to_game = {}
        for g in sched.get("games", []):
            home = g.get("home_team_tricode", "")
            away = g.get("away_team_tricode", "")
            if home: team_to_game[home] = g.get("game_id")
            if away: team_to_game[away] = g.get("game_id")
                
        for pid, pdata in players_data.items():
            team = pdata.get("team", "")
            if team in team_to_game:
                pdata["game_id"] = team_to_game[team]
                
        # Send to SnapshotManager
        sm.process_closing_lines(players_data)
        # SnapshotManager ALSO writes a final intraday snapshot for "pre_game"
        sm.write_snapshot("pre_game", players_data, bypass_dedupe=True)
        
        # Upsert fresh props to Supabase (non-fatal)
        # DATA_DIR is defined at module level in this file
        try:
            from utils.upsert_props import run_odds_update
            run_odds_update(
                dk_path=os.path.join(DATA_DIR, "draftkings.csv"),
                fd_path=os.path.join(DATA_DIR, "fanduel.csv"),
                stats_path=os.path.join(DATA_DIR, "season_stats.csv"),
            )
            upsert_line_movements_from_file(os.path.join(DATA_DIR, "line_movements_today.json"))
            upsert_historical_odds_from_file(
                os.path.join(BASE_DIR, "data", "archive", "historical_odds.json"),
                get_et_now().strftime("%Y-%m-%d"),
            )
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
