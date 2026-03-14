import os
import json
import requests
import pandas as pd
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# ENV & SESSION SETUP
# ==========================================
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path=_env_path)

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
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

MAX_WORKERS = 4  # Safe ceiling for stats.nba.com — don't go higher

def create_session():
    session = requests.Session()
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

SESSION = create_session()

# ==========================================
# PLAY TYPE DEFINITIONS
# ==========================================
PLAY_TYPES = {
    "transition":             "Transition",
    "isolation":              "Isolation",
    "pickAndRollBallHandler": "PRBallHandler",
    "pickAndRollRollMan":     "PRRollman",
    "postUp":                 "Postup",
    "spotUp":                 "Spotup",
    "handoff":                "Handoff",
    "cut":                    "Cut",
    "offScreen":              "OffScreen",
    "putback":                "OffRebound",
    "misc":                   "Misc"
}

# ==========================================
# SHARED PROXY-AWARE FETCH HELPER
# ==========================================
def _proxied_get(url, params, timeout=30):
    """
    Builds the full target URL, then routes through the Cloudflare Worker
    relay proxy (GET proxy_url?url=<target>) if PBPSTATS_PROXY_URL is set.
    Falls back to a direct request if no proxy is configured.
    """
    full_url = requests.Request('GET', url, params=params).prepare().url
    proxy_url = os.getenv("PBPSTATS_PROXY_URL")

    if proxy_url:
        r = SESSION.get(proxy_url, params={"url": full_url}, headers=HEADERS, timeout=timeout)
    else:
        r = SESSION.get(full_url, headers=HEADERS, timeout=timeout)

    r.raise_for_status()
    data = r.json()["resultSets"][0]
    return pd.DataFrame(data["rowSet"], columns=data["headers"])


# ==========================================
# API FETCH FUNCTIONS
# ==========================================
def fetch_synergy_data(play_type, grouping="offensive", is_team="P"):
    return _proxied_get(
        "https://stats.nba.com/stats/synergyplaytypes",
        {
            "LeagueID":      "00",
            "PerMode":       "PerGame",
            "PlayType":      play_type,
            "PlayerOrTeam":  is_team,
            "SeasonType":    "Regular Season",
            "SeasonYear":    "2025-26",
            "TypeGrouping":  grouping,
        }
    )

def fetch_traditional_player_stats():
    """Fetches per-game base stats — used to get player FTM."""
    return _proxied_get(
        "https://stats.nba.com/stats/leaguedashplayerstats",
        {
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
    )

def fetch_traditional_team_stats():
    """Fetches opponent team stats — used to get defensive FTM rankings."""
    return _proxied_get(
        "https://stats.nba.com/stats/leaguedashteamstats",
        {
            "Conference": "", "DateFrom": "", "DateTo": "", "Division": "", "GameScope": "",
            "GameSegment": "", "LastNGames": "0", "LeagueID": "00", "Location": "",
            "MeasureType": "Opponent",
            "Month": "0", "OpponentTeamID": "0", "Outcome": "",
            "PORound": "0", "PaceAdjust": "N", "PerMode": "PerGame", "Period": "0",
            "PlayerExperience": "", "PlayerPosition": "", "PlusMinus": "N", "Rank": "N",
            "Season": "2025-26", "SeasonSegment": "", "SeasonType": "Regular Season",
            "ShotClockRange": "", "StarterBench": "", "TeamID": "0", "TwoWay": "0",
            "VsConference": "", "VsDivision": ""
        }
    )

def fetch_misc_team_stats():
    """Fetches misc team stats — used to get OPP_PTS_2ND_CHANCE for putback proxy."""
    return _proxied_get(
        "https://stats.nba.com/stats/leaguedashteamstats",
        {
            "Conference": "", "DateFrom": "", "DateTo": "", "Division": "", "GameScope": "",
            "GameSegment": "", "LastNGames": "0", "LeagueID": "00", "Location": "",
            "MeasureType": "Misc",
            "Month": "0", "OpponentTeamID": "0", "Outcome": "",
            "PORound": "0", "PaceAdjust": "N", "PerMode": "PerGame", "Period": "0",
            "PlayerExperience": "", "PlayerPosition": "", "PlusMinus": "N", "Rank": "N",
            "Season": "2025-26", "SeasonSegment": "", "SeasonType": "Regular Season",
            "ShotClockRange": "", "StarterBench": "", "TeamID": "0", "TwoWay": "0",
            "VsConference": "", "VsDivision": ""
        }
    )


# ==========================================
# 1. PLAYER OFFENSIVE PLAY TYPES
# ==========================================
def process_player_play_types():
    players = {}

    def fetch_one_player(json_key, api_val):
        print(f"   Fetching Player Offense: {api_val}...")
        df = fetch_synergy_data(api_val, grouping="offensive", is_team="P")
        return json_key, df

    # Parallel fetch across all play types
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_one_player, jk, av): (jk, av)
            for jk, av in PLAY_TYPES.items()
        }
        for future in as_completed(futures):
            jk, av = futures[future]
            try:
                json_key, df = future.result()
                if df.empty:
                    continue
                for _, row in df.iterrows():
                    name = row["PLAYER_NAME"]
                    pts_per_game = round(float(row["PTS"]), 1)
                    if name not in players:
                        players[name] = {"points": {}}
                    players[name]["points"][json_key] = {"perMatch": pts_per_game}
            except Exception as e:
                print(f"   Error fetching Player Offense [{av}]: {e}")

    # Free throws are a single call — no parallelism needed
    print("   Fetching Player Offensive Free Throws...")
    try:
        trad_player_df = fetch_traditional_player_stats()
        for _, row in trad_player_df.iterrows():
            name = row["PLAYER_NAME"]
            if name in players:
                players[name]["points"]["freeThrows"] = {
                    "perMatch": round(float(row["FTM"]), 1)
                }
    except Exception as e:
        print(f"   Error fetching player free throws: {e}")

    return players


