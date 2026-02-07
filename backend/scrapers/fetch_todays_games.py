import requests
import pandas as pd
import json
from datetime import datetime, timezone, timedelta

# NBA Teams Arena Database
NBA_ARENAS = {
    'ATL': {'arena': 'State Farm Arena', 'city': 'Atlanta', 'state': 'GA'},
    'BOS': {'arena': 'TD Garden', 'city': 'Boston', 'state': 'MA'},
    'BKN': {'arena': 'Barclays Center', 'city': 'Brooklyn', 'state': 'NY'},
    'CHA': {'arena': 'Spectrum Center', 'city': 'Charlotte', 'state': 'NC'},
    'CHI': {'arena': 'United Center', 'city': 'Chicago', 'state': 'IL'},
    'CLE': {'arena': 'Rocket Mortgage FieldHouse', 'city': 'Cleveland', 'state': 'OH'},
    'DAL': {'arena': 'American Airlines Center', 'city': 'Dallas', 'state': 'TX'},
    'DEN': {'arena': 'Ball Arena', 'city': 'Denver', 'state': 'CO'},
    'DET': {'arena': 'Little Caesars Arena', 'city': 'Detroit', 'state': 'MI'},
    'GSW': {'arena': 'Chase Center', 'city': 'San Francisco', 'state': 'CA'},
    'HOU': {'arena': 'Toyota Center', 'city': 'Houston', 'state': 'TX'},
    'IND': {'arena': 'Gainbridge Fieldhouse', 'city': 'Indianapolis', 'state': 'IN'},
    'LAC': {'arena': 'Crypto.com Arena', 'city': 'Los Angeles', 'state': 'CA'},
    'LAL': {'arena': 'Crypto.com Arena', 'city': 'Los Angeles', 'state': 'CA'},
    'MEM': {'arena': 'FedExForum', 'city': 'Memphis', 'state': 'TN'},
    'MIA': {'arena': 'Kaseya Center', 'city': 'Miami', 'state': 'FL'},
    'MIL': {'arena': 'Fiserv Forum', 'city': 'Milwaukee', 'state': 'WI'},
    'MIN': {'arena': 'Target Center', 'city': 'Minneapolis', 'state': 'MN'},
    'NOP': {'arena': 'Smoothie King Center', 'city': 'New Orleans', 'state': 'LA'},
    'NYK': {'arena': 'Madison Square Garden', 'city': 'New York', 'state': 'NY'},
    'OKC': {'arena': 'Paycom Center', 'city': 'Oklahoma City', 'state': 'OK'},
    'ORL': {'arena': 'Kia Center', 'city': 'Orlando', 'state': 'FL'},
    'PHI': {'arena': 'Wells Fargo Center', 'city': 'Philadelphia', 'state': 'PA'},
    'PHX': {'arena': 'Footprint Center', 'city': 'Phoenix', 'state': 'AZ'},
    'POR': {'arena': 'Moda Center', 'city': 'Portland', 'state': 'OR'},
    'SAC': {'arena': 'Golden 1 Center', 'city': 'Sacramento', 'state': 'CA'},
    'SAS': {'arena': 'Frost Bank Center', 'city': 'San Antonio', 'state': 'TX'},
    'TOR': {'arena': 'Scotiabank Arena', 'city': 'Toronto', 'state': 'ON'},
    'UTA': {'arena': 'Delta Center', 'city': 'Salt Lake City', 'state': 'UT'},
    'WAS': {'arena': 'Capital One Arena', 'city': 'Washington', 'state': 'DC'},
}

def get_nba_scoreboard():
    """Get NBA scoreboard data with enhanced arena information"""
    url = 'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json'
    
    HEADERS = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "connection": "keep-alive",
        "dnt": "1",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    }
    
    response = requests.get(url=url, headers=HEADERS)
    return response.json()

