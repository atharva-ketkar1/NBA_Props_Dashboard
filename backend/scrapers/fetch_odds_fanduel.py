import pandas as pd
import json
import re
import time
import logging
import threading
from datetime import datetime, timezone
from dateutil import tz
from dotenv import load_dotenv
import os

from utils import http_client
from utils import scrape_cadence

load_dotenv()

logger = logging.getLogger(__name__)


class FanDuelRequestError(Exception):
    """A proxy/upstream request failed.

    Raised instead of returning an empty result so callers can tell "FanDuel
    says this game has no props" apart from "we never got an answer". The two
    used to be indistinguishable, which let a single 429 look like a propless
    game -- and with early-exit scanning that would silently truncate the slate.
    """


class FanDuelDeadlineReached(Exception):
    """The cooperative deadline passed; stop issuing requests.

    Caught inside `fetch_odds`, which then returns the props already collected.
    Not a `FanDuelRequestError`: nothing failed, there was simply no time left.
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

# Must exceed the worker's own worst case: it allows 2 upstream attempts at 15s
# each, so anything under ~30s cancels work that was about to succeed. The old
# value here was 15s, which could only ever have been cutting the worker off
# mid-retry.
REQUEST_TIMEOUT = float(os.getenv("FANDUEL_REQUEST_TIMEOUT", "60"))

# Stop scanning after this many consecutive games with no props. 2 rather than 1
# because a lone propless game is occasionally an anomaly (a postponed game, or a
# marquee game opened early while a nearer one is not up yet); needing two in a row
# costs one extra event scan and avoids truncating the slate on a fluke.
PROPLESS_STOP_STREAK = max(1, int(os.getenv("FANDUEL_PROPLESS_STOP_STREAK", "2")))

# How long an event's tab list stays good. The list is which prop markets exist
# for a game, and it does not change minute to minute -- but it was refetched on
# every 5-minute tick, one request per event. See _tab_cache below.
TAB_LIST_TTL_SECONDS = float(os.getenv("FANDUEL_TAB_LIST_TTL_SECONDS", "3600"))

# How long a tab that returned no usable props is skipped for. Deliberately much
# shorter than the tab-list TTL: an empty tab at noon is often a market FanDuel
# has not posted yet, so this has to re-check well before tip.
EMPTY_TAB_TTL_SECONDS = float(os.getenv("FANDUEL_EMPTY_TAB_TTL_SECONDS", "1200"))

# The cache has to live on disk, not in memory. master_cron runs as a FRESH
# PROCESS every five minutes (see the crontab: `*/5 * * * * ... master_cron.py`),
# so a module-global dict is empty on every tick and would never save a single
# request -- which is the entire point of caching the tab list. Same reason
# utils/scrape_cadence.py keeps its state in logs/scrape_cadence.json, and the
# file layout here deliberately mirrors it.
#
# Expiries are wall-clock `time.time()`, not `time.monotonic()`: monotonic is
# only comparable within one process, so a monotonic deadline written by the
# 10:05 tick is meaningless to the 10:10 tick.
_cache_lock = threading.Lock()


def _cache_path():
    return os.environ.get("FANDUEL_TAB_CACHE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "fanduel_tab_cache.json",
    )


def _cache_inert():
    """True when this process must not read or write the real cache file.

    Same contract as `health_ledger._inert()`: under pytest, with no explicit
    path set, the cache is a no-op. A test that wants to exercise it sets
    FANDUEL_TAB_CACHE_PATH, so no test can poison a real slate's cache and no
    test depends on one left behind by another.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get(
        "FANDUEL_TAB_CACHE_PATH")


def _cache_day():
    """ET date, so the cache cannot carry across a slate boundary."""
    return datetime.now(tz.gettz("America/New_York")).date().isoformat()


