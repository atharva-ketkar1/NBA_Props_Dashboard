import requests
import pandas as pd
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

def create_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

def fetch_pt_stats(measure_type, player_or_team="Player"):
    session = create_session()
    url = "https://stats.nba.com/stats/leaguedashptstats"
    params = {
        "College": "", "Conference": "", "Country": "", "DraftPick": "", "DraftYear": "",
        "GameScope": "", "Height": "", "LastNGames": "0", "LeagueID": "00", "Location": "",
        "Month": "0", "OpponentTeamID": "0", "Outcome": "", "PORound": "0", "PerMode": "Totals",
        "PlayerExperience": "", "PlayerOrTeam": player_or_team, "PlayerPosition": "",
        "PtMeasureType": measure_type, "Season": "2025-26", "SeasonSegment": "",
        "SeasonType": "Regular Season", "StarterBench": "", "TeamID": "0", "VsConference": "",
        "VsDivision": "", "Weight": ""
    }
    
    response = session.get(url, headers=HEADERS, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    
    headers = data["resultSets"][0]["headers"]
    row_set = data["resultSets"][0]["rowSet"]
    return pd.DataFrame(row_set, columns=headers)

def fetch_pt_defend(defense_category="Less Than 10Ft"):
    session = create_session()
    url = "https://stats.nba.com/stats/leaguedashptdefend"
    params = {
        "DefenseCategory": defense_category,
        "LeagueID": "00",
        "PerMode": "Totals",
        "Season": "2025-26",
        "SeasonType": "Regular Season"
    }
    
    response = session.get(url, headers=HEADERS, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    
    headers = data["resultSets"][0]["headers"]
    row_set = data["resultSets"][0]["rowSet"]
    return pd.DataFrame(row_set, columns=headers)

def process_players():
    types = {
        "CatchShoot": "catch_and_shoot",
        "PullUpShot": "pull_up"
    }
    players_data = {}
    
    for api_type, internal_key in types.items():
        try:
            df = fetch_pt_stats(api_type, player_or_team="Player")
            if df.empty: continue
                
            pts_col = [col for col in df.columns if col.endswith('_PTS')][0]
            
            for _, row in df.iterrows():
                player = row["PLAYER_NAME"]
                gp = row["GP"]
                if gp == 0: continue
                    
                pts_pg = row[pts_col] / gp
                
                if player not in players_data:
                    players_data[player] = {}
                    
                players_data[player][internal_key] = {"points": round(pts_pg, 1)}
        except Exception as e:
            print(f"Error processing player {api_type}: {e}")
            
    return players_data

def process_teams():
    teams_data = {}
    
    # 1. Map Perimeter Defense (Catch & Shoot, Pull Up) using leaguedashoppptshot
    types_opp = {
        "Catch and Shoot": "catch_and_shoot",
        "Pull Up Shots": "pull_up"
    }
    for api_type, internal_key in types_opp.items():
        try:
            session = create_session()
            url = "https://stats.nba.com/stats/leaguedashoppptshot"
            params = {
                "LeagueID": "00", "PerMode": "Totals", "PtMeasureType": api_type,
                "Season": "2025-26", "SeasonType": "Regular Season"
            }
            response = session.get(url, headers=HEADERS, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            headers = data["resultSets"][0]["headers"]
            row_set = data["resultSets"][0]["rowSet"]
            df = pd.DataFrame(row_set, columns=headers)
            
            # Rank teams by lowest Field Goals Made allowed (1 is best, 30 is worst)
            df['RANK'] = df['FGM'].rank(method='min', ascending=True)
            
            for _, row in df.iterrows():
                team = row["TEAM_NAME"]
                if team not in teams_data:
                    teams_data[team] = {}
                teams_data[team][internal_key] = {"rank": int(row['RANK'])}
        except Exception as e:
            print(f"Error processing team opp {api_type}: {e}")
            
    # 2. Map Interior Defense (<10ft) using leaguedashptdefend
    try:
        df_lt10 = fetch_pt_defend()
        if not df_lt10.empty:
            # The API returns 'FGM' for the defensive allowed metrics as well
            df_lt10['RANK'] = df_lt10['FGM'].rank(method='min', ascending=True)
            
            for _, row in df_lt10.iterrows():
                team = row["TEAM_NAME"]
                if team not in teams_data:
                    teams_data[team] = {}
                teams_data[team]["less_than_10_ft"] = {"rank": int(row['RANK'])}
    except Exception as e:
        print(f"Error processing team opp less_than_10_ft: {e}")

    return teams_data

def get_shot_type_data():
    return {
        "players": process_players(),
        "teams": process_teams()
    }

if __name__ == "__main__":
    import json
    data = get_shot_type_data()
    print("Players mapped:", len(data["players"]))
    print("Teams mapped:", len(data["teams"]))
    
    # Save a quick JSON file to verify your results visually
    with open("shot_type_analysis.json", "w") as f:
        json.dump(data, f, indent=4)