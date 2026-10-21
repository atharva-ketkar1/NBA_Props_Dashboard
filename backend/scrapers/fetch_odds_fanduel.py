import pandas as pd
import requests
import json
import time
import random
import logging
import threading
from datetime import datetime, timezone
from dateutil import tz
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os

load_dotenv()

logger = logging.getLogger(__name__)


class FanDuelRequestError(Exception):
    """A proxy/upstream request failed.

    Raised instead of returning an empty result so callers can tell "FanDuel
    says this game has no props" apart from "we never got an answer". The two
    used to be indistinguishable, which let a single 429 look like a propless
    game -- and with early-exit scanning that would silently truncate the slate.
    """

# CONSTANTS
FANDUEL_PUBLIC_ACCESS_KEY = os.getenv("FANDUEL_PUBLIC_ACCESS_KEY")
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36',
    'Origin': 'https://sportsbook.fanduel.com',
    'Referer': 'https://sportsbook.fanduel.com/',
    'x-sportsbook-region': 'OH',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    'Pragma': 'no-cache',
    'Expires': '0',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9'
}

REQUEST_TIMEOUT = 15

# The Cloudflare Worker in utils/cloudflare_worker.js rate-limits per client IP
# (RATE_LIMIT_MAX, default 60 requests / 60s) and that budget is shared with every
# other scraper routed through it. FanDuel is per-event/per-tab, so it is the one
# scraper that can exhaust the window on its own -- hence the pacing below.
# The worker's window is 60 requests / 60s, so spacing must be >= 1.0s to stay
# under it. 1.1s leaves margin for DraftKings' ~15 requests, which run immediately
# before FanDuel in the pipeline and share the same per-IP bucket.
# Raising RATE_LIMIT_MAX on the worker is the better long-term fix; this default is
# chosen to be correct under the worker's current (unset, therefore 60) config.
REQUEST_DELAY_SECONDS = float(os.getenv("FANDUEL_REQUEST_DELAY_SECONDS", "1.1"))

# Stop scanning after this many consecutive games with no props. 2 rather than 1
# because a lone propless game is occasionally an anomaly (a postponed game, or a
# marquee game opened early while a nearer one is not up yet); needing two in a row
# costs one extra event scan and avoids truncating the slate on a fluke.
PROPLESS_STOP_STREAK = max(1, int(os.getenv("FANDUEL_PROPLESS_STOP_STREAK", "2")))

_session = None
_session_lock = threading.Lock()
_pace_lock = threading.Lock()
_last_request_at = 0.0


def _get_session():
    """Pooled session + retry on 429/5xx.

    Previously every call was a bare requests.get: a new TCP+TLS handshake each
    time and no retry at all, so a single 429 permanently dropped that tab's props.
    """
    global _session
    with _session_lock:
        if _session is not None:
            return _session
        session = requests.Session()
        session.headers.update(HEADERS)
        retry = Retry(
            total=3,
            backoff_factor=1.5,          # 1.5s, 3s, 6s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"],
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _session = session
        return _session


def _pace():
    """Space out requests so we stay inside the worker's per-IP window."""
    global _last_request_at
    if REQUEST_DELAY_SECONDS <= 0:
        return
    with _pace_lock:
        elapsed = time.monotonic() - _last_request_at
        wait = REQUEST_DELAY_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.15))
        _last_request_at = time.monotonic()


def _fetch_via_proxy(url):
    """Helper clearly route request via Cloudflare Worker if set."""
    _pace()
    session = _get_session()
    proxy_url = os.environ.get("PBPSTATS_PROXY_URL")
    if not proxy_url:
        logger.warning("PBPSTATS_PROXY_URL not set. Falling back to direct connection.")
        return session.get(url, timeout=REQUEST_TIMEOUT)
    return session.get(proxy_url, params={"url": url}, timeout=REQUEST_TIMEOUT)

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
        raise FanDuelRequestError(f"props fetch failed for event {event_id}: {e}") from e

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
        # Must raise, not return []. An empty list is a real answer ("no prop tabs
        # on this game"), and fetch_odds() uses that answer to stop scanning.
        raise FanDuelRequestError(f"tab fetch failed for event {event_id}: {e}") from e

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


