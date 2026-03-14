import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MasterCron")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

LOCK_FILE = os.path.join(LOGS_DIR, "master_cron.lock")
STATE_FILE = os.path.join(LOGS_DIR, "master_cron_state.json")
SCHEDULE_PATH = os.path.join(DATA_DIR, "today_schedule.json")

def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading state: {e}")
    return {"last_pipeline_date": "", "scraped_closing_games": [], "last_intraday_time": 0}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def run_pipeline_if_needed(now, state, dry_run=False):
    """Priority 1: Full Pipeline at or after 6:00 AM ET once a day."""
    today_str = now.strftime("%Y-%m-%d")
    
    # Check if we should run it
    if now.hour >= 6:
        if state.get("last_pipeline_date") != today_str:
            logger.info("Priority 1 Match: Running 6:00 AM Full Pipeline.")
            if not dry_run:
                try:
                    sys.path.append(BASE_DIR)
                    import run_pipeline
                    run_pipeline.main()
                except Exception as e:
                    logger.error(f"Pipeline failed: {e}")
            
            # Update state regardless of internal failure to prevent endless looping
            state["last_pipeline_date"] = today_str
            # Reset scraped games for the new day
            state["scraped_closing_games"] = []
            save_state(state)
            return True
            
    return False

def check_closing_lines(now, state, dry_run=False):
    """Priority 2: Closing lines 10 mins before game."""
    if not os.path.exists(SCHEDULE_PATH):
        return False
        
    try:
        with open(SCHEDULE_PATH, 'r') as f:
            sched = json.load(f)
    except Exception as e:
        logger.error(f"Could not load schedule JSON: {e}")
        return False
        
    games_to_scrape = []
    today_str = now.strftime("%Y-%m-%d")
    
    # Safety reset if somehow pipeline didn't reset it
    if state.get("last_pipeline_date") != today_str:
         state["scraped_closing_games"] = []
    
    for game in sched.get("games", []):
        game_id = game.get("game_id")
        deadline_str = game.get("closing_scrape_deadline")
        if not deadline_str or not game_id:
            continue
            
        if game_id in state.get("scraped_closing_games", []):
            continue
            
        try:
            deadline_dt = datetime.fromisoformat(deadline_str)
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
            
    if games_to_scrape:
        logger.info(f"Priority 2 Match: Running Closing Lines for {[g['matchup'] for g in games_to_scrape]}")
        if not dry_run:
            try:
                sys.path.append(os.path.dirname(os.path.abspath(__file__)))
                import cron_closing_lines
                # Modify the state of the cron_closing_lines strictly locally if needed,
                # but we will just call scrape_and_shape_odds directly via a wrapper to ensure single run.
                # Since cron_closing_lines.main() manages its own state, it's safer to just call it.
                cron_closing_lines.main(dry_run=False)
            except Exception as e:
                logger.error(f"Closing lines failed: {e}")
                
        # Register them as scraped in our master state
        for g in games_to_scrape:
            if g["game_id"] not in state["scraped_closing_games"]:
                state["scraped_closing_games"].append(g["game_id"])
        
        save_state(state)
        return [g['matchup'] for g in games_to_scrape]
        
    return False

def run_intraday_if_needed(now, state, dry_run=False):
    """Priority 3: Intraday tracking every 30 minutes."""
    last_run = state.get("last_intraday_time", 0)
    current_timestamp = now.timestamp()
    
    # 30 minutes = 1800 seconds
    if current_timestamp - last_run >= 1800:
        logger.info("Priority 3 Match: Running Intraday Line Movement.")
        if not dry_run:
            try:
                sys.path.append(os.path.dirname(os.path.abspath(__file__)))
                import cron_line_movement
                cron_line_movement.run_intraday_snapshot()
            except Exception as e:
                logger.error(f"Intraday scrape failed: {e}")
                
        state["last_intraday_time"] = current_timestamp
        save_state(state)
        return True
        
    return False

def check_mutual_exclusion():
    if os.path.exists(LOCK_FILE):
        # Check if lock file is stale (e.g. older than 45 minutes)
        try:
            mtime = os.path.getmtime(LOCK_FILE)
            if time.time() - mtime > 2700:
                logger.warning("Found stale lock file. Removing it and proceeding.")
                os.remove(LOCK_FILE)
            else:
                logger.warning("Another instance of Master Cron is currently running. Exiting.")
                return False
        except Exception:
            return False
            
    # Create lock file
    try:
        with open(LOCK_FILE, 'w') as f:
            f.write(str(time.time()))
        return True
    except Exception as e:
        logger.error(f"Could not create lock file: {e}")
        return False

def release_lock():
    if os.path.exists(LOCK_FILE):
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass

def main(dry_run=False, mock_time=None):
    if not check_mutual_exclusion():
         return
         
    try:
        now = mock_time if mock_time else get_et_now()
        
        state = load_state()
        
        # Check Priority 1
        ran_pipeline = run_pipeline_if_needed(now, state, dry_run=dry_run)
        if ran_pipeline:
            return
            
        # Check Priority 2
        ran_closing = check_closing_lines(now, state, dry_run=dry_run)
        if ran_closing:
            return
            
        # Check Priority 3
        run_intraday_if_needed(now, state, dry_run=dry_run)
        
    finally:
        if not mock_time: # don't release lock iteratively if we're simulating fast
            release_lock()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't actually scrape, just log intent.")
    args = parser.parse_args()
    
    main(dry_run=args.dry_run)
