import requests
import time
import random
import re
from datetime import datetime
from dateutil import tz

# CONSTANTS
DK_API_BASE = "https://sportsbook-nash.draftkings.com/sites/US-OH-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets"
LEAGUE_ID = "42648" # NBA

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
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
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  ❌ Error fetching {prop_label}: {e}")
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

        # Extract Name
        market_name = market.get('name', '')
        remove_terms = ["Points", "Rebounds", "Assists", "Threes", "Three", "Pointers", "Pointer", "3-Point", "Steals", "Blocks", "Turnovers", "O/U", "+", "Made"]
        clean_name = market_name
        tokens = clean_name.split()
        player_tokens = []
        for t in tokens:
            if t in remove_terms or t == "O/U" or t.isdigit(): break
            player_tokens.append(t)
        player_name = " ".join(player_tokens).strip()

        # Event Info
        event_id = market.get('eventId')
        event = event_map.get(event_id, {})
        game_name = event.get('name', 'Unknown')
        
        # Team Extraction
        # DK typically has teamName1, teamName2, teamShortName1, teamShortName2
        # We can't easily link the player directly to the team without more parsing,
        # but we can return the matchup or both teams to help the matcher.
        team1 = event.get('teamShortName1', 'UNK')
        team2 = event.get('teamShortName2', 'UNK')
        
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

        # Store as INTEGER in the data (best practice)
        over_int = parse_odds(raw_over)
        under_int = parse_odds(raw_under)

        parsed.append({
            "player": normalize_player_name(player_name),
            "team": "Unknown", # DK doesn't give us player-team link easily here, but we can use matchup
            "team_options": [team1, team2], # NEW: Pass possible teams to matcher
            "prop_type": prop_type_key,
            "line": float(line),
            "over_odds": over_int,
            "under_odds": under_int,
            "game": game_name,
            "game_date": game_date,
            "sportsbook": "draftkings"
        })
        
    return parsed

def fetch_dk_odds():
    print("🚀 Starting DraftKings Odds Fetch...")
    all_props = []
    
    for prop_key, cat_id in PLAYER_PROP_CATEGORIES.items():
        time.sleep(random.uniform(0.3, 0.8))
        data = fetch_category(cat_id, prop_key)
        props = parse_dk_data(data, prop_key)
        
        # print(f"  Found {len(props)} props for '{prop_key}'")
        all_props.extend(props)
    
    print(f"✅ Finished DK. Collected {len(all_props)} total props.")

    # --- DEBUG: Show user the formatted output with PLUS SIGNS ---
    if len(all_props) > 10:
        sample = all_props[10]
        print("\n🔎 SAMPLE OUTPUT (With Formatting):")
        print(f"Player: {sample['player']}")
        print(f"Line:   {sample['line']}")
        # Use our helper to show the plus sign
        print(f"Over:   {format_odds_for_display(sample['over_odds'])}") 
        print(f"Under:  {format_odds_for_display(sample['under_odds'])}")
    
    return all_props

if __name__ == "__main__":
    fetch_dk_odds()