import pandas as pd
import requests
import os
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIGURATION ---
STATS_FILE = os.path.join(os.path.dirname(__file__), '../data/current/season_stats.csv')

def get_all_team_ids():
    if not os.path.exists(STATS_FILE):
        return []
    try:
        df = pd.read_csv(STATS_FILE)
        return df['TEAM_ID'].dropna().unique().tolist()
    except Exception as e:
        print(f"Error reading stats file: {e}")
        return []

def fetch_team_assists(team_id, season="2025-26"):
    url = "https://api.pbpstats.com/get-assist-networks/nba"
    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "EntityId": int(team_id),
        "EntityType": "Team"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.pbpstats.com/",
        "Origin": "https://www.pbpstats.com",
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive"
    }

    session = requests.Session()
    retry = Retry(connect=3, read=3, redirect=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)

    try:
        response = session.get(url, params=params, headers=headers, timeout=45)
        response.raise_for_status()
        data = response.json()
        
        node_map = {node['id']: node['name'] for node in data['results']['nodes']}
        player_totals = {}
        
        for link in data['results']['links']:
            source_id = link['source']
            source_name = node_map.get(source_id, str(source_id))
            
            if source_name not in player_totals:
                player_totals[source_name] = {'Rim': 0, 'Mid': 0, 'Corner3': 0, 'Arc3': 0, 'Total': 0}
            
            rim_val = link.get('AtRim', 0)
            mid_val = link.get('ShortMidRange', 0) + link.get('LongMidRange', 0)
            
            player_totals[source_name]['Rim'] += rim_val
            player_totals[source_name]['Mid'] += mid_val
            player_totals[source_name]['Corner3'] += link.get('Corner3', 0)
            player_totals[source_name]['Arc3'] += link.get('Arc3', 0)
            player_totals[source_name]['Total'] += link.get('value', 0)
            
        return player_totals
    except Exception as e:
        print(f"Error fetching PBPStats for team {team_id}: {e}")
        return {}

def get_assist_zones_data():
    """Fetches and transforms assist data for all players."""
    team_ids = get_all_team_ids()
    all_player_stats = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_tid = {executor.submit(fetch_team_assists, tid): tid for tid in team_ids}
        for future in concurrent.futures.as_completed(future_to_tid):
            tid = future_to_tid[future]
            try:
                team_stats = future.result()
                all_player_stats.update(team_stats)
            except Exception as e:
                print(f"Team {tid} generated an exception: {e}")

    results = {}
    for p_name, stats in all_player_stats.items():
        total = stats['Total']
        if total == 0:
            continue
            
        # Distribute percentages smartly exactly to 100%
        distribution = {
            'restricted_area': stats['Rim'] / total,
            'mid_range': stats['Mid'] / total,
            'left_corner': (stats['Corner3'] / 2) / total,
            'right_corner': (stats['Corner3'] / 2) / total,
            'top_key': stats['Arc3'] / total
        }
        
        makes = {
            'restricted_area': stats['Rim'],
            'mid_range': stats['Mid'],
            'left_corner': stats['Corner3'] / 2,
            'right_corner': stats['Corner3'] / 2,
            'top_key': stats['Arc3']
        }
        
        raw_pcts = {zone: val * 100 for zone, val in distribution.items()}
        int_pcts = {zone: int(val) for zone, val in raw_pcts.items()}
        remainders = {zone: val - int(val) for zone, val in raw_pcts.items()}
        
        shortfall = 100 - sum(int_pcts.values())
        sorted_zones_by_remainder = sorted(remainders.keys(), key=lambda k: remainders[k], reverse=True)
        
        for i in range(shortfall):
            zone_to_bump = sorted_zones_by_remainder[i]
            int_pcts[zone_to_bump] += 1
            
        results[p_name] = {
            zone: {
                "percentage": f"{int_pcts[zone]}%",
                "makes": str(int(makes[zone]))
            }
            for zone in int_pcts.keys()
        }
        
    return results

def main():
    print("Fetching all assist zones...")
    data = get_assist_zones_data()
    print(f"Fetched assist zones for {len(data)} players.")
    if "Victor Wembanyama" in data:
        print("Victor Wembanyama:", data["Victor Wembanyama"])

if __name__ == "__main__":
    main()