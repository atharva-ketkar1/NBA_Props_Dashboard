import time
import pandas as pd
import os
import sys
import gc
import tempfile
import psutil
import logging
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from utils.logging_utils import configure_logging, log_section, log_status
from scrapers.season_type import resolve_season

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
from utils import health_ledger
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
CURRENT_SEASON = resolve_season()
LOGS_PATH = os.path.join(DATA_DIR, f"gamelogs_{CURRENT_SEASON}.csv")
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
ET_ZONE = ZoneInfo("America/New_York")
STATS_REQUIRED_COLUMNS = {"PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION"}


def _atomic_write_dataframe_csv(df: pd.DataFrame, output_path: str) -> None:
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    temp_handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tmp",
        prefix=f".{target_path.name}.",
        dir=str(target_path.parent),
        delete=False,
        newline="",
        encoding="utf-8",
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()

    try:
        df.to_csv(temp_path, index=False)
        os.replace(temp_path, target_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

def run_dk():
    logger.info("Starting DraftKings...")
    # The daily refresh is one run and builds the baseline artifact every other
    # consumer reads, so it is not subject to the intraday cadence gate.
    data = draftkings.fetch_dk_odds(force=True)
    write_odds_csv(
        DK_PATH,
        data,
        preserve_on_empty=True,
        target_game_date=datetime.now(ET_ZONE).date().isoformat(),
        sportsbook_label="DraftKings",
    )
    df = pd.DataFrame(data or [])
    return f"DraftKings: {len(df)} rows", len(df)

def run_fd():
    logger.info("Starting FanDuel...")
    data = fanduel.fetch_odds()
    write_odds_csv(
        FD_PATH,
        data,
        preserve_on_empty=True,
        target_game_date=datetime.now(ET_ZONE).date().isoformat(),
        sportsbook_label="FanDuel",
    )
    df = pd.DataFrame(data or [])
    return f"FanDuel: {len(df)} rows", len(df)

def run_pp():
    disabled_reason = prizepicks.prizepicks_disabled_reason()
    if disabled_reason:
        return f"PrizePicks: disabled ({disabled_reason})"

    logger.info("Starting PrizePicks...")
    rows, _diagnostics = prizepicks.fetch_and_write_rows(output_path=Path(PP_PATH))
    return f"PrizePicks: {len(rows)} rows", len(rows)

def run_stats():
    logger.info("Starting Season Stats...")
    engine = nba_stats.NBAStatsEngine()
    df = engine.get_player_data()
    has_rows = isinstance(df, pd.DataFrame) and not df.empty
    has_required_columns = has_rows and STATS_REQUIRED_COLUMNS.issubset(set(df.columns))

    if not has_required_columns:
        existing_stats_usable = os.path.exists(STATS_PATH) and os.path.getsize(STATS_PATH) > 0
        if existing_stats_usable:
            logger.warning(
                "Season stats scrape returned unusable data; preserving existing season_stats.csv | rows=%s cols=%s",
                len(df) if isinstance(df, pd.DataFrame) else 0,
                list(df.columns) if isinstance(df, pd.DataFrame) else [],
            )
            # Not a success: preserving the previous file is exactly the shape of
            # failure that leaves a healthy-looking artifact on disk, and the only
            # reason the run continues at all.
            return ("Season Stats: 0 players (preserved previous file)", 0,
                    "season stats scrape returned unusable data; previous file preserved")

        missing_cols = (
            sorted(STATS_REQUIRED_COLUMNS - set(df.columns))
            if isinstance(df, pd.DataFrame)
            else sorted(STATS_REQUIRED_COLUMNS)
        )
        raise RuntimeError(
            f"Season stats scrape returned unusable data and no fallback file exists (missing_cols={missing_cols})"
        )

    _atomic_write_dataframe_csv(df, STATS_PATH)
    return f"Season Stats: {len(df)} players", len(df)

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
                # Continuing on the existing file keeps the slate usable, so this
                # is not an exception -- but it is not a success either, and the
                # loop must not record it as one. The third element says so.
                return (message, None,
                        f"game logs refresh failed; continuing on existing logs "
                        f"through {result['last_saved_date']}")
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

    # Row count deliberately not reported: an empty schedule is a normal
    # off-season or off-day answer, and `scraper:schedule` is dormancy-exempt, so
    # counting zero here would be the one empty streak that pages in July.
    return f"Schedule: {len(df)} games"



def run_shooting_zones():
    logger.info("Starting Shooting Zones...")
    data = shooting_zones.get_shooting_zones_data()
    with open(SHOOTING_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Shooting Zones: {len(data)} players", len(data)

def run_assist_zones():
    logger.info("Starting Assist Zones...")
    data = assist_zones.get_assist_zones_data()
    with open(ASSISTS_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Assist Zones: {len(data)} players", len(data)

def run_opp_assist_zones():
    logger.info("Starting Opponent Assist Zones...")
    data = opp_assist_zones.get_opp_assist_zones_data()
    with open(OPP_ASSIST_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Assist Zones: {len(data)} teams", len(data)

def run_opp_def_zones():
    logger.info("Starting Opponent Defense Zones...")
    data = opp_def_zones.get_opp_def_zones_data()
    with open(OPP_DEF_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Defense Zones: {len(data)} teams", len(data)

def run_shot_type_analysis():
    logger.info("Starting Shot Type Analysis...")
    data = shot_type_analysis.get_shot_type_data()
    with open(SHOT_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return (f"Shot Type Analysis: {len(data.get('players', {}))} players, "
            f"{len(data.get('teams', {}))} teams"), len(data.get("players", {}))

def run_opp_shot_type_analysis():
    logger.info("Starting Opponent Shot Type Analysis...")
    data = opp_shot_type_analysis.process_defensive_rankings()
    with open(OPP_SHOT_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Shot Type Analysis: {len(data)} teams", len(data)

def run_play_type_analysis():
    logger.info("Starting Play Type Analysis...")
    data = play_type_analysis.get_play_type_data()
    with open(PLAY_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return (f"Play Type Analysis: {len(data.get('players', {}))} players, "
            f"{len(data.get('teams', {}))} teams"), len(data.get("players", {}))

def run_boxscores():
    logger.info("Starting Boxscores...")
    boxscores.run_scrape(BOXSCORES_PATH)
    return "Boxscores Updated"

def _games_scheduled_today():
    """How many games today, or None if that cannot be established.

    Used only to decide whether an empty scrape is evidence of breakage. Zero rows
    is the *correct* answer for every odds and zone scraper on a gameless day --
    the All-Star break is four or five of them in a row, which is exactly the
    threshold that promotes an empty streak to an incident. Without this, the
    monitor's first act of the season would have been to page about February.

    Schedule runs first in the scraper list, so SCHEDULE_PATH is this run's own
    answer by the time any odds scraper reports. None (unreadable, or the schedule
    scrape failed) suppresses empty-tracking too: `scraper:schedule` is itself
    monitored and dormancy-exempt, so that failure is already covered and does not
    need to be inferred from everything downstream of it.
    """
    try:
        with open(SCHEDULE_PATH, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    games = payload.get("games") if isinstance(payload, dict) else payload
    return len(games) if isinstance(games, list) else None


def _unpack_scraper_result(result):
    """Normalise a `run_*` return value to (message, rows, failure_reason).

    A wrapper may return a bare message, `(message, rows)` where it already knows
    how many rows it produced, or `(message, rows, failure_reason)` for the awkward
    middle case: the refresh did not work, but the run continues on a preserved
    artifact rather than raising. That third form exists so the loop does not record
    a success for something the wrapper just warned about -- the shape of failure
    that leaves a healthy-looking file on disk and goes unnoticed for days.
    """
    if not isinstance(result, tuple):
        return result, None, None
    padded = result + (None,) * (3 - len(result))
    return padded[0], padded[1], padded[2]


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
            #
            # The third element is the health ledger unit. Every one of these
            # failures used to be logged and dropped here, which is how the injury
            # report stayed broken for three days: the except block below knew, and
            # told nothing that outlived the process. See utils/health_ledger.py.
            scrapers = [
                ("Schedule", run_schedule, "scraper:schedule"),
                ("Shooting Zones", run_shooting_zones, "scraper:shooting_zones"),
                ("Assist Zones", run_assist_zones, "scraper:assist_zones"),
                ("Opp Assist Zones", run_opp_assist_zones, "scraper:opp_assist_zones"),
                ("Opp Defense Zones", run_opp_def_zones, "scraper:opp_def_zones"),
                ("Shot Type Analysis", run_shot_type_analysis, "scraper:shot_type"),
                ("Opp Shot Type Analysis", run_opp_shot_type_analysis, "scraper:opp_shot_type"),
                ("Play Type Analysis", run_play_type_analysis, "scraper:play_type"),
                ("DraftKings", run_dk, "scraper:draftkings"),
                ("FanDuel", run_fd, "scraper:fanduel"),
                ("PrizePicks", run_pp, "scraper:prizepicks"),
                ("Season Stats", run_stats, "scraper:season_stats"),
                ("Game Logs", run_logs, "scraper:gamelogs"),
                ("Boxscores", run_boxscores, "scraper:boxscores"),
            ]
            # These are the only blockers that should cause the daily pipeline
            # to be treated as a failed run by master_cron. Game logs can lag
            # temporarily without breaking same-day schedule or props refreshes.
            critical_scrapers = {"Schedule", "Season Stats"}
            critical_failures = []
            
            for name, func, unit in scrapers:
                try:
                    log_status(logger, "RUN", "Scraper started", step=name)
                    result = func()
                    message, rows, soft_failure = _unpack_scraper_result(result)
                    if soft_failure:
                        log_status(logger, "WARN", str(message), step=name)
                        health_ledger.record_failure(unit, soft_failure,
                                                     detail=str(message))
                    else:
                        log_status(logger, "OK", str(message), step=name)
                        # Row counts are only evidence on a day that has games;
                        # see _games_scheduled_today.
                        health_ledger.record_success(
                            unit,
                            rows=rows if _games_scheduled_today() else None,
                            detail=str(message),
                        )
                except Exception as e:
                    log_status(logger, "FAIL", "Scraper failed", step=name, error=e)
                    health_ledger.record_failure(unit, e)
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
                pp_path=PP_PATH if prizepicks.prizepicks_enabled() else None,
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

            # Stamp current-roster membership. Must run after run_aggregation(),
            # which is what upserts the players rows being stamped. Non-fatal and
            # self-guarding: it skips rather than writing a partial sweep, so a
            # bad upstream roster response can never age out the league.
            try:
                from utils.roster_sync import sync_roster_membership
                sync_roster_membership()
            except Exception as e:
                log_status(logger, "WARN", "Roster membership sync failed", error=e)

            # STEP 3: Upsert props to Supabase (non-fatal)
            # The players table was already upserted inside aggregator.run_aggregation().
            # This call pushes the resolved prop lines (DK + FD) into player_props.
            log_status(logger, "RUN", "Supabase sync started")
            props_ok = True
            historical_sync_ok = True
            normalized_historical_ok = True
            legacy_historical_ok = True
            legacy_historical_fallback_enabled = True
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
                from utils.upsert_market_history import (
                    historical_odds_legacy_fallback_enabled,
                    sync_recent_historical_odds_from_file,
                    sync_recent_historical_player_props_from_file,
                )
                legacy_historical_fallback_enabled = historical_odds_legacy_fallback_enabled()
                normalized_historical_ok = bool(sync_recent_historical_player_props_from_file(
                    os.path.join(BASE_DIR, "data", "archive", "historical_odds.json"),
                ))
                if legacy_historical_fallback_enabled:
                    legacy_historical_ok = bool(sync_recent_historical_odds_from_file(
                        os.path.join(BASE_DIR, "data", "archive", "historical_odds.json"),
                    ))
                historical_sync_ok = normalized_historical_ok and legacy_historical_ok
            except Exception as e:
                log_status(logger, "WARN", "Normalized historical odds sync failed", error=e)
                historical_sync_ok = False
                normalized_historical_ok = False
                legacy_historical_ok = False

            db_sync_ok = props_ok and historical_sync_ok
            if db_sync_ok:
                health_ledger.record_success("sync:supabase_props")
                log_status(logger, "OK", "Supabase sync complete")
            else:
                # Supabase is the only durable copy: local data/current/ is scratch that the
                # next run overwrites, and the VM itself is disposable. A failed sync therefore
                # loses the day permanently unless we report failure and let master_cron retry
                # (bounded by MAX_PIPELINE_FAILURES_PER_DAY), so this is ERROR, not WARN.
                health_ledger.record_failure(
                    "sync:supabase_props",
                    f"props_ok={props_ok} normalized_historical_ok={normalized_historical_ok} "
                    f"legacy_historical_ok={legacy_historical_ok}",
                    detail="data/current is scratch and the VM is disposable, so an "
                           "unsynced day is lost permanently",
                )
                log_status(
                    logger,
                    "ERROR",
                    "Supabase sync incomplete - day not durably stored",
                    props_ok=props_ok,
                    normalized_historical_ok=normalized_historical_ok,
                    legacy_historical_ok=legacy_historical_ok,
                    legacy_historical_fallback_enabled=legacy_historical_fallback_enabled,
                )

            # Warm the prior-season cache before scoring. Early in a season the
            # Signal Score has nothing but last season to project from, and the
            # first build pulls ~550 players per season out of archive_gamelogs
            # (about a minute). Doing it here keeps that one-off cost out of
            # the scoring step and gives it its own log line. Non-fatal: a
            # cold cache just means edge_score builds it lazily instead.
            try:
                from utils.prior_season import get_prior_history
                prior_history = get_prior_history()
                log_status(logger, "OK", "Prior-season history ready",
                           players=len(prior_history))
            except Exception as e:
                log_status(logger, "WARN", "Prior-season history unavailable", error=e)

            try:
                from utils.edge_score import run_edge_score_refresh
                run_edge_score_refresh(refresh_label="pipeline")
                health_ledger.record_success("score:edge_refresh")
            except Exception as e:
                log_status(logger, "WARN", "Edge Score refresh failed", error=e)
                health_ledger.record_failure("score:edge_refresh", e)

            if critical_failures:
                log_status(logger, "WARN", "Critical scraper failures detected", count=len(critical_failures))
                for failure in critical_failures:
                    logger.warning("  %s", failure)

            # A pipeline run only "succeeded" if the data actually reached Supabase.
            # Set REQUIRE_DB_SYNC=false to fall back to the old scraper-only definition
            # (e.g. to work offline, or to stop retries while Supabase is down).
            require_db_sync = os.environ.get("REQUIRE_DB_SYNC", "true").strip().lower() not in {"0", "false", "no"}
            if require_db_sync and not db_sync_ok:
                return False

            return not critical_failures
    finally:
        stdout_capture.flush()

if __name__ == "__main__":
    main()
