import pandas as pd
import requests
import time
import random
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
MAX_WORKERS = 4  # Used for incremental updates only; full refresh always uses 1

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

# Global cooldown tracker — shared across all fetch calls during full refresh
_last_500_time = 0


def fetch_tracking_data_for_date(date_str, is_full_refresh=False):
    """
    Fetches Drives, Passing, Rebounding, 1Q Stats, and 1H Stats for a single date.
    In full refresh mode: slower pacing, exponential backoff, and global cooldown.
    In incremental mode: fast pacing for daily updates.
    """
    global _last_500_time

    # Global cooldown only needed during full refresh
    if is_full_refresh:
        elapsed = time.time() - _last_500_time
        if elapsed < 60:
            wait = 60 - elapsed
            print(f"      Global cooldown active. Waiting {wait:.1f}s before starting {date_str}...")
            time.sleep(wait)

    daily_merged = None
    encoded_date = urllib.parse.quote(date_str, safe='')

    # Slower jittered sleep for full refresh, fast for incremental
    base_sleep = (4.0 + random.uniform(0.5, 2.0)) if is_full_refresh else 1.0

    # 1. FETCH PT TRACKING DATA (Passing, Rebounds, Drives)
    for PT in PT_MEASURE_TYPES:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                time.sleep(base_sleep)

                url = (f"https://stats.nba.com/stats/leaguedashptstats?DateFrom={encoded_date}&DateTo={encoded_date}&"
                       f"LastNGames=0&LeagueID=00&Month=0&OpponentTeamID=0&PORound=0&PerMode=PerGame&"
                       f"PlayerOrTeam=Player&PtMeasureType={PT}&Season=2025-26&SeasonType=Regular%20Season&TeamID=0")

                resp = requests.get(url, headers=HEADERS, timeout=30)

                if resp.status_code == 500:
                    if is_full_refresh:
                        _last_500_time = time.time()
                        wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
                        print(f"      Server overloaded (500) for {PT} on {date_str}. Cooling down {wait}s...")
                        time.sleep(wait)
                    raise ValueError("500 Internal Server Error")

                if resp.status_code != 200:
                    raise ValueError(f"Bad status code: {resp.status_code}")

                r = resp.json()
                row_set = r['resultSets'][0].get('rowSet', [])
                api_headers = r['resultSets'][0].get('headers', [])

                if not row_set:
                    break

                df_pt = pd.DataFrame(row_set, columns=api_headers)
                cols_to_keep = ['PLAYER_ID', 'POTENTIAL_AST', 'PASSES_MADE', 'AST_POINTS_CREATED',
                                'REB_CHANCES', 'REB_CONTEST_PCT', 'DRIVES', 'DRIVE_PTS', 'DRIVE_PASSES']
                df_pt = df_pt[[c for c in cols_to_keep if c in df_pt.columns]]

                if daily_merged is None:
                    daily_merged = df_pt
                else:
                    daily_merged = pd.merge(daily_merged, df_pt, on='PLAYER_ID', how='outer')
                break

            except Exception as e:
                retry_wait = 3 * (attempt + 1) if not is_full_refresh else 10 * (attempt + 1)
                print(f"      Attempt {attempt + 1} failed for {PT} on {date_str}: {type(e).__name__} ({e})")
                if attempt < max_retries - 1:
                    time.sleep(retry_wait)
                else:
                    print(f"      Giving up on {PT} for {date_str}.")

    # 2. FETCH 1ST QUARTER STATS
    max_retries = 3
    for attempt in range(max_retries):
        try:
            time.sleep(base_sleep)
            q1_url = (f"https://stats.nba.com/stats/leaguedashplayerstats?DateFrom={encoded_date}&DateTo={encoded_date}&"
                      f"GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=Base&Month=0&OpponentTeamID=0&"
                      f"Outcome=&PORound=0&PaceAdjust=N&PerMode=Totals&Period=1&PlusMinus=N&Rank=N&"
                      f"Season=2025-26&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&VsConference=&VsDivision=")

            resp = requests.get(q1_url, headers=HEADERS, timeout=30)

            if resp.status_code == 500:
                if is_full_refresh:
                    _last_500_time = time.time()
                    wait = 30 * (2 ** attempt)
                    print(f"      Server overloaded (500) for 1Q on {date_str}. Cooling down {wait}s...")
                    time.sleep(wait)
                raise ValueError("500 Internal Server Error")

            if resp.status_code != 200:
                raise ValueError(f"Bad status code: {resp.status_code}")

            r = resp.json()
            row_set = r['resultSets'][0].get('rowSet', [])
            api_headers = r['resultSets'][0].get('headers', [])

            if row_set:
                df_1q = pd.DataFrame(row_set, columns=api_headers)
                q1_cols = ['PLAYER_ID', 'MIN', 'PTS', 'AST', 'REB', 'FG3M', 'FGM', 'FTM', 'PF']
                df_1q = df_1q[[c for c in q1_cols if c in df_1q.columns]]
                df_1q = df_1q.rename(columns={c: f"1Q_{c}" for c in q1_cols if c != 'PLAYER_ID'})

                if daily_merged is None:
                    daily_merged = df_1q
                else:
                    daily_merged = pd.merge(daily_merged, df_1q, on='PLAYER_ID', how='outer')
            break

        except Exception as e:
            retry_wait = 3 * (attempt + 1) if not is_full_refresh else 10 * (attempt + 1)
            print(f"      Attempt {attempt + 1} failed for 1Q Stats on {date_str}: {type(e).__name__} ({e})")
            if attempt < max_retries - 1:
                time.sleep(retry_wait)
            else:
                print(f"      Giving up on 1Q Stats for {date_str}.")

    # 3. FETCH 1ST HALF STATS
    for attempt in range(max_retries):
        try:
            time.sleep(base_sleep)
            h1_url = (f"https://stats.nba.com/stats/leaguedashplayerstats?DateFrom={encoded_date}&DateTo={encoded_date}&"
                      f"GameSegment=First%20Half&LastNGames=0&LeagueID=00&Location=&MeasureType=Base&Month=0&"
                      f"OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=Totals&Period=0&PlusMinus=N&Rank=N&"
                      f"Season=2025-26&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&VsConference=&VsDivision=")

            resp = requests.get(h1_url, headers=HEADERS, timeout=30)

            if resp.status_code == 500:
                if is_full_refresh:
                    _last_500_time = time.time()
                    wait = 30 * (2 ** attempt)
                    print(f"      Server overloaded (500) for 1H on {date_str}. Cooling down {wait}s...")
                    time.sleep(wait)
                raise ValueError("500 Internal Server Error")

            if resp.status_code != 200:
                raise ValueError(f"Bad status code: {resp.status_code}")

            r = resp.json()
            row_set = r['resultSets'][0].get('rowSet', [])
            api_headers = r['resultSets'][0].get('headers', [])

            if row_set:
                df_1h = pd.DataFrame(row_set, columns=api_headers)
                h1_cols = ['PLAYER_ID', 'MIN', 'PTS', 'AST', 'REB', 'FG3M', 'FGM', 'FTM']
                df_1h = df_1h[[c for c in h1_cols if c in df_1h.columns]]
                df_1h = df_1h.rename(columns={c: f"1H_{c}" for c in h1_cols if c != 'PLAYER_ID'})

                if daily_merged is None:
                    daily_merged = df_1h
                else:
                    daily_merged = pd.merge(daily_merged, df_1h, on='PLAYER_ID', how='outer')
            break

        except Exception as e:
            retry_wait = 3 * (attempt + 1) if not is_full_refresh else 10 * (attempt + 1)
            print(f"      Attempt {attempt + 1} failed for 1H Stats on {date_str}: {type(e).__name__} ({e})")
            if attempt < max_retries - 1:
                time.sleep(retry_wait)
            else:
                print(f"      Giving up on 1H Stats for {date_str}.")

    # 4. FETCH ADVANCED STATS
    for attempt in range(max_retries):
        try:
            time.sleep(base_sleep)
            adv_url = (f"https://stats.nba.com/stats/leaguedashplayerstats?DateFrom={encoded_date}&DateTo={encoded_date}&"
                      f"GameSegment=&LastNGames=0&LeagueID=00&Location=&MeasureType=Advanced&Month=0&"
                      f"OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=Totals&Period=0&PlusMinus=N&Rank=N&"
                      f"Season=2025-26&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&VsConference=&VsDivision=")

            resp = requests.get(adv_url, headers=HEADERS, timeout=30)

            if resp.status_code == 500:
                if is_full_refresh:
                    _last_500_time = time.time()
                    wait = 30 * (2 ** attempt)
                    print(f"      Server overloaded (500) for Advanced on {date_str}. Cooling down {wait}s...")
                    time.sleep(wait)
                raise ValueError("500 Internal Server Error")

            if resp.status_code != 200:
                raise ValueError(f"Bad status code: {resp.status_code}")

            r = resp.json()
            row_set = r['resultSets'][0].get('rowSet', [])
            api_headers = r['resultSets'][0].get('headers', [])

            if row_set:
                df_adv = pd.DataFrame(row_set, columns=api_headers)
                
                # Filter for the advanced metrics we want
                adv_cols = ['PLAYER_ID', 'USG_PCT', 'AST_PCT', 'REB_PCT', 'TS_PCT', 'PIE']
                df_adv = df_adv[[c for c in adv_cols if c in df_adv.columns]]

                # Convert decimals to clean whole percentages (e.g., 0.284 -> 28.4)
                cols_to_multiply = ['USG_PCT', 'AST_PCT', 'REB_PCT', 'TS_PCT', 'PIE']
                for col in cols_to_multiply:
                    if col in df_adv.columns:
                        df_adv[col] = (df_adv[col] * 100).round(1)

                if daily_merged is None:
                    daily_merged = df_adv
                else:
                    daily_merged = pd.merge(daily_merged, df_adv, on='PLAYER_ID', how='outer')
            break

        except Exception as e:
            retry_wait = 3 * (attempt + 1) if not is_full_refresh else 10 * (attempt + 1)
            print(f"      Attempt {attempt + 1} failed for Advanced Stats on {date_str}: {type(e).__name__} ({e})")
            if attempt < max_retries - 1:
                time.sleep(retry_wait)
            else:
                print(f"      Giving up on Advanced Stats for {date_str}.")

    if daily_merged is not None:
        daily_merged['DATE_STR'] = date_str

    return daily_merged