def _load_cache():
    """Read the cache, discarding it wholesale if it is from another day.

    Any read problem returns an empty cache rather than raising: a missing or
    corrupt cache must cost requests, never a scrape.
    """
    if _cache_inert():
        return {"day": _cache_day(), "tabs": {}, "empty": {}}
    try:
        with open(_cache_path(), "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or state.get("day") != _cache_day():
        return {"day": _cache_day(), "tabs": {}, "empty": {}}
    state.setdefault("tabs", {})
    state.setdefault("empty", {})
    return state


def _save_cache(state):
    if _cache_inert():
        return
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write-then-rename: a tick killed mid-write must not leave a truncated
        # file that the next tick reads as "no cache" forever.
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except OSError as error:
        logger.warning("Could not persist FanDuel tab cache: %s", error)


def clear_caches():
    """Drop the tab-list and empty-tab caches.

    Used by the pre-tip closing capture, which must not inherit any skip
    decision made hours earlier, and by tests.
    """
    with _cache_lock:
        _save_cache({"day": _cache_day(), "tabs": {}, "empty": {}})


# Nominal reservation, corrected to the real count when the run finishes.
# A measured 10-game slate issues ~81 requests (1 main page + 10 events x
# (1 tab list + ~7 tabs)); 90 reserves a little above that so a busier slate
# does not overspend before the correction lands.
EXPECTED_REQUESTS_PER_RUN = int(os.getenv("FANDUEL_EXPECTED_REQUESTS", "90"))

_request_count_lock = threading.Lock()
_request_count = 0


def _expected_request_count():
    return max(1, EXPECTED_REQUESTS_PER_RUN)


def _reset_request_count():
    global _request_count
    with _request_count_lock:
        _request_count = 0


def _note_request():
    global _request_count
    with _request_count_lock:
        _request_count += 1


def requests_made():
    with _request_count_lock:
        return _request_count


def _fetch_via_proxy(url):
    """GET `url` through the one hardened HTTP path.

    Everything this used to do by hand, utils/http_client does better and with
    measured justification in its docstring: it retries 403 so the worker draws a
    fresh User-Agent (a stale UA produces intermittent 403s with p~0.25, and the
    worker itself does not retry them), respects Retry-After, backs off with full
    jitter, and widens the pacing for ALL subsequent traffic on any 429/403 while
    walking it back after sustained clean responses.

    The decisive reason, though, is that the pace lock is *shared*. FanDuel and
    DraftKings are submitted to a ThreadPoolExecutor together by
    cron_closing_lines.scrape_and_shape_odds -- they run concurrently, not
    sequentially as the old comment here claimed -- and each used to keep its own
    1.1s clock. Two independent clocks against one 60-requests/60s worker bucket
    is ~109/min: the aggregate was roughly double the ceiling, and nothing could
    see it because neither half knew about the other. One governor makes the
    total correct by construction.

    Raises FanDuelRequestError for every failure mode, so callers keep telling
    "no answer" apart from "no props" exactly as before.
    """
    _note_request()
    try:
        return http_client.proxied_get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            label="fanduel",
        )
    except http_client.ProxyNotConfigured as error:
        # Not retryable and not FanDuel's fault: a direct connection from this
        # host is a guaranteed 403 that also advertises the egress IP, so there
        # is deliberately no fallback path.
        raise FanDuelRequestError(f"proxy not configured: {error}") from error
    except Exception as error:
        raise FanDuelRequestError(str(error)) from error

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

def get_all_available_tabs(event_id, *, use_cache=True):
    """Fetches available tabs for an event, from cache when it is still good.

    Which prop markets a game has does not change between 5-minute ticks, but
    this was one request per event per tick -- 10 of ~81 on a typical slate, for
    an answer that was already known.

    `use_cache=False` forces a refetch, which is what the pre-tip closing capture
    passes: closing-line completeness is never traded for request volume.
    """
    if use_cache:
        with _cache_lock:
            entry = _load_cache()["tabs"].get(str(event_id))
        if entry and float(entry.get("expires", 0)) > time.time():
            return list(entry.get("tabs") or [])

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

        # Only a successful fetch is cached. A failure must stay uncached so the
        # next tick retries: caching it would turn one bad response into an
        # hour of a game looking propless, which is the early-exit scan's
        # worst input.
        with _cache_lock:
            state = _load_cache()
            state["tabs"][str(event_id)] = {
                "expires": time.time() + TAB_LIST_TTL_SECONDS,
                "tabs": list(available_tabs),
            }
            _save_cache(state)
        return available_tabs
    except FanDuelRequestError:
        raise
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