def parse_game_data(game):
    """Parse game data with arena information"""
    home_team = game.get('homeTeam', {})
    away_team = game.get('awayTeam', {})
    
    home_tricode = home_team.get('teamTricode')
    away_tricode = away_team.get('teamTricode')
    
    # Get arena info from static database
    arena_info = NBA_ARENAS.get(home_tricode, {})
    
    # Parse game time
    game_time_utc = game.get('gameTimeUTC')
    game_time_et = None
    
    if game_time_utc:
        try:
            utc_time = datetime.strptime(game_time_utc, "%Y-%m-%dT%H:%M:%SZ")
            utc_time = utc_time.replace(tzinfo=timezone.utc)
            et_time = utc_time.astimezone(timezone(timedelta(hours=-5)))
            game_time_et = et_time.strftime("%I:%M %p ET")
            game_date = et_time.strftime("%Y-%m-%d")
            game_weekday = et_time.strftime("%A")
        except:
            game_time_et = game_time_utc
            game_date = None
            game_weekday = None
    
    # Determine game status
    status_text = game.get('gameStatusText', '')
    is_live = status_text in ['In Progress', 'Halftime']
    is_final = status_text == 'Final'
    is_scheduled = not (is_live or is_final)
    
    # Get game leaders
    game_leaders = game.get('gameLeaders', {})
    home_leaders = game_leaders.get('homeLeaders', {})
    away_leaders = game_leaders.get('awayLeaders', {})
    
    # Parse odds if available
    pb_odds = game.get('pbOdds', {})
    
    game_data = {
        # Game IDs
        'game_id': game.get('gameId'),
        'game_code': game.get('gameCode'),
        
        # Team IDs and names
        'home_team_id': home_team.get('teamId'),
        'home_team_name': home_team.get('teamName'),
        'home_team_city': home_team.get('teamCity'),
        'home_team_tricode': home_tricode,
        'home_team_wins': home_team.get('wins'),
        'home_team_losses': home_team.get('losses'),
        'home_score': home_team.get('score', 0),
        
        'away_team_id': away_team.get('teamId'),
        'away_team_name': away_team.get('teamName'),
        'away_team_city': away_team.get('teamCity'),
        'away_team_tricode': away_tricode,
        'away_team_wins': away_team.get('wins'),
        'away_team_losses': away_team.get('losses'),
        'away_score': away_team.get('score', 0),
        
        # Arena information (from static database)
        'arena_name': arena_info.get('arena', 'Unknown Arena'),
        'arena_city': arena_info.get('city', 'Unknown City'),
        'arena_state': arena_info.get('state', 'Unknown State'),
        'arena_full': f"{arena_info.get('arena', 'Unknown Arena')}, {arena_info.get('city', 'Unknown City')}, {arena_info.get('state', 'Unknown State')}",
        
        # Game timing
        'game_time_utc': game_time_utc,
        'game_time_et': game_time_et,
        'game_date': game_date,
        'game_weekday': game_weekday,
        'game_et': game.get('gameEt'),
        
        # Game status
        'game_status': game.get('gameStatus'),
        'game_status_text': status_text,
        'is_live': is_live,
        'is_final': is_final,
        'is_scheduled': is_scheduled,
        
        # Game progress
        'period': game.get('period'),
        'game_clock': game.get('gameClock'),
        'regulation_periods': game.get('regulationPeriods'),
        
        # Player leaders
        'home_leader_name': home_leaders.get('name'),
        'home_leader_points': home_leaders.get('points'),
        'home_leader_rebounds': home_leaders.get('rebounds'),
        'home_leader_assists': home_leaders.get('assists'),
        
        'away_leader_name': away_leaders.get('name'),
        'away_leader_points': away_leaders.get('points'),
        'away_leader_rebounds': away_leaders.get('rebounds'),
        'away_leader_assists': away_leaders.get('assists'),
        
        # Odds
        'odds_team': pb_odds.get('team'),
        'odds_value': pb_odds.get('odds'),
        'odds_suspended': pb_odds.get('suspended'),
        
        # Derived fields
        'score_differential': abs(home_team.get('score', 0) - away_team.get('score', 0)),
        'total_points': home_team.get('score', 0) + away_team.get('score', 0),
        'winning_team': 'home' if home_team.get('score', 0) > away_team.get('score', 0) else 'away' if away_team.get('score', 0) > home_team.get('score', 0) else 'tie',
        'matchup': f"{away_tricode} @ {home_tricode}",
        'display_score': f"{away_tricode} {away_team.get('score', 0)} - {home_tricode} {home_team.get('score', 0)}"
    }
    
    return game_data

def get_dashboard_data():
    """Get all data needed for NBA dashboard"""
    print("Fetching NBA scoreboard data...")
    data = get_nba_scoreboard()
    
    games = data.get('scoreboard', {}).get('games', [])
    print(f"Found {len(games)} games")
    
    all_games_data = []
    return_raw = [] # to return raw parsed list

    for game in games:
        game_data = parse_game_data(game)
        all_games_data.append(game_data)
    
    # Convert to DataFrame
    df = pd.DataFrame(all_games_data)
    
    return df, all_games_data

# Get the data
df, raw_data = get_dashboard_data()

# Display summary
print(f"\n{'='*80}")
print("NBA DASHBOARD DATA SUMMARY")
print(f"{'='*80}")

if not df.empty:
    print(f"\n📊 Overview:")
    print(f"Total games: {len(df)}")
    print(f"Live games: {df['is_live'].sum()}")
    print(f"Scheduled games: {df['is_scheduled'].sum()}")
    print(f"Final games: {df['is_final'].sum()}")

    print(f"\n🏀 Games Today:")

    for idx, row in df.iterrows():
        if row['is_live']:
            status_icon = "🟢 LIVE"
            status_info = f"Q{row['period']} {row['game_clock']}"
        elif row['is_final']:
            status_icon = "✅ FINAL"
            status_info = ""
        else:
            status_icon = "⏰"
            status_info = row['game_time_et']
        
        print(f"\n{status_icon} {row['matchup']} {status_info}")
        print(f"   Game ID: {row['game_id']}")
        print(f"   Score: {row['display_score']}")
        print(f"   Arena: {row['arena_name']}, {row['arena_city']}")
        print(f"   Status: {row['game_status_text']}")
        
        if row['home_leader_name']:
            print(f"   Home Leader: {row['home_leader_name']} ({row['home_leader_points']} pts)")
        if row['away_leader_name']:
            print(f"   Away Leader: {row['away_leader_name']} ({row['away_leader_points']} pts)")
else:
    print("No games found for today.")

# Save to files - UPDATED PATH
import os
output_path = os.path.join(os.path.dirname(__file__), '../data/current/nba_dashboard_games.json')
# Ensure directory exists
os.makedirs(os.path.dirname(output_path), exist_ok=True)

with open(output_path, 'w') as f:
    json.dump(raw_data, f, indent=2, default=str)

print(f"\n💾 Data saved to:")
print(f"   - {output_path}")