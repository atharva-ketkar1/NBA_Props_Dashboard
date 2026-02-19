from nba_api.stats.endpoints import leaguedashplayershotlocations
import pandas as pd


HEADERS = {
    "Host": "stats.nba.com",
    "Connection": "keep-alive",
    "Accept": "application/json, text/plain, */*",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/121.0.0.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9"
}

def get_player_shooting_zones(player_id, season="2025-26"):
    # 1. Fetch Data
    print(f"Fetching Shooting Zones for Season {season}...")
    shot_locs = leaguedashplayershotlocations.LeagueDashPlayerShotLocations(
        season=season,
        distance_range="By Zone", 
        per_mode_detailed="PerGame",
        headers=HEADERS,
        timeout=120
    )
    
    # 2. Extract Raw Data
    data = shot_locs.get_dict()
    rows = data['resultSets']['rowSet']
    
    # Handle empty data case
    if not rows:
        print("No data found.")
        return pd.DataFrame()

    # 3. DYNAMICALLY CALCULATE HEADERS
    # We do this to match the exact width of the data returned
    data_width = len(rows[0])
    
    # Get the list of zones (e.g., Restricted, Mid-Range, etc.)
    zone_names = data['resultSets']['headers'][0]['columnNames']
    
    # Get the "span" (usually 3 columns per zone: FGM, FGA, FG%)
    # Default to 3 if not found, but it's usually in the metadata
    span = data['resultSets']['headers'][0].get('columnSpan', 3)
    
    # Calculate how many columns belong to zones
    total_zone_cols = len(zone_names) * span
    
    # Calculate how many columns are left for Player Info (Base Columns)
    # This automatically adjusts if they add "GP" or "Age" or anything else
    num_base_cols = data_width - total_zone_cols
    
    # 4. Construct the Final Header List
    # Get the base column names from the raw response (e.g., PLAYER_NAME, TEAM_ID)
    raw_base_headers = data['resultSets']['headers'][1]['columnNames']
    final_headers = raw_base_headers[:num_base_cols]
    
    # Append the formatted Zone headers
    for zone in zone_names:
        final_headers.append(f"{zone}_FGM")
        final_headers.append(f"{zone}_FGA")
        final_headers.append(f"{zone}_FG_PCT")
        
    # Safety Check
    if len(final_headers) != data_width:
        print(f"⚠️ Warning: Header mismatch. Generated {len(final_headers)} headers for {data_width} columns.")
        # Fallback to avoid crash: just slice the data or pad headers
        
    # 5. Create DataFrame
    df = pd.DataFrame(rows, columns=final_headers)
    
    # 6. Filter by Player
    if player_id:
        df = df[df['PLAYER_ID'] == int(player_id)]
        
    return df

# --- RUN IT ---
try:
    # Alperen Sengun ID: 1630578
    df_zones = get_player_shooting_zones(player_id=1628973) 

    # Dynamic Column Selector (Only picks columns that actually exist)
    target_cols = [
        'PLAYER_NAME',
        'Restricted Area_FGA', 
        'In The Paint (Non-RA)_FGA', 
        'Mid-Range_FGA',
        'Left Corner 3_FGA', 
        'Right Corner 3_FGA', 
        'Above the Break 3_FGA'
    ]
    
    # Filter to only existing columns (in case "Left Corner 3" is missing or named differently)
    existing_cols = [c for c in target_cols if c in df_zones.columns]
    
    if not df_zones.empty:
        print(df_zones[existing_cols].T)
    else:
        print("Player not found.")
        
except Exception as e:
    print(f"Error: {e}")