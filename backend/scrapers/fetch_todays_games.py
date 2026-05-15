import requests
import pandas as pd
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

DASHBOARD_LOOKAHEAD_DAYS = 7
ET_ZONE = ZoneInfo("America/New_York")
SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
SCHEDULE_REQUEST_TIMEOUT_SECONDS = 20
SCHEDULE_REQUEST_ATTEMPTS = 3
SCHEDULE_RETRY_BACKOFF_SECONDS = 1.5
ACTION_NETWORK_SCHEDULE_FALLBACK_DAYS = 2

TEAM_INFO_BY_TRICODE = {
    "ATL": ("Atlanta", "Hawks"),
    "BOS": ("Boston", "Celtics"),
    "BKN": ("Brooklyn", "Nets"),
    "CHA": ("Charlotte", "Hornets"),
    "CHI": ("Chicago", "Bulls"),
    "CLE": ("Cleveland", "Cavaliers"),
    "DAL": ("Dallas", "Mavericks"),
    "DEN": ("Denver", "Nuggets"),
    "DET": ("Detroit", "Pistons"),
    "GSW": ("Golden State", "Warriors"),
    "HOU": ("Houston", "Rockets"),
    "IND": ("Indiana", "Pacers"),
    "LAC": ("LA", "Clippers"),
    "LAL": ("Los Angeles", "Lakers"),
    "MEM": ("Memphis", "Grizzlies"),
    "MIA": ("Miami", "Heat"),
    "MIL": ("Milwaukee", "Bucks"),
    "MIN": ("Minnesota", "Timberwolves"),
    "NOP": ("New Orleans", "Pelicans"),
    "NYK": ("New York", "Knicks"),
    "OKC": ("Oklahoma City", "Thunder"),
    "ORL": ("Orlando", "Magic"),
    "PHI": ("Philadelphia", "76ers"),
    "PHX": ("Phoenix", "Suns"),
    "POR": ("Portland", "Trail Blazers"),
    "SAC": ("Sacramento", "Kings"),
    "SAS": ("San Antonio", "Spurs"),
    "TOR": ("Toronto", "Raptors"),
    "UTA": ("Utah", "Jazz"),
    "WAS": ("Washington", "Wizards"),
}

NBA_TEAM_ID_BY_TRICODE = {
    "ATL": 1610612737,
    "BOS": 1610612738,
    "BKN": 1610612751,
    "CHA": 1610612766,
    "CHI": 1610612741,
    "CLE": 1610612739,
    "DAL": 1610612742,
    "DEN": 1610612743,
    "DET": 1610612765,
    "GSW": 1610612744,
    "HOU": 1610612745,
    "IND": 1610612754,
    "LAC": 1610612746,
    "LAL": 1610612747,
    "MEM": 1610612763,
    "MIA": 1610612748,
    "MIL": 1610612749,
    "MIN": 1610612750,
    "NOP": 1610612740,
    "NYK": 1610612752,
    "OKC": 1610612760,
    "ORL": 1610612753,
    "PHI": 1610612755,
    "PHX": 1610612756,
    "POR": 1610612757,
    "SAC": 1610612758,
    "SAS": 1610612759,
    "TOR": 1610612761,
    "UTA": 1610612762,
    "WAS": 1610612764,
}


def _normalize_status_text(value):
    return str(value or "").strip()


def _is_tbd_tipoff(game):
    return _normalize_status_text(game.get('gameStatusText')).upper() == "TBD"


def _has_unknown_teams(game):
    home_team = game.get('homeTeam', {}) or {}
    away_team = game.get('awayTeam', {}) or {}
    return (
        not home_team.get('teamId')
        or not away_team.get('teamId')
        or not home_team.get('teamTricode')
        or not away_team.get('teamTricode')
    )


def _should_skip_dashboard_game(game):
    # Skip blank bracket placeholders and speculative playoff rows with no real tip.
    return _has_unknown_teams(game) or (bool(game.get('ifNecessary')) and _is_tbd_tipoff(game))


