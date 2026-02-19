import pandas as pd
import requests
import time
import urllib.parse
import os
from nba_api.stats.endpoints import leaguegamelog
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# CONFIGURATION
# ==========================================
UPDATE_WINDOW_DAYS = 5 
MAX_HISTORY_GAMES = 35 
MAX_WORKERS = 4  # Concurrency limit to prevent throttling

# Browser fingerprinting to bypass NBA protections
HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "dnt": "1",
    "origin": "https://www.nba.com",
    "referer": "https://www.nba.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
}

PT_MEASURE_TYPES = ["Passing", "Rebounding", "Drives"]

def fetch_tracking_data_for_date(date_str):
    """
    Fetches Drives, Passing, and Rebounding for a single date.
    Uses your original requests logic for safety, but adds retry handling.
    """
    daily_merged = None
    encoded_date = urllib.parse.quote(date_str, safe='')
    
    for PT in PT_MEASURE_TYPES:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # 1.0s sleep combined with 2 workers keeps pacing perfectly human-like
                time.sleep(1.0) 
                
                url = (f"https://stats.nba.com/stats/leaguedashptstats?DateFrom={encoded_date}&DateTo={encoded_date}&"
                       f"LastNGames=0&LeagueID=00&Month=0&OpponentTeamID=0&PORound=0&PerMode=PerGame&"
                       f"PlayerOrTeam=Player&PtMeasureType={PT}&Season=2025-26&SeasonType=Regular%20Season&TeamID=0")
                
                # Using your exact Code#1 requests implementation
                resp = requests.get(url, headers=HEADERS, timeout=15)
                
                if resp.status_code != 200: 
                    raise ValueError(f"Bad status code: {resp.status_code}")
                
                r = resp.json()
                row_set = r['resultSets'][0].get('rowSet', [])
                api_headers = r['resultSets'][0].get('headers', [])
                
                if not row_set: 
                    break # Success, but no data for this specific tracking type today
                
                df_pt = pd.DataFrame(row_set, columns=api_headers)
                cols_to_keep = ['PLAYER_ID', 'POTENTIAL_AST', 'AST_POINTS_CREATED', 'REB_CHANCES', 'REB_CONTEST_PCT', 'DRIVES', 'DRIVE_PTS', 'DRIVE_PASSES']
                df_pt = df_pt[[c for c in cols_to_keep if c in df_pt.columns]]

                if daily_merged is None: 
                    daily_merged = df_pt
                else: 
                    daily_merged = pd.merge(daily_merged, df_pt, on='PLAYER_ID', how='outer')
                
                # Success! Break the retry loop
                break 

            except Exception as e:
                print(f"      ⚠️ Attempt {attempt + 1} failed for {PT} on {date_str}: {type(e).__name__} ({e})")
                if attempt < max_retries - 1:
                    time.sleep(3 * (attempt + 1)) # Wait 3s, then 6s before retrying
                else:
                    print(f"      ❌ Giving up on {PT} for {date_str}.")
            
    if daily_merged is not None:
        daily_merged['DATE_STR'] = date_str
        
    return daily_merged

