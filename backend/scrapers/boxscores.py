import requests
import json
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# CONFIGURATION
# ==========================================
MAX_HISTORY_DAYS = 300  # Check the whole season for missing games
MAX_WORKERS = 10  # Because we only use the CDN now, we can max out the workers!

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/"
}

def get_all_recent_game_ids(days_back):
    """
    Hits the master CDN schedule file ONCE to get all Game IDs for the season,
    then filters for games that happened in our target window.
    Zero risk of timeouts or bot-blocks.
    """
    print("      Fetching Master Season Schedule from CDN...")
    url = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
    
    recent_game_ids = []
    cutoff_date = datetime.now() - timedelta(days=days_back)
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        schedule_data = resp.json()
        
        # Navigate the JSON tree to find the dates array
        game_dates = schedule_data.get('leagueSchedule', {}).get('gameDates', [])
        
        for date_obj in game_dates:
            # Format is usually "MM/DD/YYYY 00:00:00"
            raw_date_str = date_obj.get('gameDate', '').split(' ')[0] 
            
            try:
                game_date = datetime.strptime(raw_date_str, "%m/%d/%Y")
                # Only grab games that fall within our lookback window AND are in the past
                if cutoff_date <= game_date <= datetime.now():
                    for game in date_obj.get('games', []):
                        # Only append games that are actually finished (Status 3)
                        if game.get('gameStatus') == 3:
                            recent_game_ids.append(game.get('gameId'))
            except ValueError:
                continue # Skip if date parsing fails for some reason
                
        return recent_game_ids

    except Exception as e:
        print(f"      [Critical Error] Failed to fetch Master Schedule: {e}")
        return []

def fetch_and_parse_boxscore(game_id):
    """
    Hits the CDN for the raw boxscore, extracting exact margins and DNPs.
    Since this is the CDN, we do not need extreme backoffs.
    """
    url = f"https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
    
    try:
        # Fast timeout since CDN is incredibly responsive
        resp = requests.get(url, headers=HEADERS, timeout=10)
        
        if resp.status_code != 200:
            return game_id, None

        boxscore_json = resp.json()
        game_data = boxscore_json.get('game', {})
        
        if game_data.get('gameStatus') != 3:
            return game_id, None

        home_team = game_data.get('gameTeam', game_data.get('homeTeam', {}))
        away_team = game_data.get('awayTeam', {})
        
        # 1. Calculate Margins
        home_score = home_team.get('score', 0)
        away_score = away_team.get('score', 0)
        
        margins = {
            home_team.get('teamTricode'): home_score - away_score,
            away_team.get('teamTricode'): away_score - home_score
        }
        
        # 2. Extract DNPs
        dnps = []
        for team in [home_team, away_team]:
            for player in team.get('players', []):
                if player.get('played') == "0":
                    full_name = f"{player.get('firstName', '')} {player.get('familyName', '')}".strip()
                    reason = player.get('notPlayingDescription', player.get('notPlayingReason', 'DNP'))
                    
                    dnps.append({
                        "name": full_name,
                        "team": team.get('teamTricode'),
                        "reason": reason
                    })
                    
        return game_id, {
            "margins": margins,
            "dnps": dnps
        }

    except Exception as e:
        # Only log real connection errors, ignore standard missing data
        if "404" not in str(e):
            print(f"      Failed to parse Boxscore {game_id}: {e}")
        return game_id, None

def run_scrape(output_path):
    print(f"   Managing Boxscores at {output_path}")

    existing_data = {}

    if os.path.exists(output_path):
        try:
            with open(output_path, 'r') as f:
                existing_data = json.load(f)
            if existing_data:
                print(f"      Found {len(existing_data)} existing logs. Identifying missing games for SMART INCREMENTAL update.")
        except Exception:
            print("      Corrupt JSON. Forcing full refresh.")
            existing_data = {}

    days_to_check = MAX_HISTORY_DAYS
    
    # Step 1: Get all Game IDs instantly from the Master Schedule
    all_game_ids = get_all_recent_game_ids(days_to_check)
    
    # Step 2: Filter out games we already have
    game_ids_to_process = [gid for gid in all_game_ids if gid not in existing_data]
    
    print(f"      Found {len(game_ids_to_process)} NEW completed games missing from boxscores.")

    if not game_ids_to_process:
        print("      No new games to process. Exiting.")
        return

    # Step 3: Fetch the actual boxscores concurrently from the CDN
    print(f"      Fetching Boxscore data with {MAX_WORKERS} workers...")
    valid_updates = 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_game = {executor.submit(fetch_and_parse_boxscore, gid): gid for gid in game_ids_to_process}
        
        for i, future in enumerate(as_completed(future_to_game)):
            game_id, parsed_data = future.result()
            
            # If valid data came back, save it to our dictionary
            if parsed_data:
                existing_data[game_id] = parsed_data
                valid_updates += 1
            
            if (i + 1) % 25 == 0 or (i + 1) == len(game_ids_to_process):
                print(f"      [{i+1}/{len(game_ids_to_process)}] Processed new boxscores...")

    # Step 4: Save to data sink
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(existing_data, f, indent=4)
        
    print(f"   Updated boxscores saved! (Total tracked: {len(existing_data)}, Updated just now: {valid_updates})")

if __name__ == "__main__":
    current_script_dir = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(current_script_dir)
    output_path = os.path.join(backend_dir, "data", "current", "boxscores.json")
    run_scrape(output_path)