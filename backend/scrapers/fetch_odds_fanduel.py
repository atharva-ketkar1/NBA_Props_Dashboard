import pandas as pd
import requests
import json
import time
import logging
from datetime import datetime, timezone
from dateutil import tz
from dotenv import load_dotenv
import os

load_dotenv()

logger = logging.getLogger(__name__)

# CONSTANTS
FANDUEL_PUBLIC_ACCESS_KEY = os.getenv("FANDUEL_PUBLIC_ACCESS_KEY")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Origin': 'https://sportsbook.fanduel.com',
    'Referer': 'https://sportsbook.fanduel.com/',
    'x-sportsbook-region': 'OH',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9'
}

def _fetch_via_proxy(url):
    """Helper clearly route request via Cloudflare Worker if set."""
    proxy_url = os.environ.get("PBPSTATS_PROXY_URL")
    if not proxy_url:
        logger.warning("PBPSTATS_PROXY_URL not set. Falling back to direct connection.")
        return requests.get(url, headers=HEADERS, timeout=15)
    return requests.get(proxy_url, params={"url": url}, headers=HEADERS, timeout=15)

def get_nba_main_page_data():
    """Fetches the main NBA page."""
    url = f"https://api.sportsbook.fanduel.com/sbapi/content-managed-page?page=CUSTOM&customPageId=nba&pbHorizontal=false&_ak={FANDUEL_PUBLIC_ACCESS_KEY}&timezone=America%2FNew_York"
    try:
        #response = requests.get(url, headers=HEADERS, timeout=15)
        response = _fetch_via_proxy(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Error fetching NBA main page: %s", e)
        return None

def get_player_props(event_id, prop_tab_name):
    """Fetches specific prop tab data."""
    cache_buster = int(time.time())
    url = f"https://api.sportsbook.fanduel.com/sbapi/event-page?_ak={FANDUEL_PUBLIC_ACCESS_KEY}&eventId={event_id}&tab={prop_tab_name}&_={cache_buster}"
    try:
        response = _fetch_via_proxy(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error("Error fetching props for event %s: %s", event_id, e)
        return None

def get_all_available_tabs(event_id):
    """Fetches available tabs for an event."""
    cache_buster = int(time.time())
    url = f"https://api.sportsbook.fanduel.com/sbapi/event-page?_ak={FANDUEL_PUBLIC_ACCESS_KEY}&eventId={event_id}&_={cache_buster}"
    try:
        response = _fetch_via_proxy(url)
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
        logger.error("Error fetching tabs for event %s: %s", event_id, e)
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

TEAM_MAP = {
    'hawks': 'ATL', 'celtics': 'BOS', 'nets': 'BKN', 'hornets': 'CHA', 'bulls': 'CHI',
    'cavaliers': 'CLE', 'mavericks': 'DAL', 'nuggets': 'DEN', 'pistons': 'DET', 'warriors': 'GSW',
    'rockets': 'HOU', 'pacers': 'IND', 'clippers': 'LAC', 'lakers': 'LAL', 'grizzlies': 'MEM',
    'heat': 'MIA', 'bucks': 'MIL', 'timberwolves': 'MIN', 'pelicans': 'NOP', 'knicks': 'NYK',
    'thunder': 'OKC', 'magic': 'ORL', 'sixers': 'PHI', '76ers': 'PHI', 'suns': 'PHX',
    'blazers': 'POR', 'trail_blazers': 'POR', 'kings': 'SAC', 'spurs': 'SAS', 'raptors': 'TOR',
    'jazz': 'UTA', 'wizards': 'WAS'
}

def extract_team_name(logo_url):
    """
    Extracts team abbreviation from logo URL or team name.
    Returns 'UNK' if not found.
    """
    if not logo_url: return "UNK"
    try:
        # Example URL: .../teams/cleveland_cavaliers.png or .../teams/cavaliers.png
        # We try to grab the last part
        slug = logo_url.split('/')[-1].replace('.png', '').replace('_jersey', '').lower()

        # Parse slug parts first so "hornets" does not get misread as "nets".
        parts = slug.split('_')
        for part in parts:
            if part in TEAM_MAP:
                return TEAM_MAP[part]

        # Fall back to full-slug substring matching for non-standard formats.
        for key, tricode in TEAM_MAP.items():
            if key in slug:
                return tricode

        return "UNK"
    except: 
        return "UNK"


def extract_event_start_time(event):
    for field in ('startDate', 'openDate', 'eventDate', 'startTime'):
        raw = event.get(field, '')
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz.gettz('America/New_York'))
            return dt.astimezone(timezone.utc)
        except Exception as e:
            logger.warning("Could not parse %s '%s': %s", field, raw, e)
    return None


def extract_event_game_date(event):
    start_dt = extract_event_start_time(event)
    if start_dt is None:
        return ''
    return start_dt.astimezone(tz.gettz('America/New_York')).strftime('%Y-%m-%d')


def is_event_pregame(event, now=None):
    if bool((event or {}).get("inPlay", False)):
        return False

    start_dt = extract_event_start_time(event or {})
    if start_dt is None:
        return True

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return current_time.astimezone(timezone.utc) < start_dt


def payload_marks_event_in_play(payload, event_id):
    events = (payload or {}).get('attachments', {}).get('events', {})
    if isinstance(events, dict):
        event_items = events.items()
    elif isinstance(events, list):
        event_items = enumerate(events)
    else:
        event_items = []

    for key, event in event_items:
        if str(key) != str(event_id) and str((event or {}).get('eventId', '')) != str(event_id):
            continue
        return bool((event or {}).get('inPlay', False))
    return False

def fetch_odds():
    logger.info("[RUN] FanDuel odds fetch")
    main_page = get_nba_main_page_data()
    if not main_page: return []

    upcoming_events = []
    attachments = main_page.get('attachments', {})
    events_data = attachments.get('events', {})
    
    upcoming_events = [
        event for event in events_data.values()
        if is_event_pregame(event)
        and ' @ ' in event.get('name', '')  # only real matchup games
    ]

    all_props = []

    for event in upcoming_events:
        event_id = event['eventId']
        game_name = event['name']
        logger.debug("Processing Game: %s", game_name)

        event_props = []
        discard_event = False
        tabs = get_all_available_tabs(event_id)
        for tab in tabs:
            if not is_event_pregame(event):
                discard_event = True
                break

            prop_data = get_player_props(event_id, tab['name'])
            if not prop_data: continue

            if payload_marks_event_in_play(prop_data, event_id) or not is_event_pregame(event):
                discard_event = True
                break

            markets = prop_data.get('attachments', {}).get('markets', {})
            if any(market.get('inPlay', False) for market in markets.values()):
                discard_event = True
                break

            for market in markets.values():
                market_name = market.get('marketName', '')
                if " - " not in market_name: continue
                
                try:
                    player_name_raw, prop_type_raw = market_name.rsplit(' - ', 1)
                except: continue

                prop_type_lower = prop_type_raw.lower()
                exclusion_words = ['quarter', 'qtr', 'half', '1h', '2h', 'alt', 'alternate']
                
                if any(x in prop_type_lower for x in exclusion_words):
                    continue

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
                    "game_date": extract_event_game_date(event),
                    "sportsbook": "fanduel",
                    "inPlay": False,
                }
                event_props.append(prop_entry)

        if discard_event or not is_event_pregame(event):
            logger.info("Discarded FanDuel event after tip/live detection | event=%s game=%s", event_id, game_name)
            continue

        all_props.extend(event_props)
    
    logger.info("[OK] FanDuel odds fetch complete | props=%d", len(all_props))
    return all_props

if __name__ == "__main__":
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    props = fetch_odds()
    df = pd.DataFrame(props)
    df.to_csv("fanduel_props.csv", index=False)
    logger.info(json.dumps(props[:2], indent=2))
