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
# 🧠 Compute Shot Distribution for One Player
# ---------------------------------------------------------
def get_player_zone_distribution(df, player_name):

    player = df[df["PLAYER_NAME"] == player_name]

    if player.empty:
        raise ValueError(f"Player '{player_name}' not found")

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

    player.columns = list(player.columns[:6]) + zone_cols
    p = player.iloc[0]

    # Total attempts (exclude duplicate aggregate corner-3 column)
    total_fga = (
        p["RA_FGA"] +
        p["PAINT_FGA"] +
        p["MID_FGA"] +
        p["LC3_FGA"] +
        p["RC3_FGA"] +
        p["AB3_FGA"]
    )

    distribution = {
        "Restricted Area": p["RA_FGA"] / total_fga,
        "Paint": p["PAINT_FGA"] / total_fga,
        "Midrange": p["MID_FGA"] / total_fga,
        "Left Corner 3": p["LC3_FGA"] / total_fga,
        "Right Corner 3": p["RC3_FGA"] / total_fga,
        "Above Break 3": p["AB3_FGA"] / total_fga,
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

    return int_pcts

# ---------------------------------------------------------
# 🚀 MAIN
# ---------------------------------------------------------
def main():

    df = fetch_shot_locations()

    player_name = "Jalen Brunson"

    dist = get_player_zone_distribution(df, player_name)

    print(f"\nShot Distribution for {player_name} (2025-26):\n")

    for zone, pct in dist.items():
        print(f"{zone:<18} : {pct:>5}%")



if __name__ == "__main__":
    main()