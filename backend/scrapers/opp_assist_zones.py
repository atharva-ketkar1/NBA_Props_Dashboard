import pandas as pd
import requests
import json
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

try:
    from .season_type import resolve_season, resolve_season_type
except ImportError:
    from season_type import resolve_season, resolve_season_type

def fetch_opponent_assists_allowed(season=None):
    """Fetches Opponent Assists Allowed by Zone for all 30 teams in a single request via Proxy."""
    import urllib.parse
    if season is None:
        season = resolve_season()
    
    target_url = "https://api.pbpstats.com/get-totals/nba"
    target_params = {
        "Season": season,
        "SeasonType": resolve_season_type(),
        "Type": "Opponent"
    }
    
    query_string = urllib.parse.urlencode(target_params)
    full_target_url = f"{target_url}?{query_string}"
    
    proxy_url = os.environ.get("PBPSTATS_PROXY_URL")
    if not proxy_url:
        raise ValueError("PBPSTATS_PROXY_URL environment variable is not set. Please check your .env file.")
        
    params = {"url": full_target_url}

    
    headers = {
        "Accept": "application/json, text/plain, */*",
    }

    session = requests.Session()
    retry = Retry(connect=3, read=3, redirect=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)

    try:
        response = session.get(proxy_url, params=params, headers=headers, timeout=45)
        response.raise_for_status()
        data = response.json()
        
        # PBPStats Totals endpoint returns a list of dictionaries for all teams
        team_stats = data.get('multi_row_table_data', [])
        
        results = {}
        for team in team_stats:
            team_name = team.get('Name')
            if not team_name:
                continue
                
            # Extract assist zones allowed by the defense
            rim = team.get('AtRimAssists', 0)
            mid = team.get('ShortMidRangeAssists', 0) + team.get('LongMidRangeAssists', 0)
            corner3 = team.get('Corner3Assists', 0)
            arc3 = team.get('Arc3Assists', 0)
            
            total = rim + mid + corner3 + arc3
            if total == 0:
                continue
                
            # Distribute percentages smartly exactly to 100%
            distribution = {
                'restricted_area': rim / total,
                'mid_range': mid / total,
                'left_corner': (corner3 / 2) / total,
                'right_corner': (corner3 / 2) / total,
                'top_key': arc3 / total
            }
            
            makes = {
                'restricted_area': rim,
                'mid_range': mid,
                'left_corner': corner3 / 2,
                'right_corner': corner3 / 2,
                'top_key': arc3
            }
            
            raw_pcts = {zone: val * 100 for zone, val in distribution.items()}
            int_pcts = {zone: int(val) for zone, val in raw_pcts.items()}
            remainders = {zone: val - int(val) for zone, val in raw_pcts.items()}
            
            shortfall = 100 - sum(int_pcts.values())
            sorted_zones_by_remainder = sorted(remainders.keys(), key=lambda k: remainders[k], reverse=True)
            
            for i in range(int(shortfall)):
                zone_to_bump = sorted_zones_by_remainder[i]
                int_pcts[zone_to_bump] += 1
                
            # Calculate rank per zone over teams later, but we need ranks relative to other teams
            # So first we collect everything then rank it
            results[team_name] = {
                "makes": makes,
                "int_pcts": int_pcts
            }
            
        return results
        
    except Exception as e:
        print(f"\nCRITICAL: Error fetching Opponent PBPStats from proxy: {e}")
        print("Returning empty dataset to protect pipeline integrity.\n")
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

TEAM_ABBR_TO_FULL = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards"
}

def get_opp_assist_zones_data():
    raw_data = fetch_opponent_assists_allowed()
    zones_data = convert_to_zones(raw_data)
    
    # Map abbreviations back to full names
    mapped_data = {}
    for team_abbr, data in zones_data.items():
        full_name = TEAM_ABBR_TO_FULL.get(team_abbr, team_abbr)
        mapped_data[full_name] = data
        
    return mapped_data

def main():
    print("Fetching all opponent assist zones data via Cloudflare Proxy...")
    data = get_opp_assist_zones_data()
    print(f"Fetched defensive assist zones for {len(data)} teams.\n")

if __name__ == "__main__":
    main()
