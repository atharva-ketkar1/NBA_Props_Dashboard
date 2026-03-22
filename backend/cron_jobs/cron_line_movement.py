import os
import logging
import time

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
    start_time = time.time()
    logger.info(f"--- Running Intraday Snapshot: {label} ---")
    try:
        players_data = scrape_and_shape_odds(is_closing=False)
        sm = SnapshotManager()
        success = sm.write_snapshot(label, players_data, filter_to_active_schedule=True)
        duration_seconds = time.time() - start_time
        if success:
            logger.info(
                "Intraday snapshot written locally | label=%s players=%d duration=%.1fs",
                label,
                len(players_data),
                duration_seconds,
            )
            # Upsert fresh props to Supabase (non-fatal)
            try:
                from utils.upsert_props import run_odds_update
                # Derive paths relative to this file — never import from run_pipeline.py
                _data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "current")
                props_ok = run_odds_update(
                    dk_path=os.path.join(_data_dir, "draftkings.csv"),
                    fd_path=os.path.join(_data_dir, "fanduel.csv"),
                    stats_path=os.path.join(_data_dir, "season_stats.csv"),
                )
                
                # Also upsert line movements blob to Supabase
                from utils.upsert_market_history import upsert_line_movements_from_file
                lm_path = os.path.join(_data_dir, "line_movements_today.json")
                lm_ok = upsert_line_movements_from_file(lm_path)

                if not props_ok or not lm_ok:
                    logger.warning(
                        "Intraday DB sync incomplete | label=%s players=%d props_ok=%s line_movements_ok=%s",
                        label,
                        len(players_data),
                        props_ok,
                        lm_ok,
                    )
                    return True

                logger.info(
                    "Intraday DB sync complete | label=%s players=%d",
                    label,
                    len(players_data),
                )
                    
            except Exception as e:
                logger.warning(f"Supabase upsert failed after local snapshot success: {e}")
                return True
            return True
        else:
            logger.info(
                "Intraday snapshot skipped by dedupe | label=%s players=%d duration=%.1fs",
                label,
                len(players_data),
                duration_seconds,
            )
            return True
    except Exception as e:
        logger.error(f"Failed intraday snapshot: {e}")
        return False

if __name__ == "__main__":
    run_intraday_snapshot()
