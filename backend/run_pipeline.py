import time
import pandas as pd
import os
import sys
import gc
import psutil
from datetime import datetime

# Add path to scrapers
sys.path.append(os.path.join(os.path.dirname(__file__), 'scrapers'))

# Import modules
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
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
import json

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
    print("   Starting DraftKings...")
    data = draftkings.fetch_dk_odds()
    df = pd.DataFrame(data)
    df.to_csv(DK_PATH, index=False)
    return f"DraftKings: {len(df)} rows"

def run_fd():
    print("   Starting FanDuel...")
    data = fanduel.fetch_odds()
    df = pd.DataFrame(data)
    df.to_csv(FD_PATH, index=False)
    return f"FanDuel: {len(df)} rows"

def run_stats():
    print("   Starting Season Stats...")
    engine = nba_stats.NBAStatsEngine()
    df = engine.get_player_data()
    df.to_csv(STATS_PATH, index=False)
    return f"Season Stats: {len(df)} players"

def run_logs():
    print("   Starting Game Logs (Incremental)...")
    # This runs the fast update
    gamelogs.run_scrape(LOGS_PATH)
    return "Game Logs Updated"

def run_schedule():
    print("   Starting Game Schedule...")
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
        print(f"   Warning: games DB upsert failed (non-fatal): {e}")

    return f"Schedule: {len(df)} games"


def run_shooting_zones():
    print("   Starting Shooting Zones...")
    data = shooting_zones.get_shooting_zones_data()
    with open(SHOOTING_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Shooting Zones: {len(data)} players"

def run_assist_zones():
    print("   Starting Assist Zones...")
    data = assist_zones.get_assist_zones_data()
    with open(ASSISTS_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Assist Zones: {len(data)} players"

def run_opp_assist_zones():
    print("   Starting Opponent Assist Zones...")
    data = opp_assist_zones.get_opp_assist_zones_data()
    with open(OPP_ASSIST_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Assist Zones: {len(data)} teams"

def run_opp_def_zones():
    print("   Starting Opponent Defense Zones...")
    data = opp_def_zones.get_opp_def_zones_data()
    with open(OPP_DEF_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Defense Zones: {len(data)} teams"

def run_shot_type_analysis():
    print("   Starting Shot Type Analysis...")
    data = shot_type_analysis.get_shot_type_data()
    with open(SHOT_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Shot Type Analysis: {len(data.get('players', {}))} players, {len(data.get('teams', {}))} teams"

def run_opp_shot_type_analysis():
    print("   Starting Opponent Shot Type Analysis...")
    data = opp_shot_type_analysis.process_defensive_rankings()
    with open(OPP_SHOT_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Opp Shot Type Analysis: {len(data)} teams"

def run_play_type_analysis():
    print("   Starting Play Type Analysis...")
    data = play_type_analysis.get_play_type_data()
    with open(PLAY_TYPE_PATH, "w") as f:
        json.dump(data, f, indent=4)
    return f"Play Type Analysis: {len(data.get('players', {}))} players, {len(data.get('teams', {}))} teams"

def run_boxscores():
    print("   Starting Boxscores...")
    boxscores.run_scrape(BOXSCORES_PATH)
    return "Boxscores Updated"

def log_memory(stage_name):
    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / (1024 * 1024)
    print(f"[{stage_name}] Memory Usage: {mem_mb:.1f} MB")

def main():
    start_time = time.time()
    start_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_PATH, 'w') as f:
        f.write(f"Pipeline started: {start_str}\n")
    print(f"PIPELINE STARTED: {start_str}")
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
        ("Season Stats", run_stats),
        ("Game Logs", run_logs),
        ("Boxscores", run_boxscores)
    ]
    
    for name, func in scrapers:
        try:
            result = func()
            print(f"   {result}")
        except Exception as e:
            print(f"   Scraper Failed [{name}]: {e}")
        finally:
            gc.collect()
            log_memory(f"After {name}")

    # STEP 2: Run Aggregator
    print("\nRunning Aggregator...")
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
    print(f"\nPIPELINE COMPLETE in {total_time:.2f} seconds")

    # STEP 3: Upsert props to Supabase (non-fatal)
    # The players table was already upserted inside aggregator.run_aggregation().
    # This call pushes the resolved prop lines (DK + FD) into player_props.
    print("\nUpserting props to Supabase...")
    props_ok = True
    try:
        from utils.upsert_props import run_odds_update
        props_ok = bool(run_odds_update(
            dk_path=DK_PATH,
            fd_path=FD_PATH,
            stats_path=STATS_PATH,
        ))
    except Exception as e:
        print(f"   Warning: props upsert failed (non-fatal): {e}")
        props_ok = False

    return props_ok

if __name__ == "__main__":
    main()
