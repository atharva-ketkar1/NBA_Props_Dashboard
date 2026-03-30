import os
import sys
import time
import json
import logging
import argparse
import atexit
import signal
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from utils.logging_utils import configure_logging, log_section, log_status

configure_logging()
logger = logging.getLogger("MasterCron")

load_dotenv(os.path.join(BASE_DIR, ".env"))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

LOCK_FILE = os.path.join(LOGS_DIR, "master_cron.lock")
STATE_FILE = os.path.join(LOGS_DIR, "master_cron_state.json")
SCHEDULE_PATH = os.path.join(DATA_DIR, "today_schedule.json")
LOCK_STALE_SECONDS = 2700
_LOCK_REGISTERED = False

def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))


def get_intraday_interval_seconds():
    raw_value = os.getenv("INTRADAY_INTERVAL_SECONDS", "900")
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = 900
    return max(300, parsed)


def parse_mock_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "mock time must be ISO format like 2026-03-18T21:20:00 or 2026-03-18T21:20:00-04:00"
        ) from exc

    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("America/New_York"))
    return dt.astimezone(ZoneInfo("America/New_York"))

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


def _is_pid_running(pid):
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _load_lock_info():
    if not os.path.exists(LOCK_FILE):
        return {}

    try:
        with open(LOCK_FILE, "r") as f:
            raw = f.read().strip()
    except Exception:
        return {}

    if not raw:
        return {}

    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass

    try:
        return {"created_at": float(raw)}
    except ValueError:
        return {}


def _write_lock_info():
    payload = {
        "pid": os.getpid(),
        "created_at": time.time(),
    }
    with open(LOCK_FILE, "w") as f:
        json.dump(payload, f)


def _current_process_owns_lock():
    lock_info = _load_lock_info()
    return lock_info.get("pid") == os.getpid()


def _handle_termination(signum, _frame):
    signal_name = signal.Signals(signum).name
    logger.warning("Received %s. Releasing cron lock before exit.", signal_name)
    release_lock()
    raise SystemExit(128 + signum)

def run_pipeline_if_needed(now, state, dry_run=False):
    """Priority 1: Full Pipeline at or after 6:00 AM ET once a day."""
    today_str = now.strftime("%Y-%m-%d")
    
    # Check if we should run it
    if now.hour >= 6:
        if state.get("last_pipeline_date") != today_str:
            log_section(logger, "Priority 1 - Full pipeline", date=today_str, dry_run=dry_run)
            pipeline_ok = True
            if not dry_run:
                try:
                    sys.path.append(BASE_DIR)
                    import run_pipeline
                    pipeline_result = run_pipeline.main()
                    if pipeline_result is False:
                        pipeline_ok = False
                except Exception as e:
                    log_status(logger, "FAIL", "Pipeline failed", error=e)
                    pipeline_ok = False

                # Purge stale player_props and line_movements rows (rolling 3-day window)
                # historical_odds is intentionally NOT purged — it's our full-season archive
                if pipeline_ok:
                    try:
                        from utils.supabase_client import get_supabase_client
                        cutoff = (datetime.today().date() - timedelta(days=3)).isoformat()
                        sb = get_supabase_client()
                        sb.table("player_props").delete().lt("game_date", cutoff).execute()
                        sb.table("line_movements").delete().lt("game_date", cutoff).execute()
                        log_status(logger, "OK", "Pruned stale props history", cutoff=cutoff)
                    except Exception as e:
                        log_status(logger, "WARN", "Stale DB cleanup failed", error=e)

            if not pipeline_ok:
                return False

            state["last_pipeline_date"] = today_str
            # Reset scraped games for the new day
            state["scraped_closing_games"] = []
            if not dry_run:
                save_state(state)
            return True
            
    return False