def run_scrape(output_path, n_games=85):
    print(f"   Managing Game Logs at {output_path}")

    target_dates = []
    existing_df = pd.DataFrame()
    full_refresh = True

    if os.path.exists(output_path):
        try:
            print("      Found existing logs. Running INCREMENTAL update.")
            existing_df = pd.read_csv(output_path)
            if 'GAME_DATE' in existing_df.columns:
                existing_df['GAME_DATE'] = pd.to_datetime(existing_df['GAME_DATE'])
                existing_df['DATE_STR'] = existing_df['GAME_DATE'].dt.strftime('%m/%d/%Y')
            full_refresh = False
        except Exception as e:
            print(f"      Corrupt CSV ({e}). Forcing full refresh.")
            full_refresh = True

    # Fetch base game logs
    df_logs = None
    for attempt in range(3):
        try:
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
            break
        except Exception as e:
            print(f"   Base log attempt {attempt + 1} failed: {type(e).__name__} ({e})")
            if attempt < 2:
                time.sleep(3)

    if df_logs is None:
        print("   Fatal Error: Could not fetch base LeagueGameLog after 3 attempts.")
        return

    all_active_dates = sorted(df_logs['DATE_STR'].unique().tolist(), key=lambda x: datetime.strptime(x, '%m/%d/%Y'), reverse=True)
    today_obj = datetime.now().date()
    valid_dates = [d for d in all_active_dates if datetime.strptime(d, '%m/%d/%Y').date() < today_obj]

    if full_refresh:
        target_dates = valid_dates[:n_games + 5]
        workers = 1
        print(f"      Full Refresh (Reliability Mode): Fetching {len(target_dates)} dates with 1 worker.")
    else:
        cutoff_date = datetime.now() - timedelta(days=UPDATE_WINDOW_DAYS)
        target_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') >= cutoff_date]
        if not existing_df.empty:
            last_saved_date = existing_df['GAME_DATE'].max()
            missed_dates = [d for d in valid_dates if datetime.strptime(d, '%m/%d/%Y') > last_saved_date]
            target_dates = list(set(target_dates + missed_dates))
        workers = MAX_WORKERS
        print(f"      Incremental: Scraping {len(target_dates)} dates with {workers} workers (Since {cutoff_date.strftime('%Y-%m-%d')})")

    if not target_dates:
        print("      Data is up to date (No completed games to fetch).")
        return

    print(f"      Launching {workers} workers for {len(target_dates)} dates...")
    advanced_data_list = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(fetch_tracking_data_for_date, d, full_refresh): d for d in target_dates}

        for i, future in enumerate(as_completed(future_map)):
            date_str = future_map[future]
            try:
                result = future.result()
                if result is not None:
                    advanced_data_list.append(result)
                print(f"      [{i+1}/{len(target_dates)}] Completed {date_str}")
            except Exception as e:
                print(f"      Failed {date_str}: {e}")

    if advanced_data_list:
        df_advanced_new = pd.concat(advanced_data_list, ignore_index=True)
        df_logs_filtered = df_logs[df_logs['DATE_STR'].isin(target_dates)]
        df_new_final = pd.merge(df_logs_filtered, df_advanced_new, on=['PLAYER_ID', 'DATE_STR'], how='left')

        df_new_final = df_new_final.fillna(0)
        df_new_final['PTS+REB+AST'] = df_new_final['PTS'] + df_new_final['REB'] + df_new_final['AST']
        df_new_final['PTS+REB'] = df_new_final['PTS'] + df_new_final['REB']
        df_new_final['PTS+AST'] = df_new_final['PTS'] + df_new_final['AST']
        df_new_final['REB+AST'] = df_new_final['REB'] + df_new_final['AST']
        df_new_final['STL+BLK'] = df_new_final['STL'] + df_new_final['BLK']

        if not existing_df.empty:
            print(f"      Replacing overlapping data for {len(target_dates)} dates...")
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

        cols_to_drop = [
            'SEASON_ID', 'PLAYER_NAME', 'TEAM_ID', 'TEAM_ABBREVIATION',
            'TEAM_NAME', 'VIDEO_AVAILABLE', 'DATE_STR',
            'FG_PCT', 'FG3_PCT', 'FT_PCT'
        ]
        final_df = final_df.drop(columns=[c for c in cols_to_drop if c in final_df.columns])

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        final_df.to_csv(output_path, index=False)
        print(f"   Updated logs saved! (Rows: {len(final_df)})")


if __name__ == "__main__":
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(current_script_dir)
    output_path = os.path.join(backend_dir, "data", "current", "gamelogs.csv")
    run_scrape(output_path)