import requests
import pandas as pd
import json
from datetime import datetime, timezone, timedelta
import os

def get_nba_schedule():
    """Get NBA schedule data"""
    url = 'https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json'
    
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
    """Parse game data from the schedule endpoint"""
    home_team = game.get('homeTeam', {})
    away_team = game.get('awayTeam', {})
    
    home_tricode = home_team.get('teamTricode')
    away_tricode = away_team.get('teamTricode')
    
    # Parse game time (schedule uses gameDateTimeEst and gameDateTimeUTC)
    game_time_utc = game.get('gameDateTimeUTC')
    game_time_et_raw = game.get('gameDateTimeEst')
    
    game_time_et_display = None
    game_date = None
    game_weekday = None
    
    if game_time_et_raw:
        try:
            # Parse the EST time string from the API
            dt = datetime.strptime(game_time_et_raw, "%Y-%m-%dT%H:%M:%SZ")
            game_time_et_display = dt.strftime("%I:%M %p ET")
            game_date = dt.strftime("%Y-%m-%d")
            game_weekday = dt.strftime("%A")
        except:
            game_time_et_display = game_time_et_raw
    
    # Determine game status from gameStatus (1: Scheduled, 2: Live, 3: Final)
    game_status_num = game.get('gameStatus', 1)
    status_text = game.get('gameStatusText', '')
    
    is_live = game_status_num == 2
    is_final = game_status_num == 3
    is_scheduled = game_status_num == 1
    
    # Extract scores (schedule endpoint might have them for completed games, default to 0)
    home_score = home_team.get('score', 0)
    away_score = away_team.get('score', 0)
    
    # Optional player leaders (depending on if they are populated in the static file)
    points_leaders = game.get('pointsLeaders', [])
    home_leader_name, home_leader_points = None, None
    away_leader_name, away_leader_points = None, None
    
    for leader in points_leaders:
        if leader.get('teamTricode') == home_tricode:
            home_leader_name = f"{leader.get('firstName', '')} {leader.get('lastName', '')}".strip()
            home_leader_points = leader.get('points')
        elif leader.get('teamTricode') == away_tricode:
            away_leader_name = f"{leader.get('firstName', '')} {leader.get('lastName', '')}".strip()
            away_leader_points = leader.get('points')

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
        'home_score': home_score,
        
        'away_team_id': away_team.get('teamId'),
        'away_team_name': away_team.get('teamName'),
        'away_team_city': away_team.get('teamCity'),
        'away_team_tricode': away_tricode,
        'away_team_wins': away_team.get('wins'),
        'away_team_losses': away_team.get('losses'),
        'away_score': away_score,
        
        # Arena information (directly from API now)
        'arena_name': game.get('arenaName', 'Unknown Arena'),
        'arena_city': game.get('arenaCity', 'Unknown City'),
        'arena_state': game.get('arenaState', 'Unknown State'),
        'arena_full': f"{game.get('arenaName', 'Unknown Arena')}, {game.get('arenaCity', 'Unknown City')}, {game.get('arenaState', 'Unknown State')}",
        
        # Game timing
        'game_time_utc': game_time_utc,
        'game_time_et': game_time_et_display,
        'game_date': game_date,
        'game_weekday': game_weekday,
        
        # Game status
        'game_status': game_status_num,
        'game_status_text': status_text,
        'is_live': is_live,
        'is_final': is_final,
        'is_scheduled': is_scheduled,
        
        # Player leaders
        'home_leader_name': home_leader_name,
        'home_leader_points': home_leader_points,
        
        'away_leader_name': away_leader_name,
        'away_leader_points': away_leader_points,
        
        # Derived fields
        'score_differential': abs(home_score - away_score) if home_score is not None and away_score is not None else 0,
        'total_points': (home_score or 0) + (away_score or 0),
        'winning_team': 'home' if home_score > away_score else 'away' if away_score > home_score else 'tie',
        'matchup': f"{away_tricode} @ {home_tricode}",
        'display_score': f"{away_tricode} {away_score} - {home_tricode} {home_score}"
    }
    
    return game_data

def get_dashboard_data():
    """Get today's games from the schedule"""
    print("Fetching NBA schedule data...")
    data = get_nba_schedule()
    
    # 1. Determine "Today" in US Eastern Time (ET)
    et_tz = timezone(timedelta(hours=-5))
    today_et = datetime.now(et_tz)
    
    # Format matches the 'gameDate' field: "MM/DD/YYYY 00:00:00"
    target_date_str = today_et.strftime("%m/%d/%Y 00:00:00")
    print(f"Looking for games on: {target_date_str}")
    
    game_dates = data.get('leagueSchedule', {}).get('gameDates', [])
    todays_games_list = []
    
    # 2. Find the object for today's date
    for date_obj in game_dates:
        if date_obj.get('gameDate') == target_date_str:
            todays_games_list = date_obj.get('games', [])
            break
            
    print(f"Found {len(todays_games_list)} games for today")
    
    all_games_data = []
    
    for game in todays_games_list:
        game_data = parse_game_data(game)
        all_games_data.append(game_data)
    
    # Convert to DataFrame
    df = pd.DataFrame(all_games_data) if all_games_data else pd.DataFrame()
    
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
    print(f"Scheduled games: {df['is_scheduled'].sum()}")
    print(f"Live games: {df['is_live'].sum()}")
    print(f"Final games: {df['is_final'].sum()}")

    print(f"\n🏀 Games Today:")

    for idx, row in df.iterrows():
        if row['is_live']:
            status_icon = "🟢 LIVE"
            status_info = ""
        elif row['is_final']:
            status_icon = "✅ FINAL"
            status_info = ""
        else:
            status_icon = "⏰"
            status_info = row['game_time_et']
        
        print(f"\n{status_icon} {row['matchup']} {status_info}")
        print(f"   Game ID: {row['game_id']}")
        print(f"   Score: {row['display_score']}")
        print(f"   Arena: {row['arena_full']}")
        print(f"   Status: {row['game_status_text']}")
        
        if row.get('home_leader_name'):
            print(f"   Home Leader: {row['home_leader_name']} ({row['home_leader_points']} pts)")
        if row.get('away_leader_name'):
            print(f"   Away Leader: {row['away_leader_name']} ({row['away_leader_points']} pts)")
else:
    print("No games found for today.")

# Save to files
output_path = os.path.join(os.path.dirname(__file__), '../data/current/nba_dashboard_games.json')
# Ensure directory exists
os.makedirs(os.path.dirname(output_path), exist_ok=True)

with open(output_path, 'w') as f:
    json.dump(raw_data, f, indent=2, default=str)

print(f"\n💾 Data saved to:")
print(f"   - {output_path}")