def _get_dashboard_window_dates(days_ahead: int = DASHBOARD_LOOKAHEAD_DAYS):
    today_et = datetime.now(ET_ZONE)
    return [
        (today_et + timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days_ahead)
    ]


def _parse_action_fallback_time(game):
    raw_utc = str(game.get("game_time_utc") or "").strip()
    if not raw_utc:
        return None, None, None

    try:
        dt_utc = datetime.fromisoformat(raw_utc.replace("Z", "+00:00"))
    except ValueError:
        return raw_utc, game.get("game_time_et"), None

    dt_et = dt_utc.astimezone(ET_ZONE)
    return raw_utc, dt_et.strftime("%I:%M %p ET"), dt_et.isoformat()


def _split_team_name(tricode, fallback_name):
    team_info = TEAM_INFO_BY_TRICODE.get(str(tricode or "").strip().upper())
    if team_info:
        return team_info

    fallback_text = " ".join(str(fallback_name or "").split()).strip()
    if not fallback_text:
        return None, None

    parts = fallback_text.split(" ", 1)
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[1]


def _fallback_game_id(game_date, away_tricode, home_tricode, action_network_game_id):
    action_id = str(action_network_game_id or "").strip()
    if action_id:
        return f"AN{action_id}"

    game_date_key = str(game_date or "").replace("-", "")
    matchup_key = f"{away_tricode or 'AWY'}{home_tricode or 'HME'}"
    return f"{game_date_key}/{matchup_key}"


def _action_network_game_to_schedule_game(game):
    game_date = str(game.get("game_date") or "").strip()
    away_tricode = str(game.get("away_team_tricode") or "").strip().upper()
    home_tricode = str(game.get("home_team_tricode") or "").strip().upper()
    if not game_date or not away_tricode or not home_tricode:
        return None

    game_time_utc, game_time_et, closing_scrape_deadline = _parse_action_fallback_time(game)
    away_city, away_name = _split_team_name(away_tricode, game.get("away_team_name"))
    home_city, home_name = _split_team_name(home_tricode, game.get("home_team_name"))
    game_id = _fallback_game_id(
        game_date,
        away_tricode,
        home_tricode,
        game.get("action_network_game_id"),
    )

    try:
        weekday = datetime.strptime(game_date, "%Y-%m-%d").strftime("%A")
    except ValueError:
        weekday = None

    return {
        "game_id": game_id,
        "game_code": f"{game_date.replace('-', '')}/{away_tricode}{home_tricode}",
        "schedule_source": "action_network_fallback",
        "home_team_id": NBA_TEAM_ID_BY_TRICODE.get(home_tricode) or game.get("home_team_id"),
        "home_team_name": home_name,
        "home_team_city": home_city,
        "home_team_tricode": home_tricode,
        "home_team_wins": None,
        "home_team_losses": None,
        "home_score": 0,
        "away_team_id": NBA_TEAM_ID_BY_TRICODE.get(away_tricode) or game.get("away_team_id"),
        "away_team_name": away_name,
        "away_team_city": away_city,
        "away_team_tricode": away_tricode,
        "away_team_wins": None,
        "away_team_losses": None,
        "away_score": 0,
        "arena_name": "Unknown Arena",
        "arena_city": "Unknown City",
        "arena_state": "Unknown State",
        "arena_full": "Unknown Arena, Unknown City, Unknown State",
        "game_time_utc": game_time_utc,
        "game_time_et": game_time_et or "TBD",
        "game_date": game_date,
        "game_weekday": weekday,
        "closing_scrape_deadline": closing_scrape_deadline,
        "game_status": 1,
        "game_status_text": game_time_et or "TBD",
        "is_live": False,
        "is_final": False,
        "is_scheduled": True,
        "home_leader_name": None,
        "home_leader_points": None,
        "away_leader_name": None,
        "away_leader_points": None,
        "score_differential": 0,
        "total_points": 0,
        "winning_team": "tie",
        "matchup": f"{away_tricode} @ {home_tricode}",
        "display_score": f"{away_tricode} 0 - {home_tricode} 0",
    }


