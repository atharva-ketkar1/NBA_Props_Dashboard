import requests
import pandas as pd
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from .season_type import resolve_season, resolve_season_type
except ImportError:
    from season_type import resolve_season, resolve_season_type

# ---------------- HEADERS ---------------- #
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

# ---------------- SESSION ---------------- #
def create_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

SESSION = create_session()

# ---------------- GENERIC FETCH ---------------- #
def fetch_dataframe(url, params):
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()

    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])

# =========================================================
# PLAYER OFFENSE — Shot Types Calculation
# =========================================================
def fetch_pt_shot_stats(measure_type):
    url = "https://stats.nba.com/stats/leaguedashplayerptshot"
    season = resolve_season()

    params = {
        "CloseDefDistRange": "",
        "College": "",
        "Conference": "",
        "Country": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "DraftPick": "",
        "DraftYear": "",
        "DribbleRange": "",
        "GameSegment": "",
        "GeneralRange": measure_type,  
        "Height": "",
        "LastNGames": "0",
        "LeagueID": "00",
        "Location": "",
        "Month": "0",
        "OpponentTeamID": "0",
        "Outcome": "",
        "PORound": "0",
        "PerMode": "Totals", 
        "Period": "0",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": resolve_season_type(),
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": "0",
        "TouchTimeRange": "",
        "VsConference": "",
        "VsDivision": "",
        "Weight": ""
    }

    return fetch_dataframe(url, params)

def calculate_shot_type_points(row):
    # Safely calculate points derived specifically from 2s and 3s
    fg2m = int(row.get('FG2M', 0) or 0)
    fg3m = int(row.get('FG3M', 0) or 0)
    return (fg2m * 2) + (fg3m * 3)

# =========================================================
# PROCESS PLAYERS
# =========================================================
def process_players():
    types = {
        "Catch and Shoot": "catchAndShoot",
        "Pullups": "pullups",
        "Less Than 10 ft": "lessThanTenFeet"
    }

    players = {}

    # Step 1: Collect raw points for each category
    for api_type, key in types.items():
        try:
            print(f"Fetching {api_type} data...")
            df = fetch_pt_shot_stats(api_type)
        except Exception as e:
            print(f"Error fetching {api_type}: {e}")
            continue

        if df.empty:
            continue

        for _, r in df.iterrows():
            name = r["PLAYER_NAME"]
            gp = int(r["GP"])
            
            if gp == 0:
                continue

            points = calculate_shot_type_points(r)

            if name not in players:
                players[name] = {
                    "raw_points": {
                        "catchAndShoot": 0, 
                        "pullups": 0, 
                        "lessThanTenFeet": 0
                    },
                    "matchesPlayed": gp
                }
            
            # Inject the calculated points for this specific shot type
            players[name]["raw_points"][key] = points

    # Step 2: Format the final payload (Outputting pure numbers for React to parse)
    final_players = {}
    
    for name, data in players.items():
        raw = data["raw_points"]
        
        final_players[name] = {
            "pointsByShotType": {
                "catchAndShoot": raw["catchAndShoot"],
                "pullups": raw["pullups"],
                "lessThanTenFeet": raw["lessThanTenFeet"],
                "matchesPlayed": data["matchesPlayed"]
            }
        }

    return final_players

# =========================================================
# MAIN ENTRY
# =========================================================
def get_shot_type_data():
    return {
        "players": process_players(),
        "teams": {} # Left empty dynamically since defense was requested to be ignored
    }

if __name__ == "__main__":
    data = get_shot_type_data()

    print("Players mapped:", len(data["players"]))

    with open("shot_type_analysis.json", "w") as f:
        json.dump(data, f, indent=4)
        print("Successfully saved to shot_type_analysis.json!")
