import argparse
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logger = logging.getLogger(__name__)

ET_ZONE = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).resolve().parent.parent
CURRENT_OUTPUT_PATH = BASE_DIR / "data" / "current" / "action_network_odds.json"
ARCHIVE_DIR = BASE_DIR / "data" / "archive" / "action_network_odds"
DEFAULT_SCHEDULE_PATH = BASE_DIR / "data" / "current" / "today_schedule.json"
DEFAULT_MIN_REFRESH_INTERVAL_SECONDS = 3600

ACTION_NETWORK_SCOREBOARD_URL = "https://api.actionnetwork.com/web/v2/scoreboard/nba"
DEFAULT_BOOK_IDS = [
    "15",
    "30",
    "4556",
    "4557",
    "4559",
    "4560",
    "4562",
    "4561",
    "4558",
    "79",
    "2988",
    "75",
]

BOOK_LABELS = {
    "15": "DraftKings",
    "30": "FanDuel",
    "75": "Caesars",
    "79": "BetMGM",
    "2988": "ESPN BET",
    "4556": "Bet365",
    "4557": "Fanatics",
    "4558": "Hard Rock",
    "4559": "BetRivers",
    "4560": "Bally Bet",
    "4561": "Betway",
    "4562": "PointsBet",
}

HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "dnt": "1",
    "origin": "https://www.actionnetwork.com",
    "pragma": "no-cache",
    "referer": "https://www.actionnetwork.com/nba/odds",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch game spreads/totals/moneylines/team totals from Action Network.",
    )
    parser.add_argument(
        "--date",
        help="Target ET game date in YYYYMMDD or YYYY-MM-DD. Defaults to today + --days-ahead.",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=0,
        help="Offset from today's ET date when --date is omitted.",
    )
    parser.add_argument(
        "--book-ids",
        default=",".join(DEFAULT_BOOK_IDS),
        help="Comma-separated Action Network book IDs.",
    )
    parser.add_argument(
        "--output-path",
        default=str(CURRENT_OUTPUT_PATH),
        help="Current output JSON path.",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(ARCHIVE_DIR),
        help="Daily archive directory for deduped snapshots.",
    )
    parser.add_argument(
        "--schedule-path",
        default=str(DEFAULT_SCHEDULE_PATH),
        help="Schedule artifact used to attach NBA game_id and canonical game metadata.",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip writing the daily archive snapshot file.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the normalized payload to stdout.",
    )
    return parser.parse_args()


def get_et_now() -> datetime:
    return datetime.now(ET_ZONE)