def run_scrape(output_path, n_games=20):
    print(f"   📅 Managing Game Logs at {output_path}")
    
    # 1. DETERMINE SCRAPE STRATEGY
    target_dates = []
    existing_df = pd.DataFrame()
    full_refresh = True

    if os.path.exists(output_path):
        try:
            print("      Found existing logs. Running INCREMENTAL update.")
            existing_df = pd.read_csv(output_path)
            if 'GAME_DATE' in existing_df.columns:
                existing_df['GAME_DATE'] = pd.to_datetime(existing_df['GAME_DATE'])
            full_refresh = False
        except Exception as e:
            print(f"      ⚠️ Corrupt CSV ({e}). forcing full refresh.")
            full_refresh = True

    # 2. FETCH BASE LOGS (Your original base log logic)
    # 2. FETCH BASE LOGS (Now with Stealth Headers & Retries)
    df_logs = None
    for attempt in range(3):
        try:
            # We explicitly pass your custom HEADERS and a 15s timeout here
            game_log = leaguegamelog.LeagueGameLog(
                season='2025-26', 
                player_or_team_abbreviation='P', 
                direction='DESC', 
                sorter='DATE',
                headers=HEADERS, 
                timeout=15
            )
            df_logs = game_log.get_data_frames()[0]
            df_logs['GAME_DATE'] = pd.to_datetime(df_logs['GAME_DATE'])
            df_logs['DATE_STR'] = df_logs['GAME_DATE'].dt.strftime('%m/%d/%Y')
            break  # Success! Break out of the retry loop
            
        except Exception as e:
            print(f"   ⚠️ Base log attempt {attempt + 1} failed: {type(e).__name__} ({e})")
            if attempt < 2:
                time.sleep(3) # Wait 3 seconds before trying again
                
    if df_logs is None:
        print("   ❌ Fatal Error: Could not fetch base LeagueGameLog after 3 attempts.")
        return

    # 3. DEFINE DATES TO SCRAPE (Strictly < Today)
    all_active_dates = sorted(df_logs['DATE_STR'].unique().tolist(), key=lambda x: datetime.strptime(x, '%m/%d/%Y'), reverse=True)
    today_obj = datetime.now().date()
    valid_dates = [d for d in all_active_dates if datetime.strptime(d, '%m/%d/%Y').date() < today_obj]
    
    if full_refresh:
        target_dates = valid_dates[:n_games+5]
        print(f"      🔄 Full Refresh: Scraping last {len(target_dates)} COMPLETED dates.")
    else:
        cutoff_date = datetime.now() - timedelta(days=UPDATE_WINDOW_DAYS)
        target_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') >= cutoff_date]
        if not existing_df.empty:
            last_saved_date = existing_df['GAME_DATE'].max()
            missed_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') > last_saved_date]
            target_dates = list(set(target_dates + missed_dates))
            
        print(f"      ➕ Incremental: Scraping {len(target_dates)} dates (Since {cutoff_date.strftime('%Y-%m-%d')}, excluding Today)")

    if not target_dates:
        print("      ✅ Data is up to date (No completed games to fetch).")
        return

    # 4. FETCH ADVANCED STATS (ThreadPool Speed!)
    print(f"      🚀 Launching {MAX_WORKERS} threads for {len(target_dates)} dates...")
    advanced_data_list = []
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(fetch_tracking_data_for_date, d): d for d in target_dates}
        
        for i, future in enumerate(as_completed(future_map)):
            date_str = future_map[future]
            try:
                result = future.result()
                if result is not None:
                    advanced_data_list.append(result)
                print(f"      [{i+1}/{len(target_dates)}] Completed {date_str}")
            except Exception as e:
                print(f"      ⚠️ Failed {date_str}: {e}")

    # 5. MERGE & SAVE
    if advanced_data_list:
        df_advanced_new = pd.concat(advanced_data_list, ignore_index=True)
        df_logs_filtered = df_logs[df_logs['DATE_STR'].isin(target_dates)]
        df_new_final = pd.merge(df_logs_filtered, df_advanced_new, on=['PLAYER_ID', 'DATE_STR'], how='left')
        
        # Combos
        df_new_final = df_new_final.fillna(0)
        df_new_final['PTS+REB+AST'] = df_new_final['PTS'] + df_new_final['REB'] + df_new_final['AST']
        df_new_final['PTS+REB'] = df_new_final['PTS'] + df_new_final['REB']
        df_new_final['PTS+AST'] = df_new_final['PTS'] + df_new_final['AST']
        df_new_final['REB+AST'] = df_new_final['REB'] + df_new_final['AST']
        df_new_final['STL+BLK'] = df_new_final['STL'] + df_new_final['BLK']

        if not existing_df.empty:
            print(f"      ✂️ Replacing overlapping data for {len(target_dates)} dates...")
            existing_df = existing_df[~existing_df['DATE_STR'].isin(target_dates)]
            final_df = pd.concat([existing_df, df_new_final], ignore_index=True)
        else:
            final_df = df_new_final
    else:
        final_df = existing_df

    if not final_df.empty:
        final_df['GAME_DATE'] = pd.to_datetime(final_df['GAME_DATE'])
        final_df = final_df.sort_values(by=['PLAYER_ID', 'GAME_DATE'], ascending=[True, False])
        final_df = final_df.groupby('PLAYER_ID').head(MAX_HISTORY_GAMES).reset_index(drop=True)
        
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        final_df.to_csv(output_path, index=False)
        print(f"   💾 Updated logs saved! (Rows: {len(final_df)})")

if __name__ == "__main__":
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(current_script_dir)
    output_path = os.path.join(backend_dir, "data", "current", "gamelogs.csv")
    run_scrape(output_path)