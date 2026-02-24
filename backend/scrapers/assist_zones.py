import pandas as pd
import requests
import os
import time
import random
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

STATS_FILE = os.path.join(os.path.dirname(__file__), '../data/current/season_stats.csv')

# ---------- SESSION FACTORY ----------
def create_session():
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.nba.com",
        "referer": "https://www.nba.com/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    }

    session = requests.Session()
    session.headers.update(headers)

    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[403, 429, 500, 502, 503, 504],
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)

    return session

# ---------- TEAM IDS ----------
def get_all_team_ids():
    if not os.path.exists(STATS_FILE):
        return []
    df = pd.read_csv(STATS_FILE)
    return df['TEAM_ID'].dropna().unique().tolist()

# ---------- NORMALIZE NAME ----------
def normalize_name(name_str):
    """Converts 'Last, First' to 'First Last' safely"""
    if not isinstance(name_str, str) or ',' not in name_str:
        return name_str
    parts = name_str.split(',')
    if len(parts) == 2:
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name_str

# ---------- FETCH RECEIVER PROFILES ----------
def fetch_receiver_shot_profiles(session):
    print("Fetching league shot locations to build receiver profiles...")
    url = "https://stats.nba.com/stats/leaguedashplayershotlocations"
    params = {
        "DistanceRange": "By Zone", "LastNGames": "0", "LeagueID": "00", "MeasureType": "Base", 
        "Month": "0", "OpponentTeamID": "0", "PaceAdjust": "N", "PerMode": "Totals", 
        "Period": "0", "PlusMinus": "N", "Rank": "N", "Season": "2024-25",
        "SeasonType": "Regular Season", "TeamID": "0",
    }
    
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()["resultSets"]
    
    cols = data["headers"][1]["columnNames"]
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
    
    df = pd.DataFrame(data["rowSet"])
    df = df.fillna(0)
    # ensure we have the correct columns before padding
    if len(df.columns) > len(cols[:6]) + len(zone_cols):
        df = df.iloc[:, :(len(cols[:6]) + len(zone_cols))]
        
    df.columns = list(cols[:6]) + zone_cols[:len(df.columns) - 6]
    
    profiles = {}
    for _, row in df.iterrows():
        pid = row.get("PLAYER_ID")
        if not pid: continue
            
        ra = row.get("RA_FGM", 0)
        paint = row.get("PAINT_FGM", 0)
        mid = row.get("MID_FGM", 0)
        lc3 = row.get("LC3_FGM", 0)
        rc3 = row.get("RC3_FGM", 0)
        ab3 = row.get("AB3_FGM", 0)
        
        total_2pt = ra + paint + mid
        total_3pt = lc3 + rc3 + ab3
        
        profiles[pid] = {
            "2pt": {
                "RA": ra / total_2pt if total_2pt else 0,
                "MID": (paint + mid) / total_2pt if total_2pt else 0
            },
            "3pt": {
                "LC3": lc3 / total_3pt if total_3pt else 0,
                "RC3": rc3 / total_3pt if total_3pt else 0,
                "AB3": ab3 / total_3pt if total_3pt else 0
            }
        }
    return profiles

