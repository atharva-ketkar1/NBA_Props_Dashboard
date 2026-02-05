import os
import requests
import time
import pandas as pd

# ==========================================
# CONFIGURATION
# ==========================================
SAVE_FOLDER = "team_logos"

# The URL pattern you found:
LOGO_URL_TEMPLATE = "https://cdn.nba.com/logos/nba/{team_id}/primary/L/logo.svg"

# Use the same "Clean" headers (No 'Host: stats.nba.com')
CDN_HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "en-US,en;q=0.9",
    "connection": "keep-alive",
    "dnt": "1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
}

# 1. Create the directory
if not os.path.exists(SAVE_FOLDER):
    os.makedirs(SAVE_FOLDER)
    print(f"Created folder: {SAVE_FOLDER}")

# 2. Get unique list of Team IDs from your df_final
# We assume df_final is still in memory from your main script
if 'df_final' in locals() and 'TEAM_ID' in df_final.columns:
    unique_teams = df_final['TEAM_ID'].unique()
    print(f"Found {len(unique_teams)} unique teams to download.")
else:
    # Fallback for testing (Hawks and Celtics IDs)
    print("Warning: df_final not found or missing TEAM_ID. Using test data.")
    unique_teams = [1610612737, 1610612738] 

# 3. Loop and Download
for team_id in unique_teams:
    # Construct the file path (e.g., team_logos/1610612737.svg)
    file_name = f"{team_id}.svg"
    file_path = os.path.join(SAVE_FOLDER, file_name)
    
    # Skip if exists
    if os.path.exists(file_path):
        print(f"[Skipping] Team {team_id} - Already exists")
        continue

    url = LOGO_URL_TEMPLATE.format(team_id=team_id)

    try:
        response = requests.get(url, headers=CDN_HEADERS)
        
        if response.status_code == 200:
            with open(file_path, 'wb') as f:
                f.write(response.content)
            print(f"[Downloaded] Team {team_id}")
        else:
            print(f"[Failed] Team {team_id} (Status: {response.status_code})")
            
        time.sleep(0.5)

    except Exception as e:
        print(f"[Error] Team {team_id}: {e}")

print("\n--- Team Logo Download Complete ---")