def _get_action_network_schedule_fallback(days_ahead: int):
    try:
        from scrapers import fetch_action_network_odds as action_network_odds
    except ImportError:
        import fetch_action_network_odds as action_network_odds

    fallback_days = max(1, min(days_ahead, ACTION_NETWORK_SCHEDULE_FALLBACK_DAYS))
    games_by_key = {}

    for offset in range(fallback_days):
        query_date = action_network_odds.resolve_query_date(None, days_ahead=offset)
        try:
            raw_payload = action_network_odds.fetch_raw_action_network_payload(
                query_date=query_date,
                book_ids=action_network_odds.DEFAULT_BOOK_IDS,
            )
            parsed_payload = action_network_odds.parse_action_network_payload(
                raw_payload,
                query_date=query_date,
                book_ids=action_network_odds.DEFAULT_BOOK_IDS,
                schedule_rows=[],
            )
        except Exception as exc:
            print(f"Action Network schedule fallback failed for {query_date}: {exc}")
            continue

        for action_game in parsed_payload.get("games", []):
            schedule_game = _action_network_game_to_schedule_game(action_game)
            if not schedule_game:
                continue
            key = (
                schedule_game.get("game_date"),
                schedule_game.get("away_team_tricode"),
                schedule_game.get("home_team_tricode"),
            )
            games_by_key[key] = schedule_game

    return sorted(
        games_by_key.values(),
        key=lambda game: (
            str(game.get("game_date") or ""),
            str(game.get("game_time_utc") or ""),
            str(game.get("matchup") or ""),
        ),
    )


def upsert_games_to_db(raw_data: list) -> None:
    """Upsert the dashboard lookahead schedule into Supabase games table.
    Non-fatal: logs errors and continues if Supabase is unavailable.
    """
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
        from utils.supabase_client import get_supabase_client
        supabase = get_supabase_client()

        rows = []
        for g in raw_data:
            rows.append({
                "game_id":              g["game_id"],
                "game_date":            g["game_date"],
                "home_team_id":         g["home_team_id"],
                "home_team_name":       g["home_team_name"],
                "home_team_city":       g["home_team_city"],
                "home_team_tricode":    g["home_team_tricode"],
                "home_team_wins":       g.get("home_team_wins"),
                "home_team_losses":     g.get("home_team_losses"),
                "home_score":           g.get("home_score", 0),
                "away_team_id":         g["away_team_id"],
                "away_team_name":       g["away_team_name"],
                "away_team_city":       g["away_team_city"],
                "away_team_tricode":    g["away_team_tricode"],
                "away_team_wins":       g.get("away_team_wins"),
                "away_team_losses":     g.get("away_team_losses"),
                "away_score":           g.get("away_score", 0),
                "arena_name":           g.get("arena_name"),
                "arena_city":           g.get("arena_city"),
                "game_time_utc":        g.get("game_time_utc"),
                "game_time_et":         g.get("game_time_et"),
                "game_status":          g.get("game_status"),
                "game_status_text":     (g.get("game_status_text") or "").strip(),
                "is_live":              bool(g.get("is_live", False)),
                "is_final":             bool(g.get("is_final", False)),
                "is_scheduled":         bool(g.get("is_scheduled", True)),
                "matchup":              g.get("matchup"),
                "closing_scrape_deadline": g.get("closing_scrape_deadline"),
            })

        is_partial_fallback = bool(raw_data) and all(
            game.get("schedule_source") == "action_network_fallback"
            for game in raw_data
            if isinstance(game, dict)
        )
        window_dates = (
            sorted(
                {
                    str(game.get("game_date") or "").strip()
                    for game in raw_data
                    if isinstance(game, dict) and str(game.get("game_date") or "").strip()
                }
            )
            if is_partial_fallback
            else _get_dashboard_window_dates()
        )
        stale_row_count = 0
        if window_dates:
            existing_rows = (
                supabase
                .table("games")
                .select("game_id, game_date")
                .gte("game_date", window_dates[0])
                .lte("game_date", window_dates[-1])
                .execute()
            ).data or []
            incoming_ids = {str(row["game_id"]) for row in rows if row.get("game_id")}

            for existing_row in existing_rows:
                game_id = str(existing_row.get("game_id") or "").strip()
                if not game_id or game_id in incoming_ids:
                    continue
                supabase.table("games").delete().eq("game_id", game_id).execute()
                stale_row_count += 1

        if not rows:
            print(f"   games upsert: 0 games written to Supabase ({stale_row_count} stale rows removed)")
            return

        # Batch upsert (all games fit in one batch — max ~30 rows per day)
        supabase.table("games").upsert(rows, on_conflict="game_id").execute()
        print(f"   games upsert: {len(rows)} games written to Supabase ({stale_row_count} stale rows removed)")
    except Exception as e:
        print(f"   Warning: games upsert failed (non-fatal): {e}")