def _tricode_from_slug(slug):
    """Resolves a lowercased team slug to a tricode, or None.

    Whole words are checked before substrings, which is what keeps "hornets"
    from being misread as "nets" -- TEAM_MAP iterates with 'nets' ahead of
    'hornets', so the substring pass alone would hand Charlotte to Brooklyn.
    """
    if not slug:
        return None

    for part in re.split(r'[^a-z0-9]+', slug):
        if part in TEAM_MAP:
            return TEAM_MAP[part]

    # Fall back to substring matching for non-standard formats (and for the
    # multi-word 'trail_blazers' key, which no single word can match).
    for key, tricode in TEAM_MAP.items():
        if key in slug:
            return tricode

    return None


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
        return _tricode_from_slug(slug) or "UNK"
    except:
        return "UNK"


def extract_team_options(game_name):
    """Both clubs in a matchup label like "Utah Jazz @ Minnesota Timberwolves".

    FanDuel omits `secondaryLogo` on some runners -- 79 of 511 rows on
    2026-11-08, and per-player rather than per-market, so it takes out every
    prop for that player at once. extract_team_name can only return UNK there,
    which strips the row of the team the matcher's fuzzy tiers need: tier 3
    requires team agreement outright, and tier 2 falls back to an unverified
    best-name guess. The matchup still names both clubs, so hand them over as
    options. That is what resolves "Cam Johnson", whose
    fuzz.ratio against the roster's "Cameron Johnson" is 84.6 -- just under the
    85 tier-2 cutoff, leaving the team-checked tier as his only route.
    """
    if not game_name:
        return []

    options = []
    for side in str(game_name).split('@'):
        tricode = _tricode_from_slug(side.strip().lower())
        if tricode and tricode not in options:
            options.append(tricode)
    return options


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

