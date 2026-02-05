
import requests
import time
import random
import re
from datetime import datetime
from dateutil import tz

# CONSTANTS
# Using OH-SB (Ohio) as the region, similar to reference
DK_API_BASE = "https://sportsbook-nash.draftkings.com/sites/US-OH-SB/api/sportscontent/controldata/league/leagueSubcategory/v1/markets"
LEAGUE_ID = "42648" # NBA
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
    "Accept": "*/*",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

# Mapping from Reference File
PLAYER_PROP_CATEGORIES = {
    'points': '12488',
    'threes': '12497', # Threes Made
    'rebounds': '12492',
    'assists': '12495',
    'ra': '9974',      # Rebounds + Assists
    'pra': '5001',     # Points + Rebounds + Assists
    'pr': '9976',      # Points + Rebounds
    'pa': '9973',      # Points + Assists
    'steals': '13508',
    'blocks': '13780',
    'stocks': '13781' # Steals + Blocks
}

def normalize_player_name(name):
    if not name: return "unknown_player"
    name = name.lower().strip()
    name = name.replace('.', '').replace("'", "")
    suffixes = [' jr', ' sr', ' ii', ' iii', ' iv', ' v']
    for suffix in suffixes:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name

def fetch_category(subcategory_id, prop_label):
    timestamp = int(time.time() * 1000)
    # URL construction matches the reference file accurately
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
    
    # Map Selections to Market ID
    # We need 'Over' and 'Under'
    selections_by_market = {}
    for sel in selections:
        m_id = sel.get('marketId')
        label = sel.get('label', '').lower()
        
        if not m_id: continue
        if m_id not in selections_by_market:
            selections_by_market[m_id] = {}
            
        if label == 'over': selections_by_market[m_id]['Over'] = sel
        if label == 'under': selections_by_market[m_id]['Under'] = sel

    parsed = []
    
    for market in markets:
        m_id = market.get('id')
        outcomes = selections_by_market.get(m_id)
        if not outcomes or 'Over' not in outcomes or 'Under' not in outcomes:
            continue
            
        over_sel = outcomes['Over']
        under_sel = outcomes['Under']
        
        # Regex to find player name
        # Market Name Ex: "LeBron James Points O/U"
        # We assume the prop type key (e.g. 'points') might match text, but DK names vary.
        # Reference file logic:
        # search_name = prop_type_name.split(' ')[0]
        # match = re.search(r'\b' + re.escape(search_name), market_name, re.IGNORECASE)
        
        market_name = market.get('name', '')
        # Simple heuristic: Split by prop name or just take everything before first number? 
        # Actually reference logic is good but 'prop_type_key' is short code here.
        # Let's just strip known suffixes.
        
        
        clean_name = market_name
        remove_terms = ["Points", "Rebounds", "Assists", "Threes", "Three", "Pointers", "Pointer", "3-Point", "Steals", "Blocks", "Turnovers", "O/U", "+", "Made"]
        
        # Lebron James Points -> Lebron James
        # Julius Randle Three Pointers -> Julius Randle
        
        tokens = clean_name.split()
        player_tokens = []
        for t in tokens:
            if t in remove_terms or t == "O/U":
                break
            player_tokens.append(t)
            
        player_name = " ".join(player_tokens).strip()

        # Event/Game Info
        event_id = market.get('eventId')
        event = event_map.get(event_id, {})
        game_name = event.get('name', 'Unknown')
        
        # Game Date
        start_date = event.get('startEventDate')
        game_date = datetime.now().strftime('%Y-%m-%d')
        if start_date:
            try:
                dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                game_date = dt.astimezone(tz.gettz('America/New_York')).strftime('%Y-%m-%d')
            except: pass

        line = over_sel.get('points')
        over_odds = over_sel.get('displayOdds', {}).get('american')
        under_odds = under_sel.get('displayOdds', {}).get('american')
        
        if line is None or over_odds is None: continue
        
        try:
            o_odds_int = int(over_odds)
            u_odds_int = int(under_odds)
        except: 
            o_odds_int = 0
            u_odds_int = 0

        parsed.append({
            "player": normalize_player_name(player_name),
            "team": "Unknown", # DK hard to parse team from this view
            "prop_type": prop_type_key,
            "line": float(line),
            "over_odds": o_odds_int,
            "under_odds": u_odds_int,
            "game": game_name,
            "game_date": game_date,
            "sportsbook": "draftkings"
        })
        
    return parsed

def fetch_dk_odds():
    print("🚀 Starting DraftKings Odds Fetch (Direct API)...")
    all_props = []
    
    for prop_key, cat_id in PLAYER_PROP_CATEGORIES.items():
        # Random sleep like reference
        time.sleep(random.uniform(0.5, 1.5))
        
        data = fetch_category(cat_id, prop_key)
        props = parse_dk_data(data, prop_key)
        
        print(f"  Found {len(props)} props for '{prop_key}'")
        all_props.extend(props)
        
    print(f"✅ Finished DK. Collected {len(all_props)} total props.")
    return all_props

if __name__ == "__main__":
    fetch_dk_odds()
