import time
import pandas as pd
import concurrent.futures
import os
import sys

# Add path to scrapers
sys.path.append(os.path.join(os.path.dirname(__file__), 'scrapers'))

# Import modules
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
from scrapers import season_stats_scrape as nba_stats
from scrapers import gamelogs as gamelogs
from scrapers import fetch_todays_games as schedule # <--- NEW IMPORT
from utils import aggregator

# CONFIGURATION
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
os.makedirs(DATA_DIR, exist_ok=True)

# Define Paths
STATS_PATH = os.path.join(DATA_DIR, "stats.csv")
DK_PATH = os.path.join(DATA_DIR, "draftkings.csv")
FD_PATH = os.path.join(DATA_DIR, "fanduel.csv")
LOGS_PATH = os.path.join(DATA_DIR, "gamelogs.csv")
MASTER_PATH = os.path.join(DATA_DIR, "master_feed.json")
GAMES_PATH = os.path.join(DATA_DIR, "nba_dashboard_games.json") # <--- NEW PATH

def run_dk():
    print("   🔵 Starting DraftKings...")
    data = draftkings.fetch_dk_odds()
    df = pd.DataFrame(data)
    df.to_csv(DK_PATH, index=False)
    return f"DraftKings: {len(df)} rows"

def run_fd():
    print("   🔵 Starting FanDuel...")
    data = fanduel.fetch_odds()
    df = pd.DataFrame(data)
    df.to_csv(FD_PATH, index=False)
    return f"FanDuel: {len(df)} rows"

def run_stats():
    print("   🟠 Starting Season Stats...")
    engine = nba_stats.NBAStatsEngine()
    df = engine.get_player_data()
    df.to_csv(STATS_PATH, index=False)
    return f"Season Stats: {len(df)} players"

def run_logs():
    print("   🟣 Starting Game Logs (Incremental)...")
    # This runs the fast update
    gamelogs.run_scrape(LOGS_PATH)
    return "Game Logs Updated"

def run_schedule():
    print("   📅 Starting Game Schedule...")
    # This scraper saves its own file, so we just run it
    df, _ = schedule.get_dashboard_data()
    # Ensure it saves to the right place via the imported module if needed, 
    # but based on previous edit it saves to ../data/current/nba_dashboard_games.json relative to itself.
    # To be safe, we can rely on its internal saving or explicit save here if it returned raw data.
    # The previous edit to fetch_todays_games.py handles the saving.
    return f"Schedule: {len(df)} games"

def main():
    start_time = time.time()
    print("🚀 PIPELINE STARTED")

    # STEP 1: Run Scrapers (Parallel)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(run_dk),
            executor.submit(run_fd),
            executor.submit(run_stats),
            executor.submit(run_logs),
            executor.submit(run_schedule) # <--- ADDED
        ]
        
        for future in concurrent.futures.as_completed(futures):
            try:
                print(f"   ✅ {future.result()}")
            except Exception as e:
                print(f"   ❌ Scraper Failed: {e}")

    # STEP 2: Run Aggregator
    print("\n🔗 Running Aggregator...")
    aggregator.run_aggregation(
        stats_path=STATS_PATH,
        dk_path=DK_PATH,
        fd_path=FD_PATH,
        logs_path=LOGS_PATH, # <--- PASS NEW PATH
        output_path=MASTER_PATH
    )

    total_time = time.time() - start_time
    print(f"\n✨ PIPELINE COMPLETE in {total_time:.2f} seconds")

if __name__ == "__main__":
    main()