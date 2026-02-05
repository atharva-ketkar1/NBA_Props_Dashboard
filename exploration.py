from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog
import pandas as pd

# 1. Helper function to get a player's ID by name
def get_player_id(player_name):
    nba_players = players.get_players()
    found_player = [p for p in nba_players if p['full_name'].lower() == player_name.lower()]
    
    if found_player:
        return found_player[0]['id']
    else:
        print(f"Player '{player_name}' not found.")
        return None

# 2. Main logic to fetch last 10 games
def get_last_10_games(player_name):
    print(f"Fetching data for {player_name}...")
    
    player_id = get_player_id(player_name)
    if not player_id:
        return

    # Fetch regular season game logs for 2024-25
    # (season_all calls the 'PlayerGameLog' endpoint)
    gamelog = playergamelog.PlayerGameLog(player_id=player_id, season='2025-26')
    
    # Convert to a DataFrame (table format)
    df = gamelog.get_data_frames()[0]

    # Select only the columns we care about
    # WL = Win/Loss, MIN = Minutes, PTS = Points, REB = Rebounds, AST = Assists, FG3M = 3 Pointers Made
    columns_to_keep = ['GAME_DATE', 'MATCHUP', 'WL', 'MIN', 'PTS', 'REB', 'AST', 'FG3M']
    
    if df.empty:
        print("No games found for this season.")
        return

    # Slice the top 10 rows (The API returns them newest first, so top 10 = last 10)
    last_10 = df.head(10)

    print(f"\n--- {player_name}: Last 10 Games ---")
    print(last_10.to_string(index=False))

# --- Run it ---
get_last_10_games("LeBron James")
get_last_10_games("Stephen Curry")
get_last_10_games("AJ Green")