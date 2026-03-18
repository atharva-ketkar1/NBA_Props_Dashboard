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

# The Exact Play Types as defined by Synergy / NBA API
PLAY_TYPES = {
    "transition": "Transition",
    "isolation": "Isolation",
    "pickAndRollBallHandler": "PRBallHandler",
    "pickAndRollRollMan": "PRRollman",
    "postUp": "Postup",
    "spotUp": "Spotup",
    "handoff": "Handoff",
    "cut": "Cut",
    "offScreen": "OffScreen",
    "putback": "OffRebound",    
    "misc": "Misc"
}

# ==========================================
# API FETCH FUNCTIONS
# ==========================================
def fetch_synergy_data(play_type, grouping="offensive", is_team="P"):
    url = "https://stats.nba.com/stats/synergyplaytypes"
    params = {
        "LeagueID": "00",
        "PerMode": "PerGame",       
        "PlayType": play_type,
        "PlayerOrTeam": is_team,    
        "SeasonType": "Regular Season",
        "SeasonYear": "2025-26",    
        "TypeGrouping": grouping    
    }
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])

def fetch_traditional_player_stats():
    # Needed specifically to get Player Offensive Free Throws (FTM)
    url = "https://stats.nba.com/stats/leaguedashplayerstats"
    params = {
        "College": "", "Conference": "", "Country": "", "DateFrom": "", "DateTo": "",
        "Division": "", "DraftPick": "", "DraftYear": "", "GameScope": "", "GameSegment": "",
        "Height": "", "LastNGames": "0", "LeagueID": "00", "Location": "", 
        "MeasureType": "Base", 
        "Month": "0", "OpponentTeamID": "0", "Outcome": "", "PORound": "0", "PaceAdjust": "N",
        "PerMode": "PerGame", "Period": "0", "PlayerExperience": "", "PlayerPosition": "",
        "PlusMinus": "N", "Rank": "N",
        "Season": "2025-26", "SeasonSegment": "", "SeasonType": "Regular Season", 
        "ShotClockRange": "", "StarterBench": "", "TeamID": "0", "TwoWay": "0", 
        "VsConference": "", "VsDivision": "", "Weight": ""
    }
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=20)
    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])

def fetch_traditional_team_stats():
    # Needed specifically to get Defensive Free Throw rankings (OPP_FTM)
    url = "https://stats.nba.com/stats/leaguedashteamstats"
    params = {
        "Conference": "", "DateFrom": "", "DateTo": "", "Division": "", "GameScope": "",
        "GameSegment": "", "LastNGames": "0", "LeagueID": "00", "Location": "",
        "MeasureType": "Opponent", # Team Defense uses Opponent
        "Month": "0", "OpponentTeamID": "0", "Outcome": "",
        "PORound": "0", "PaceAdjust": "N", "PerMode": "PerGame", "Period": "0",
        "PlayerExperience": "", "PlayerPosition": "", "PlusMinus": "N", "Rank": "N",
        "Season": "2025-26", "SeasonSegment": "", "SeasonType": "Regular Season",
        "ShotClockRange": "", "StarterBench": "", "TeamID": "0", "TwoWay": "0",
        "VsConference": "", "VsDivision": ""
    }
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=20)
    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])

def fetch_misc_team_stats():
    # Needed to get Opponent 2nd Chance Points (Proxy for Defensive Putbacks)
    url = "https://stats.nba.com/stats/leaguedashteamstats"
    params = {
        "Conference": "", "DateFrom": "", "DateTo": "", "Division": "", "GameScope": "",
        "GameSegment": "", "LastNGames": "0", "LeagueID": "00", "Location": "",
        "MeasureType": "Misc", # "Misc" gives us OPP_PTS_2ND_CHANCE
        "Month": "0", "OpponentTeamID": "0", "Outcome": "",
        "PORound": "0", "PaceAdjust": "N", "PerMode": "PerGame", "Period": "0",
        "PlayerExperience": "", "PlayerPosition": "", "PlusMinus": "N", "Rank": "N",
        "Season": "2025-26", "SeasonSegment": "", "SeasonType": "Regular Season",
        "ShotClockRange": "", "StarterBench": "", "TeamID": "0", "TwoWay": "0",
        "VsConference": "", "VsDivision": ""
    }
    r = SESSION.get(url, headers=HEADERS, params=params, timeout=20)
    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])

