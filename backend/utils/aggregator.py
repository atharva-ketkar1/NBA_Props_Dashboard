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

# ==========================================
# 3. MAIN AGGREGATION LOGIC
# ==========================================
def run_aggregation(stats_path, dk_path, fd_path, logs_path, output_path):
    print(f"   🔨 Aggregating Data...")

    # A. Load All Data
    df_stats = load_csv(stats_path)
    df_dk = load_csv(dk_path)
    df_fd = load_csv(fd_path)
    df_logs = load_csv(logs_path)

    print(f"      Loaded: Stats({len(df_stats)}), DK({len(df_dk)}), FD({len(df_fd)}), Logs({len(df_logs)})")

    if df_stats.empty:
        print("   ❌ No stats found. Aborting.")
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

    # D. Build Master Dictionary
    master_data = {}

    for _, row in df_stats.iterrows():
        pid = int(row['PLAYER_ID'])
        
        # 1. Extract Season Stats (Using DISPLAY_STATS list)
        season_stats = {}
        for k in DISPLAY_STATS:
            season_stats[k] = safe_float(row.get(k, 0))

        master_data[pid] = {
            "id": pid,
            "name": row['PLAYER_NAME'],
            "team": row['TEAM_ABBREVIATION'],
            "stats": season_stats,
            "game_log": logs_map.get(pid, []), # <--- NEW: Injects the 30-game history
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
        print(f"   ✅ Saved Master Feed ({len(final_output)} players) to {output_path}")
    except Exception as e:
        print(f"   ❌ Error saving JSON: {e}")

if __name__ == "__main__":
    # Test Run
    base = "backend/data/current"
    run_aggregation(
        f"{base}/stats.csv",
        f"{base}/draftkings.csv",
        f"{base}/fanduel.csv",
        f"{base}/gamelogs.csv",
        f"{base}/master_feed.json"
    )