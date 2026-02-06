import pandas as pd
import requests
import time
import urllib.parse
import os
from nba_api.stats.endpoints import leaguegamelog
from datetime import datetime, timedelta

# ==========================================
# CONFIGURATION
# ==========================================
# How many days back to re-scrape to catch corrections?
UPDATE_WINDOW_DAYS = 5 

# Max history to keep 
MAX_HISTORY_GAMES = 35 

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

    # 2. FETCH BASE LOGS
    try:
        game_log = leaguegamelog.LeagueGameLog(season='2025-26', player_or_team_abbreviation='P', direction='DESC', sorter='DATE')
        df_logs = game_log.get_data_frames()[0]
        df_logs['GAME_DATE'] = pd.to_datetime(df_logs['GAME_DATE'])
        df_logs['DATE_STR'] = df_logs['GAME_DATE'].dt.strftime('%m/%d/%Y')
    except Exception as e:
        print(f"   ❌ Error fetching LeagueGameLog: {e}")
        return

    # 3. DEFINE DATES TO SCRAPE
    all_active_dates = sorted(df_logs['DATE_STR'].unique().tolist(), key=lambda x: datetime.strptime(x, '%m/%d/%Y'), reverse=True)
    
    # --- CRITICAL FIX: EXCLUDE TODAY ---
    # We define 'today' based on the system clock. 
    # Any game date matching 'today' is ignored to prevent partial stats.
    today_obj = datetime.now().date()
    
    # Filter valid dates (Strictly < Today)
    valid_dates = [d for d in all_active_dates if datetime.strptime(d, '%m/%d/%Y').date() < today_obj]
    
    if full_refresh:
        target_dates = valid_dates[:n_games+5]
        print(f"      🔄 Full Refresh: Scraping last {len(target_dates)} COMPLETED dates.")
    else:
        # INCREMENTAL: Only scrape last 5 days (excluding today)
        cutoff_date = datetime.now() - timedelta(days=UPDATE_WINDOW_DAYS)
        target_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') >= cutoff_date]
        
        # Catch up on missed days
        if not existing_df.empty:
            last_saved_date = existing_df['GAME_DATE'].max()
            missed_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') > last_saved_date]
            target_dates = list(set(target_dates + missed_dates))
            
        print(f"      ➕ Incremental: Scraping {len(target_dates)} dates (Since {cutoff_date.strftime('%Y-%m-%d')}, excluding Today)")

    if not target_dates:
        print("      ✅ Data is up to date (No completed games to fetch).")
        return

    # 4. FETCH ADVANCED STATS
    advanced_data_list = []
    for i, date_str in enumerate(target_dates):
        print(f"      [{i+1}/{len(target_dates)}] Scraping {date_str}...")
        encoded_date = urllib.parse.quote(date_str, safe='')
        daily_merged = None
        
        for PT in PT_MEASURE_TYPES:
            try:
                url = (f"https://stats.nba.com/stats/leaguedashptstats?DateFrom={encoded_date}&DateTo={encoded_date}&"
                       f"LastNGames=0&LeagueID=00&Month=0&OpponentTeamID=0&PORound=0&PerMode=PerGame&"
                       f"PlayerOrTeam=Player&PtMeasureType={PT}&Season=2025-26&SeasonType=Regular%20Season&TeamID=0")
                time.sleep(0.6) 
                resp = requests.get(url, headers=HEADERS, timeout=15)
                if resp.status_code != 200: continue
                
                r = resp.json()
                row_set = r['resultSets'][0].get('rowSet', [])
                headers = r['resultSets'][0].get('headers', [])
                if not row_set: continue
                
                df_pt = pd.DataFrame(row_set, columns=headers)
                cols_to_keep = ['PLAYER_ID', 'POTENTIAL_AST', 'AST_POINTS_CREATED', 'REB_CHANCES', 'REB_CONTEST_PCT', 'DRIVES', 'DRIVE_PTS', 'DRIVE_PASSES']
                df_pt = df_pt[[c for c in cols_to_keep if c in df_pt.columns]]

                if daily_merged is None: daily_merged = df_pt
                else: daily_merged = pd.merge(daily_merged, df_pt, on='PLAYER_ID', how='outer')
            except: pass

        if daily_merged is not None:
            daily_merged['DATE_STR'] = date_str
            advanced_data_list.append(daily_merged)

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