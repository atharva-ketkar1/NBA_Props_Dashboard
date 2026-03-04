import os
import json
import logging
import time
import concurrent.futures
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

import sys
sys.path.append(os.path.join(os.path.dirname(__file__), 'scrapers'))
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
from utils.snapshot_manager import SnapshotManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Scheduler")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
MASTER_PATH = os.path.join(DATA_DIR, "master_feed.json")

# Map betting props to internal names to match aggregator
PROP_MAP = {
    'points': 'PTS', 'rebounds': 'REB', 'assists': 'AST',
    'threes': 'FG3M', 'blocks': 'BLK', 'steals': 'STL',
    'pra': 'PTS+REB+AST', 'pr': 'PTS+REB', 'pa': 'PTS+AST', 'ra': 'REB+AST', 'stocks': 'STL+BLK'
}

def get_et_now():
    return datetime.now(timezone(timedelta(hours=-5)))

def load_name_to_id_map():
    """Load player name to ID mapping from master_feed.json"""
    mapping = {}
    if os.path.exists(MASTER_PATH):
        try:
            with open(MASTER_PATH, 'r') as f:
                master_feed = json.load(f)
                for p in master_feed:
                    name = p.get("name", "").lower().strip()
                    pid = str(p.get("id", "")) # string ID as required by schema
                    if name and pid:
                        mapping[name] = pid
        except Exception as e:
            logger.error(f"Error loading master feed for IDs: {e}")
    return mapping

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
            # Force a hard 2-minute limit so PM2 never freezes
            dk_data = f1.result(timeout=120) 
        except concurrent.futures.TimeoutError:
            logger.error("DraftKings scraper timed out (Infinite block prevented).")
        except Exception as e:
            # Catches 403s, JSON decode errors, or general API connection drops
            logger.error(f"DraftKings scraper crashed (Potential 403/Network Error): {e}")
            
        # --- FanDuel Safeguard ---
        try:
            fd_data = f2.result(timeout=120)
        except concurrent.futures.TimeoutError:
            logger.error("FanDuel scraper timed out (Infinite block prevented).")
        except Exception as e:
            logger.error(f"FanDuel scraper crashed (Potential 403/Network Error): {e}")
            
    # Fallback to empty lists if a scraper failed or returned None
    if not dk_data: dk_data = []
    if not fd_data: fd_data = []
        
    name_to_id = load_name_to_id_map()
    players_dict = {}
    
    # Process FanDuel first
    for row in fd_data:
        name = row.get("player", "").lower().strip()
        pid = name_to_id.get(name, name) # fallback to name if ID rarely fails
        
        prop = PROP_MAP.get(row.get("prop_type", ""), row.get("prop_type", "")).upper()
        if not prop: continue
        
        if pid not in players_dict:
            players_dict[pid] = {
                "name": row.get("player", ""),
                "team": row.get("team", ""),
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
        name = row.get("player", "").lower().strip()
        pid = name_to_id.get(name, name)
        
        prop = PROP_MAP.get(row.get("prop_type", ""), row.get("prop_type", "")).upper()
        if not prop: continue
        
        if pid not in players_dict:
            players_dict[pid] = {
                "name": row.get("player", ""),
                "team": row.get("team", ""),
                "props": {},
                "draftkings_available": True
            }
        else:
            players_dict[pid]["draftkings_available"] = True
            if players_dict[pid]["team"] == "":
                players_dict[pid]["team"] = row.get("team", "")
                
        if prop not in players_dict[pid]["props"]:
            players_dict[pid]["props"][prop] = {}
            
        players_dict[pid]["props"][prop]["draftkings"] = {
            "line": row.get("line"),
            "over": row.get("over_odds"),
            "under": row.get("under_odds")
        }
        
    return players_dict

def run_intraday_snapshot(label):
    logger.info(f"--- Running Intraday Snapshot: {label} ---")
    try:
        players_data = scrape_and_shape_odds(is_closing=False)
        sm = SnapshotManager()
        success = sm.write_snapshot(label, players_data)
        if success:
            logger.info("Intraday snapshot written successfully.")
        else:
            logger.info("Intraday snapshot skipped (likely deduplication <30m).")
    except Exception as e:
        logger.error(f"Failed intraday snapshot: {e}")

def run_closing_lines_scrape():
    logger.info("--- Running Closing Lines Scrape ---")
    try:
        # For closing lines we ALSO write a final snapshot as per prompt:
        # "Closing lines -> historical_odds.json + final snapshot -> line_movements_today.json"
        players_data = scrape_and_shape_odds(is_closing=True)
        sm = SnapshotManager()
        
        # Need to attach game_id for each player for Gate 1.
        # How do we know what game a player is in?
        # The schedule has game_id -> home/away_team. We can map team to game_id.
        schedule_path = os.path.join(DATA_DIR, "today_schedule.json")
        team_to_game = {}
        if os.path.exists(schedule_path):
            with open(schedule_path, 'r') as f:
                sched = json.load(f)
                for g in sched.get("games", []):
                    # tricode is like "BOS" "NYK"
                    # If we don't have strictly tri-codes, we handle what we have
                    home = g.get("home_team_tricode", "")
                    away = g.get("away_team_tricode", "")
                    if home: team_to_game[home] = g.get("game_id")
                    if away: team_to_game[away] = g.get("game_id")
        
        # Attach game_ids
        for pid, pdata in players_data.items():
            team = pdata.get("team", "")
            if team in team_to_game:
                pdata["game_id"] = team_to_game[team]
        
        # Process closing lines (Gates 1 & 2 logic)
        sm.process_closing_lines(players_data)
        logger.info("Closing lines checked and processed.")
        
        # Write final snapshot
        sm.write_snapshot("pre_game", players_data, bypass_dedupe=True)
        
    except Exception as e:
        logger.error(f"Failed closing lines scrape: {e}")

def schedule_dynamic_jobs(scheduler):
    logger.info("Scheduling dynamic pre-game closing line jobs...")
    now = get_et_now()
    schedule_path = os.path.join(DATA_DIR, "today_schedule.json")
    
    for job in scheduler.get_jobs():
        if job.id.startswith('dynamic_closing_'):
            job.remove()
            
    if os.path.exists(schedule_path):
        try:
            with open(schedule_path, 'r') as f:
                sched = json.load(f)
                
            unique_scrape_times = set()
            for game in sched.get("games", []):
                deadline_str = game.get("closing_scrape_deadline")
                if deadline_str:
                    try:
                        # Subtract 5 minutes to run at 10:25 instead of 10:30
                        deadline_dt = datetime.fromisoformat(deadline_str) - timedelta(minutes=5)
                        if now < deadline_dt:
                            unique_scrape_times.add(deadline_dt)
                    except ValueError:
                        pass
                        
            for i, dt in enumerate(sorted(list(unique_scrape_times))):
                scheduler.add_job(
                    run_closing_lines_scrape, 
                    DateTrigger(run_date=dt),
                    id=f'dynamic_closing_{i}_{dt.strftime("%Y%m%d%H%M")}',
                    replace_existing=True
                )
                logger.info(f"Scheduled closing lines scrape for {dt}")
        except Exception as e:
            logger.error(f"Error scheduling closing lines jobs: {e}")
    else:
        logger.warning(f"Schedule file not found: {schedule_path}")

def schedule_jobs():
    logger.info("Initializing Daily Scheduler...")
    scheduler = BlockingScheduler(timezone=timezone(timedelta(hours=-5))) # ET

    # Schedule Intraday Scrapes (Static - Every Day)
    intraday_times = [
        ("early_morning", 7, 0),
        ("morning", 9, 0),
        ("open", 11, 0),
        ("midday", 13, 0),
        ("afternoon", 15, 0),
        ("late_afternoon", 17, 0),
        ("evening", 19, 0),
        ("night", 21, 0),
        ("late_night", 23, 0)
    ]
    
    for label, hour, minute in intraday_times:
        try:
            scheduler.add_job(
                run_intraday_snapshot, 
                CronTrigger(hour=hour, minute=minute), 
                args=[label],
                id=f'intraday_{label}',
                replace_existing=True
            )
            logger.info(f"Scheduled intraday ({label}) for {hour:02d}:{minute:02d} daily")
        except Exception as e:
            logger.error(f"Error scheduling intraday job: {e}")

    # Initial Run of Dynamic Jobs (for when process just started)
    schedule_dynamic_jobs(scheduler)
    
    # Schedule Daily Reset for Dynamic Jobs at 6:30 AM (after 6:00 AM full pipeline run)
    try:
        scheduler.add_job(
            schedule_dynamic_jobs,
            CronTrigger(hour=6, minute=30),
            args=[scheduler],
            id='daily_dynamic_job_reset',
            replace_existing=True
        )
        logger.info("Scheduled daily dynamic job re-evaluation at 6:30 AM")
    except Exception as e:
        logger.error(f"Error scheduling daily reset: {e}")

    logger.info("Starting blocking scheduler. Waiting for jobs to run...")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")

if __name__ == "__main__":
    schedule_jobs()