def is_real_matchup(event):
    """True for an actual NBA game, excluding FanDuel's simulated-game feed.

    The NBA page mixes in a separate competition of simulated games named like
    "Los Angeles Lakers (HAWK) @ Boston Celtics (GODFATHER)". They tip every few
    minutes all day, carry no player props, and -- because they are chronologically
    nearest -- they land at the front of a start-time-ordered scan. Left in, they
    both waste the proxy's rate-limit budget and make the slate look propless.

    Three independent signals agree on the split (verified against a live payload:
    16 real vs 12 simulated), so all three are applied rather than hardcoding
    FanDuel's competitionId, which can rotate.
    """
    event = event or {}
    name = event.get('name', '')
    if ' @ ' not in name:          # futures, awards, season-long markets
        return False
    if '(' in name:                # simulated games carry a (NICKNAME) per team
        return False
    if event.get('statsAvailable') is False:
        return False
    return True


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
    
    matchups = [event for event in events_data.values() if is_real_matchup(event)]
    upcoming_events = [event for event in matchups if is_event_pregame(event)]

    excluded = len(events_data) - len(matchups)
    if excluded:
        logger.info(
            "Filtered %d non-game events (simulated games, futures, awards) | real_matchups=%d pregame=%d",
            excluded, len(matchups), len(upcoming_events),
        )
    if not matchups and events_data:
        # Loud, because silently returning zero props looks identical to "FanDuel
        # has no props yet" -- and this filter is exactly what would break if
        # FanDuel changed its naming or competition layout.
        logger.warning(
            "FanDuel returned %d events but none matched a real NBA matchup; "
            "the event filter may be stale.", len(events_data),
        )

    # FanDuel only posts player props a day or two ahead, so the slate is
    # chronologically front-loaded: once games stop having props, nothing further
    # out has them either. Scan in start-time order and stop after a run of
    # propless games rather than sweeping every pregame event FanDuel lists.
    # Events with an unparseable start time sort last so they are still scanned,
    # just never used as the basis for stopping.
    _far_future = datetime.max.replace(tzinfo=timezone.utc)
    upcoming_events.sort(
        key=lambda e: (
            extract_event_start_time(e) is None,
            extract_event_start_time(e) or _far_future,
        )
    )

    all_props = []
    propless_streak = 0
    scanned = 0
    failed_event_ids = []
    stopped_early = False

    for event in upcoming_events:
        if propless_streak >= PROPLESS_STOP_STREAK:
            stopped_early = True
            logger.info(
                "Stopping FanDuel scan: %d consecutive games with no props | remaining_unscanned=%d",
                propless_streak,
                len(upcoming_events) - scanned,
            )
            break

        event_id = event['eventId']
        game_name = event['name']
        logger.debug("Processing Game: %s", game_name)
        scanned += 1

        event_props = []
        discard_event = False
        request_failed = False
        tabs = []
        try:
            tabs = get_all_available_tabs(event_id)
        except FanDuelRequestError as e:
            request_failed = True
            failed_event_ids.append(str(event_id))
            logger.warning(
                "Incomplete FanDuel event scan | event=%s game=%s error=%s", event_id, game_name, e
            )
        for tab in tabs:
            if not is_event_pregame(event):
                discard_event = True
                break

            try:
                prop_data = get_player_props(event_id, tab['name'])
            except FanDuelRequestError as e:
                # One unreachable tab makes this event's coverage unknown, but the
                # other tabs are still worth collecting.
                request_failed = True
                if str(event_id) not in failed_event_ids:
                    failed_event_ids.append(str(event_id))
                logger.warning(
                    "Incomplete FanDuel tab scan | event=%s tab=%s error=%s", event_id, tab['name'], e
                )
                continue
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
            # A game that just tipped says nothing about whether later games have
            # props, so it neither extends nor resets the streak.
            continue

        all_props.extend(event_props)

        if request_failed:
            # Coverage unknown: never let a dropped request masquerade as evidence
            # that this game -- and therefore every game after it -- has no props.
            continue
        if event_props:
            propless_streak = 0
        else:
            propless_streak += 1
            logger.debug(
                "No props for %s (%s) | propless_streak=%d", game_name, event_id, propless_streak
            )

    logger.info(
        "[OK] FanDuel odds fetch complete | props=%d events_scanned=%d/%d stopped_early=%s incomplete_events=%d",
        len(all_props),
        scanned,
        len(upcoming_events),
        stopped_early,
        len(failed_event_ids),
    )
    if failed_event_ids:
        # Surfaced as WARN because props=N previously looked like a clean result
        # even when most events had silently failed.
        logger.warning(
            "FanDuel coverage incomplete for %d event(s): %s",
            len(failed_event_ids),
            ", ".join(failed_event_ids[:10]),
        )
    return all_props

if __name__ == "__main__":
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    props = fetch_odds()
    df = pd.DataFrame(props)
    df.to_csv("fanduel_props.csv", index=False)
    logger.info(json.dumps(props[:2], indent=2))
