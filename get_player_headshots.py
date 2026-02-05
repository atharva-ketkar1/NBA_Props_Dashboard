import os
import requests
import time

HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "dnt": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
}

# ==========================================
# CONFIGURATION
# ==========================================
SAVE_FOLDER = "player_headshots"
HEADSHOT_URL_TEMPLATE = "https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

# Headers are important so the NBA CDN doesn't block the script as a bot

# 1. Create the directory if it doesn't exist
if not os.path.exists(SAVE_FOLDER):
    os.makedirs(SAVE_FOLDER)
    print(f"Created folder: {SAVE_FOLDER}")

# 2. Get unique list of Player IDs from your existing dataframe
# We use drop_duplicates so we don't download Luka's image twice if he appears twice in your logs
unique_players = Stat_Leaders[['PLAYER_ID', 'PLAYER_NAME']].drop_duplicates()
# For testing, only download the first 20 players
unique_players = unique_players.iloc[:20]

print(f"Found {len(unique_players)} unique players to download.")

# 3. Loop and Download
for index, row in unique_players.iterrows():
    player_id = row['PLAYER_ID']
    player_name = row['PLAYER_NAME']
    
    # Construct the file path (e.g., player_headshots/1629029.png)
    file_name = f"{player_id}.png"
    file_path = os.path.join(SAVE_FOLDER, file_name)
    
    # Skip if we already downloaded it (saves time on re-runs)
    if os.path.exists(file_path):
        print(f"[Skipping] {player_name} - Already exists")
        continue

    url = HEADSHOT_URL_TEMPLATE.format(player_id=player_id)

    try:
        print(url)
        response = requests.get(url, headers=HEADERS, stream=True)
        
        if response.status_code == 200:
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            print(f"[Downloaded] {player_name}")
        else:
            print(f"[Failed] {player_name} (Status: {response.status_code})")
            
        # Be polite to the server
        time.sleep(0.5)

    except Exception as e:
        print(f"[Error] Could not download {player_name}: {e}")

print("\n--- Download Process Complete ---")