def get_nba_schedule():
    """Get NBA schedule data"""
    HEADERS = {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9",
        "connection": "keep-alive",
        "dnt": "1",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    }

    last_error = None
    for attempt in range(1, SCHEDULE_REQUEST_ATTEMPTS + 1):
        try:
            response = requests.get(
                url=SCHEDULE_URL,
                headers=HEADERS,
                timeout=SCHEDULE_REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError as exc:
                excerpt = (response.text or "").strip()
                excerpt = " ".join(excerpt.split())
                if len(excerpt) > 180:
                    excerpt = f"{excerpt[:180]}..."
                raise RuntimeError(
                    f"NBA schedule endpoint returned non-JSON payload (status={response.status_code}, excerpt={excerpt!r})"
                ) from exc

            if not isinstance(payload, dict) or "leagueSchedule" not in payload:
                raise RuntimeError("NBA schedule endpoint returned an unexpected payload shape.")
            return payload
        except Exception as exc:
            last_error = exc
            if attempt < SCHEDULE_REQUEST_ATTEMPTS:
                time.sleep(SCHEDULE_RETRY_BACKOFF_SECONDS * attempt)

    raise RuntimeError(
        f"Unable to fetch NBA schedule after {SCHEDULE_REQUEST_ATTEMPTS} attempts: {last_error}"
    )

def parse_game_data(game):
    """Parse game data from the schedule endpoint"""
    home_team = game.get('homeTeam', {})
    away_team = game.get('awayTeam', {})
    
    home_tricode = home_team.get('teamTricode')
    away_tricode = away_team.get('teamTricode')
    
    # Parse game time (schedule uses gameDateTimeEst and gameDateTimeUTC)
    game_time_utc = game.get('gameDateTimeUTC')
    game_time_et_raw = game.get('gameDateTimeEst')
    status_text = _normalize_status_text(game.get('gameStatusText'))
    is_tbd_tipoff = _is_tbd_tipoff(game)
    
    game_time_et_display = None
    game_date = None
    game_weekday = None
    closing_scrape_deadline = None
    
    if game_time_et_raw:
        try:
            # Parse the EST time string from the API
            dt = datetime.strptime(game_time_et_raw, "%Y-%m-%dT%H:%M:%SZ")
            dt_et = dt.replace(tzinfo=ET_ZONE)
            game_date = dt.strftime("%Y-%m-%d")
            game_weekday = dt.strftime("%A")
            if is_tbd_tipoff:
                game_time_utc = None
                game_time_et_display = "TBD"
            else:
                closing_scrape_deadline = dt_et.isoformat()
                game_time_et_display = dt.strftime("%I:%M %p ET")
        except:
            game_time_et_display = "TBD" if is_tbd_tipoff else game_time_et_raw

    if not game_date:
        game_date_raw = game.get('gameDateEst')
        if game_date_raw:
            try:
                date_dt = datetime.strptime(game_date_raw, "%Y-%m-%dT%H:%M:%SZ")
                game_date = date_dt.strftime("%Y-%m-%d")
                game_weekday = date_dt.strftime("%A")
            except:
                pass
    
    # Determine game status from gameStatus (1: Scheduled, 2: Live, 3: Final)
    game_status_num = game.get('gameStatus', 1)
    
    is_live = game_status_num == 2
    is_final = game_status_num == 3
    is_scheduled = game_status_num == 1
    
    # Extract scores (schedule endpoint might have them for completed games, default to 0)
    home_score = home_team.get('score', 0)
    away_score = away_team.get('score', 0)
    
    # Optional player leaders (depending on if they are populated in the static file)
    points_leaders = game.get('pointsLeaders', [])
    home_leader_name, home_leader_points = None, None
    away_leader_name, away_leader_points = None, None
    
    for leader in points_leaders:
        if leader.get('teamTricode') == home_tricode:
            home_leader_name = f"{leader.get('firstName', '')} {leader.get('lastName', '')}".strip()
            home_leader_points = leader.get('points')
        elif leader.get('teamTricode') == away_tricode:
            away_leader_name = f"{leader.get('firstName', '')} {leader.get('lastName', '')}".strip()
            away_leader_points = leader.get('points')

    game_data = {
        # Game IDs
        'game_id': game.get('gameId'),
        'game_code': game.get('gameCode'),
        
        # Team IDs and names
        'home_team_id': home_team.get('teamId'),
        'home_team_name': home_team.get('teamName'),
        'home_team_city': home_team.get('teamCity'),
        'home_team_tricode': home_tricode,
        'home_team_wins': home_team.get('wins'),
        'home_team_losses': home_team.get('losses'),
        'home_score': home_score,
        
        'away_team_id': away_team.get('teamId'),
        'away_team_name': away_team.get('teamName'),
        'away_team_city': away_team.get('teamCity'),
        'away_team_tricode': away_tricode,
        'away_team_wins': away_team.get('wins'),
        'away_team_losses': away_team.get('losses'),
        'away_score': away_score,
        
        # Arena information (directly from API now)
        'arena_name': game.get('arenaName', 'Unknown Arena'),
        'arena_city': game.get('arenaCity', 'Unknown City'),
        'arena_state': game.get('arenaState', 'Unknown State'),
        'arena_full': f"{game.get('arenaName', 'Unknown Arena')}, {game.get('arenaCity', 'Unknown City')}, {game.get('arenaState', 'Unknown State')}",
        
        # Game timing
        'game_time_utc': game_time_utc,
        'game_time_et': game_time_et_display,
        'game_date': game_date,
        'game_weekday': game_weekday,
        'closing_scrape_deadline': closing_scrape_deadline,
        
        # Game status
        'game_status': game_status_num,
        'game_status_text': status_text,
        'is_live': is_live,
        'is_final': is_final,
        'is_scheduled': is_scheduled,
        
        # Player leaders
        'home_leader_name': home_leader_name,
        'home_leader_points': home_leader_points,
        
        'away_leader_name': away_leader_name,
        'away_leader_points': away_leader_points,
        
        # Derived fields
        'score_differential': abs(home_score - away_score) if home_score is not None and away_score is not None else 0,
        'total_points': (home_score or 0) + (away_score or 0),
        'winning_team': 'home' if home_score > away_score else 'away' if away_score > home_score else 'tie',
        'matchup': f"{away_tricode} @ {home_tricode}",
        'display_score': f"{away_tricode} {away_score} - {home_tricode} {home_score}"
    }
    
    return game_data

def get_dashboard_data(days_ahead: int = DASHBOARD_LOOKAHEAD_DAYS):
    """Get upcoming games from the schedule for the dashboard window."""
    print("Fetching NBA schedule data...")
    try:
        data = get_nba_schedule()
    except Exception as exc:
        print(f"NBA schedule fetch failed: {exc}")
        print("Falling back to Action Network scoreboard schedule...")
        fallback_games = _get_action_network_schedule_fallback(days_ahead)
        if not fallback_games:
            raise
        print(f"Prepared {len(fallback_games)} dashboard games from Action Network fallback")
        return pd.DataFrame(fallback_games), fallback_games
    
    # 1. Determine the dashboard schedule window in US Eastern Time (ET)
    et_tz = ZoneInfo("America/New_York")
    today_et = datetime.now(et_tz)
    target_date_strings = [
        (today_et + timedelta(days=offset)).strftime("%m/%d/%Y 00:00:00")
        for offset in range(days_ahead)
    ]
    print(f"Looking for games from {target_date_strings[0]} through {target_date_strings[-1]}")
    
    game_dates = data.get('leagueSchedule', {}).get('gameDates', [])
    todays_games_list = []
    
    # 2. Find the objects within the dashboard window
    for date_obj in game_dates:
        if date_obj.get('gameDate') in target_date_strings:
            todays_games_list.extend(date_obj.get('games', []))
            
    print(f"Found {len(todays_games_list)} games in the next {days_ahead} days")
    
    all_games_data = []
    
    for game in todays_games_list:
        if _should_skip_dashboard_game(game):
            continue
        game_data = parse_game_data(game)
        all_games_data.append(game_data)

    print(f"Prepared {len(all_games_data)} dashboard games after filtering placeholders")
    
    # Convert to DataFrame
    df = pd.DataFrame(all_games_data) if all_games_data else pd.DataFrame()
    
    return df, all_games_data

if __name__ == "__main__":
    # Get the data
    df, raw_data = get_dashboard_data()

    # Display summary
    print(f"\n{'='*80}")
    print("NBA DASHBOARD DATA SUMMARY")
    print(f"{'='*80}")

    if not df.empty:
        print(f"\nOverview:")
        print(f"Total games: {len(df)}")
        print(f"Scheduled games: {df['is_scheduled'].sum()}")
        print(f"Live games: {df['is_live'].sum()}")
        print(f"Final games: {df['is_final'].sum()}")

        print(f"\nGames Today:")

        for idx, row in df.iterrows():
            if row['is_live']:
                status_icon = "🟢 LIVE"
                status_info = ""
            elif row['is_final']:
                status_icon = "✅ FINAL"
                status_info = ""
            else:
                status_icon = "⏰"
                status_info = row['game_time_et']
            
            print(f"\n{status_icon} {row['matchup']} {status_info}")
            print(f"   Game ID: {row['game_id']}")
            print(f"   Score: {row['display_score']}")
            print(f"   Arena: {row['arena_full']}")
            print(f"   Status: {row['game_status_text']}")
            
            if row.get('home_leader_name'):
                print(f"   Home Leader: {row['home_leader_name']} ({row['home_leader_points']} pts)")
            if row.get('away_leader_name'):
                print(f"   Away Leader: {row['away_leader_name']} ({row['away_leader_points']} pts)")
    else:
        print("No games found for today.")

    # Save to files
    output_path = os.path.join(os.path.dirname(__file__), '../data/current/nba_dashboard_games.json')
    schedule_path = os.path.join(os.path.dirname(__file__), '../data/current/today_schedule.json')

    # Ensure directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(raw_data, f, indent=2, default=str)

    # Write the schedule data for the PM2 scheduler
    schedule_data = {
        "games": raw_data
    }
    with open(schedule_path, 'w') as f:
        json.dump(schedule_data, f, indent=2, default=str)

    print(f"\nData saved to:")
    print(f"   - {output_path}")
    print(f"   - {schedule_path}")
