import pandas as pd
import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://stats.nba.com/stats/leaguedashteamshotlocations"

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

# ---- SESSION WITH RETRIES ----
def create_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.5,
                    status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

def fetch_positional_defense(position, season="2025-26"):
    session = create_session()
    
    params = dict(
        DistanceRange='By Zone',
        GameScope='',
        LastNGames=0,
        LeagueID='',
        Location='',
        MeasureType='Opponent',
        Month=0,
        OpponentTeamID=0,
        Outcome='',
        PaceAdjust='N',
        PerMode='PerGame',
        Period=0,
        PlayerPosition=position,
        PlusMinus='N',
        Rank='N',
        Season=season,
        SeasonSegment='',
        SeasonType='Regular Season',
        ShotClockRange='',
        TeamID=0,
        VsConference='',
        VsDivision=''
    )

    response = session.get(URL, headers=HEADERS, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()
    result_set = data["resultSets"]

    columns = result_set["headers"][1]["columnNames"]
    df = pd.DataFrame(result_set["rowSet"], columns=columns)
    
    return df

def convert_to_zones(df):
    results = {}
    if df.empty:
        return results

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
        # Assuming first 5 columns are TEAM_ID, TEAM_NAME etc... 
        df_zones.columns = list(df_zones.columns[:len(df_zones.columns)-len(zone_cols)]) + zone_cols
    except Exception as e:
        print(f"Error renaming columns in opp defense zones: {e}")
        return results

    df_zones['RA_RANK'] = df_zones['RA_FGM'].rank(method='min', ascending=True)
    df_zones['PAINT_RANK'] = df_zones['PAINT_FGM'].rank(method='min', ascending=True)
    df_zones['MID_RANK'] = df_zones['MID_FGM'].rank(method='min', ascending=True)
    df_zones['LC3_RANK'] = df_zones['LC3_FGM'].rank(method='min', ascending=True)
    df_zones['RC3_RANK'] = df_zones['RC3_FGM'].rank(method='min', ascending=True)
    df_zones['AB3_RANK'] = df_zones['AB3_FGM'].rank(method='min', ascending=True)

    for _, row in df_zones.iterrows():
        team_id = str(row["TEAM_ID"])
        team_name = row["TEAM_NAME"]

        total_fga = (
            row["RA_FGA"] + row["PAINT_FGA"] + row["MID_FGA"] +
            row["LC3_FGA"] + row["RC3_FGA"] + row["AB3_FGA"]
        )

        if total_fga == 0:
            continue

        makes = {
            "restricted_area": row["RA_FGM"],
            "paint": row["PAINT_FGM"],
            "mid_range": row["MID_FGM"],
            "left_corner": row["LC3_FGM"],
            "right_corner": row["RC3_FGM"],
            "top_key": row["AB3_FGM"],
        }
        
        ranks = {
            "restricted_area": int(row["RA_RANK"]),
            "paint": int(row["PAINT_RANK"]),
            "mid_range": int(row["MID_RANK"]),
            "left_corner": int(row["LC3_RANK"]),
            "right_corner": int(row["RC3_RANK"]),
            "top_key": int(row["AB3_RANK"]),
        }

        # Calculate raw percentages safely
        percentages = {
            "restricted_area": f"{int((row['RA_FGM'] / row['RA_FGA']) * 100) if row['RA_FGA'] > 0 else 0}%",
            "paint": f"{int((row['PAINT_FGM'] / row['PAINT_FGA']) * 100) if row['PAINT_FGA'] > 0 else 0}%",
            "mid_range": f"{int((row['MID_FGM'] / row['MID_FGA']) * 100) if row['MID_FGA'] > 0 else 0}%",
            "left_corner": f"{int((row['LC3_FGM'] / row['LC3_FGA']) * 100) if row['LC3_FGA'] > 0 else 0}%",
            "right_corner": f"{int((row['RC3_FGM'] / row['RC3_FGA']) * 100) if row['RC3_FGA'] > 0 else 0}%",
            "top_key": f"{int((row['AB3_FGM'] / row['AB3_FGA']) * 100) if row['AB3_FGA'] > 0 else 0}%",
        }

        results[team_name] = {
            zone: {
                "percentage": percentages[zone],
                "rank": str(ranks[zone]),
                "makes": f"{makes[zone]:.1f}"
            }
            for zone in makes.keys()
        }

    return results

def get_opp_def_zones_data():
    full_data = {}
    positions = ['G', 'F', 'C', 'ALL']
    
    for pos in positions:
        try:
            api_pos = '' if pos == 'ALL' else pos
            df = fetch_positional_defense(api_pos)
            zones = convert_to_zones(df)
            
            for team, team_data in zones.items():
                if team not in full_data:
                    full_data[team] = {}
                full_data[team][pos] = team_data
        except Exception as e:
            print(f"Error fetching positional defense for {pos}: {e}")
            
    return full_data

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    data = get_opp_def_zones_data()
    print(f"Fetched positional defense for {len(data)} teams.")
    if "Houston Rockets" in data:
        print("Houston Rockets G:", data["Houston Rockets"].get('G', {}))
        print("Houston Rockets ALL:", data["Houston Rockets"].get('ALL', {}))

if __name__ == "__main__":
    main()