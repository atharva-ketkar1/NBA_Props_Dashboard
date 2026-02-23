import pandas as pd
import requests
import os
import time
import random
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

STATS_FILE = os.path.join(os.path.dirname(__file__), '../data/current/season_stats.csv')


# ---------- SESSION FACTORY ----------
def create_session():
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://api.pbpstats.com",
        "Referer": "https://api.pbpstats.com/docs",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }

    session = requests.Session()
    session.headers.update(headers)

    retry = Retry(
        total=4,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
    )

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)

    return session


# ---------- TEAM IDS ----------
def get_all_team_ids():
    if not os.path.exists(STATS_FILE):
        return []
    df = pd.read_csv(STATS_FILE)
    return df['TEAM_ID'].dropna().unique().tolist()


# ---------- FETCH ONE TEAM ----------
def fetch_team_assists(session, team_id, season="2025-26"):
    url = "https://api.pbpstats.com/get-assist-networks/nba"

    params = {
        "Season": season,
        "SeasonType": "Regular Season",
        "EntityId": int(team_id),
        "EntityType": "Team"
    }

    response = session.get(url, params=params, timeout=30)

    # --- Adaptive backoff for throttling ---
    if response.status_code in (403, 429):
        sleep_time = random.uniform(5, 10)
        print(f"Throttled → sleeping {sleep_time:.1f}s")
        time.sleep(sleep_time)
        response = session.get(url, params=params, timeout=30)

    response.raise_for_status()
    return response.json()


# ---------- MAIN PIPELINE ----------
def get_assist_zones_data():
    team_ids = get_all_team_ids()
    session = create_session()

    all_player_stats = {}

    for i, tid in enumerate(team_ids):

        try:
            data = fetch_team_assists(session, tid)

            node_map = {n['id']: n['name'] for n in data['results']['nodes']}

            for link in data['results']['links']:
                name = node_map.get(link['source'], str(link['source']))

                stats = all_player_stats.setdefault(
                    name,
                    {'Rim': 0, 'Mid': 0, 'Corner3': 0, 'Arc3': 0, 'Total': 0}
                )

                stats['Rim'] += link.get('AtRim', 0)
                stats['Mid'] += link.get('ShortMidRange', 0) + link.get('LongMidRange', 0)
                stats['Corner3'] += link.get('Corner3', 0)
                stats['Arc3'] += link.get('Arc3', 0)
                stats['Total'] += link.get('value', 0)

        except Exception as e:
            print(f"Team {tid} error: {e}")

        # --- OPTIMAL pacing ---
        time.sleep(random.uniform(0.35, 0.9))

        # --- Batch cooldown every 8 teams ---
        if (i + 1) % 8 == 0:
            time.sleep(random.uniform(2.5, 4.5))

    return all_player_stats


# ---------- RUN ----------
if __name__ == "__main__":
    print("Fetching assist zones...")
    data = get_assist_zones_data()
    print(f"Players collected: {len(data)}")