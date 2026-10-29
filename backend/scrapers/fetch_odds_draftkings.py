import time
import random
import re
import logging
from datetime import datetime
from dateutil import tz
import pandas as pd
import os
import urllib.parse
from dotenv import load_dotenv

import os as _hc_os, sys as _hc_sys
_hc_sys.path.insert(0, _hc_os.path.join(_hc_os.path.dirname(_hc_os.path.abspath(__file__)), ".."))
from utils.http_client import proxied_get_json, CircuitOpen, UpstreamFailed, WorkerRejected, ProxyNotConfigured  # noqa: E402  (needs the path insert above)
from utils.book_toggles import book_is_paused  # noqa: E402
from utils.scrape_cadence import should_scrape_dk, record_requests  # noqa: E402

# Distinguishes "backed off, do not ask again this run" from "this one category
# failed", which is a None. Neither is an exception to the caller.
CIRCUIT_OPEN = object()

load_dotenv()

logger = logging.getLogger(__name__)

# CONSTANTS
DK_API_BASE = "https://sportsbook-nash.draftkings.com/sites/US-IL-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets"
LEAGUE_ID = "42648" # NBA

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
    "Accept": "*/*",
    "Cache-Control": "no-cache, no-store, must-revalidate",
}

PLAYER_PROP_CATEGORIES = {
    'points': '12488',
    'threes': '12497',
    'rebounds': '12492',
    'assists': '12495',
    'pra': '5001',
    'pr': '9976',
    'pa': '9973',
    'ra': '9974',
    'steals': '13508',
    'blocks': '13780',
    'stocks': '13781'
}

def normalize_player_name(name):
    if not name: return "unknown_player"
    name = name.lower().strip()
    name = name.replace('.', '').replace("'", "")
    suffixes = [' jr', ' sr', ' ii', ' iii', ' iv', ' v']
    for suffix in suffixes:
        if name.endswith(suffix): name = name[:-len(suffix)]
    return name

def parse_odds(odds_val):
    """
    Safely converts odds to integer, handling 'EVEN' and weird minus signs.
    """
    if odds_val is None: return 0
    
    if isinstance(odds_val, str):
        # 1. Handle "EVEN" (which is +100)
        if odds_val.upper() == "EVEN": return 100
        
        # 2. SANITIZE: Replace unicode minus signs (which break Python) with standard hyphens
        # DraftKings often sends \u2212 instead of -
        clean_val = odds_val.replace("−", "-").replace("\u2212", "-").replace("\u2013", "-").replace("+", "")
        
        try:
            return int(float(clean_val))
        except:
            return 0
            
    return int(odds_val)

def format_odds_for_display(odds_int):
    """
    Helper to show the user '+105' instead of '105'.
    (Used only for printing/UI, not for storage)
    """
    if odds_int > 0:
        return f"+{odds_int}"
    return str(odds_int)

def fetch_category(subcategory_id, prop_label):
    timestamp = int(time.time() * 1000)
    url = (
        f"{DK_API_BASE}?isBatchable=false&templateVars={LEAGUE_ID}%2C{subcategory_id}"
        f"&eventsQuery=%24filter%3DleagueId%20eq%20%27{LEAGUE_ID}%27%20AND%20clientMetadata%2FSubcategories%2Fany%28s%3A%20s%2FId%20eq%20%27{subcategory_id}%27%29"
        f"&marketsQuery=%24filter%3DclientMetadata%2FsubCategoryId%20eq%20%27{subcategory_id}%27%20AND%20tags%2Fall%28t%3A%20t%20ne%20%27SportcastBetBuilder%27%29&include=Events&entity=events"
        f"&_={timestamp}"
    )

    try:
        # proxied_get_json retries 403s with a fresh worker-side User-Agent
        # instead of dropping the category on the first stale-UA response. The
        # breaker inside it stops that retrying once the host is refusing this
        # egress outright, where re-rolling the UA cannot help.
        return proxied_get_json(url, headers=HEADERS, label=f"draftkings-{prop_label}")
    except CircuitOpen as e:
        logger.warning("Backing off %s: %s", prop_label, e)
        return CIRCUIT_OPEN
    except (UpstreamFailed, WorkerRejected, ProxyNotConfigured) as e:
        logger.error("Error fetching %s: %s", prop_label, e)
        return None