# ==========================================
# 1. PLAYER OFFENSIVE PLAY TYPES
# ==========================================
def process_player_play_types():
    players = {}
    
    for json_key, api_val in PLAY_TYPES.items():
        print(f"Fetching Player Offense: {api_val}...")
        try:
            df = fetch_synergy_data(api_val, grouping="offensive", is_team="P")
        except Exception as e:
            print(f"Error fetching {api_val}: {e}")
            continue
            
        if df.empty: continue
            
        for _, row in df.iterrows():
            name = row["PLAYER_NAME"]
            pts_per_game = round(float(row["PTS"]), 1)
            
            if name not in players:
                players[name] = {"points": {}}
                
            players[name]["points"][json_key] = {
                "perMatch": pts_per_game,
            }
            
    # ------ FREE THROWS (PLAYER OFFENSE) ------
    print("Fetching Player Offensive Free Throws...")
    try:
        trad_player_df = fetch_traditional_player_stats()
        for _, row in trad_player_df.iterrows():
            name = row["PLAYER_NAME"]
            ftm_per_game = round(float(row["FTM"]), 1)
            
            # Only add free throws to players we already mapped via Synergy
            if name in players:
                players[name]["points"]["freeThrows"] = {
                    "perMatch": ftm_per_game
                }
    except Exception as e:
        print(f"Error fetching player free throws: {e}")

    return players

# ==========================================
# 2. TEAM DEFENSIVE PLAY TYPE RANKINGS
# ==========================================
def process_team_defensive_play_types():
    teams = {}
    
    for json_key, api_val in PLAY_TYPES.items():
        # CRUCIAL FIX: Skip Synergy Putback for defense (handled via Misc stats proxy)
        if api_val == "Putback":
            continue 
            
        print(f"Fetching Team Defense: {api_val}...")
        try:
            df = fetch_synergy_data(api_val, grouping="defensive", is_team="T")
        except Exception as e:
            print(f"Error fetching {api_val}: {e}")
            continue
            
        if df.empty: continue
            
        # Rank by Points Allowed (PTS). Ascending=True -> Rank 1 = Lowest Points Allowed (Toughest Matchup)
        df["PTS"] = pd.to_numeric(df["PTS"])
        df["PPP"] = pd.to_numeric(df["PPP"])
        
        # Strategy 2: Sort by PTS (Primary) and PPP (Secondary tiebreaker)
        df = df.sort_values(by=["PTS", "PPP"], ascending=[True, True]).reset_index(drop=True)
        
        # Assign strict 1-30 ranks based on this flawless mathematical order
        df["RANK"] = df.index + 1
        
        for _, row in df.iterrows():
            team_name = row["TEAM_NAME"]
            if team_name not in teams:
                teams[team_name] = {"rankings": {}}
            
            teams[team_name]["rankings"][json_key] = int(row["RANK"])
            
    # ------ PUTBACKS (PROXY VIA 2ND CHANCE POINTS) ------
    print("Fetching Defensive Putbacks (via 2nd Chance Points)...")
    try:
        misc_df = fetch_misc_team_stats()
        # Rank by Opponent 2nd Chance Points (Ascending=True -> Rank 1 = Lowest Allowed)
        misc_df["PUTBACK_RANK"] = misc_df["OPP_PTS_2ND_CHANCE"].rank(ascending=True, method="first").astype(int)
        
        for _, row in misc_df.iterrows():
            team_name = row["TEAM_NAME"]
            if team_name in teams:
                teams[team_name]["rankings"]["putback"] = int(row["PUTBACK_RANK"])
    except Exception as e:
        print(f"Error fetching defensive putbacks: {e}")

    # ------ FREE THROWS (DEFENSIVE RANKINGS) ------
    print("Fetching Defensive Free Throws...")
    try:
        trad_df = fetch_traditional_team_stats()
        trad_df["FT_RANK"] = trad_df["OPP_FTM"].rank(ascending=True, method="first").astype(int)
        
        for _, row in trad_df.iterrows():
            team_name = row["TEAM_NAME"]
            if team_name in teams:
                teams[team_name]["rankings"]["freeThrows"] = int(row["FT_RANK"])
    except Exception as e:
        print(f"Error fetching defensive FTs: {e}")

    return teams

# ==========================================
# MAIN ENTRY
# ==========================================
def get_play_type_data():
    return {
        "players": process_player_play_types(),
        "teams": process_team_defensive_play_types()
    }

if __name__ == "__main__":
    payload = get_play_type_data()
    
    with open("play_type_analysis.json", "w") as f:
        json.dump(payload, f, indent=4)
        print("Successfully generated play_type_analysis.json!")