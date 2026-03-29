import time
import pandas as pd
import os
import sys
import gc
import psutil
import logging
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path

from utils.logging_utils import configure_logging, log_section, log_status

# Add path to scrapers
sys.path.append(os.path.join(os.path.dirname(__file__), 'scrapers'))

# Import modules
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
from scrapers import fetch_odds_prizepicks as prizepicks
from scrapers import season_stats_scrape as nba_stats
from scrapers import gamelogs as gamelogs
from scrapers import fetch_todays_games as schedule
from scrapers import shooting_zones as shooting_zones
from scrapers import assist_zones as assist_zones
from scrapers import opp_assist_zones as opp_assist_zones
from scrapers import opp_def_zones as opp_def_zones
from scrapers import shot_type_analysis as shot_type_analysis
from scrapers import opp_shot_type_analysis as opp_shot_type_analysis
from scrapers import play_type_analysis as play_type_analysis
from scrapers import boxscores as boxscores
from utils import aggregator
from utils.odds_csv import write_odds_csv
import json

logger = logging.getLogger("RunPipeline")
stdout_logger = logging.getLogger("PipelineStdout")


def ensure_logging_configured():
    configure_logging()


class LoggerWriter:
    def __init__(self, logger_instance, level=logging.INFO):
        self.logger = logger_instance
        self.level = level
        self.buffer = ""

    def write(self, message):
        if not message:
            return 0

        self.buffer += message
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                self.logger.log(self.level, line)
        return len(message)

    def flush(self):
        if self.buffer.strip():
            self.logger.log(self.level, self.buffer.rstrip())
        self.buffer = ""

# CONFIGURATION
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
os.makedirs(DATA_DIR, exist_ok=True)

