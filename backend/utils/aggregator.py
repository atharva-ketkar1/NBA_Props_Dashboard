# File: backend/utils/aggregator.py
import pandas as pd
import difflib
import json
import os
import numpy as np

# 1. MAPPING FOR BETTING (Odds -> Stats)
# This matches a DraftKings "points" prop to your "PTS" column.
PROP_MAP = {
    'points': 'PTS', 'rebounds': 'REB', 'assists': 'AST',
    'threes': 'FG3M', 'blocks': 'BLK', 'steals': 'STL',
    'pra': 'PTS+REB+AST', 'pr': 'PTS+REB', 'pa': 'PTS+AST', 'ra': 'REB+AST', 'stocks': 'STL+BLK'
}

# 2. STATS TO DISPLAY (The full list for your Frontend)
DISPLAY_STATS = [
    # --- 1. ID & Team Info ---
    'PLAYER_ID', 'TEAM_ID', 'TEAM_ABBREVIATION', 'AGE', 'GP', 'MIN',

    # --- 2. Scoring & Shooting ---
    'PTS', 'FGM', 'FGA', 'FG_PCT', 
    'FG3M', 'FG3A', 'FG3_PCT', 
    'FTM', 'FTA', 'FT_PCT', 
    'PLUS_MINUS',

    # --- 3. Traditional Box Score ---
    'REB', 'OREB', 'DREB', 
    'AST', 'TOV', 
    'STL', 'BLK', 'PF', 'PFD',
    'DD2', 'TD3', # Double-Doubles, Triple-Doubles

    # --- 4. Advanced Tracking (The Good Stuff) ---
    'POTENTIAL_AST', 'PASSES_MADE', 
    'DRIVES', 'DRIVE_PTS', 
    'REB_CHANCES', 'REB_CONTEST_PCT',

    # --- 5. Custom "Edge" Metrics (Calculated by you) ---
    'AST_CONVERSION_PCT', 
    'REB_HUSTLE_PCT', 
    'AGGRESSION_SCORE',

    # --- 6. Combos (Calculated by Aggregator) ---
    'PTS+REB+AST', 'PTS+REB', 'PTS+AST', 'REB+AST', 'STL+BLK'
]

def normalize_name(name):
    if not isinstance(name, str): return ""
    name = name.lower().strip().replace('.', '').replace("'", "")
    for suffix in [' jr', ' sr', ' ii', ' iii', ' iv', ' v']:
        if name.endswith(suffix): name = name[:-len(suffix)]
    return name

def get_best_match_id(name, name_to_id_map):
    norm = normalize_name(name)
    if norm in name_to_id_map: return name_to_id_map[norm]
    matches = difflib.get_close_matches(norm, list(name_to_id_map.keys()), n=1, cutoff=0.85)
    return name_to_id_map[matches[0]] if matches else None

def run_aggregation(stats_path, dk_path, fd_path, output_path):
    print(f"   🔨 Aggregating: {stats_path} + {dk_path} + {fd_path}")
    
    # 1. Load Stats
    try:
        df_stats = pd.read_csv(stats_path)
        
        # --- CALCULATE COMBOS ---
        # Ensure base columns exist (default to 0 if missing)
        base_cols = ['PTS', 'REB', 'AST', 'STL', 'BLK', 'FG3M']
        for c in base_cols: 
            if c not in df_stats.columns: df_stats[c] = 0
            
        df_stats['PTS+REB+AST'] = df_stats['PTS'] + df_stats['REB'] + df_stats['AST']
        df_stats['PTS+REB'] = df_stats['PTS'] + df_stats['REB']
        df_stats['PTS+AST'] = df_stats['PTS'] + df_stats['AST']
        df_stats['REB+AST'] = df_stats['REB'] + df_stats['AST']
        df_stats['STL+BLK'] = df_stats['STL'] + df_stats['BLK']

        # Map for fuzzy matching
        df_stats['norm_name'] = df_stats['PLAYER_NAME'].apply(normalize_name)
        name_to_id_map = dict(zip(df_stats['norm_name'], df_stats['PLAYER_ID']))
    except Exception as e:
        print(f"❌ Failed to load stats: {e}")
        return

    # 2. Setup Master Dict
    master_data = {}
    for _, row in df_stats.iterrows():
        pid = int(row['PLAYER_ID'])
        
        # --- DYNAMIC STATS EXTRACTION ---
        # We grab everything in DISPLAY_STATS. If a column is missing in CSV, default to 0.
        stats_payload = {}
        for k in DISPLAY_STATS:
            if k in row:
                val = row[k]
                # Clean up ugly floats (e.g. 23.40000001 -> 23.4)
                if isinstance(val, float):
                    stats_payload[k] = round(val, 2)
                else:
                    stats_payload[k] = val
            else:
                stats_payload[k] = 0

        master_data[pid] = {
            "name": row['PLAYER_NAME'],
            "team": row['TEAM_ABBREVIATION'],
            "season_stats": stats_payload,  # <--- Now includes MIN, POTENTIAL_AST, etc.
            "props": {}
        }

    # 3. Merge Odds (Same logic as before)
    for book_name, path in [("DraftKings", dk_path), ("FanDuel", fd_path)]:
        if not os.path.exists(path): continue
        
        df_odds = pd.read_csv(path)
        for _, row in df_odds.iterrows():
            pid = get_best_match_id(row['player'], name_to_id_map)
            if not pid: continue
            
            clean_key = PROP_MAP.get(row['prop_type'], row['prop_type']).upper()
            if clean_key not in master_data[pid]['props']:
                master_data[pid]['props'][clean_key] = {}
            
            master_data[pid]['props'][clean_key][book_name] = {
                "line": row['line'],
                "over": row['over_odds'],
                "under": row['under_odds']
            }

    # 4. Save
    active_players = {k: v for k, v in master_data.items() if v['props']}
    with open(output_path, "w") as f:
        json.dump(active_players, f, indent=2)
    print(f"   💾 Saved Master Feed ({len(active_players)} players) to {output_path}")