def fetch_odds(*, deadline=None, force=False):
    """Scrape FanDuel player props for the upcoming slate.

    `deadline` is a `time.monotonic()` value after which no NEW request is
    started; whatever has been collected is returned instead. This mirrors
    DraftKings' contract. Before it existed, the caller's only protection was
    `f2.result(timeout=120)`, which *abandons the whole result* -- at ~90s for a
    10-game slate, a 35% slowdown silently discarded all ~838 props. Partial
    rows beat none: a prop whose line was captured is still a usable quote.

    `force` bypasses the tab/empty-tab caches, for the pre-tip closing capture
    where completeness matters more than request volume.
    """
    # Reserved before the first request, because what blocks a book is total
    # daily volume, not any individual request. `force` is the pre-tip capture,
    # which still respects the ceiling -- same as DraftKings, where the budget is
    # the one guard that survives `force`.
    expected = _expected_request_count()
    decision = scrape_cadence.check_budget("FD", expected)
    if not decision.allowed:
        logger.warning("[SKIP] FanDuel odds fetch | %s", decision.reason)
        # Empty list, matching the paused-book contract: no rows means nothing
        # refreshes, rather than stale lines being re-stamped as current.
        return []

    logger.info("[RUN] FanDuel odds fetch | force=%s pace=%.1fs budget=%d/%d",
                force, http_client.current_pace(),
                decision.spent, decision.budget)
    _reset_request_count()
    if force:
        clear_caches()

    def _out_of_time():
        return deadline is not None and time.monotonic() >= deadline

    main_page = get_nba_main_page_data()
    if not main_page:
        # Return the unspent reservation: one failed request must not cost the
        # day 90 of its ceiling, or a flaky morning would silence the evening.
        scrape_cadence.record_book_requests("FD", requests_made() - expected)
        return []

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
    hit_deadline = False
    skipped_empty_tabs = 0

    # Loaded once and written back once, rather than touching the file per tab:
    # a 10-game slate iterates ~70 tabs, and this is a cache, not a ledger.
    with _cache_lock:
        empty_tabs = dict(_load_cache()["empty"])

    for event in upcoming_events:
        if propless_streak >= PROPLESS_STOP_STREAK:
            stopped_early = True
            logger.info(
                "Stopping FanDuel scan: %d consecutive games with no props | remaining_unscanned=%d",
                propless_streak,
                len(upcoming_events) - scanned,
            )
            break

        if _out_of_time():
            hit_deadline = True
            logger.warning(
                "[PARTIAL] FanDuel odds fetch hit its deadline | props=%d "
                "events=%d/%d -- returning what the book already quoted",
                len(all_props), scanned, len(upcoming_events),
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
            tabs = get_all_available_tabs(event_id, use_cache=not force)
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

            if _out_of_time():
                hit_deadline = True
                request_failed = True   # coverage for this event is unknown
                if str(event_id) not in failed_event_ids:
                    failed_event_ids.append(str(event_id))
                logger.warning(
                    "[PARTIAL] FanDuel deadline reached mid-event | event=%s "
                    "props=%d", event_id, len(all_props) + len(event_props),
                )
                break

            # A tab that produced nothing usable recently is skipped, on a much
            # shorter TTL than the tab list itself. Alt-line and quarter/half
            # tabs are filtered out downstream anyway, so they cost a full
            # request to yield zero every single tick.
            tab_key = f"{event_id}|{tab['name']}"
            if not force and float(empty_tabs.get(tab_key, 0)) > time.time():
                skipped_empty_tabs += 1
                continue

            tab_yield_start = len(event_props)
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

                team = extract_team_name(over_runner.get('secondaryLogo', ''))

                prop_entry = {
                    "player": normalize_player_name(player_name_raw),
                    "team": team,
                    # Only as a substitute for a missing team, never alongside a
                    # known one: _required_teams unions context and options, so
                    # volunteering the opponent on a row we already placed would
                    # widen the check and weaken the wrong-player guard (a
                    # "Jaden Smith" row could then take Jalen Smith's id off the
                    # other club -- see tests/test_player_matcher.py).
                    "team_options": extract_team_options(game_name) if team == "UNK" else [],
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

            # Record the verdict for this tab. Only a tab that was actually
            # fetched and returned nothing usable gets marked -- a tab skipped
            # by the cache never reaches here, so its TTL cannot be refreshed
            # indefinitely by its own absence. It expires and gets re-checked.
            if len(event_props) == tab_yield_start:
                empty_tabs[tab_key] = time.time() + EMPTY_TAB_TTL_SECONDS
            else:
                empty_tabs.pop(tab_key, None)

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

    # Drop expired entries on the way out so the file cannot grow without bound
    # across a long slate day.
    now = time.time()
    with _cache_lock:
        state = _load_cache()
        state["empty"] = {k: v for k, v in empty_tabs.items() if float(v) > now}
        _save_cache(state)

    # Settle the reservation against what was actually spent. The caching below
    # is the whole reason this matters: a run that skips 20 tabs must return
    # those 20 to the day's budget, or the ceiling would punish the saving.
    actual = requests_made()
    scrape_cadence.record_book_requests("FD", actual - expected)

    logger.info(
        "[%s] FanDuel odds fetch complete | props=%d events_scanned=%d/%d "
        "stopped_early=%s incomplete_events=%d requests=%d "
        "skipped_empty_tabs=%d pace=%.1fs",
        "PARTIAL" if hit_deadline else "OK",
        len(all_props),
        scanned,
        len(upcoming_events),
        stopped_early,
        len(failed_event_ids),
        actual,
        skipped_empty_tabs,
        http_client.current_pace(),
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