def parse_dk_data(data, prop_type_key):
    if not data: return []
    
    events = data.get('events', [])
    markets = data.get('markets', [])
    selections = data.get('selections', [])
    
    event_map = {e['id']: e for e in events}
    
    # Map Selections by MarketId
    selections_by_market = {}
    for sel in selections:
        m_id = sel.get('marketId')
        if not m_id: continue
        if m_id not in selections_by_market:
            selections_by_market[m_id] = []
        selections_by_market[m_id].append(sel)

    parsed = []
    
    for market in markets:
        m_id = market.get('id')
        market_outcomes = selections_by_market.get(m_id, [])
        
        if len(market_outcomes) < 2: continue

        # Identify Over/Under
        over_sel = None
        under_sel = None
        
        for sel in market_outcomes:
            label = sel.get('label', '').lower()
            outcome_type = sel.get('outcomeType', '').lower()
            
            if 'over' in label or 'over' in outcome_type: over_sel = sel
            elif 'under' in label or 'under' in outcome_type: under_sel = sel
        
        if not over_sel or not under_sel: continue

        # --- DYNAMIC PLAYER NAME EXTRACTION ---
        player_name = ""
        
        # 1. Try to pull exact name from the 'participants' array (New DraftKings Structure)
        participants = over_sel.get('participants', [])
        if not participants:
            participants = under_sel.get('participants', [])
            
        for p in participants:
            if p.get('type', '') == 'Player':
                player_name = p.get('name', '')
                break
                
        # 2. Fallback: Parse from Market Name (Old DraftKings Structure)
        if not player_name:
            market_name = market.get('name', '')
            remove_terms = ["Points", "Rebounds", "Assists", "Threes", "Three", "Pointers", "Pointer", "3-Point", "Steals", "Blocks", "Turnovers", "O/U", "+", "Made"]
            tokens = market_name.split()
            player_tokens = []
            for t in tokens:
                if t in remove_terms or t == "O/U" or t.isdigit(): break
                player_tokens.append(t)
            player_name = " ".join(player_tokens).strip()

        if not player_name: continue # Skip if we still can't figure out who the player is

        # Event Info
        event_id = market.get('eventId')
        event = event_map.get(event_id, {})
        game_name = event.get('name', 'Unknown')
        
        start_date = event.get('startEventDate')
        game_date = datetime.now().strftime('%Y-%m-%d')
        if start_date:
            try:
                dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                game_date = dt.astimezone(tz.gettz('America/New_York')).strftime('%Y-%m-%d')
            except: pass

        # Odds Parsing
        line = over_sel.get('points')
        if line is None: continue 

        raw_over = over_sel.get('displayOdds', {}).get('american')
        raw_under = under_sel.get('displayOdds', {}).get('american')

        over_int = parse_odds(raw_over)
        under_int = parse_odds(raw_under)

        parsed.append({
            "player": normalize_player_name(player_name),
            "prop_type": prop_type_key,
            "line": float(line),
            "over_odds": over_int,
            "under_odds": under_int,
            "game": game_name,
            "game_date": game_date,
            "sportsbook": "draftkings"
        })
        
    return parsed

