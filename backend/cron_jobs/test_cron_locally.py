import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone

# Add backend to path so imports work
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils.logging_utils import configure_logging, log_section, log_status
from master_cron import load_state, save_state, run_pipeline_if_needed, check_closing_lines, run_intraday_if_needed, STATE_FILE, SCHEDULE_PATH

configure_logging()
logger = logging.getLogger("CronSimulator")

def setup_mock_environment():
    log_section(logger, "Local cron simulation setup")
    
    # Reset state
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        
    if not os.path.exists(SCHEDULE_PATH):
        log_status(logger, "FAIL", "Schedule file not found", path=SCHEDULE_PATH)
        return None
        
    with open(SCHEDULE_PATH, 'r') as f:
        sched = json.load(f)
        
    games = sched.get("games", [])
    if not games:
        log_status(logger, "WARN", "No games found in schedule")
        return None
        
    log_status(logger, "OK", "Loaded schedule", games=len(games))
    
    # Just return the schedule to parse times in run_simulation
    return sched

def run_simulation():
    sched = setup_mock_environment()
    if not sched:
        return
        
    # We will simulate a day starting at 5:50 AM ET
    now = datetime.now(timezone(timedelta(hours=-5)))
    start_time = now.replace(hour=5, minute=50, second=0, microsecond=0)
    
    log_section(logger, "Starting local cron simulation")
    
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
            log_status(logger, "OK", "Priority 1 triggered", at=time_str)
            priority_triggered = True
            
        if not priority_triggered:
            closing_res = check_closing_lines(current_time, state, dry_run=True)
            if closing_res:
                matchups = closing_res if isinstance(closing_res, list) else []
                log_status(logger, "OK", "Priority 2 triggered", at=time_str, matchups=matchups)
                priority_triggered = True
                
        if not priority_triggered:
            if run_intraday_if_needed(current_time, state, dry_run=True):
                log_status(logger, "OK", "Priority 3 triggered", at=time_str)
                priority_triggered = True
            
        if not priority_triggered and tick % 12 == 0: # Print a heartbeat every hour
             log_status(logger, "INFO", "Heartbeat", at=time_str)
             
        current_time += timedelta(minutes=5)
        
    log_section(logger, "Simulation complete")

if __name__ == "__main__":
    run_simulation()
