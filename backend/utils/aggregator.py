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
    'PTS+REB+AST', 'PTS+REB', 'PTS+AST', 'REB+AST', 'STL+BLK', 'USG_PCT'
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
def run_aggregation(stats_path, dk_path, fd_path, logs_path, shooting_path, assists_path, opp_assist_path, opp_def_path, games_path, shot_type_path, opp_shot_type_path, play_type_path, boxscores_path, output_path):
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
    opp_shot_type_data = load_json(opp_shot_type_path)
    play_type_data = load_json(play_type_path)
    boxscores_data = load_json(boxscores_path)

    print(f"      Loaded: Stats({len(df_stats)}), DK({len(df_dk)}), FD({len(df_fd)}), Logs({len(df_logs)}), Shooting({len(shooting_data)}), Assists({len(assists_data)}), OppAssist({len(opp_assist_data)}), OppDef({len(opp_def_data)}), ShotTypes({len(shot_type_data)}), OppShotTypes({len(opp_shot_type_data)}), PlayTypes({len(play_type_data.get('players', {}))})")

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

    # Build ID to Stats Map for DNP lookup
    id_to_stats_map = {}
    for _, row in df_stats.iterrows():
        id_to_stats_map[int(row['PLAYER_ID'])] = {
            'PTS': safe_float(row.get('PTS', 0)),
            'AST': safe_float(row.get('AST', 0)),
            'REB': safe_float(row.get('REB', 0)),
            'FG3M': safe_float(row.get('FG3M', 0)),
            'STL': safe_float(row.get('STL', 0)),
            'BLK': safe_float(row.get('BLK', 0)),
            'TOV': safe_float(row.get('TOV', 0)),
            'PTS+REB+AST': safe_float(row.get('PTS+REB+AST', 0)),
            'PTS+REB': safe_float(row.get('PTS+REB', 0)),
            'PTS+AST': safe_float(row.get('PTS+AST', 0)),
            'REB+AST': safe_float(row.get('REB+AST', 0)),
            'STL+BLK': safe_float(row.get('STL+BLK', 0)),
            '1Q_PTS': safe_float(row.get('1Q_PTS', 0)),
            '1Q_AST': safe_float(row.get('1Q_AST', 0)),
            '1Q_REB': safe_float(row.get('1Q_REB', 0)),
            '1H_PTS': safe_float(row.get('1H_PTS', 0)),
        }

    # C. Prepare Game Logs (Group by Player)
    logs_map = {}
    if not df_logs.empty and 'PLAYER_ID' in df_logs.columns:
        df_logs = df_logs.fillna(0)
        # Group by ID and convert to list of dicts
        for pid, group in df_logs.groupby('PLAYER_ID'):
            player_logs = group.to_dict(orient='records')
            for log in player_logs:
                game_id = str(log.get('GAME_ID', '')).zfill(10)
                if game_id in boxscores_data:
                    b_data = boxscores_data[game_id]
                    
                    team = str(log.get('TEAM_ABBREVIATION', ''))
                    if not team or team == '0' or team == '0.0':
                        matchup = str(log.get('MATCHUP', ''))
                        if ' @ ' in matchup:
                            team = matchup.split(' @ ')[0]
                        elif ' vs. ' in matchup:
                            team = matchup.split(' vs. ')[0]
                            
                    log['margin'] = b_data['margins'].get(team, 0)
                    
                    team_dnps = []
                    for dnp in b_data['dnps']:
                        if dnp['team'] == team:
                            dnp_pid = matcher.match_player(dnp['name'], team, [])
                            d_stats = id_to_stats_map.get(dnp_pid, {}) if dnp_pid else {}
                            if not d_stats:
                                d_stats = {k: 0.0 for k in ['PTS', 'AST', 'REB', 'FG3M', 'STL', 'BLK', 'TOV', 'PTS+REB+AST', 'PTS+REB', 'PTS+AST', 'REB+AST', 'STL+BLK', '1Q_PTS', '1Q_AST', '1Q_REB', '1H_PTS']}
                            
                            team_dnps.append({
                                'name': dnp['name'],
                                'reason': dnp['reason'],
                                'stats': d_stats,
                                'id': dnp_pid
                            })
                    log['dnps'] = team_dnps
            logs_map[int(pid)] = player_logs

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
        
        if home_team and home_team not in team_games:
            team_games[home_team] = {'opp_tricode': away_team, 'opp_name': away_full}
        if away_team and away_team not in team_games:
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
        pts_by_shot = player_shot_type.get('pointsByShotType', {})
        matches_played = pts_by_shot.get('matchesPlayed', 1)
        if matches_played == 0: matches_played = 1
        
        cs_pts = pts_by_shot.get('catchAndShoot', 0) / matches_played
        pu_pts = pts_by_shot.get('pullups', 0) / matches_played

        # 3. The Remainder is strictly <10ft points
        lt10_pts = max(0, floor_pts - cs_pts - pu_pts)

        # 4. Convert to Percentages
        cs_pct, pu_pct, lt10_pct = 0, 0, 0
        if floor_pts > 0:
            cs_pct = round((cs_pts / floor_pts) * 100)
            pu_pct = round((pu_pts / floor_pts) * 100)
            lt10_pct = 100 - cs_pct - pu_pct 

        # 5. Format the Player Object
        player_shot_type = {
            'catch_and_shoot': {'points': round(cs_pts, 1), 'percentage': cs_pct},
            'pull_up': {'points': round(pu_pts, 1), 'percentage': pu_pct},
            'less_than_10_ft': {'points': round(lt10_pts, 1), 'percentage': lt10_pct}
        }
            
        # 6. Get the Opponent Defensive Ranks
        opp_shot_type_def = {}
        if opp_info:
            opp_name = opp_info['opp_name']
            
            opp_ranks = opp_shot_type_data.get(opp_name, {}).get('rankings', {})
            
            # Map the camelCase keys from opp_shot_type_analysis to the underscore keys we use
            opp_shot_type_def = {
                'catch_and_shoot': {'rank': opp_ranks.get('catchAndShoot', 15)},
                'pull_up': {'rank': opp_ranks.get('pullups', 15)},
                'less_than_10_ft': {'rank': opp_ranks.get('lessThanTenFeet', 15)}
            }

        # --- PLAY TYPE ANALYSIS LOGIC ---
        player_play_type = play_type_data.get('players', {}).get(row['PLAYER_NAME'], {}).get('points', {})
        
        opp_play_type_def = {}
        if opp_info:
            opp_name = opp_info['opp_name']
            opp_play_type_def = play_type_data.get('teams', {}).get(opp_name, {}).get('rankings', {})
            
        play_types_config = [
            ("Free Throws", "freeThrows"),
            ("Post Up", "postUp"),
            ("PNR Roll Man", "pickAndRollRollMan"),
            ("Putback", "putback"),
            ("Spot Up", "spotUp"),
            ("Cut", "cut"),
            ("Isolation", "isolation"),
            ("Transition", "transition"),
            ("PNR Ball Handler", "pickAndRollBallHandler"),
            ("Handoff", "handoff"),
            ("Off Screen", "offScreen"),
            ("Misc", "misc")
        ]

        total_play_type_pts = sum([player_play_type.get(key, {}).get('perMatch', 0) for _, key in play_types_config])
        
        play_type_array = []
        for label, key in play_types_config:
            pts = player_play_type.get(key, {}).get('perMatch', 0)
            pct = round((pts / total_play_type_pts) * 100) if total_play_type_pts > 0 else 0
            rank = opp_play_type_def.get(key, "N/A")
            
            if pts > 0:
                play_type_array.append({
                    "type": label,
                    "points": f"{pts} ({pct}%)",
                    "percent": f"{pct}%",
                    "rank": rank,
                    "raw_pts": pts
                })
                
        # Sort by points descending
        play_type_array.sort(key=lambda x: x['raw_pts'], reverse=True)
        # Remove raw_pts
        for item in play_type_array:
            del item['raw_pts']

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
            "play_type_analysis": play_type_array,
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
        f"{base}/opponent_defensive_ranks.json",
        f"{base}/play_type_analysis.json",
        f"{base}/boxscores.json",
        f"{base}/master_feed.json"
    )