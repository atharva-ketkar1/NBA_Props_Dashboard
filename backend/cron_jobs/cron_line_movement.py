import os
import logging
from datetime import datetime, timezone, timedelta

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scrapers'))

# Reusing the existing scrape_and_shape_odds from cron_closing_lines to avoid duplication
# but we need to import it. Since cron_closing_lines.py has the logic, let's just 
# extract it into a shared util later if needed, or import it directly here.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from cron_closing_lines import scrape_and_shape_odds
from utils.snapshot_manager import SnapshotManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("CronLineMovement")

def run_intraday_snapshot(label="intraday"):
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

if __name__ == "__main__":
    run_intraday_snapshot()
