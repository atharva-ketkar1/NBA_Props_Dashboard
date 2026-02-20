import requests
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://stats.nba.com/stats/leaguedashplayershotlocations"

# ---- PROTECTION LAYER: HEADERS ----
HEADERS = {
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

# ---- REQUIRED PARAMETERS ----
PARAMS = {
    "DistanceRange": "By Zone",
    "LastNGames": "0",
    "LeagueID": "00",
    "MeasureType": "Base",
    "Month": "0",
    "OpponentTeamID": "0",
    "PaceAdjust": "N",
    "PerMode": "Totals",
    "Period": "0",
    "PlusMinus": "N",
    "Rank": "N",
    "Season": "2025-26",
    "SeasonType": "Regular Season",
    "TeamID": "0",
}

# ---- SESSION WITH RETRIES ----
def create_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5,
                    status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_shot_locations():
    session = create_session()

    response = session.get(
        URL,
        headers=HEADERS,
        params=PARAMS,
        timeout=15
    )
    response.raise_for_status()

    data = response.json()
    result_set = data["resultSets"]

    columns = result_set["headers"][1]["columnNames"]
    df = pd.DataFrame(result_set["rowSet"], columns=columns)

    return df


# ---------------------------------------------------------
# 🧠 Compute Shot Distribution for All Players
# ---------------------------------------------------------
def get_all_player_zone_distributions(df):
    results = {}
    
    if df.empty:
        return results

    # Rename duplicate zone columns
    zone_cols = [
        "RA_FGM","RA_FGA","RA_PCT",
        "PAINT_FGM","PAINT_FGA","PAINT_PCT",
        "MID_FGM","MID_FGA","MID_PCT",
        "LC3_FGM","LC3_FGA","LC3_PCT",
        "RC3_FGM","RC3_FGA","RC3_PCT",
        "AB3_FGM","AB3_FGA","AB3_PCT",
        "BC_FGM","BC_FGA","BC_PCT",
        "C3_FGM","C3_FGA","C3_PCT",
    ]

    df_zones = df.copy()
    df_zones = df_zones.fillna(0)
    try:
        df_zones.columns = list(df_zones.columns[:6]) + zone_cols
    except Exception as e:
        print(f"Error renaming columns in shooting zones: {e}")
        return results

    for _, p in df_zones.iterrows():
        player_name = p["PLAYER_NAME"]

        # Total attempts (exclude duplicate aggregate corner-3 column)
        total_fga = (
            p["RA_FGA"] +
            p["PAINT_FGA"] +
            p["MID_FGA"] +
            p["LC3_FGA"] +
            p["RC3_FGA"] +
            p["AB3_FGA"]
        )

        if total_fga == 0:
            continue

        distribution = {
            "restricted_area": p["RA_FGA"] / total_fga,
            "paint": p["PAINT_FGA"] / total_fga,
            "mid_range": p["MID_FGA"] / total_fga,
            "left_corner": p["LC3_FGA"] / total_fga,
            "right_corner": p["RC3_FGA"] / total_fga,
            "top_key": p["AB3_FGA"] / total_fga,
        }
        
        makes = {
            "restricted_area": p["RA_FGM"],
            "paint": p["PAINT_FGM"],
            "mid_range": p["MID_FGM"],
            "left_corner": p["LC3_FGM"],
            "right_corner": p["RC3_FGM"],
            "top_key": p["AB3_FGM"],
        }

        # 1. Get raw percentages (e.g., 15.33203125)
        raw_pcts = {zone: val * 100 for zone, val in distribution.items()}
        
        # 2. Separate into integer parts and decimal remainders
        int_pcts = {zone: int(val) for zone, val in raw_pcts.items()}
        remainders = {zone: val - int(val) for zone, val in raw_pcts.items()}
        
        # 3. Calculate how many percentage points we need to reach exactly 100
        shortfall = 100 - sum(int_pcts.values())
        
        # 4. Sort the zones by who had the highest decimal remainder
        sorted_zones_by_remainder = sorted(remainders.keys(), key=lambda k: remainders[k], reverse=True)
        
        # 5. Distribute the missing points to those with the highest remainders
        for i in range(shortfall):
            zone_to_bump = sorted_zones_by_remainder[i]
            int_pcts[zone_to_bump] += 1

        results[player_name] = {
            zone: {
                "percentage": f"{int_pcts[zone]}%",
                "makes": str(int(makes[zone]))
            }
            for zone in int_pcts.keys()
        }

    return results

def get_shooting_zones_data():
    """Entry point for the pipeline"""
    try:
        df = fetch_shot_locations()
        return get_all_player_zone_distributions(df)
    except Exception as e:
        print(f"Error fetching shooting zones: {e}")
        return {}

# ---------------------------------------------------------
# 🚀 MAIN
# ---------------------------------------------------------
def main():
    data = get_shooting_zones_data()
    print(f"Fetched shooting zones for {len(data)} players.")
    if "Jalen Brunson" in data:
        print("Jalen Brunson:", data["Jalen Brunson"])

if __name__ == "__main__":
    main()