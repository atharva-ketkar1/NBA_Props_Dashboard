import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import concurrent.futures
import time
import random

class NBAStatsEngine:
    def __init__(self):
        self.session = requests.Session()
        
        # --- PROTECTION LAYER 1: HEADERS ---
        # Mimic a real browser session exactly
        self.headers = {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9",
            "connection": "keep-alive",
            "dnt": "1",
            "host": "stats.nba.com",
            "origin": "https://www.nba.com",
            "referer": "https://www.nba.com/",
            "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
        }
        self.session.headers.update(self.headers)

        # --- PROTECTION LAYER 2: AUTOMATIC RETRIES ---
        # If we get a 429 (Block) or 503 (Server Error), wait and try again automatically.
        # backoff_factor=1 means: wait 1s, then 2s, then 4s between retries.
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _fetch_url(self, url, tag=None):
        """Helper to fetch a single URL with jitter and error handling."""
        try:
            # --- PROTECTION LAYER 3: JITTER ---
            # Sleep a tiny random amount (0.1 to 0.5s) so requests don't hit 
            # the server at the exact same microsecond.
            time.sleep(random.uniform(0.1, 0.5))
            
            # Timeout prevents the script from hanging forever if the server is slow
            r = self.session.get(url, timeout=15)
            r.raise_for_status() # Raises error for 4xx/5xx codes
            
            data = r.json()
            headers = data['resultSets'][0]['headers']
            rows = data['resultSets'][0]['rowSet']
            return tag, pd.DataFrame(rows, columns=headers)
            
        except requests.exceptions.HTTPError as errh:
            print(f"Http Error for {tag}: {errh}")
        except requests.exceptions.ConnectionError as errc:
            print(f"Error Connecting for {tag}: {errc}")
        except requests.exceptions.Timeout as errt:
            print(f"Timeout Error for {tag}: {errt}")
        except requests.exceptions.RequestException as err:
            print(f"OOps: Something Else for {tag}: {err}")
            
        # Return empty DF on failure so pipeline doesn't break
        return tag, pd.DataFrame()

    def get_player_data(self):
        """Fetches all necessary endpoints in parallel."""
        start_time = time.time()
        
        # 1. Define Endpoints
        base_url = "https://stats.nba.com/stats/leaguedashplayerstats?College=&Conference=&Country=&DateFrom=&DateTo=&Division=&DraftPick=&DraftYear=&GameScope=&GameSegment=&Height=&ISTRound=&LastNGames=0&LeagueID=00&Location=&MeasureType=Base&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PaceAdjust=N&PerMode=PerGame&Period=0&PlayerExperience=&PlayerPosition=&PlusMinus=N&Rank=N&Season=2025-26&SeasonSegment=&SeasonType=Regular%20Season&ShotClockRange=&StarterBench=&TeamID=0&VsConference=&VsDivision=&Weight="
        
        adv_url_template = "https://stats.nba.com/stats/leaguedashptstats?College=&Conference=&Country=&DateFrom=&DateTo=&Division=&DraftPick=&DraftYear=&GameScope=&Height=&ISTRound=&LastNGames=0&LeagueID=00&Location=&Month=0&OpponentTeamID=0&Outcome=&PORound=0&PerMode=PerGame&PlayerExperience=&PlayerOrTeam=Player&PlayerPosition=&PtMeasureType={}&SeasonSegment=&SeasonType=Regular%20Season&StarterBench=&TeamID=0&VsConference=&VsDivision=&Weight="
        
        urls_map = {
            "Base": base_url,
            "Passing": adv_url_template.format("Passing&Season=2025-26"),
            "Drives": adv_url_template.format("Drives&Season=2025-26"),
            "Rebounding": adv_url_template.format("Rebounding&Season=2025-26")
        }

        # 2. Parallel Execution with Max Workers
        # Limited to 3 workers to be "polite" to the API (reduces instant load)
        dfs = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_url = {executor.submit(self._fetch_url, url, tag): tag for tag, url in urls_map.items()}
            for future in concurrent.futures.as_completed(future_to_url):
                tag, df = future.result()
                dfs[tag] = df

        # 3. Process Base Stats
        if dfs["Base"].empty:
            print("CRITICAL: Base stats failed to load. Aborting.")
            return pd.DataFrame()

        main_df = dfs["Base"]
        
        columns_to_keep = [
            'PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION', 'AGE', 'GP', 'MIN', 'TEAM_ID',
            'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'FG3M', 
            'OREB', 'DREB', 'DD2', 'TD3',
            'FGM', 'FGA', 'FG3A', 'FTM', 'FTA', 
            'PF', 'PFD', 'PLUS_MINUS',
            'FG_PCT', 'FG3_PCT', 'FT_PCT'
        ]
        
        main_df = main_df[[c for c in columns_to_keep if c in main_df.columns]]

        # 4. Merge Advanced Stats
        merge_keys = ['PLAYER_ID', 'TEAM_ID']

        if not dfs.get("Passing", pd.DataFrame()).empty:
            pass_cols = merge_keys + ['POTENTIAL_AST', 'PASSES_MADE']
            main_df = main_df.merge(dfs["Passing"][pass_cols], on=merge_keys, how='left')

        if not dfs.get("Drives", pd.DataFrame()).empty:
            drive_cols = merge_keys + ['DRIVES', 'DRIVE_PTS']
            main_df = main_df.merge(dfs["Drives"][drive_cols], on=merge_keys, how='left')

        if not dfs.get("Rebounding", pd.DataFrame()).empty:
            reb_cols = merge_keys + ['REB_CHANCES', 'REB_CONTEST_PCT']
            main_df = main_df.merge(dfs["Rebounding"][reb_cols], on=merge_keys, how='left')

        # 5. Calculate Edge Metrics
        main_df = main_df.fillna(0)

        main_df['AST_CONVERSION_PCT'] = main_df.apply(
            lambda x: round(x['AST'] / x['POTENTIAL_AST'], 3) if x['POTENTIAL_AST'] > 0 else 0, axis=1
        )
        main_df['REB_HUSTLE_PCT'] = main_df.apply(
            lambda x: round(x['REB'] / x['REB_CHANCES'], 3) if x['REB_CHANCES'] > 0 else 0, axis=1
        )
        main_df['AGGRESSION_SCORE'] = main_df['FGA'] + main_df['FTA'] + main_df['DRIVES']

        print(f"Data Fetch & Processing Complete: {time.time() - start_time:.2f} seconds")
        return main_df

if __name__ == "__main__":
    engine = NBAStatsEngine()
    final_stats = engine.get_player_data()
    
    if not final_stats.empty:
        print(f"Total Players Processed: {len(final_stats)}")
        final_stats.to_csv("stats.csv", index=False)
    else:
        print("Failed to retrieve data.")