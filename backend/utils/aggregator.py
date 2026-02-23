import pandas as pd
import json
import os
import numpy as np
from utils.player_matcher import PlayerMatcher

# ==========================================
# 1. CONFIGURATION MAPPINGS
# ==========================================
# Maps betting props to internal column names
PROP_MAP = {
    'points': 'PTS', 'rebounds': 'REB', 'assists': 'AST',
    'threes': 'FG3M', 'blocks': 'BLK', 'steals': 'STL',
    'pra': 'PTS+REB+AST', 'pr': 'PTS+REB', 'pa': 'PTS+AST', 'ra': 'REB+AST', 'stocks': 'STL+BLK'
}

# The explicit list of stats to send to the Frontend
DISPLAY_STATS = [
    # Basic
    'PLAYER_ID', 'TEAM_ABBREVIATION', 'MIN', 'GP',
    # Scoring
    'PTS', 'FGM', 'FGA', 'FG_PCT', 'FG3M', 'FG3A', 'FG3_PCT', 'FTM', 'FTA', 'FT_PCT', 'PLUS_MINUS',
    # Traditional
    'REB', 'OREB', 'DREB', 'AST', 'TOV', 'STL', 'BLK', 'PF',
    # Advanced / Calculated
    'POTENTIAL_AST', 'DRIVES', 'DRIVE_PTS', 'REB_CHANCES', 
    'PTS+REB+AST', 'PTS+REB', 'PTS+AST', 'REB+AST', 'STL+BLK'
]

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def normalize_name(name):
    """Standardizes names for fuzzy matching."""
    if not isinstance(name, str): return ""
    name = name.lower().strip().replace('.', '').replace("'", "")
    for suffix in [' jr', ' sr', ' ii', ' iii', ' iv', ' v']:
        if name.endswith(suffix): name = name[:-len(suffix)]
    return name

def get_best_match_id(name, name_to_id_map):
    """Finds the PLAYER_ID for a messy betting name."""
    norm = normalize_name(name)
    if norm in name_to_id_map: return name_to_id_map[norm]
    # Fuzzy match
    matches = difflib.get_close_matches(norm, list(name_to_id_map.keys()), n=1, cutoff=0.85)
    return name_to_id_map[matches[0]] if matches else None

def safe_float(x):
    """Converts to float safely, handling errors."""
    try:
        return float(x)
    except:
        return 0.0

def load_csv(path):
    """Loads a CSV if it exists, else empty DF."""
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except:
            return pd.DataFrame()
    return pd.DataFrame()

def load_json(path):
    """Loads a JSON if it exists, else empty dict."""
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}