def resolve_query_date(raw_date: Optional[str], days_ahead: int = 0) -> str:
    if raw_date:
        date_text = raw_date.strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_text, fmt).strftime("%Y%m%d")
            except ValueError:
                continue
        raise ValueError(f"Invalid --date value: {raw_date}. Expected YYYYMMDD or YYYY-MM-DD.")

    target_date = (get_et_now() + timedelta(days=days_ahead)).date()
    return target_date.strftime("%Y%m%d")


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_tricode(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    return text or None


def _schedule_key(
    game_date: Optional[str],
    away_team_tricode: Optional[str],
    home_team_tricode: Optional[str],
) -> Optional[Tuple[str, str, str]]:
    game_date = str(game_date or "").strip()
    away_team_tricode = _normalize_tricode(away_team_tricode)
    home_team_tricode = _normalize_tricode(home_team_tricode)
    if not game_date or not away_team_tricode or not home_team_tricode:
        return None
    return game_date, away_team_tricode, home_team_tricode


def load_schedule_rows(schedule_path: Path) -> List[Dict[str, Any]]:
    if not schedule_path.exists():
        logger.warning("Schedule file not found for Action Network merge: %s", schedule_path)
        return []
    try:
        with schedule_path.open("r") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("Unable to load Action Network schedule file %s: %s", schedule_path, exc)
        return []

    if isinstance(payload, dict):
        rows = payload.get("games", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _build_schedule_index(schedule_rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    index = {}
    for game in schedule_rows:
        key = _schedule_key(
            game.get("game_date"),
            game.get("away_team_tricode"),
            game.get("home_team_tricode"),
        )
        if key and key not in index:
            index[key] = game
    return index


def _team_lookup(game: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    teams = game.get("teams") if isinstance(game.get("teams"), list) else []
    output = {}
    for team in teams:
        if not isinstance(team, dict):
            continue
        team_id = _safe_int(team.get("id"))
        if team_id is None:
            continue
        output[team_id] = {
            "team_id": team_id,
            "team_tricode": team.get("abbr"),
            "team_name": team.get("full_name") or team.get("display_name"),
            "team_city": team.get("location"),
            "team_logo": team.get("logo"),
            "team_core_id": team.get("core_id"),
        }
    return output


def _parse_start_time(raw_value: Any) -> Dict[str, Optional[str]]:
    start_time_utc = str(raw_value or "").strip() or None
    if not start_time_utc:
        return {"start_time_utc": None, "start_time_et": None, "game_date": None}
    try:
        dt = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return {"start_time_utc": start_time_utc, "start_time_et": None, "game_date": None}

    et_dt = dt.astimezone(ET_ZONE)
    return {
        "start_time_utc": start_time_utc,
        "start_time_et": et_dt.isoformat(),
        "game_date": et_dt.date().isoformat(),
    }


def _bet_percentages(outcome: Dict[str, Any]) -> Dict[str, Optional[float]]:
    bet_info = outcome.get("bet_info") if isinstance(outcome.get("bet_info"), dict) else {}
    tickets = bet_info.get("tickets") if isinstance(bet_info.get("tickets"), dict) else {}
    money = bet_info.get("money") if isinstance(bet_info.get("money"), dict) else {}
    return {
        "tickets_pct": _safe_float(tickets.get("percent")),
        "money_pct": _safe_float(money.get("percent")),
    }


def _normalize_outcome(
    outcome: Dict[str, Any],
    team_index: Dict[int, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not isinstance(outcome, dict):
        return None
    if bool(outcome.get("is_alt_market")):
        return None

    line_status = str(outcome.get("line_status") or "normal").strip().lower()
    if line_status and line_status != "normal":
        return None

    period = str(outcome.get("period") or "").strip().lower()
    if period and period != "event":
        return None

    side = str(outcome.get("side") or "").strip().lower()
    team_id = _safe_int(outcome.get("team_id"))
    team_info = team_index.get(team_id, {}) if team_id is not None else {}

    return {
        "side": side or None,
        "line": _safe_float(outcome.get("value")),
        "odds": _safe_int(outcome.get("odds")),
        "team_id": team_id,
        "team_tricode": team_info.get("team_tricode"),
        "market_id": outcome.get("market_id"),
        "outcome_id": outcome.get("outcome_id"),
        "period": outcome.get("period"),
        "is_live": bool(outcome.get("is_live")),
        "deeplink_id": outcome.get("deeplink_id"),
        "odds_coefficient_score": _safe_float(outcome.get("odds_coefficient_score")),
        **_bet_percentages(outcome),
    }


def _parse_side_market(
    outcomes: Any,
    *,
    team_index: Dict[int, Dict[str, Any]],
    valid_sides: set,
) -> Dict[str, Any]:
    parsed = {}
    if not isinstance(outcomes, list):
        return parsed
    for outcome in outcomes:
        normalized = _normalize_outcome(outcome, team_index)
        if not normalized:
            continue
        side = normalized.get("side")
        if side not in valid_sides:
            continue
        if side in parsed:
            continue
        parsed[side] = normalized
    return parsed


def _parse_team_total_market(outcomes: Any, team_index: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    parsed: Dict[str, Dict[str, Any]] = {}
    if not isinstance(outcomes, list):
        return parsed
    for outcome in outcomes:
        normalized = _normalize_outcome(outcome, team_index)
        if not normalized:
            continue
        side = normalized.get("side")
        team_id = normalized.get("team_id")
        if side not in {"over", "under"} or team_id is None:
            continue
        team_info = team_index.get(team_id, {})
        team_key = str(team_info.get("team_tricode") or team_id)
        parsed.setdefault(team_key, {
            "team_id": team_id,
            "team_tricode": team_info.get("team_tricode"),
            "team_name": team_info.get("team_name"),
        })
        if side in parsed[team_key]:
            continue
        parsed[team_key][side] = {
            key: value
            for key, value in normalized.items()
            if key not in {"side", "team_id", "team_tricode"}
        }
        if normalized.get("line") is not None:
            parsed[team_key]["line"] = normalized.get("line")
    return parsed


def _compact_offer(outcome: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(outcome, dict):
        return None
    return {
        "line": outcome.get("line"),
        "odds": outcome.get("odds"),
        "tickets_pct": outcome.get("tickets_pct"),
        "money_pct": outcome.get("money_pct"),
    }


def _compact_team_total_market(team_total: Dict[str, Any]) -> Dict[str, Any]:
    compact = {}
    if not isinstance(team_total, dict):
        return compact
    for team_key, outcome in team_total.items():
        if not isinstance(outcome, dict):
            continue
        compact[team_key] = {
            "line": outcome.get("line"),
            "over": _compact_offer(outcome.get("over")),
            "under": _compact_offer(outcome.get("under")),
        }
    return compact


def _compact_book_markets(book_market: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(book_market, dict):
        return {}
    return {
        "book_id": book_market.get("book_id"),
        "book_label": book_market.get("book_label"),
        "spread": {
            "away": _compact_offer((book_market.get("spread") or {}).get("away")),
            "home": _compact_offer((book_market.get("spread") or {}).get("home")),
        },
        "moneyline": {
            "away": _compact_offer((book_market.get("moneyline") or {}).get("away")),
            "home": _compact_offer((book_market.get("moneyline") or {}).get("home")),
        },
        "total": {
            "line": (book_market.get("total") or {}).get("line"),
            "over": _compact_offer((book_market.get("total") or {}).get("over")),
            "under": _compact_offer((book_market.get("total") or {}).get("under")),
        },
        "team_total": _compact_team_total_market(book_market.get("team_total") or {}),
    }


def _build_compact_game_payload(
    *,
    game_id: Optional[str],
    action_network_game_id: Any,
    schedule_game: Optional[Dict[str, Any]],
    fallback_game: Dict[str, Any],
    markets: Dict[str, Any],
    has_action_network_markets: bool,
) -> Dict[str, Any]:
    schedule_game = schedule_game or {}
    return {
        "game_id": game_id,
        "action_network_game_id": action_network_game_id,
        "game_date": schedule_game.get("game_date") or fallback_game.get("game_date"),
        "game_time_utc": schedule_game.get("game_time_utc") or fallback_game.get("start_time_utc"),
        "game_time_et": schedule_game.get("game_time_et") or fallback_game.get("start_time_et"),
        "closing_scrape_deadline": schedule_game.get("closing_scrape_deadline"),
        "matchup": schedule_game.get("matchup") or fallback_game.get("matchup"),
        "away_team_id": schedule_game.get("away_team_id") or fallback_game.get("away_team_id"),
        "home_team_id": schedule_game.get("home_team_id") or fallback_game.get("home_team_id"),
        "away_team_tricode": _normalize_tricode(
            schedule_game.get("away_team_tricode") or fallback_game.get("away_team_tricode")
        ),
        "home_team_tricode": _normalize_tricode(
            schedule_game.get("home_team_tricode") or fallback_game.get("home_team_tricode")
        ),
        "away_team_name": schedule_game.get("away_team_name") or fallback_game.get("away_team_name"),
        "home_team_name": schedule_game.get("home_team_name") or fallback_game.get("home_team_name"),
        "is_live": bool(schedule_game.get("is_live", False)),
        "is_final": bool(schedule_game.get("is_final", False)),
        "is_scheduled": bool(schedule_game.get("is_scheduled", True)),
        "game_status": schedule_game.get("game_status"),
        "game_status_text": schedule_game.get("game_status_text"),
        "has_action_network_markets": has_action_network_markets,
        "markets": {
            str(book_id): _compact_book_markets(book_market)
            for book_id, book_market in (markets or {}).items()
            if isinstance(book_market, dict)
        },
    }


def _parse_book_markets(
    raw_book_markets: Dict[str, Any],
    *,
    book_id: str,
    team_index: Dict[int, Dict[str, Any]],
) -> Dict[str, Any]:
    event_markets = raw_book_markets.get("event") if isinstance(raw_book_markets.get("event"), dict) else {}
    total_market = _parse_side_market(
        event_markets.get("total"),
        team_index=team_index,
        valid_sides={"over", "under"},
    )
    total_line = total_market.get("over", {}).get("line")
    if total_line is None:
        total_line = total_market.get("under", {}).get("line")

    return {
        "book_id": book_id,
        "book_label": BOOK_LABELS.get(book_id, f"Action Book {book_id}"),
        "spread": _parse_side_market(
            event_markets.get("spread"),
            team_index=team_index,
            valid_sides={"home", "away"},
        ),
        "moneyline": _parse_side_market(
            event_markets.get("moneyline"),
            team_index=team_index,
            valid_sides={"home", "away"},
        ),
        "total": {
            "line": total_line,
            "over": total_market.get("over"),
            "under": total_market.get("under"),
        },
        "team_total": _parse_team_total_market(
            event_markets.get("core_bet_type_6_team_score"),
            team_index,
        ),
    }


def parse_action_network_payload(
    payload: Dict[str, Any],
    *,
    query_date: str,
    book_ids: List[str],
    schedule_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    games = payload.get("games") if isinstance(payload.get("games"), list) else []
    query_date_iso = datetime.strptime(query_date, "%Y%m%d").date().isoformat()
    schedule_rows = schedule_rows or []
    schedule_index = _build_schedule_index(schedule_rows)
    parsed_games_by_key = {}
    unmatched_action_games = []

    for game in games:
        if not isinstance(game, dict):
            continue

        team_index = _team_lookup(game)
        away_team_id = _safe_int(game.get("away_team_id"))
        home_team_id = _safe_int(game.get("home_team_id"))
        away_team = team_index.get(away_team_id, {})
        home_team = team_index.get(home_team_id, {})
        time_info = _parse_start_time(game.get("start_time"))

        market_payload = game.get("markets") if isinstance(game.get("markets"), dict) else {}
        parsed_book_markets = {}
        for book_id in book_ids:
            raw_book_markets = market_payload.get(str(book_id))
            if not isinstance(raw_book_markets, dict):
                continue
            parsed_book_markets[str(book_id)] = _parse_book_markets(
                raw_book_markets,
                book_id=str(book_id),
                team_index=team_index,
            )

        fallback_game = {
            "action_network_game_id": game.get("id"),
            "start_time_utc": time_info["start_time_utc"],
            "start_time_et": time_info["start_time_et"],
            "game_date": time_info["game_date"],
            "away_team_id": away_team_id,
            "home_team_id": home_team_id,
            "away_team_tricode": _normalize_tricode(away_team.get("team_tricode")),
            "home_team_tricode": _normalize_tricode(home_team.get("team_tricode")),
            "away_team_name": away_team.get("team_name"),
            "home_team_name": home_team.get("team_name"),
            "matchup": (
                f"{away_team.get('team_tricode')} @ {home_team.get('team_tricode')}"
                if away_team.get("team_tricode") and home_team.get("team_tricode")
                else None
            ),
            "markets": parsed_book_markets,
        }
        key = _schedule_key(
            fallback_game.get("game_date"),
            fallback_game.get("away_team_tricode"),
            fallback_game.get("home_team_tricode"),
        )
        if key:
            parsed_games_by_key[key] = fallback_game
        else:
            unmatched_action_games.append(fallback_game)

    parsed_games = []
    schedule_games_for_date = [
        schedule_game
        for schedule_game in schedule_rows
        if str(schedule_game.get("game_date") or "").strip() == query_date_iso
    ]

    if schedule_games_for_date:
        for schedule_game in schedule_games_for_date:
            key = _schedule_key(
                schedule_game.get("game_date"),
                schedule_game.get("away_team_tricode"),
                schedule_game.get("home_team_tricode"),
            )
            matched_game = parsed_games_by_key.pop(key, None) if key else None
            parsed_games.append(
                _build_compact_game_payload(
                    game_id=schedule_game.get("game_id"),
                    action_network_game_id=(matched_game or {}).get("action_network_game_id"),
                    schedule_game=schedule_game,
                    fallback_game=matched_game or {},
                    markets=(matched_game or {}).get("markets", {}),
                    has_action_network_markets=bool(matched_game),
                )
            )
    else:
        for matched_game in parsed_games_by_key.values():
            parsed_games.append(
                _build_compact_game_payload(
                    game_id=None,
                    action_network_game_id=matched_game.get("action_network_game_id"),
                    schedule_game=None,
                    fallback_game=matched_game,
                    markets=matched_game.get("markets", {}),
                    has_action_network_markets=bool(matched_game.get("markets")),
                )
            )

    for matched_game in parsed_games_by_key.values():
        unmatched_action_games.append(matched_game)

    return {
        "source": "action_network",
        "query_date": query_date_iso,
        "generated_at": get_et_now().isoformat(),
        "book_ids": book_ids,
        "content_live_count": payload.get("content_live_count"),
        "schedule_game_count": len(schedule_games_for_date),
        "matched_game_count": sum(
            1 for game in parsed_games if game.get("has_action_network_markets")
        ),
        "unmatched_action_network_game_count": len(unmatched_action_games),
        "game_count": len(parsed_games),
        "games": parsed_games,
        "unmatched_action_network_games": [
            {
                "action_network_game_id": game.get("action_network_game_id"),
                "game_date": game.get("game_date"),
                "matchup": game.get("matchup"),
                "away_team_tricode": game.get("away_team_tricode"),
                "home_team_tricode": game.get("home_team_tricode"),
            }
            for game in unmatched_action_games
        ],
    }


def _request_url(book_ids: List[str], query_date: str) -> str:
    return (
        f"{ACTION_NETWORK_SCOREBOARD_URL}?"
        f"{urlencode({'bookIds': ','.join(book_ids), 'date': query_date, 'periods': 'event'})}"
    )


def fetch_raw_action_network_payload(
    *,
    query_date: str,
    book_ids: List[str],
    timeout_seconds: int = 20,
) -> Dict[str, Any]:
    request_url = _request_url(book_ids, query_date)
    proxy_url = os.environ.get("ACTION_NETWORK_PROXY_URL", "").strip()

    if proxy_url:
        logger.info("Routing Action Network request through ACTION_NETWORK_PROXY_URL")
        response = requests.get(
            proxy_url,
            params={"url": request_url},
            headers=HEADERS,
            timeout=timeout_seconds,
        )
    else:
        response = requests.get(
            ACTION_NETWORK_SCOREBOARD_URL,
            params={
                "bookIds": ",".join(book_ids),
                "date": query_date,
                "periods": "event",
            },
            headers=HEADERS,
            timeout=timeout_seconds,
        )

    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Action Network payload is not a JSON object.")
    return payload


def fetch_action_network_odds(
    *,
    query_date: Optional[str] = None,
    days_ahead: int = 0,
    book_ids: Optional[List[str]] = None,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
) -> Dict[str, Any]:
    resolved_book_ids = [
        str(book_id).strip()
        for book_id in (book_ids or DEFAULT_BOOK_IDS)
        if str(book_id).strip()
    ]
    schedule_rows = load_schedule_rows(schedule_path)
    
    offsets = [days_ahead] if query_date else [days_ahead, days_ahead + 1]
    combined_payload = None
    
    for offset in offsets:
        target_date = resolve_query_date(query_date, days_ahead=offset)
        logger.info("[RUN] Action Network odds fetch | date=%s", target_date)
        raw_payload = fetch_raw_action_network_payload(
            query_date=target_date,
            book_ids=resolved_book_ids,
        )
        payload = parse_action_network_payload(
            raw_payload,
            query_date=target_date,
            book_ids=resolved_book_ids,
            schedule_rows=schedule_rows,
        )
        
        if combined_payload is None:
            combined_payload = payload
        else:
            combined_payload["games"].extend(payload.get("games", []))
            combined_payload["unmatched_action_network_games"].extend(
                payload.get("unmatched_action_network_games", [])
            )
            for key in ["game_count", "schedule_game_count", "matched_game_count", "unmatched_action_network_game_count"]:
                if key in payload:
                    combined_payload[key] = combined_payload.get(key, 0) + payload[key]

    logger.info(
        "[OK] Action Network odds fetch complete | date=%s games=%d",
        combined_payload["query_date"],
        combined_payload["game_count"],
    )
    return combined_payload


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2)


def _load_existing_payload(output_path: Path) -> Optional[Dict[str, Any]]:
    if not output_path.exists():
        return None
    try:
        with output_path.open("r") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("Unable to load existing Action Network artifact %s: %s", output_path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _is_existing_payload_fresh(
    payload: Dict[str, Any],
    *,
    query_date: str,
    min_refresh_interval_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    if min_refresh_interval_seconds <= 0:
        return False

    expected_query_date = datetime.strptime(query_date, "%Y%m%d").date().isoformat()
    if str(payload.get("query_date") or "").strip() != expected_query_date:
        return False

    generated_at = str(payload.get("generated_at") or "").strip()
    if not generated_at:
        return False

    try:
        generated_dt = datetime.fromisoformat(generated_at)
    except ValueError:
        return False

    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=ET_ZONE)

    now = now or get_et_now()
    age_seconds = (now - generated_dt.astimezone(ET_ZONE)).total_seconds()
    return age_seconds < min_refresh_interval_seconds


def _games_fingerprint(games: List[Dict[str, Any]]) -> str:
    serialized = json.dumps(games or [], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def write_current_and_archive(
    payload: Dict[str, Any],
    *,
    output_path: Path = CURRENT_OUTPUT_PATH,
    archive_dir: Path = ARCHIVE_DIR,
    archive_enabled: bool = True,
) -> bool:
    _write_json(output_path, payload)
    if not archive_enabled:
        return True

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{payload['query_date']}.json"
    existing = {}
    if archive_path.exists():
        try:
            with archive_path.open("r") as handle:
                existing = json.load(handle)
        except Exception:
            existing = {}

    if not isinstance(existing, dict):
        existing = {}

    snapshots = existing.get("snapshots") if isinstance(existing.get("snapshots"), list) else []
    fingerprint = _games_fingerprint(payload.get("games", []))
    last_fingerprint = snapshots[-1].get("fingerprint") if snapshots and isinstance(snapshots[-1], dict) else None
    if fingerprint == last_fingerprint:
        return False

    snapshots.append({
        "captured_at": payload.get("generated_at"),
        "fingerprint": fingerprint,
        "game_count": payload.get("game_count"),
        "games": payload.get("games", []),
    })
    archive_payload = {
        "source": payload.get("source", "action_network"),
        "query_date": payload.get("query_date"),
        "book_ids": payload.get("book_ids", []),
        "snapshots": snapshots,
    }
    _write_json(archive_path, archive_payload)
    return True


def refresh_action_network_odds_if_needed(
    *,
    query_date: Optional[str] = None,
    days_ahead: int = 0,
    book_ids: Optional[List[str]] = None,
    output_path: Path = CURRENT_OUTPUT_PATH,
    archive_dir: Path = ARCHIVE_DIR,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    min_refresh_interval_seconds: int = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
) -> Dict[str, Any]:
    resolved_query_date = resolve_query_date(query_date, days_ahead=days_ahead)
    existing_payload = _load_existing_payload(output_path)

    if existing_payload and _is_existing_payload_fresh(
        existing_payload,
        query_date=resolved_query_date,
        min_refresh_interval_seconds=min_refresh_interval_seconds,
    ):
        logger.debug(
            "[SKIP] Action Network odds fetch skipped; recent artifact is still fresh | "
            "date=%s output=%s",
            existing_payload.get("query_date"),
            output_path,
        )
        return {
            "payload": existing_payload,
            "refreshed": False,
            "output_path": str(output_path),
            "archive_updated": False,
        }

    payload = fetch_action_network_odds(
        query_date=query_date,
        days_ahead=days_ahead,
        book_ids=book_ids,
        schedule_path=schedule_path,
    )
    archive_updated = write_current_and_archive(
        payload,
        output_path=output_path,
        archive_dir=archive_dir,
        archive_enabled=True,
    )
    return {
        "payload": payload,
        "refreshed": True,
        "output_path": str(output_path),
        "archive_updated": archive_updated,
    }


def main() -> int:
    args = _parse_args()
    payload = fetch_action_network_odds(
        query_date=args.date,
        days_ahead=args.days_ahead,
        book_ids=args.book_ids.split(","),
        schedule_path=Path(args.schedule_path),
    )
    wrote_archive = write_current_and_archive(
        payload,
        output_path=Path(args.output_path),
        archive_dir=Path(args.archive_dir),
        archive_enabled=not args.no_archive,
    )
    if args.stdout:
        print(json.dumps(payload, indent=2))
    logger.info(
        "Action Network artifact written | output=%s archive_updated=%s",
        args.output_path,
        wrote_archive,
    )
    return 0


if __name__ == "__main__":
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    raise SystemExit(main())
