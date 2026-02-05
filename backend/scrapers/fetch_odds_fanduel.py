
import requests
import json
import time
from datetime import datetime, timedelta, timezone
from dateutil import tz

# CONSTANTS
FANDUEL_PUBLIC_ACCESS_KEY = "FhMFpcPWXMeyZxOx" 
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'x-sportsbook-region': 'OH',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
    'Accept': 'application/json'
}

def get_nba_main_page_data():
    """Fetches the main NBA page."""
    url = f"https://api.sportsbook.fanduel.com/sbapi/content-managed-page?page=CUSTOM&customPageId=nba&pbHorizontal=false&_ak={FANDUEL_PUBLIC_ACCESS_KEY}&timezone=America%2FNew_York"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching NBA main page: {e}")
        return None

def get_player_props(event_id, prop_tab_name):
    """Fetches specific prop tab data."""
    cache_buster = int(time.time())
    url = f"https://api.sportsbook.fanduel.com/sbapi/event-page?_ak={FANDUEL_PUBLIC_ACCESS_KEY}&eventId={event_id}&tab={prop_tab_name}&_={cache_buster}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching props for event {event_id}: {e}")
        return None

def get_all_available_tabs(event_id):
    """Fetches available tabs for an event."""
    cache_buster = int(time.time())
    url = f"https://api.sportsbook.fanduel.com/sbapi/event-page?_ak={FANDUEL_PUBLIC_ACCESS_KEY}&eventId={event_id}&_={cache_buster}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
        tabs = data.get('layout', {}).get('tabs', {})
        available_tabs = []
        
        ignored_tabs = ['game-lines', 'popular', 'odds', 'same-game-parlay', 'quick-bets', 'half', 'quarter', 
                        '4th-quarter', '1st-quarter', '2nd-quarter', '3rd-quarter', 'total-parlays', 'team-props', 
                        'race-to', 'margin', 'parlays', 'teasers', 'featured', 'live-sgp', 'same-game-parlay™']

        for tab_id, tab_info in tabs.items():
            tab_title = tab_info.get('title', '')
            tab_name = tab_title.lower().replace(' ', '-')
            if tab_name in ignored_tabs:
                continue
            available_tabs.append({'name': tab_name, 'title': tab_title})
        return available_tabs
    except Exception as e:
        print(f"Error fetching tabs for event {event_id}: {e}")
        return []

def normalize_player_name(name):
    if not name: return "unknown_player"
    name = name.lower().strip()
    name = name.replace('.', '').replace("'", "")
    suffixes = [' jr', ' sr', ' ii', ' iii', ' iv', ' v']
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name

def normalize_prop_type(prop_name):
    if not prop_name: return "unknown_prop"
    prop_name_lower = prop_name.lower().strip()
    mapping = {
        'points': 'points', 'rebounds': 'rebounds', 'assists': 'assists', 'made threes': 'threes', '3-point': 'threes',
        'steals': 'steals', 'blocks': 'blocks', 'turnovers': 'turnovers',
        'pts + reb + ast': 'pra', 'pts + reb': 'pr', 'pts + ast': 'pa', 'reb + ast': 'ra', 'steals + blocks': 'stocks'
    }
    for key, val in mapping.items():
        if key in prop_name_lower: return val
    return prop_name_lower.replace(' ', '_')

def extract_team_name(logo_url):
    if not logo_url: return "Unknown Team"
    try:
        team_slug = logo_url.split('/')[-1].replace('.png', '').replace('_jersey', '')
        return ' '.join(word.capitalize() for word in team_slug.split('_'))
    except: return "Unknown Team"

def fetch_odds():
    print("🚀 Starting FanDuel Odds Fetch...")
    main_page = get_nba_main_page_data()
    if not main_page: return []

    upcoming_events = []
    attachments = main_page.get('attachments', {})
    events_data = attachments.get('events', {})
    
    # Parse Upcoming Games
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    for event_id, event in events_data.items():
        open_date = event.get('openDate')
        if not open_date: continue
        
        # Simple date check (UTC to Local approximation for filtering)
        if today_str in open_date or (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d') in open_date:
             upcoming_events.append(event)

    all_props = []

    for event in upcoming_events:
        event_id = event['eventId']
        game_name = event['name']
        print(f"  Processing Game: {game_name}")
        
        tabs = get_all_available_tabs(event_id)
        for tab in tabs:
            prop_data = get_player_props(event_id, tab['name'])
            if not prop_data: continue
            
            markets = prop_data.get('attachments', {}).get('markets', {})
            for market in markets.values():
                market_name = market.get('marketName', '')
                if " - " not in market_name: continue
                
                try:
                    player_name_raw, prop_type_raw = market_name.rsplit(' - ', 1)
                except: continue

                runners = market.get('runners', [])
                if len(runners) != 2: continue
                
                over_runner = next((r for r in runners if r.get('result', {}).get('type') == 'OVER'), None)
                under_runner = next((r for r in runners if r.get('result', {}).get('type') == 'UNDER'), None)
                
                if not over_runner or not under_runner: continue

                line = over_runner.get('handicap')
                over_odds = over_runner.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds')
                under_odds = under_runner.get('winRunnerOdds', {}).get('americanDisplayOdds', {}).get('americanOdds')
                
                if line is None or over_odds is None: continue

                prop_entry = {
                    "player": normalize_player_name(player_name_raw),
                    "team": extract_team_name(over_runner.get('secondaryLogo', '')),
                    "prop_type": normalize_prop_type(prop_type_raw),
                    "line": float(line),
                    "over_odds": int(over_odds),
                    "under_odds": int(under_odds),
                    "game": game_name,
                    "game_date": today_str, # Using scrape date as strict game date for now
                    "sportsbook": "fanduel"
                }
                all_props.append(prop_entry)
    
    print(f"✅ Finished. Collected {len(all_props)} props.")
    return all_props

if __name__ == "__main__":
    props = fetch_odds()
    df = pd.DataFrame(props)
    df.to_csv("fanduel_props.csv", index=False)
    print(json.dumps(props[:2], indent=2))
