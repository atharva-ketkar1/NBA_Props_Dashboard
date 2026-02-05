import time
import pandas as pd
import concurrent.futures
from datetime import datetime
import os

# Import your scrapers as modules
# Ensure your scraper files are in a folder named 'scrapers' with an empty __init__.py
from scrapers import fetch_odds_draftkings as draftkings
from scrapers import fetch_odds_fanduel as fanduel
from scrapers import season_stats_scrape as nba_stats
from utils import aggregator  

# CONFIGURATION
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 2. Force the data folder to be inside 'backend/data/current'
DATA_DIR = os.path.join(BASE_DIR, "data", "current")
os.makedirs(DATA_DIR, exist_ok=True)

def run_dk():
    print("   🔵 Starting DraftKings...")
    data = draftkings.fetch_dk_odds() # Ensure this function returns the list
    df = pd.DataFrame(data)
    df.to_csv(f"{DATA_DIR}/draftkings.csv", index=False)
    return f"DraftKings: {len(df)} rows"

def run_fd():
    print("   🔵 Starting FanDuel...")
    data = fanduel.fetch_odds() # Ensure this function returns the list
    df = pd.DataFrame(data)
    df.to_csv(f"{DATA_DIR}/fanduel.csv", index=False)
    return f"FanDuel: {len(df)} rows"

def run_stats():
    print("   🟠 Starting Season Stats...")
    engine = nba_stats.NBAStatsEngine()
    df = engine.get_player_data() # Ensure this returns the DF
    df.to_csv(f"{DATA_DIR}/stats.csv", index=False)
    return f"Season Stats: {len(df)} players"

def main():
    start_time = time.time()
    print("🚀 PIPELINE STARTED")

    # STEP 1: Run Scrapers in PARALLEL
    # This runs all 3 functions at the exact same time
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(run_dk),
            executor.submit(run_fd),
            executor.submit(run_stats)
        ]
        
        for future in concurrent.futures.as_completed(futures):
            try:
                print(f"   ✅ {future.result()}")
            except Exception as e:
                print(f"   ❌ Scraper Failed: {e}")

    # STEP 2: Run Aggregator (Must wait for Step 1 to finish)
    print("\n🔗 Running Aggregator...")
    try:
        # You might need to adjust aggregator to accept paths as arguments
        # or just let it read from the known 'data/current' folder
        aggregator.run_aggregation(
            stats_path=f"{DATA_DIR}/stats.csv",
            dk_path=f"{DATA_DIR}/draftkings.csv",
            fd_path=f"{DATA_DIR}/fanduel.csv",
            output_path=f"{DATA_DIR}/master_feed.json"
        )
        print("   ✅ Master Feed Updated!")
    except Exception as e:
        print(f"   ❌ Aggregation Failed: {e}")

    total_time = time.time() - start_time
    print(f"\n✨ PIPELINE COMPLETE in {total_time:.2f} seconds")

if __name__ == "__main__":
    main()