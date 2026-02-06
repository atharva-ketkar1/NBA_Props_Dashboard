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
from scrapers import gamelogs as gamelogs # <--- IMPORT NEW SCRAPER
from utils import aggregator

# CONFIGURATION
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
os.makedirs(DATA_DIR, exist_ok=True)

# Define Paths
STATS_PATH = os.path.join(DATA_DIR, "stats.csv")
DK_PATH = os.path.join(DATA_DIR, "draftkings.csv")
FD_PATH = os.path.join(DATA_DIR, "fanduel.csv")
LOGS_PATH = os.path.join(DATA_DIR, "gamelogs.csv") # <--- NEW PATH
MASTER_PATH = os.path.join(DATA_DIR, "master_feed.json")

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

def main():
    start_time = time.time()
    print("🚀 PIPELINE STARTED")

    # STEP 1: Run Scrapers (Parallel)
    # Note: gamelogs is fast now, so we can include it in the thread pool!
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(run_dk),   # Uncomment to run
            executor.submit(run_fd),   # Uncomment to run
            executor.submit(run_stats), # Uncomment to run
            executor.submit(run_logs)     # Running this to link it up
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