# ==========================================
# 3. MAIN AGGREGATION LOGIC
# ==========================================
def run_aggregation(stats_path, dk_path, fd_path, logs_path, shooting_path, assists_path, opp_assist_path, opp_def_path, games_path, shot_type_path, output_path):
    print(f"   Aggregating Data...")

    # A. Load All Data
    df_stats = load_csv(stats_path)
    df_dk = load_csv(dk_path)
    df_fd = load_csv(fd_path)
    df_logs = load_csv(logs_path)
    
    shooting_data = load_json(shooting_path)
    assists_data = load_json(assists_path)
    opp_assist_data = load_json(opp_assist_path)
    opp_def_data = load_json(opp_def_path)
    games_data = load_json(games_path)
    shot_type_data = load_json(shot_type_path)

    print(f"      Loaded: Stats({len(df_stats)}), DK({len(df_dk)}), FD({len(df_fd)}), Logs({len(df_logs)}), Shooting({len(shooting_data)}), Assists({len(assists_data)}), OppAssist({len(opp_assist_data)}), OppDef({len(opp_def_data)}), ShotTypes({len(shot_type_data)})")

    if df_stats.empty:
        print("   No stats found. Aborting.")
        return

    # B. Prepare Stats Data (Calculate Combos)
    df_stats = df_stats.fillna(0)
    df_stats['PTS+REB+AST'] = df_stats['PTS'] + df_stats['REB'] + df_stats['AST']
    df_stats['PTS+REB'] = df_stats['PTS'] + df_stats['REB']
    df_stats['PTS+AST'] = df_stats['PTS'] + df_stats['AST']
    df_stats['REB+AST'] = df_stats['REB'] + df_stats['AST']
    df_stats['STL+BLK'] = df_stats['STL'] + df_stats['BLK']

    # Create Matcher Instance (using PlayerMatcher)
    stats_records = df_stats[['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION']].to_dict('records')
    matcher = PlayerMatcher(stats_records)

    # C. Prepare Game Logs (Group by Player)
    logs_map = {}
    if not df_logs.empty and 'PLAYER_ID' in df_logs.columns:
        df_logs = df_logs.fillna(0)
        # Group by ID and convert to list of dicts
        for pid, group in df_logs.groupby('PLAYER_ID'):
            logs_map[int(pid)] = group.to_dict(orient='records')

    # D. Map assist data from pbpstats to PLAYER_ID
    assists_by_pid = {}
    for p_name, data in assists_data.items():
        pid = matcher.match_player(p_name, 'UNK', [])
        if pid:
            assists_by_pid[pid] = data

    # Define team mapping because games api returns mascots ("Suns") but stats api returns full names ("Phoenix Suns")
    TEAM_FULL_NAMES = {
        'Hawks': 'Atlanta Hawks', 'Celtics': 'Boston Celtics', 'Nets': 'Brooklyn Nets',
        'Hornets': 'Charlotte Hornets', 'Bulls': 'Chicago Bulls', 'Cavaliers': 'Cleveland Cavaliers',
        'Mavericks': 'Dallas Mavericks', 'Nuggets': 'Denver Nuggets', 'Pistons': 'Detroit Pistons',
        'Warriors': 'Golden State Warriors', 'Rockets': 'Houston Rockets', 'Pacers': 'Indiana Pacers',
        'Clippers': 'LA Clippers', 'Lakers': 'Los Angeles Lakers', 'Grizzlies': 'Memphis Grizzlies',
        'Heat': 'Miami Heat', 'Bucks': 'Milwaukee Bucks', 'Timberwolves': 'Minnesota Timberwolves',
        'Pelicans': 'New Orleans Pelicans', 'Knicks': 'New York Knicks', 'Thunder': 'Oklahoma City Thunder',
        'Magic': 'Orlando Magic', '76ers': 'Philadelphia 76ers', 'Suns': 'Phoenix Suns',
        'Trail Blazers': 'Portland Trail Blazers', 'Kings': 'Sacramento Kings', 'Spurs': 'San Antonio Spurs',
        'Raptors': 'Toronto Raptors', 'Jazz': 'Utah Jazz', 'Wizards': 'Washington Wizards'
    }

    # D. Build Games Map
    team_games = {}
    for g in games_data:
        home_team = g.get('home_team_tricode', '')
        away_team = g.get('away_team_tricode', '')
        
        home_mascot = g.get('home_team_name', '')
        away_mascot = g.get('away_team_name', '')
        
        home_full = TEAM_FULL_NAMES.get(home_mascot, home_mascot)
        away_full = TEAM_FULL_NAMES.get(away_mascot, away_mascot)
        
        team_games[home_team] = {'opp_tricode': away_team, 'opp_name': away_full}
        team_games[away_team] = {'opp_tricode': home_team, 'opp_name': home_full}

    # E. Build Master Dictionary
    master_data = {}

    # Define simple position mapping
    def get_simple_pos(pos_str):
        if not pos_str: return 'G'
        if 'G' in pos_str: return 'G'
        if 'C' in pos_str: return 'C'
        if 'F' in pos_str: return 'F'
        return 'G'

    for _, row in df_stats.iterrows():
        pid = int(row['PLAYER_ID'])
        
        # 1. Extract Season Stats (Using DISPLAY_STATS list)
        season_stats = {}
        for k in DISPLAY_STATS:
            season_stats[k] = safe_float(row.get(k, 0))

        # 2. Get opponent defense context
        # Some stats rows have TEAM_ABBREVIATION as numeric 0.0 or a weird type if missing. 
        # But we also have TEAM_ABBREVIATION in the logs or in master_data early. 
        team = str(row['TEAM_ABBREVIATION'])
        if team == '0.0' or team == '0':
            # Fallback to logs
            p_logs = logs_map.get(pid, [])
            if p_logs and len(p_logs) > 0 and 'TEAM_ABBREVIATION' in p_logs[0]:
                team = p_logs[0]['TEAM_ABBREVIATION']
        
        # Try getting exact position from season stats (from playerindex API), fallback to logs
        exact_pos = str(row.get('POSITION', ''))
        if not exact_pos or exact_pos == 'nan':
            exact_pos = 'G'
            p_logs = logs_map.get(pid, [])
            if p_logs and len(p_logs) > 0 and 'START_POSITION' in p_logs[0]:
                exact_pos = p_logs[0]['START_POSITION'] or 'G'
        
        sim_pos = get_simple_pos(exact_pos)

        opp_info = team_games.get(team, None)
        opp_def_zones = None
        opp_def_zones_positional = None
        opp_assist_zones = None
        opp_assist_zones_positional = None

        if opp_info:
            opp_name = opp_info['opp_name']
            
            if opp_name in opp_def_data:
                team_def_data = opp_def_data[opp_name]
                if 'ALL' in team_def_data:
                    opp_def_zones = team_def_data['ALL']
                elif sim_pos in team_def_data: # Fallback
                    opp_def_zones = team_def_data[sim_pos]
                
                if sim_pos in team_def_data:
                    opp_def_zones_positional = team_def_data[sim_pos]
                else:
                    print(f"DEBUG: Found {opp_name} but missing pos {sim_pos}")
            else:
                print(f"DEBUG: Could not find team {opp_name} in opp_def_data keys: {list(opp_def_data.keys())[:5]}")
                
            if opp_name in opp_assist_data:
                team_ast_data = opp_assist_data[opp_name]
                if 'ALL' in team_ast_data:
                    opp_assist_zones = team_ast_data['ALL']
                elif sim_pos in team_ast_data:
                    opp_assist_zones = team_ast_data[sim_pos]
                    
                if sim_pos in team_ast_data:
                    opp_assist_zones_positional = team_ast_data[sim_pos]

        # --- SHOT TYPE ANALYSIS LOGIC ---
        player_shot_type = shot_type_data.get('players', {}).get(row['PLAYER_NAME'], {}).copy()
        
        # 1. Calculate the player's total points strictly from Field Goals
        total_pts = season_stats.get('PTS', 0)
        ftm = season_stats.get('FTM', 0)
        floor_pts = total_pts - ftm 

        # 2. Get the raw points from Catch & Shoot and Pull Ups
        cs_pts = player_shot_type.get('catch_and_shoot', {}).get('points', 0)
        pu_pts = player_shot_type.get('pull_up', {}).get('points', 0)

        # 3. The Remainder is strictly <10ft points
        lt10_pts = max(0, floor_pts - cs_pts - pu_pts)

        # 4. Convert to Percentages
        cs_pct, pu_pct, lt10_pct = 0, 0, 0
        if floor_pts > 0:
            cs_pct = round((cs_pts / floor_pts) * 100)
            pu_pct = round((pu_pts / floor_pts) * 100)
            # Use subtraction for the final bucket to ensure they always sum to exactly 100%
            lt10_pct = 100 - cs_pct - pu_pct 

        # 5. Format the Player Object
        player_shot_type = {
            'catch_and_shoot': {'points': cs_pts, 'percentage': cs_pct},
            'pull_up': {'points': pu_pts, 'percentage': pu_pct},
            'less_than_10_ft': {'points': round(lt10_pts, 1), 'percentage': lt10_pct}
        }
            
        # 6. Get the Opponent Defensive Ranks
        opp_shot_type_def = {}
        if opp_info:
            opp_name = opp_info['opp_name']
            # This relies on your scraper pulling the actual 'less_than_10_ft' category 
            # using leaguedashptdefend, rather than trying to calculate it organically here.
            opp_shot_type_def = shot_type_data.get('teams', {}).get(opp_name, {}).copy()
            
            # Fallbacks just in case the scraper missed the team
            if 'catch_and_shoot' not in opp_shot_type_def: opp_shot_type_def['catch_and_shoot'] = {'rank': 15}
            if 'pull_up' not in opp_shot_type_def: opp_shot_type_def['pull_up'] = {'rank': 15}
            if 'less_than_10_ft' not in opp_shot_type_def: opp_shot_type_def['less_than_10_ft'] = {'rank': 15}

        master_data[pid] = {
            "id": pid,
            "name": row['PLAYER_NAME'],
            "team": team,
            "position": exact_pos,
            "stats": season_stats,
            "game_log": logs_map.get(pid, []), 
            "shooting_zones": shooting_data.get(row['PLAYER_NAME'], None),
            "assist_zones": assists_by_pid.get(pid, None),
            "opp_assist_zones": opp_assist_zones,
            "opp_assist_zones_positional": opp_assist_zones_positional,
            "opp_def_zones": opp_def_zones,
            "opp_def_zones_positional": opp_def_zones_positional,
            "shot_type_analysis": {
                "player": player_shot_type,
                "opp_def": opp_shot_type_def
            },
            "props": {}
        }

    # E. Merge Betting Odds
    # Helper to process odds files
    def process_odds(df, book_name):
        if df.empty: return
        for _, row in df.iterrows():
            # Extract basic info
            player_name = row.get('player', '')
            team_context = row.get('team', 'UNK')
            
            # Extract team options if available (from DK scraper update)
            team_opts = []
            raw_opts = row.get('team_options')
            if isinstance(raw_opts, str) and "[" in raw_opts:
                try:
                    import ast
                    team_opts = ast.literal_eval(raw_opts)
                except: pass

            # Match Player using robust matcher
            pid = matcher.match_player(player_name, team_context, team_opts)
            
            if not pid or pid not in master_data: continue

            # Map prop type (e.g. 'points' -> 'PTS')
            raw_prop = row.get('prop_type', '')
            clean_key = PROP_MAP.get(raw_prop, raw_prop).upper()
            
            # Initialize dict structure
            if clean_key not in master_data[pid]['props']:
                master_data[pid]['props'][clean_key] = {}
            
            # Add the line
            master_data[pid]['props'][clean_key][book_name] = {
                "line": row.get('line'),
                "over": row.get('over_odds'),
                "under": row.get('under_odds'),
                "implied": row.get('implied_prob', 0)
            }

    process_odds(df_dk, "dk")
    process_odds(df_fd, "fd")

    # F. Filter & Save
    # Only save players who have EITHER stats OR odds (removes G-League noise)
    final_output = []
    for pid, data in master_data.items():
        if data['props'] or data['stats']['GP'] > 0:
            final_output.append(data)

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(final_output, f, indent=4)
        print(f"   Saved Master Feed ({len(final_output)} players) to {output_path}")
    except Exception as e:
        print(f"   Error saving JSON: {e}")

if __name__ == "__main__":
    # Test Run
    base = "backend/data/current"
    run_aggregation(
        f"{base}/season_stats.csv",
        f"{base}/draftkings.csv",
        f"{base}/fanduel.csv",
        f"{base}/gamelogs.csv",
        f"{base}/shooting_zones.json",
        f"{base}/assist_zones.json",
        f"{base}/opp_assist_zones.json",
        f"{base}/opp_def_zones.json",
        f"{base}/nba_dashboard_games.json",
        f"{base}/shot_type_analysis.json",
        f"{base}/master_feed.json"
    )