def check_closing_lines(now, state, dry_run=False):
    """Priority 2: Pre-tip props refresh plus local closing archive."""
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
        log_section(
            logger,
            "Priority 2 - Pre-tip props refresh",
            games=len(games_to_scrape),
            matchups=[g["matchup"] for g in games_to_scrape],
            dry_run=dry_run,
        )
        closing_ok = True
        if not dry_run:
            try:
                sys.path.append(os.path.dirname(os.path.abspath(__file__)))
                import cron_closing_lines
                # Modify the state of the cron_closing_lines strictly locally if needed,
                # but we will just call scrape_and_shape_odds directly via a wrapper to ensure single run.
                # Since cron_closing_lines.main() manages its own state, it's safer to just call it.
                closing_ok = bool(cron_closing_lines.main(dry_run=False))
            except Exception as e:
                log_status(logger, "FAIL", "Pre-tip props refresh failed", error=e)
                closing_ok = False
                
        if not closing_ok:
            return False

        for g in games_to_scrape:
            if g["game_id"] not in state["scraped_closing_games"]:
                state["scraped_closing_games"].append(g["game_id"])

        if not dry_run:
            save_state(state)
        return [g['matchup'] for g in games_to_scrape]
        
    return False

def run_intraday_if_needed(now, state, dry_run=False):
    """Priority 3: Intraday props refresh plus snapshot sync."""
    last_run = state.get("last_intraday_time", 0)
    current_timestamp = now.timestamp()
    interval_seconds = get_intraday_interval_seconds()

    if current_timestamp - last_run >= interval_seconds:
        log_section(logger, "Priority 3 - Intraday props refresh", every_s=interval_seconds, dry_run=dry_run)
        intraday_ok = True
        if not dry_run:
            try:
                sys.path.append(os.path.dirname(os.path.abspath(__file__)))
                import cron_line_movement
                intraday_ok = bool(cron_line_movement.run_intraday_refresh())
            except Exception as e:
                log_status(logger, "FAIL", "Intraday props refresh failed", error=e)
                intraday_ok = False

        if not intraday_ok:
            return False
                
        # Anchor the interval to the completion time, not the start time.
        # Otherwise a long-running scrape can immediately retrigger on the next
        # cron tick and create a write-amplification loop.
        state["last_intraday_time"] = time.time()
        if not dry_run:
            save_state(state)
        return True
        
    return False

def check_mutual_exclusion():
    global _LOCK_REGISTERED
    if os.path.exists(LOCK_FILE):
        try:
            lock_info = _load_lock_info()
            lock_pid = lock_info.get("pid")

            if lock_pid and _is_pid_running(lock_pid):
                log_status(logger, "WARN", "Another master cron instance is active", pid=lock_pid)
                return False

            if lock_pid and not _is_pid_running(lock_pid):
                log_status(logger, "WARN", "Removing orphaned cron lock", pid=lock_pid)
                os.remove(LOCK_FILE)
            else:
                mtime = os.path.getmtime(LOCK_FILE)
                if time.time() - mtime > LOCK_STALE_SECONDS:
                    log_status(logger, "WARN", "Removing stale cron lock")
                    os.remove(LOCK_FILE)
                else:
                    log_status(logger, "WARN", "Another master cron instance is active")
                    return False
        except Exception:
            return False
            
    # Create lock file
    try:
        _write_lock_info()
        if not _LOCK_REGISTERED:
            atexit.register(release_lock)
            signal.signal(signal.SIGTERM, _handle_termination)
            signal.signal(signal.SIGINT, _handle_termination)
            _LOCK_REGISTERED = True
        return True
    except Exception as e:
        log_status(logger, "FAIL", "Could not create cron lock", error=e)
        return False

def release_lock():
    if os.path.exists(LOCK_FILE) and _current_process_owns_lock():
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
        ran_intraday = run_intraday_if_needed(now, state, dry_run=dry_run)
        if ran_intraday:
            return

        logger.debug("No priority matched at this time.")
        
    finally:
        release_lock()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't actually scrape, just log intent.")
    parser.add_argument(
        "--mock-time",
        type=parse_mock_time,
        help="Simulate an ET time in ISO format, e.g. 2026-03-18T21:20:00",
    )
    args = parser.parse_args()
    
    main(dry_run=args.dry_run, mock_time=args.mock_time)
