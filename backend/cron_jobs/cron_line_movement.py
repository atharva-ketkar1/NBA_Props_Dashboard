import os
import logging
import time

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scrapers'))
from utils.logging_utils import configure_logging, log_section, log_status

# Reusing the existing scrape_and_shape_odds from cron_closing_lines to avoid duplication
# but we need to import it. Since cron_closing_lines.py has the logic, let's just 
# extract it into a shared util later if needed, or import it directly here.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from cron_closing_lines import scrape_and_shape_odds
from utils.snapshot_manager import SnapshotManager

configure_logging()
logger = logging.getLogger("CronLineMovement")

def run_intraday_snapshot(label="intraday"):
    start_time = time.time()
    log_section(logger, "Intraday snapshot", label=label)
    try:
        players_data = scrape_and_shape_odds(is_closing=False)
        sm = SnapshotManager()
        success = sm.write_snapshot(label, players_data, filter_to_active_schedule=True)
        duration_seconds = time.time() - start_time
        if success:
            log_status(
                logger,
                "OK",
                "Intraday snapshot written locally",
                label=label,
                players=len(players_data),
                duration_s=f"{duration_seconds:.1f}",
            )
            # Upsert fresh props to Supabase (non-fatal)
            try:
                from utils.upsert_props import run_odds_update
                from scrapers import fetch_odds_prizepicks as prizepicks
                # Derive paths relative to this file — never import from run_pipeline.py
                _data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "current")
                pp_path = None
                if prizepicks.prizepicks_enabled():
                    try:
                        prizepicks.fetch_and_write_rows(output_path=prizepicks.DEFAULT_OUTPUT_PATH)
                        pp_path = str(prizepicks.DEFAULT_OUTPUT_PATH)
                    except Exception as error:
                        log_status(logger, "WARN", "PrizePicks refresh failed", error=error)
                props_ok = run_odds_update(
                    dk_path=os.path.join(_data_dir, "draftkings.csv"),
                    fd_path=os.path.join(_data_dir, "fanduel.csv"),
                    stats_path=os.path.join(_data_dir, "season_stats.csv"),
                    pp_path=pp_path,
                )
                
                # Also upsert line movements blob to Supabase
                from utils.upsert_market_history import upsert_line_movements_from_file
                lm_path = os.path.join(_data_dir, "line_movements_today.json")
                lm_ok = upsert_line_movements_from_file(lm_path)

                if not props_ok or not lm_ok:
                    log_status(
                        logger,
                        "WARN",
                        "Intraday DB sync incomplete",
                        label=label,
                        players=len(players_data),
                        props_ok=props_ok,
                        line_movements_ok=lm_ok,
                    )
                    return True

                log_status(logger, "OK", "Intraday DB sync complete", label=label, players=len(players_data))
                    
            except Exception as e:
                log_status(logger, "WARN", "Supabase upsert failed after snapshot success", error=e)
                return True
            return True
        else:
            log_status(
                logger,
                "SKIP",
                "Intraday snapshot skipped by dedupe",
                label=label,
                players=len(players_data),
                duration_s=f"{duration_seconds:.1f}",
            )
            return True
    except Exception as e:
        log_status(logger, "FAIL", "Intraday snapshot failed", error=e)
        return False

if __name__ == "__main__":
    run_intraday_snapshot()