# ==========================================
# 2. TEAM DEFENSIVE PLAY TYPE RANKINGS
# ==========================================
def process_team_defensive_play_types():
    teams = {}

    # Putback is handled separately via Misc stats — exclude from Synergy loop
    defensive_play_types = {k: v for k, v in PLAY_TYPES.items() if v != "OffRebound"}

    def fetch_one_team(json_key, api_val):
        print(f"   Fetching Team Defense: {api_val}...")
        df = fetch_synergy_data(api_val, grouping="defensive", is_team="T")
        return json_key, df

    # Parallel fetch across all defensive play types
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_one_team, jk, av): (jk, av)
            for jk, av in defensive_play_types.items()
        }
        for future in as_completed(futures):
            jk, av = futures[future]
            try:
                json_key, df = future.result()
                if df.empty:
                    continue

                df["PTS"] = pd.to_numeric(df["PTS"])
                df["PPP"] = pd.to_numeric(df["PPP"])

                # Sort by PTS (primary) then PPP (tiebreaker) — ascending = best defense = rank 1
                df = df.sort_values(by=["PTS", "PPP"], ascending=[True, True]).reset_index(drop=True)
                df["RANK"] = df.index + 1

                for _, row in df.iterrows():
                    team_name = row["TEAM_NAME"]
                    if team_name not in teams:
                        teams[team_name] = {"rankings": {}}
                    teams[team_name]["rankings"][json_key] = int(row["RANK"])

            except Exception as e:
                print(f"   Error fetching Team Defense [{av}]: {e}")

    # Putbacks — proxy via 2nd Chance Points
    print("   Fetching Defensive Putbacks (via 2nd Chance Points)...")
    try:
        misc_df = fetch_misc_team_stats()
        misc_df["PUTBACK_RANK"] = misc_df["OPP_PTS_2ND_CHANCE"].rank(
            ascending=True, method="first"
        ).astype(int)
        for _, row in misc_df.iterrows():
            team_name = row["TEAM_NAME"]
            if team_name in teams:
                teams[team_name]["rankings"]["putback"] = int(row["PUTBACK_RANK"])
    except Exception as e:
        print(f"   Error fetching defensive putbacks: {e}")

    # Free throws
    print("   Fetching Defensive Free Throws...")
    try:
        trad_df = fetch_traditional_team_stats()
        trad_df["FT_RANK"] = trad_df["OPP_FTM"].rank(ascending=True, method="first").astype(int)
        for _, row in trad_df.iterrows():
            team_name = row["TEAM_NAME"]
            if team_name in teams:
                teams[team_name]["rankings"]["freeThrows"] = int(row["FT_RANK"])
    except Exception as e:
        print(f"   Error fetching defensive FTs: {e}")

    return teams


# ==========================================
# MAIN ENTRY
# ==========================================
def get_play_type_data():
    return {
        "players": process_player_play_types(),
        "teams":   process_team_defensive_play_types(),
    }

if __name__ == "__main__":
    payload = get_play_type_data()
    with open("play_type_analysis.json", "w") as f:
        json.dump(payload, f, indent=4)
        print("Successfully generated play_type_analysis.json!")