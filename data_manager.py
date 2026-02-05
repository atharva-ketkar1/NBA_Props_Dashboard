import pandas as pd
import json
import time
import difflib
from datetime import datetime

# --- IMPORT YOUR EXISTING MODULES ---
# (Make sure these files are in the same folder)
import fetch_odds_fanduel as fanduel
import get_last_n_games_stats as game_logs
import season_stats_scrape as season_stats

# ==========================================
# 1. HELPER: FUZZY MATCHER
# ==========================================
class PlayerMatcher:
    def __init__(self, df_stats):
        self.df_stats = df_stats
        self.df_stats['norm_name'] = self.df_stats['PLAYER_NAME'].apply(self.normalize)
        self.id_map = dict(zip(self.df_stats.norm_name, self.df_stats.PLAYER_ID))
        self.valid_names = list(self.id_map.keys())

    @staticmethod
    def normalize(name):
        if not isinstance(name, str): return ""
        name = name.lower().strip()
        name = name.replace("'", "").replace(".", "").replace("-", " ")
        suffixes = [' jr', ' sr', ' ii', ' iii']
        for s in suffixes:
            if name.endswith(s): name = name[:-len(s)]
        return name.strip()

    def find_id(self, betting_name):
        clean = self.normalize(betting_name)
        # Exact match
        if clean in self.id_map:
            return self.id_map[clean]
        # Fuzzy match
        matches = difflib.get_close_matches(clean, self.valid_names, n=1, cutoff=0.8)
        if matches:
            return self.id_map[matches[0]]
        return None

# ==========================================
# 2. MAIN PIPELINE
# ==========================================
def run_pipeline():
    print("🚀 STARTING PROPSMADNESS PIPELINE...\n")

    # --- STEP A: LOAD SEASON STATS (SOURCE OF TRUTH) ---
    print("1️⃣  Loading Season Stats...")
    # In production, maybe load from CSV to save time if already scraped today
    try:
        df_stats = pd.read_csv("stats.csv")
        print(f"   Loaded {len(df_stats)} players from local CSV.")
    except FileNotFoundError:
        print("   CSV not found, scraping fresh stats...")
        engine = season_stats.NBAStatsEngine()
        df_stats = engine.get_player_data()
        df_stats.to_csv("stats.csv", index=False)

    matcher = PlayerMatcher(df_stats)

    # --- STEP B: FETCH BETTING ODDS ---
    print("\n2️⃣  Fetching Live Odds (FanDuel)...")
    raw_props = fanduel.fetch_odds() # Returns list of dicts
    print(f"   Found {len(raw_props)} total props.")

    # --- STEP C: MATCH ODDS TO PLAYER IDs ---
    print("\n3️⃣  Matching Players...")
    matched_props = []
    target_player_ids = []

    for prop in raw_props:
        pid = matcher.find_id(prop['player'])
        if pid:
            prop['player_id'] = pid
            
            # Attach Season Averages to the prop card immediately
            player_stats = df_stats[df_stats['PLAYER_ID'] == pid].iloc[0]
            prop['season_stats'] = {
                'PTS': float(player_stats.get('PTS', 0)),
                'REB': float(player_stats.get('REB', 0)),
                'AST': float(player_stats.get('AST', 0)),
                'MIN': float(player_stats.get('MIN', 0)),
                'FG_PCT': float(player_stats.get('FG_PCT', 0))
            }
            
            matched_props.append(prop)
            target_player_ids.append(pid)
        else:
            # print(f"   ⚠️ Could not match: {prop['player']}")
            pass

    # Remove duplicates (e.g., same player, different props) for the game log fetch
    unique_ids = list(set(target_player_ids))
    print(f"   Successfully matched {len(matched_props)} props across {len(unique_ids)} unique players.")

    # --- STEP D: FETCH LAST 20 GAMES ---
    print("\n4️⃣  Fetching Last 20 Games History...")
    
    # We need to temporarily hijack the configuration of your game_logs script
    # or ensure it can handle a list of IDs. 
    # For now, let's assume we modify get_last_n_games_stats.py to accept N=20
    # Or we just set the global variable if it allows.
    game_logs.N_GAMES = 20  # Override the setting
    
    # NOTE: Your original script fetches ALL players. 
    # To be efficient, we should filter the `df_logs` inside that script to only `unique_ids`.
    # For this example, we run it as is (might be slow) or assume it's optimized.
    
    # This function call assumes you refactored your script to return the DataFrame
    # If it prints to stdout, you'll need to adjust `get_last_n_games_stats.py` to `return df_final`
    df_history = game_logs.fetch_history_for_ids(unique_ids, n=20) 

    # --- STEP E: ASSEMBLE FRONTEND PAYLOAD ---
    print("\n5️⃣  Assembling Final JSON Payload...")
    
    final_cards = []

    for prop in matched_props:
        pid = prop['player_id']
        line = prop['line']
        prop_type = prop['prop_type'] # e.g., 'points'

        # Get history for this player
        player_games = df_history[df_history['PLAYER_ID'] == pid].copy()
        
        # Sort by date descending
        player_games = player_games.sort_values(by='GAME_DATE', ascending=False)

        # Convert history to simple list for JSON
        history_list = []
        hit_count = 0
        
        for _, game in player_games.iterrows():
            # Determine the stat value based on prop type
            val = 0
            if prop_type == 'points': val = game['PTS']
            elif prop_type == 'rebounds': val = game['REB']
            elif prop_type == 'assists': val = game['AST']
            elif prop_type == 'threes': val = game['FG3M']
            # ... handle other props (PRA, etc) ...

            is_hit = val > line
            if is_hit: hit_count += 1
            
            history_list.append({
                "date": game['DATE_STR'],
                "opponent": game['MATCHUP'],
                "value": int(val),
                "result": "OVER" if is_hit else "UNDER"
            })

        # Calculate Hit Rate
        total_games = len(history_list)
        hit_rate = round((hit_count / total_games * 100), 1) if total_games > 0 else 0

        # Add calculated fields to prop
        prop['history'] = history_list
        prop['hit_rate'] = f"{hit_rate}% ({hit_count}/{total_games})"
        prop['diff_season'] = round(prop['season_stats'].get('PTS', 0) - line, 1) # Example for points

        final_cards.append(prop)

    # --- SAVE OUTPUT ---
    with open("app_data.json", "w") as f:
        json.dump(final_cards, f, indent=2)

    print(f"\n✅ DONE! Generated {len(final_cards)} cards. Saved to 'app_data.json'.")

# Needed adjustment for get_last_n_games_stats.py:
# You'll need to wrap its main logic in a function `fetch_history_for_ids(ids, n)` 
# so it can be called here.

if __name__ == "__main__":
    run_pipeline()