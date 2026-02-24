import requests
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def fetch_defensive_shot_locations(season="2024-25"):
    """Fetches Opponent Field Goals Made by Zone for all 30 teams."""
    url = "https://stats.nba.com/stats/leaguedashteamshotlocations"
    
    params = {
        "DistanceRange": "By Zone", "LastNGames": "0", "LeagueID": "00", "MeasureType": "Opponent", 
        "Month": "0", "OpponentTeamID": "0", "PaceAdjust": "N", "PerMode": "Totals", 
        "Period": "0", "PlusMinus": "N", "Rank": "N", "Season": "2024-25",
        "SeasonType": "Regular Season", "TeamID": "0",
    }
    
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://www.nba.com",
        "referer": "https://www.nba.com/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    }

    session = requests.Session()
    retry = Retry(connect=3, read=3, redirect=3, backoff_factor=1, status_forcelist=[403, 429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)

    try:
        response = session.get(url, params=params, headers=headers, timeout=45)
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
        
        all_cols = list(cols[:2]) + zone_cols
        results = {}
        for row in data["rowSet"]:
            team_name = row[1]
            
            # Extract assist zones allowed by the defense mapping to NBA Stats columns
            rim = row[2]  # RA_FGM
            mid = row[5] + row[8]  # PAINT_FGM (non-RA) + MID_FGM
            lc3 = row[11] # LC3_FGM
            rc3 = row[14] # RC3_FGM
            ab3 = row[17] # AB3_FGM
            
            total = rim + mid + lc3 + rc3 + ab3
            if total == 0:
                continue
                
            # Distribute percentages smartly exactly to 100%
            distribution = {
                'restricted_area': rim / total,
                'mid_range': mid / total,
                'left_corner': lc3 / total,
                'right_corner': rc3 / total,
                'top_key': ab3 / total
            }
            
            makes = {
                'restricted_area': rim,
                'mid_range': mid,
                'left_corner': lc3,
                'right_corner': rc3,
                'top_key': ab3
            }
            
            raw_pcts = {zone: val * 100 for zone, val in distribution.items()}
            int_pcts = {zone: int(val) for zone, val in raw_pcts.items()}
            remainders = {zone: val - int(val) for zone, val in raw_pcts.items()}
            
            shortfall = 100 - sum(int_pcts.values())
            sorted_zones_by_remainder = sorted(remainders.keys(), key=lambda k: remainders[k], reverse=True)
            
            for i in range(int(shortfall)):
                zone_to_bump = sorted_zones_by_remainder[i]
                int_pcts[zone_to_bump] += 1
                
            results[team_name] = {
                "makes": makes,
                "int_pcts": int_pcts
            }
            
        return results
        
    except Exception as e:
        print(f"Error fetching Opponent NBA Stats: {e}")
        return {}

def convert_to_zones(team_data):
    """
    Computes rankings and normalizes output to include rank, percentage, makes.
    Also duplicates the data across "ALL", "G", "F", "C" to mimic positional support.
    """
    if not team_data:
        return {}

    # Gather makes for ranking
    zones = ['restricted_area', 'mid_range', 'left_corner', 'right_corner', 'top_key']
    ranks = {z: {} for z in zones}
    
    # Calculate ranks (1 = fewest makes allowed, 30 = most makes allowed)
    for z in zones:
        sorted_teams = sorted(team_data.keys(), key=lambda t: team_data[t]['makes'][z])
        # Rank them
        for i, t in enumerate(sorted_teams):
            ranks[z][t] = i + 1

    final_results = {}
    for team, data in team_data.items():
        team_zone_data = {
            zone: {
                "percentage": f"{data['int_pcts'][zone]}%",
                "makes": f"{data['makes'][zone]:.1f}",
                "rank": str(ranks[zone][team])
            }
            for zone in zones
        }
        
        # PBPStats doesn't support positioning for the multi_row total, so we mirror 'ALL' to positional keys
        # so aggregator can store ast_def_zones_positional properly
        final_results[team] = {
            "ALL": team_zone_data,
            "G": team_zone_data,
            "F": team_zone_data,
            "C": team_zone_data
        }
        
    return final_results

# Standardizing city names to match dashboard expectations
# NBA Api Returns "Los Angeles Lakers" instead of LAL, already full name
# Wait, let's verify if NBA api returns "Los Angeles Lakers" or "L.A. Lakers".
# Yes, "Los Angeles Lakers", "LA Clippers"

def get_opp_assist_zones_data():
    raw_data = fetch_defensive_shot_locations()
    zones_data = convert_to_zones(raw_data)
        
    return zones_data

def main():
    print("Fetching all opponent assist zones in a single request (via NBA Stats)...")
    data = get_opp_assist_zones_data()
    print(f"Fetched defensive assist zones for {len(data)} teams.\n")
    
    # Testing exactly for the LA Lakers
    test_team = "Detroit Pistons"
    if test_team in data:
        print(f"--- {test_team} (Assists Allowed) ---")
        print(json.dumps(data[test_team].get("ALL", {}), indent=2))
    else:
        print(f"{test_team} not found in data.")

if __name__ == "__main__":
    main()
