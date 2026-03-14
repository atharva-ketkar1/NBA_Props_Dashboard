import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone

# Add backend to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from master_cron import load_state, save_state, run_pipeline_if_needed, check_closing_lines, run_intraday_if_needed, STATE_FILE, SCHEDULE_PATH

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CronSimulator")

def setup_mock_environment():
    logger.info("Setting up mock environment from existing schedule...")
    
    # Reset state
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        
    if not os.path.exists(SCHEDULE_PATH):
        logger.error(f"Schedule file not found at {SCHEDULE_PATH}")
        return None
        
    with open(SCHEDULE_PATH, 'r') as f:
        sched = json.load(f)
        
    games = sched.get("games", [])
    if not games:
        logger.warning("No games found in schedule.")
        return None
        
    logger.info(f"Loaded {len(games)} games from actual schedule.")
    
    # Just return the schedule to parse times in run_simulation
    return sched

def run_simulation():
    sched = setup_mock_environment()
    if not sched:
        return
        
    # We will simulate a day starting at 5:50 AM ET
    now = datetime.now(timezone(timedelta(hours=-5)))
    start_time = now.replace(hour=5, minute=50, second=0, microsecond=0)
    
    logger.info(f"--- STARTING SIMULATION ---")
    
    current_time = start_time
    end_time = now.replace(hour=23, minute=55, second=0, microsecond=0)
    
    # Tick every 5 minutes
    tick = 0
    while current_time <= end_time:
        tick += 1
        time_str = current_time.strftime("%H:%M")
        
        # We don't want to spam the console with 170 iterations of "doing nothing"
        # We will only log when a priority triggers
        
        state = load_state()
        priority_triggered = False
        
        # Priority 1
        if run_pipeline_if_needed(current_time, state, dry_run=True):
            logger.info(f"[{time_str}] Priority 1: PIPELINE triggered.")
            priority_triggered = True
            
        if not priority_triggered:
            closing_res = check_closing_lines(current_time, state, dry_run=True)
            if closing_res:
                matchups = closing_res if isinstance(closing_res, list) else []
                logger.info(f"[{time_str}] Priority 2: CLOSING LINES triggered for {matchups}")
                priority_triggered = True
                
        if not priority_triggered:
            if run_intraday_if_needed(current_time, state, dry_run=True):
                logger.info(f"[{time_str}] Priority 3: INTRADAY MOVEMENT triggered.")
                priority_triggered = True
            
        if not priority_triggered and tick % 12 == 0: # Print a heartbeat every hour
             logger.info(f"[{time_str}] Heartbeat ... no actions triggered in this 5 minute window.")
             
        current_time += timedelta(minutes=5)
        
    logger.info("--- SIMULATION COMPLETE ---")

if __name__ == "__main__":
    run_simulation()