# ---------- MAIN PIPELINE ----------
def get_assist_zones_data():
    session = create_session()
    
    # 1. Get exact shot profiles for all potential receivers
    receiver_profiles = fetch_receiver_shot_profiles(session)
    
    # 2. Iterate teams and fetch passing data
    team_ids = get_all_team_ids()
    if not team_ids:
        print("No teams found. Run season stats first.")
        return {}

    all_player_stats = {}
    print(f"Fetching passing networks for {len(team_ids)} teams...")

    for i, tid in enumerate(team_ids):
        url = "https://stats.nba.com/stats/teamdashptpass"
        params = {
            "DateFrom": "", "DateTo": "", "GameSegment": "", "LastNGames": "0",
            "LeagueID": "00", "Location": "", "Month": "0", "OpponentTeamID": "0",
            "Outcome": "", "PerMode": "Totals", "Period": "0", "TeamID": str(int(tid)),
            "Season": "2024-25", "SeasonSegment": "", "SeasonType": "Regular Season",
            "VsConference": "", "VsDivision": ""
        }

        try:
            response = session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()["resultSets"][0]
            
            headers = data["headers"]
            pass_from_idx = headers.index("PASS_FROM")
            teammate_id_idx = headers.index("PASS_TEAMMATE_PLAYER_ID")
            fg2m_idx = headers.index("FG2M")
            fg3m_idx = headers.index("FG3M")

            for row in data["rowSet"]:
                raw_name = row[pass_from_idx]
                assister_name = normalize_name(raw_name)
                receiver_id = row[teammate_id_idx]
                fg2m = row[fg2m_idx]
                fg3m = row[fg3m_idx]

                if assister_name not in all_player_stats:
                    all_player_stats[assister_name] = {
                        'Rim': 0.0, 'Mid': 0.0, 'Corner3': 0.0, 'Arc3': 0.0, 
                        'RightCorner3': 0.0, 'Total': 0.0, 'RawMakes': {}
                    }
                
                stats = all_player_stats[assister_name]
                
                # Approximate where the assists happened based on receiver's profile
                if receiver_id in receiver_profiles:
                    prof = receiver_profiles[receiver_id]
                    
                    # 2PT Assists
                    stats['Rim'] += fg2m * prof["2pt"]["RA"]
                    stats['Mid'] += fg2m * prof["2pt"]["MID"]
                    
                    # 3PT Assists (Separate corners to combine later or match old script)
                    # Old script had: Rim, Mid, Corner3, Arc3. Wait, let's keep it close to old script.
                    # Old script mapped Corner3 to combined corners in aggregator. 
                    stats['Corner3'] += fg3m * prof["3pt"]["LC3"]
                    stats['RightCorner3'] += fg3m * prof["3pt"]["RC3"]
                    stats['Arc3'] += fg3m * prof["3pt"]["AB3"]
                    stats['Total'] += (fg2m + fg3m)

        except Exception as e:
            print(f"Team {tid} error: {e}")

        # --- OPTIMAL pacing to avoid bans ---
        time.sleep(random.uniform(1.0, 1.5))
        if (i + 1) % 8 == 0:
            time.sleep(random.uniform(3.5, 5.5))

    return format_for_aggregator(all_player_stats)

def format_for_aggregator(raw_stats):
    """Format matching the structure expected by AssistZones.tsx"""
    formatted = {}
    for player, s in raw_stats.items():
        if s['Total'] == 0: continue
        
        # Calculate actual sum of mapped zones (may differ slightly from Total due to missing profiles)
        zone_sum = s['Rim'] + s['Mid'] + s['Corner3'] + s['RightCorner3'] + s['Arc3']
        if zone_sum <= 0: continue
        
        # Calculate raw percentages
        dist = {
            'restricted_area': s['Rim'] / zone_sum,
            'mid_range': s['Mid'] / zone_sum,
            'left_corner': s['Corner3'] / zone_sum,
            'right_corner': s['RightCorner3'] / zone_sum,
            'top_key': s['Arc3'] / zone_sum
        }
        
        raw_pcts = {k: v * 100 for k, v in dist.items()}
        int_pcts = {k: int(v) for k, v in raw_pcts.items()}
        remainders = {k: v - int(v) for k, v in raw_pcts.items()}
        
        shortfall = 100 - sum(int_pcts.values())
        sorted_zones = sorted(remainders.keys(), key=lambda k: remainders[k], reverse=True)
        
        # Distribute the shortfall points
        for i in range(int(shortfall)):
            int_pcts[sorted_zones[i % len(sorted_zones)]] += 1
            
        makes = {
            'restricted_area': s['Rim'],
            'mid_range': s['Mid'],
            'left_corner': s['Corner3'],
            'right_corner': s['RightCorner3'],
            'top_key': s['Arc3']
        }
        
        formatted[player] = {
            zone: {
                "percentage": f"{int_pcts[zone]}%",
                "makes": f"{makes[zone]:.1f}"
            }
            for zone in dist.keys()
        }
    return formatted

# ---------- RUN ----------
if __name__ == "__main__":
    print("Fetching assist zones (Analytical Approximation via NBA Stats)...")
    data = get_assist_zones_data()
    print(f"Players collected: {len(data)}")
    
    # Validation test
    if "Victor Wembanyama" in data:
        print(f"--- Victor Wembanyama ---")
        import json
        print(json.dumps(data["Victor Wembanyama"], indent=2))