import pandas as pd
import requests
import time
import urllib.parse
from nba_api.stats.endpoints import leaguegamelog
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
N_GAMES = 2            # How many games per player to keep
MAX_LOOKBACK = 6     # How many days back to check (e.g., 2 weeks)
TARGET_SEASON = '2025-26'

HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "dnt": "1",
    "host": "stats.nba.com",
    "origin": "https://www.nba.com",
    "referer": "https://www.nba.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
}

cols_to_keep = [
    'PLAYER_ID', 'PLAYER_NAME', 'GAME_DATE', 'MATCHUP', 'WL', 'MIN', 'TEAM_ID',
    'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV',
    'FGM', 'FGA', 'FG3M', 'FG3A', 'FTM', 'FTA',
    'POTENTIAL_AST', 'AST_POINTS_CREATED',
    'REB_CHANCES', 'REB_CONTEST_PCT',
    'DRIVES', 'DRIVE_PTS', 'DRIVE_PASSES'
]

PT_MEASURE_TYPES = ["Passing", "Rebounding", "Drives"]

# ==========================================
# STEP 1: FETCH GAME LOGS
# ==========================================
print(f"--- Step 1: Fetching Game Logs ---")

game_log = leaguegamelog.LeagueGameLog(
    season=TARGET_SEASON,
    player_or_team_abbreviation='P', 
    direction='DESC',                
    sorter='DATE'
)
df_logs = game_log.get_data_frames()[0]
df_logs['GAME_DATE'] = pd.to_datetime(df_logs['GAME_DATE'])

# --- DATE FILTER LOGIC ---
# 1. Get Today at midnight
today_midnight = pd.Timestamp.now().normalize()

# 2. Define the "Cutoff" (The oldest date we are willing to accept)
cutoff_date = today_midnight - pd.Timedelta(days=MAX_LOOKBACK)

print(f"Looking for games between {cutoff_date.strftime('%m/%d/%Y')} and {(today_midnight - pd.Timedelta(days=1)).strftime('%m/%d/%Y')}")

# 3. Filter: 
#    A. Strictly BEFORE today (< today_midnight)
#    B. NEW: strictly AFTER or EQUAL to the cutoff (>= cutoff_date)
df_logs = df_logs[
    (df_logs['GAME_DATE'] < today_midnight) & 
    (df_logs['GAME_DATE'] >= cutoff_date)
]

# 4. Sort and Keep only the last N games per player within that window
df_logs['DATE_STR'] = df_logs['GAME_DATE'].dt.strftime('%m/%d/%Y')
df_logs = df_logs.sort_values(by=['PLAYER_ID', 'GAME_DATE'], ascending=[True, False])

df_base = df_logs.groupby('PLAYER_ID').head(N_GAMES).copy().reset_index(drop=True)

# Get the list of unique dates we need to fetch advanced stats for
unique_dates = df_base['DATE_STR'].unique().tolist()
unique_dates.sort(key=lambda x: datetime.strptime(x, '%m/%d/%Y'), reverse=True)

print(f"Games found spanning {len(unique_dates)} unique dates.")

# ==========================================
# STEP 2: SCRAPE ADVANCED STATS
# ==========================================
print("--- Step 2: Fetching Advanced Tracking Data ---")

advanced_data_list = []

for date_str in unique_dates:
    encoded_date = urllib.parse.quote(date_str, safe='')
    daily_merged = None
    
    for PT in PT_MEASURE_TYPES:
        try:
            url = (
                f"https://stats.nba.com/stats/leaguedashptstats?College=&Conference=&Country=&"
                f"DateFrom={encoded_date}&DateTo={encoded_date}&"
                f"Division=&DraftPick=&DraftYear=&GameScope=&Height=&ISTRound=&"
                f"LastNGames=0&LeagueID=00&Location=&Month=0&OpponentTeamID=0&Outcome=&"
                f"PORound=0&PerMode=PerGame&PlayerExperience=&PlayerOrTeam=Player&"
                f"PlayerPosition=&PtMeasureType={PT}&Season={TARGET_SEASON}&"
                f"SeasonSegment=&SeasonType=Regular%20Season&StarterBench=&"
                f"TeamID=0&VsConference=&VsDivision=&Weight="
            )
            
            r = requests.get(url, headers=HEADERS).json()
            df_pt = pd.DataFrame(r['resultSets'][0]['row_set' if 'row_set' in r['resultSets'][0] else 'rowSet'], 
                                columns=r['resultSets'][0]['headers'])

            if df_pt.empty:
                continue

            # Identify columns to keep from this specific PT type, excluding base stats
            base_stats_to_exclude = ['MIN', 'PTS', 'REB', 'AST']
            current_cols = [c for c in cols_to_keep if c in df_pt.columns and c not in base_stats_to_exclude]
            if 'PLAYER_ID' not in current_cols: current_cols.insert(0, 'PLAYER_ID')
            
            df_pt = df_pt[current_cols]

            if daily_merged is None:
                daily_merged = df_pt
            else:
                cols_to_use = df_pt.columns.difference(daily_merged.columns).tolist() + ['PLAYER_ID']
                daily_merged = pd.merge(daily_merged, df_pt[cols_to_use], on='PLAYER_ID', how='outer')
            
            time.sleep(0.6)
            
        except Exception as e:
            print(f"[Error] {date_str} - {PT}: {e}")

    if daily_merged is not None:
        daily_merged['DATE_STR'] = date_str
        advanced_data_list.append(daily_merged)
        print(f"[Success] Processed {date_str}")

# ==========================================
# STEP 3: MERGE & CALCULATE NEW STATS
# ==========================================
if advanced_data_list:
    df_advanced_total = pd.concat(advanced_data_list, ignore_index=True)
    df_advanced_total = df_advanced_total.drop(columns=['PLAYER_NAME'], errors='ignore')
else:
    df_advanced_total = pd.DataFrame(columns=['PLAYER_ID', 'DATE_STR'])

print("--- Step 3: Merging Base Logs with Advanced Stats ---")

df_final = pd.merge(df_base, df_advanced_total, on=['PLAYER_ID', 'DATE_STR'], how='left')

team_cols = [c for c in df_final.columns if c.startswith('TEAM_ID')]
if team_cols:
    df_final['TEAM_ID'] = df_final[team_cols[0]]
    df_final = df_final.drop(columns=team_cols)

# 1. Filter to desired columns first
df_final = df_final[[c for c in cols_to_keep if c in df_final.columns]].copy()

# 2. Add Combination Stats
print("--- Calculating Combo Stats & Double Doubles ---")
df_final['Pts+Ast'] = df_final['PTS'] + df_final['AST']
df_final['Pts+Reb'] = df_final['PTS'] + df_final['REB']
df_final['Reb+Ast'] = df_final['REB'] + df_final['AST']
df_final['Pts+Reb+Ast'] = df_final['PTS'] + df_final['REB'] + df_final['AST']

# 3. Add Double Double Logic
categories = ['PTS', 'REB', 'AST', 'STL', 'BLK']
counts = df_final[categories].ge(10).sum(axis=1)
df_final['Double_Double'] = counts >= 2
df_final['Double_Double'] = df_final['Double_Double'].map({True: 'Yes', False: 'No'})

print("\n--- Final Dataframe Created ---")
if not df_final.empty:
    pd.set_option('display.max_columns', None)

    print(df_final.head(20))