LOG_PATH = os.path.join(BASE_DIR, "logs", "pipeline.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Define Paths
STATS_PATH = os.path.join(DATA_DIR, "season_stats.csv")
DK_PATH = os.path.join(DATA_DIR, "draftkings.csv")
FD_PATH = os.path.join(DATA_DIR, "fanduel.csv")
PP_PATH = os.path.join(DATA_DIR, "prizepicks.csv")
LOGS_PATH = os.path.join(DATA_DIR, "gamelogs_2025-26.csv")
MASTER_PATH = os.path.join(DATA_DIR, "master_feed.json")
GAMES_PATH = os.path.join(DATA_DIR, "nba_dashboard_games.json")
SCHEDULE_PATH = os.path.join(DATA_DIR, "today_schedule.json")
SHOOTING_PATH = os.path.join(DATA_DIR, "shooting_zones.json")
ASSISTS_PATH = os.path.join(DATA_DIR, "assist_zones.json")
OPP_ASSIST_PATH = os.path.join(DATA_DIR, "opp_assist_zones.json")
OPP_DEF_PATH = os.path.join(DATA_DIR, "opp_def_zones.json")
SHOT_TYPE_PATH = os.path.join(DATA_DIR, "shot_type_analysis.json")
OPP_SHOT_TYPE_PATH = os.path.join(DATA_DIR, "opponent_defensive_ranks.json")
PLAY_TYPE_PATH = os.path.join(DATA_DIR, "play_type_analysis.json")
BOXSCORES_PATH = os.path.join(DATA_DIR, "boxscores.json")

def run_dk():
    logger.info("Starting DraftKings...")
    data = draftkings.fetch_dk_odds()
    write_odds_csv(DK_PATH, data)
    df = pd.DataFrame(data or [])
    return f"DraftKings: {len(df)} rows"

def run_fd():
    logger.info("Starting FanDuel...")
    data = fanduel.fetch_odds()
    write_odds_csv(FD_PATH, data)
    df = pd.DataFrame(data or [])
    return f"FanDuel: {len(df)} rows"

def run_pp():
    if not prizepicks.prizepicks_enabled():
        return "PrizePicks: disabled"

    logger.info("Starting PrizePicks...")
    rows, _diagnostics = prizepicks.fetch_and_write_rows(output_path=Path(PP_PATH))
    return f"PrizePicks: {len(rows)} rows"

def run_stats():
    logger.info("Starting Season Stats...")
    engine = nba_stats.NBAStatsEngine()
    df = engine.get_player_data()
    df.to_csv(STATS_PATH, index=False)
    return f"Season Stats: {len(df)} players"

def run_logs():
    logger.info("Starting Game Logs (Incremental)...")
    result = gamelogs.run_scrape(LOGS_PATH)

    if isinstance(result, dict):
        status = result.get("status")
        message = result.get("message", "Game Logs Updated")
        if status == "failed":
            if result.get("last_saved_date") and os.path.exists(LOGS_PATH):
                logger.warning(
                    "Game logs refresh failed; continuing with existing logs through %s.",
                    result["last_saved_date"],
                )
                return message
            raise RuntimeError(message)
        if status == "partial":
            logger.warning("Game logs completed with partial coverage: %s", message)
        return message

    if result is False:
        raise RuntimeError("Game logs failed.")

    return "Game Logs Updated"

def run_schedule():
    logger.info("Starting Game Schedule...")
    df, raw_data = schedule.get_dashboard_data()

    # Save local JSON (for local dev fallback)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(GAMES_PATH, "w") as f:
        json.dump(raw_data, f, indent=2, default=str)
    with open(SCHEDULE_PATH, "w") as f:
        json.dump({"games": raw_data}, f, indent=2, default=str)

    # Upsert to Supabase (non-fatal)
    try:
        schedule.upsert_games_to_db(raw_data)
    except Exception as e:
        logger.warning("games DB upsert failed (non-fatal): %s", e)

    return f"Schedule: {len(df)} games"


def run_shooting_zones():
    logger.info("Starting Shooting Zones...")
    data = shooting_zones.get_shooting_zones_data()
    with open(SHOOTING_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Shooting Zones: {len(data)} players"

def run_assist_zones():
    logger.info("Starting Assist Zones...")
    data = assist_zones.get_assist_zones_data()
    with open(ASSISTS_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Assist Zones: {len(data)} players"

def run_opp_assist_zones():
    logger.info("Starting Opponent Assist Zones...")
    data = opp_assist_zones.get_opp_assist_zones_data()
    with open(OPP_ASSIST_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Assist Zones: {len(data)} teams"

def run_opp_def_zones():
    logger.info("Starting Opponent Defense Zones...")
    data = opp_def_zones.get_opp_def_zones_data()
    with open(OPP_DEF_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Defense Zones: {len(data)} teams"

def run_shot_type_analysis():
    logger.info("Starting Shot Type Analysis...")
    data = shot_type_analysis.get_shot_type_data()
    with open(SHOT_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Shot Type Analysis: {len(data.get('players', {}))} players, {len(data.get('teams', {}))} teams"

def run_opp_shot_type_analysis():
    logger.info("Starting Opponent Shot Type Analysis...")
    data = opp_shot_type_analysis.process_defensive_rankings()
    with open(OPP_SHOT_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Shot Type Analysis: {len(data)} teams"

def run_play_type_analysis():
    logger.info("Starting Play Type Analysis...")
    data = play_type_analysis.get_play_type_data()
    with open(PLAY_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Play Type Analysis: {len(data.get('players', {}))} players, {len(data.get('teams', {}))} teams"

def run_boxscores():
    logger.info("Starting Boxscores...")
    boxscores.run_scrape(BOXSCORES_PATH)
    return "Boxscores Updated"

def log_memory(stage_name):
    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / (1024 * 1024)
    log_status(logger, "INFO", "Memory usage", stage=stage_name, rss_mb=f"{mem_mb:.1f}")

def main():
    ensure_logging_configured()
    start_time = time.time()
    start_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_PATH, 'w') as f:
        f.write(f"Pipeline started: {start_str}\n")
    stdout_capture = LoggerWriter(stdout_logger)

    try:
        with redirect_stdout(stdout_capture):
            log_section(logger, "Daily pipeline", started_at=start_str)
            log_memory("START")

            # STEP 1: Run Scrapers (Sequential - Memory Safe)
            # Order: Schedule -> Zones -> Odds -> Stats -> Logs -> Boxscores
            scrapers = [
                ("Schedule", run_schedule),
                ("Shooting Zones", run_shooting_zones),
                ("Assist Zones", run_assist_zones),
                ("Opp Assist Zones", run_opp_assist_zones),
                ("Opp Defense Zones", run_opp_def_zones),
                ("Shot Type Analysis", run_shot_type_analysis),
                ("Opp Shot Type Analysis", run_opp_shot_type_analysis),
                ("Play Type Analysis", run_play_type_analysis),
                ("DraftKings", run_dk),
                ("FanDuel", run_fd),
                ("PrizePicks", run_pp),
                ("Season Stats", run_stats),
                ("Game Logs", run_logs),
                ("Boxscores", run_boxscores)
            ]
            critical_scrapers = {"Schedule", "Season Stats", "Game Logs"}
            critical_failures = []
            
            for name, func in scrapers:
                try:
                    log_status(logger, "RUN", "Scraper started", step=name)
                    result = func()
                    log_status(logger, "OK", str(result), step=name)
                except Exception as e:
                    log_status(logger, "FAIL", "Scraper failed", step=name, error=e)
                    if name in critical_scrapers:
                        critical_failures.append(f"{name}: {e}")
                finally:
                    gc.collect()
                    log_memory(f"After {name}")

            # STEP 2: Run Aggregator
            log_status(logger, "RUN", "Aggregator started")
            aggregator.run_aggregation(
                stats_path=STATS_PATH,
                dk_path=DK_PATH,
                fd_path=FD_PATH,
                logs_path=LOGS_PATH, 
                shooting_path=SHOOTING_PATH,
                assists_path=ASSISTS_PATH,
                opp_assist_path=OPP_ASSIST_PATH,
                opp_def_path=OPP_DEF_PATH,
                games_path=GAMES_PATH,
                shot_type_path=SHOT_TYPE_PATH,
                opp_shot_type_path=OPP_SHOT_TYPE_PATH,
                play_type_path=PLAY_TYPE_PATH,
                boxscores_path=BOXSCORES_PATH,
                output_path=MASTER_PATH
            )

            total_time = time.time() - start_time
            log_status(logger, "OK", "Pipeline complete", duration_s=f"{total_time:.1f}")

            # STEP 3: Upsert props to Supabase (non-fatal)
            # The players table was already upserted inside aggregator.run_aggregation().
            # This call pushes the resolved prop lines (DK + FD) into player_props.
            log_status(logger, "RUN", "Supabase sync started")
            props_ok = True
            historical_sync_ok = True
            try:
                from utils.upsert_props import run_odds_update
                props_ok = bool(run_odds_update(
                    dk_path=DK_PATH,
                    fd_path=FD_PATH,
                    stats_path=STATS_PATH,
                    pp_path=PP_PATH if prizepicks.prizepicks_enabled() else None,
                ))
            except Exception as e:
                log_status(logger, "WARN", "Props sync failed", error=e)
                props_ok = False

            try:
                from utils.upsert_market_history import sync_recent_historical_odds_from_file
                historical_sync_ok = bool(sync_recent_historical_odds_from_file(
                    os.path.join(BASE_DIR, "data", "archive", "historical_odds.json"),
                ))
            except Exception as e:
                log_status(logger, "WARN", "Historical odds sync failed", error=e)
                historical_sync_ok = False

            if props_ok and historical_sync_ok:
                log_status(logger, "OK", "Supabase sync complete")
            else:
                log_status(
                    logger,
                    "WARN",
                    "Supabase sync incomplete",
                    props_ok=props_ok,
                    historical_ok=historical_sync_ok,
                )

            if critical_failures:
                log_status(logger, "WARN", "Critical scraper failures detected", count=len(critical_failures))
                for failure in critical_failures:
                    logger.warning("  %s", failure)

            return not critical_failures
    finally:
        stdout_capture.flush()

if __name__ == "__main__":
    main()
