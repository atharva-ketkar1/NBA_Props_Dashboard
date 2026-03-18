import requests
import pandas as pd
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------- HEADERS & SESSION ---------------- #
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
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

def create_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

SESSION = create_session()

def fetch_dataframe(url, params):
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])

# =========================================================
# OVERALL TEAM DEFENSIVE RANKINGS
# =========================================================
def fetch_opp_shot_stats(measure_type):
    url = "https://stats.nba.com/stats/leaguedashoppptshot"

    params = {
        "Conference": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "GameSegment": "",
        "GeneralRange": measure_type, 
        "LastNGames": "0",
        "LeagueID": "00",
        "Location": "",
        "Month": "0",
        "OpponentTeamID": "0",
        "Outcome": "",
        "PORound": "0",
        "PerMode": "PerGame",         
        "Period": "0",
        "PlayerExperience": "",
        "PlayerPosition": "",         # Empty string for overall team defense
        "Season": "2025-26",          # Using current active season
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "TeamID": "0",
        "VsConference": "",
        "VsDivision": ""
    }
    return fetch_dataframe(url, params)

def process_defensive_rankings():
    # Map the API query string to your exact SSOT JSON keys
    types = {
        "Catch and Shoot": "catchAndShoot",
        "Pullups": "pullups",
        "Less Than 10 ft": "lessThanTenFeet"
    }

    teams = {}

    for api_type, type_key in types.items():
        try:
            print(f"Fetching Opponent Defense for: {api_type}...")
            df = fetch_opp_shot_stats(api_type)
        except Exception as e:
            print(f"Error fetching {api_type}: {e}")
            continue

        if df.empty:
            continue

        # Step 1: Calculate Points Allowed Per Game (FG2M * 2) + (FG3M * 3)
        df["PTS_ALLOWED"] = (df["FG2M"] * 2) + (df["FG3M"] * 3)

        # Step 2: Rank teams (ascending=True means Rank 1 is the Lowest Points Allowed / Best Defense)
        df["RANK"] = df["PTS_ALLOWED"].rank(ascending=True, method="min").astype(int)

        # Step 3: Populate the dictionary
        for _, row in df.iterrows():
            team_name = row["TEAM_NAME"]
            
            # Initialize team if it hasn't been added yet
            if team_name not in teams:
                teams[team_name] = {"rankings": {}}
            
            # Inject the calculated rank
            teams[team_name]["rankings"][type_key] = int(row["RANK"])

    return teams

if __name__ == "__main__":
    defensive_data = process_defensive_rankings()

    print(f"Teams mapped: {len(defensive_data)}")
    
    # Save the artifact so aggregator.py can consume it
    with open("opponent_defensive_ranks.json", "w") as f:
        json.dump(defensive_data, f, indent=4)
        print("Successfully saved rankings to opponent_defensive_ranks.json!")