def fetch_dk_odds(force=False, deadline=None):
    """Scrape DraftKings player props.

    `force` skips the cadence gate for the pre-tip closing-line capture and the
    once-a-day pipeline run. A closing line captured late is not a closing
    line, so that path is never delayed or throttled -- only the intraday
    snapshot, which is where the volume actually accumulates.

    `deadline` is a `time.monotonic()` value after which no further category is
    started, and whatever has been collected is returned. It exists because the
    caller's `future.result(timeout=...)` cannot stop this function: a Python
    thread is not killable, so an external timeout abandoned the *result* while
    the requests carried on. Observed on 2026-10-28 -- the caller logged a
    timeout at 19:52:10 and the scrape kept asking a refusing host until
    19:53:57. Only a deadline checked in here can actually stop the traffic.

    Partial rows are returned rather than dropped. Fewer categories means fewer
    candidates downstream, not wrong prices: every line returned is one the
    book was quoting just now.
    """
    # Gated here rather than at each caller so run_pipeline, the intraday
    # refresh and the pre-tip capture are all covered by the one switch.
    # Returning empty is what every caller already handles when a scrape
    # fails, so a paused book costs nothing downstream beyond having no rows.
    if book_is_paused("dk"):
        logger.warning(
            "[SKIP] DraftKings odds fetch paused via EDGE_SCORE_PAUSED_BOOKS; "
            "returning no rows so stale lines are not refreshed"
        )
        return []

    # Decided before the first request, because the thing that drew the block
    # was total daily volume arriving on a fixed clock -- not any individual
    # request. The same empty-list contract as the pause above.
    expected = len(PLAYER_PROP_CATEGORIES)
    decision = should_scrape_dk(force=force, expected_requests=expected)
    if not decision.allowed:
        logger.info("[SKIP] DraftKings odds fetch | %s", decision.reason)
        return []

    if decision.jitter_seconds > 0:
        # Breaks the :02-:04-past-the-boundary arrival clustering.
        time.sleep(decision.jitter_seconds)

    logger.info("[RUN] DraftKings odds fetch | cadence=%s budget=%d/%d jitter=%.0fs",
                decision.window, decision.spent, decision.budget,
                decision.jitter_seconds)
    all_props = []
    spent_requests = 0
    failed_categories = 0

    for prop_key, cat_id in PLAYER_PROP_CATEGORIES.items():
        # Checked between categories, so a single in-flight request can still
        # overshoot by up to its own timeout. The caller's backstop covers that
        # margin; what matters here is that no NEW request is started.
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "[PARTIAL] DraftKings odds fetch hit its deadline | props=%d "
                "categories=%d/%d -- returning what the book already quoted",
                len(all_props), spent_requests, expected)
            record_requests(-(expected - spent_requests))
            return all_props

        time.sleep(random.uniform(0.3, 0.8))
        data = fetch_category(cat_id, prop_key)
        if data is CIRCUIT_OPEN:
            # The block is per-host, so the remaining ten categories would each
            # raise the same thing. Abandon the slate in one line rather than
            # eleven, and leave the cooldown to decide when to look again.
            logger.warning(
                "[SKIP] DraftKings egress is blocked upstream; abandoning the "
                "remaining categories until the backoff expires"
            )
            # Give back the budget we reserved but never spent, so a blocked
            # day does not also exhaust tomorrow's first look.
            record_requests(-(expected - spent_requests))
            return []
        spent_requests += 1
        # fetch_category returns None for a category that errored out, as
        # opposed to a category that answered with nothing in it.
        if data is None:
            failed_categories += 1
        props = parse_dk_data(data, prop_key)

        # print(f"  Found {len(props)} props for '{prop_key}'")
        all_props.extend(props)

    if all_props:
        logger.info("[OK] DraftKings odds fetch complete | props=%d | failed_categories=%d",
                    len(all_props), failed_categories)
    elif failed_categories:
        # Nothing came back and requests were refused: the run is a wash.
        # Logging that as [OK] is how five hours of outage on 2026-10-28 read
        # as success in the cron log -- nine consecutive "complete | props=0"
        # lines. The preserved CSV keeps serving its last real rows, which is
        # the odds-csv layer's call, not a reason to call this complete.
        logger.warning(
            "[EMPTY] DraftKings odds fetch returned no rows | %d/%d categories "
            "failed -- existing CSV is left in place, so its lines are now older",
            failed_categories, spent_requests)
    else:
        # Every category answered, the board was just empty. A dark slate is
        # not a failure, so this stays an INFO.
        logger.info(
            "[OK] DraftKings odds fetch complete | props=0 | "
            "every category answered, the board is empty")

    # --- DEBUG: Show user the formatted output with PLUS SIGNS ---
    if len(all_props) > 10:
        sample = all_props[10]
        logger.debug("SAMPLE OUTPUT (With Formatting):")
        logger.debug("Player: %s", sample['player'])
        logger.debug("Line:   %s", sample['line'])
        # Use our helper to show the plus sign
        logger.debug("Over:   %s", format_odds_for_display(sample['over_odds']))
        logger.debug("Under:  %s", format_odds_for_display(sample['under_odds']))
    
    return all_props

if __name__ == "__main__":
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Run by hand means "I want the data now", so skip the cadence gate rather
    # than printing an empty frame because no game is near.
    df = pd.DataFrame(fetch_dk_odds(force=True))
    df.to_csv("draftkings_odds.csv", index=False)
