import csv
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from utils.logging_utils import log_status
from utils.player_matcher import PlayerMatcher

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logger = logging.getLogger("EdgeScore")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default

ET_ZONE = ZoneInfo("America/New_York")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURRENT_DIR = os.path.join(BASE_DIR, "data", "current")
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "archive")

MASTER_FEED_PATH = os.path.join(CURRENT_DIR, "master_feed.json")
SCHEDULE_PATH = os.path.join(CURRENT_DIR, "today_schedule.json")
LINE_MOVEMENTS_PATH = os.path.join(CURRENT_DIR, "line_movements_today.json")
PRIZEPICKS_PATH = os.path.join(CURRENT_DIR, "prizepicks.csv")
EDGE_SCORE_PATH = os.path.join(CURRENT_DIR, "edge_scores_top15.json")
EDGE_SCORE_STATE_PATH = os.path.join(CURRENT_DIR, "edge_score_notification_state.json")
EDGE_SCORE_HISTORY_PATH = os.path.join(ARCHIVE_DIR, "edge_scores_history.json")
EDGE_SCORE_RESULTS_HISTORY_PATH = os.path.join(ARCHIVE_DIR, "edge_score_results_history.json")

EDGE_SCORE_TABLE = "edge_scores_current"
EDGE_LIMIT = max(1, _env_int("EDGE_SCORE_LIMIT", 15))
EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS = max(
    0,
    _env_int("EDGE_SCORE_NOTIFICATION_MIN_INTERVAL_SECONDS", 900),
)
EDGE_SCORE_DISCORD_WEBHOOK_URL = os.getenv("EDGE_SCORE_DISCORD_WEBHOOK_URL", "").strip()
EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL = (
    os.getenv("EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL", "")
    or os.getenv("EDGE_SCORE_DISCORD_LOG_WEBHOOK_URL", "")
).strip()
EDGE_SCORE_DISCORD_ASSETS_BASE_URL = (
    os.getenv("EDGE_SCORE_DISCORD_ASSETS_BASE_URL", "")
    or os.getenv("VITE_ASSETS_URL", "")
).strip().rstrip("/")
EDGE_DISCORD_MIN_SIGNAL_SCORE = max(1.0, min(99.0, _env_float("EDGE_SCORE_DISCORD_MIN_SIGNAL_SCORE", 72.5)))
EDGE_DISCORD_MAX_RANK = max(1, _env_int("EDGE_SCORE_DISCORD_MAX_RANK", 5))
EDGE_DISCORD_PER_BOOK_LIMIT = max(1, _env_int("EDGE_SCORE_DISCORD_PER_BOOK_LIMIT", 2))
EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE = max(
    1.0,
    min(99.0, _env_float("EDGE_SCORE_DISCORD_TRACKER_MIN_SIGNAL_SCORE", 77.5)),
)
EDGE_SPORTSBOOK_BOARD_LIMIT = max(1, _env_int("EDGE_SPORTSBOOK_BOARD_LIMIT", 10))
EDGE_DISCORD_LINE_MOVE_POINTS = max(0.25, _env_float("EDGE_SCORE_DISCORD_LINE_MOVE_POINTS", 1.0))
EDGE_DISCORD_ODDS_MOVE_AMERICAN = max(5, _env_int("EDGE_SCORE_DISCORD_ODDS_MOVE_AMERICAN", 25))
EDGE_DISCORD_SCORE_DELTA = max(1.0, _env_float("EDGE_SCORE_DISCORD_SCORE_DELTA", 6.0))
EDGE_DISCORD_RANK_DELTA = max(1, _env_int("EDGE_SCORE_DISCORD_RANK_DELTA", 4))
UNDATED_PROP_KEY = "__undated__"

SUPPORTED_BOOKS = {"dk", "fd", "pp"}
BOOK_ALIASES = {
    "dk": "dk",
    "draftkings": "dk",
    "fd": "fd",
    "fanduel": "fd",
    "pp": "pp",
    "prizepicks": "pp",
}
BOOK_LABELS = {
    "dk": "DraftKings",
    "fd": "FanDuel",
    "pp": "PrizePicks",
}
DISCORD_ALERT_REFRESH_LABELS = {"intraday"}
SIDE_MULTIPLIERS = {
    "over": 1.0,
    "under": -1.0,
}
COMPONENT_WEIGHTS = {
    "projection": 0.34,
    "recent_form": 0.18,
    "matchup": 0.16,
    "market": 0.12,
    "line_movement": 0.07,
    "similar_players": 0.07,
    "head_to_head": 0.04,
    "back_to_back": 0.02,
}
TOTAL_COMPONENT_WEIGHT = sum(COMPONENT_WEIGHTS.values())

STAT_PROFILES = {
    "PTS": {
        "display": "Points",
        "scale": 6.0,
        "focus": "points",
        "components": {"PTS": 1.0},
        "b2b_bias": -0.12,
    },
    "AST": {
        "display": "Assists",
        "scale": 2.8,
        "focus": "assists",
        "components": {"AST": 1.0},
        "b2b_bias": -0.08,
    },
    "REB": {
        "display": "Rebounds",
        "scale": 3.0,
        "focus": "rebounds",
        "components": {"REB": 1.0},
        "b2b_bias": -0.04,
    },
    "FG3M": {
        "display": "Threes",
        "scale": 1.6,
        "focus": "threes",
        "components": {"FG3M": 1.0},
        "b2b_bias": -0.10,
    },
    "BLK": {
        "display": "Blocks",
        "scale": 1.2,
        "focus": "stocks",
        "components": {"BLK": 1.0},
        "b2b_bias": -0.03,
    },
    "STL": {
        "display": "Steals",
        "scale": 1.2,
        "focus": "stocks",
        "components": {"STL": 1.0},
        "b2b_bias": -0.03,
    },
    "STL+BLK": {
        "display": "Stocks",
        "scale": 1.8,
        "focus": "stocks",
        "components": {"STL": 0.5, "BLK": 0.5},
        "b2b_bias": -0.03,
    },
    "PTS+REB+AST": {
        "display": "PRA",
        "scale": 7.5,
        "focus": "combo",
        "components": {"PTS": 0.45, "REB": 0.28, "AST": 0.27},
        "b2b_bias": -0.10,
    },
    "PTS+REB": {
        "display": "PR",
        "scale": 6.5,
        "focus": "combo",
        "components": {"PTS": 0.62, "REB": 0.38},
        "b2b_bias": -0.08,
    },
    "PTS+AST": {
        "display": "PA",
        "scale": 6.5,
        "focus": "combo",
        "components": {"PTS": 0.62, "AST": 0.38},
        "b2b_bias": -0.10,
    },
    "REB+AST": {
        "display": "RA",
        "scale": 4.8,
        "focus": "combo",
        "components": {"REB": 0.55, "AST": 0.45},
        "b2b_bias": -0.06,
    },
}
PLAY_TYPE_LABELS = [
    "Free Throws",
    "Post Up",
    "PNR Roll Man",
    "Putback",
    "Spot Up",
    "Cut",
    "Isolation",
    "Transition",
    "PNR Ball Handler",
    "Handoff",
    "Off Screen",
    "Misc",
]

BOOK_DISPLAY_ORDER = ["dk", "fd", "pp"]
BOOK_EMBED_COLORS = {
    "dk": 0xF97316,
    "fd": 0x2563EB,
    "pp": 0xDC2626,
}
SPORTSBOOK_LOGO_FILES = {
    "dk": "draftkings.webp",
    "fd": "fanduel.webp",
    "pp": "prizepicks.webp",
}
PP_PROP_MAP = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "threes": "FG3M",
    "blocks": "BLK",
    "steals": "STL",
    "pra": "PTS+REB+AST",
    "pr": "PTS+REB",
    "pa": "PTS+AST",
    "ra": "REB+AST",
    "stocks": "STL+BLK",
}
PP_LABEL_PROP_MAP = {
    "3PTM": "FG3M",
    "3-PT MADE": "FG3M",
    "ASSISTS": "AST",
    "BLKS+STLS": "STL+BLK",
    "BLOCKED SHOTS": "BLK",
    "BLOCKS": "BLK",
    "POINTS": "PTS",
    "PRA": "PTS+REB+AST",
    "PTS+ASTS": "PTS+AST",
    "PTS+REBS": "PTS+REB",
    "PTS+REBS+ASTS": "PTS+REB+AST",
    "RA": "REB+AST",
    "REBOUNDS": "REB",
    "REBS+ASTS": "REB+AST",
    "STEALS": "STL",
    "STEALS+BLOCKS": "STL+BLK",
}


def get_et_now() -> datetime:
    return datetime.now(ET_ZONE)


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.warning("Could not load JSON from %s: %s", path, exc)
        return default


def _write_json_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(temp_path, path)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _average(values: List[Optional[float]]) -> Optional[float]:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _normalize_book_key(raw_book: Any) -> str:
    return BOOK_ALIASES.get(str(raw_book or "").strip().lower(), "")


def _normalize_pp_stat_type(raw_prop_type: Any, raw_prop_label: Any) -> str:
    raw_key = str(raw_prop_type or "").strip().lower()
    mapped = PP_PROP_MAP.get(raw_key)
    if mapped:
        return mapped

    if isinstance(raw_prop_label, str):
        cleaned = re.sub(r"\s+", " ", raw_prop_label).upper().strip()
        return PP_LABEL_PROP_MAP.get(cleaned, "")

    return ""


def _normalize_game_date(raw_value: Any) -> str:
    if not raw_value:
        return ""
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if len(stripped) >= 10 and stripped[4] == "-" and stripped[7] == "-":
            if len(stripped) == 10:
                return stripped
            try:
                dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                return dt.astimezone(ET_ZONE).strftime("%Y-%m-%d")
            except ValueError:
                return stripped[:10]
    return str(raw_value)


def _parse_date(raw_value: Any) -> Optional[datetime]:
    normalized = _normalize_game_date(raw_value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=ET_ZONE)
    except ValueError:
        return None


def _parse_dt(raw_value: Any) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET_ZONE)
    return dt.astimezone(ET_ZONE)


def _american_to_implied(raw_odds: Any) -> Optional[float]:
    odds = _safe_float(raw_odds)
    if odds is None or odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def _format_odds(raw_odds: Any) -> str:
    odds = _safe_float(raw_odds)
    if odds is None:
        return "-"
    odds_int = int(round(odds))
    return f"+{odds_int}" if odds_int > 0 else str(odds_int)


def _has_discord_alert_target() -> bool:
    return bool(EDGE_SCORE_DISCORD_WEBHOOK_URL or EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL)


def _results_recap_webhook_url() -> str:
    return EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL or EDGE_SCORE_DISCORD_WEBHOOK_URL


def _post_discord_webhook(webhook_url: str, payload: Dict[str, Any]) -> bool:
    if not webhook_url:
        return False
    response = requests.post(
        webhook_url,
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return True


def _format_discord_timestamp(raw_value: Any) -> str:
    dt = _parse_dt(raw_value) or get_et_now()
    month = dt.strftime("%b")
    time_text = dt.strftime("%I:%M %p").lstrip("0")
    return f"{month} {dt.day}, {dt.year} • {time_text} ET"


def _format_tracker_time(raw_value: Any) -> str:
    dt = _parse_dt(raw_value) or get_et_now()
    return dt.strftime("%I:%M %p ET").lstrip("0")


def _sportsbook_logo_url(book: Any) -> Optional[str]:
    normalized = str(book or "").strip().lower()
    logo_file = SPORTSBOOK_LOGO_FILES.get(normalized)
    if not logo_file:
        return None
    if not EDGE_SCORE_DISCORD_ASSETS_BASE_URL.startswith("http"):
        return None
    return f"{EDGE_SCORE_DISCORD_ASSETS_BASE_URL}/assets/sportsbook_logos/{logo_file}"


def _stat_value_from_stats(stats: Dict[str, Any], stat_type: str) -> Optional[float]:
    if not isinstance(stats, dict):
        return None

    if stat_type in {"PTS", "REB", "AST", "FG3M", "BLK", "STL"}:
        return _safe_float(stats.get(stat_type))
    if stat_type == "STL+BLK":
        return _average([
            _safe_float(stats.get("STL+BLK")),
            (_safe_float(stats.get("STL"), 0.0) or 0.0) + (_safe_float(stats.get("BLK"), 0.0) or 0.0),
        ])
    if stat_type == "PTS+REB+AST":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("PTS", "REB", "AST"))
    if stat_type == "PTS+REB":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("PTS", "REB"))
    if stat_type == "PTS+AST":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("PTS", "AST"))
    if stat_type == "REB+AST":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("REB", "AST"))
    return None


def _stat_value_from_game(game: Dict[str, Any], stat_type: str) -> Optional[float]:
    if not isinstance(game, dict):
        return None

    if stat_type in {"PTS", "REB", "AST", "FG3M", "BLK", "STL"}:
        return _safe_float(game.get(stat_type))
    if stat_type == "STL+BLK":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        steals = _safe_float(game.get("STL"), 0.0) or 0.0
        blocks = _safe_float(game.get("BLK"), 0.0) or 0.0
        return steals + blocks
    if stat_type == "PTS+REB+AST":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("PTS", "REB", "AST"))
    if stat_type == "PTS+REB":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("PTS", "REB"))
    if stat_type == "PTS+AST":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("PTS", "AST"))
    if stat_type == "REB+AST":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("REB", "AST"))
    return None


def _normalize_score_by_scale(value: Optional[float], scale: float) -> float:
    if value is None:
        return 0.0
    return _clamp(value / max(scale, 0.5), -1.0, 1.0)


def _parse_percent(raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    text = str(raw_value).strip().replace("%", "")
    return _safe_float(text)


def _rank_to_ease(raw_rank: Any) -> Optional[float]:
    rank = _safe_float(raw_rank)
    if rank is None:
        return None
    rank = _clamp(rank, 1.0, 30.0)
    return (rank - 15.5) / 14.5


def _side_name(side: str) -> str:
    return "Over" if side == "over" else "Under"


def _book_sort_key(book: Any) -> Tuple[int, str]:
    normalized = str(book or "").strip().lower()
    try:
        order = BOOK_DISPLAY_ORDER.index(normalized)
    except ValueError:
        order = len(BOOK_DISPLAY_ORDER)
    return order, normalized


def _candidate_sort_key(candidate: Dict[str, Any]) -> Tuple[float, float, float]:
    return (
        candidate.get("edge_score", 0.0),
        candidate.get("confidence", 0.0),
        candidate.get("signal_score", 0.0),
    )


def _format_signal_score_threshold(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _player_headshot_url(player_id: Any) -> Optional[str]:
    normalized = str(player_id or "").strip()
    if not normalized.isdigit():
        return None
    return f"https://cdn.nba.com/headshots/nba/latest/260x190/{normalized}.png"


def _simple_position(position: Any) -> str:
    text = str(position or "").upper()
    if "G" in text:
        return "G"
    if "C" in text:
        return "C"
    if "F" in text:
        return "F"
    return "G"


def _extract_logs(player: Dict[str, Any]) -> List[Dict[str, Any]]:
    logs = player.get("game_log") if isinstance(player, dict) else []
    if not isinstance(logs, list):
        return []

    ordered = []
    for game in logs:
        if not isinstance(game, dict):
            continue
        minutes = _safe_float(game.get("MIN"), 0.0) or 0.0
        if minutes <= 0:
            continue
        game_date = _parse_date(game.get("GAME_DATE"))
        if game_date is None:
            continue
        ordered.append((game_date, game))
    ordered.sort(key=lambda item: item[0], reverse=True)
    return [game for _, game in ordered]


def _weighted_average(pairs: List[Tuple[Optional[float], float]]) -> Optional[float]:
    numerator = 0.0
    denominator = 0.0
    for value, weight in pairs:
        if value is None or weight <= 0:
            continue
        numerator += value * weight
        denominator += weight
    if denominator <= 0:
        return None
    return numerator / denominator


def _build_schedule_context(schedule_payload: Any) -> Dict[str, Any]:
    if isinstance(schedule_payload, dict):
        games = schedule_payload.get("games", [])
    elif isinstance(schedule_payload, list):
        games = schedule_payload
    else:
        games = []

    games = [game for game in games if isinstance(game, dict)]
    now = get_et_now()
    today_str = now.strftime("%Y-%m-%d")
    non_final_games = [game for game in games if not game.get("is_final")]

    active_candidates = []
    for game in non_final_games:
        deadline_dt = _parse_dt(game.get("closing_scrape_deadline"))
        if deadline_dt is not None and deadline_dt >= (now - timedelta(minutes=15)):
            active_candidates.append(game)

    active_games = []
    if active_candidates:
        target_date = min(
            _normalize_game_date(game.get("game_date"))
            for game in active_candidates
            if _normalize_game_date(game.get("game_date"))
        )
        active_games = [
            game for game in active_candidates
            if _normalize_game_date(game.get("game_date")) == target_date
        ]

    if not active_games:
        future_games = []
        for game in non_final_games:
            game_date = _normalize_game_date(game.get("game_date"))
            deadline_dt = _parse_dt(game.get("closing_scrape_deadline"))
            if deadline_dt and deadline_dt >= (now - timedelta(hours=2)):
                future_games.append((deadline_dt, game))
            elif game_date >= today_str:
                future_games.append((datetime.max.replace(tzinfo=ET_ZONE), game))

        future_games.sort(key=lambda item: (item[0], _normalize_game_date(item[1].get("game_date"))))
        if future_games:
            target_date = _normalize_game_date(future_games[0][1].get("game_date"))
            active_games = [
                game for _, game in future_games
                if _normalize_game_date(game.get("game_date")) == target_date
            ]

    active_teams = set()
    active_dates = set()
    opponent_by_team = {}
    game_by_team = {}
    for game in active_games:
        home_team = game.get("home_team_tricode")
        away_team = game.get("away_team_tricode")
        game_date = _normalize_game_date(game.get("game_date"))
        if home_team:
            active_teams.add(home_team)
            game_by_team[home_team] = game
            if away_team:
                opponent_by_team[home_team] = away_team
        if away_team:
            active_teams.add(away_team)
            game_by_team[away_team] = game
            if home_team:
                opponent_by_team[away_team] = home_team
        if game_date:
            active_dates.add(game_date)

    return {
        "games": active_games,
        "active_teams": active_teams,
        "active_dates": active_dates,
        "game_by_team": game_by_team,
        "opponent_by_team": opponent_by_team,
    }


def _normalize_props_tree(props_tree: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    return _normalize_props_tree_for_game_date(props_tree)


def _resolve_dated_prop_bucket(
    prop_bucket: Any,
    target_game_date: Optional[str] = None,
    active_dates: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(prop_bucket, dict):
        return None

    normalized_target = _normalize_game_date(target_game_date)
    normalized_active_dates = {
        _normalize_game_date(game_date)
        for game_date in (active_dates or set())
        if _normalize_game_date(game_date)
    }

    if normalized_target:
        target_match = prop_bucket.get(normalized_target)
        if isinstance(target_match, dict):
            return target_match

    dated_candidates: List[Tuple[str, Dict[str, Any]]] = []
    undated_candidate = prop_bucket.get(UNDATED_PROP_KEY)
    if not isinstance(undated_candidate, dict):
        undated_candidate = None

    for raw_date, raw_prop in prop_bucket.items():
        if raw_date == UNDATED_PROP_KEY or not isinstance(raw_prop, dict):
            continue
        normalized_date = _normalize_game_date(raw_date)
        if not normalized_date:
            continue
        dated_candidates.append((normalized_date, raw_prop))

    if normalized_active_dates:
        active_matches = [
            (game_date, prop)
            for game_date, prop in dated_candidates
            if game_date in normalized_active_dates
        ]
        if active_matches:
            active_matches.sort(key=lambda item: item[0], reverse=True)
            return active_matches[0][1]

    if undated_candidate and not dated_candidates:
        return undated_candidate

    if dated_candidates and not normalized_target:
        dated_candidates.sort(key=lambda item: item[0], reverse=True)
        return dated_candidates[0][1]

    return undated_candidate


def _normalize_props_tree_for_game_date(
    props_tree: Any,
    target_game_date: Optional[str] = None,
    active_dates: Optional[set] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    normalized: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not isinstance(props_tree, dict):
        return normalized

    normalized_target_game_date = _normalize_game_date(target_game_date)
    normalized_active_dates = {
        _normalize_game_date(game_date)
        for game_date in (active_dates or set())
        if _normalize_game_date(game_date)
    }

    for raw_stat, raw_books in props_tree.items():
        stat_type = str(raw_stat or "").upper()
        if not stat_type or not isinstance(raw_books, dict):
            continue
        for raw_book, raw_prop in raw_books.items():
            book = _normalize_book_key(raw_book)
            if book not in SUPPORTED_BOOKS or not isinstance(raw_prop, dict):
                continue

            if any(key in raw_prop for key in ("line", "over", "under", "game_date")):
                candidate_prop = raw_prop
            else:
                candidate_prop = _resolve_dated_prop_bucket(
                    raw_prop,
                    target_game_date=normalized_target_game_date,
                    active_dates=normalized_active_dates,
                )

            if not isinstance(candidate_prop, dict):
                continue

            prop_game_date = _normalize_game_date(candidate_prop.get("game_date")) or normalized_target_game_date
            if normalized_target_game_date and prop_game_date and prop_game_date != normalized_target_game_date:
                continue
            if normalized_active_dates and prop_game_date and prop_game_date not in normalized_active_dates:
                continue

            line = _safe_float(candidate_prop.get("line"))
            if line is None:
                continue
            normalized.setdefault(stat_type, {})[book] = {
                "line": line,
                "over": _safe_float(candidate_prop.get("over")),
                "under": _safe_float(candidate_prop.get("under")),
                "game_date": prop_game_date,
                "game_id": candidate_prop.get("game_id"),
            }
    return normalized


def _merge_props_trees(
    base_props: Dict[str, Dict[str, Dict[str, Any]]],
    overlay_props: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    merged: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for stat_type, book_map in base_props.items():
        merged[stat_type] = {book: dict(prop) for book, prop in book_map.items()}
    for stat_type, book_map in overlay_props.items():
        merged.setdefault(stat_type, {})
        for book, prop in book_map.items():
            merged[stat_type][book] = dict(prop)
    return merged


def _build_master_feed_matcher(master_feed: Any) -> Tuple[Optional[PlayerMatcher], Dict[str, str]]:
    players_metadata = []
    id_to_team: Dict[str, str] = {}

    if not isinstance(master_feed, list):
        return None, id_to_team

    for player in master_feed:
        if not isinstance(player, dict):
            continue
        player_id = player.get("id")
        player_name = player.get("name")
        team = player.get("team", "UNK")
        if player_id and player_name:
            players_metadata.append({
                "PLAYER_ID": player_id,
                "PLAYER_NAME": player_name,
                "TEAM_ABBREVIATION": team,
            })
        if player_id and team:
            id_to_team[str(player_id)] = team

    if not players_metadata:
        return None, id_to_team

    return PlayerMatcher(players_metadata), id_to_team


def _merge_overlay_indexes(
    base_index: Dict[str, Dict[str, Dict[str, Any]]],
    incoming_index: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    merged = {
        player_id: {
            stat_type: {book: dict(prop) for book, prop in stat_map.items()}
            for stat_type, stat_map in player_map.items()
        }
        for player_id, player_map in base_index.items()
    }

    for player_id, player_map in incoming_index.items():
        merged.setdefault(player_id, {})
        for stat_type, stat_map in player_map.items():
            merged[player_id].setdefault(stat_type, {})
            for book, prop in stat_map.items():
                merged[player_id][stat_type][book] = dict(prop)

    return merged


def _load_prizepicks_overlay(
    master_feed: Any,
    schedule_context: Dict[str, Any],
    prizepicks_path: str,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not prizepicks_path or not os.path.exists(prizepicks_path):
        return {}

    matcher, id_to_team = _build_master_feed_matcher(master_feed)
    if matcher is None:
        return {}

    active_teams = set(schedule_context.get("active_teams") or [])
    game_by_team = schedule_context.get("game_by_team") or {}
    active_dates = set(schedule_context.get("active_dates") or [])

    candidates: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    try:
        with open(prizepicks_path, "r", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not isinstance(row, dict):
                    continue

                raw_team = str(row.get("team") or "").strip()
                if active_teams and raw_team not in active_teams:
                    continue

                raw_name = row.get("raw_player_name") or row.get("player") or ""
                player_id = matcher.match_player(raw_name, raw_team or "UNK")
                if not player_id:
                    continue

                canonical_team = id_to_team.get(str(player_id), raw_team)
                schedule_game = game_by_team.get(canonical_team, {}) if canonical_team else {}
                schedule_game_date = _normalize_game_date(schedule_game.get("game_date"))
                row_game_date = _normalize_game_date(row.get("game_date"))
                if schedule_game_date:
                    if row_game_date and row_game_date != schedule_game_date:
                        continue
                    game_date = schedule_game_date
                else:
                    game_date = row_game_date

                if active_dates and game_date and game_date not in active_dates:
                    continue

                stat_type = _normalize_pp_stat_type(row.get("prop_type"), row.get("prop_label"))
                if not stat_type or stat_type not in STAT_PROFILES:
                    continue

                line = _safe_float(row.get("line"))
                if line is None:
                    continue

                game_id = row.get("game_id") or schedule_game.get("game_id")
                candidate_key = (str(player_id), stat_type, game_date or "")
                candidates.setdefault(candidate_key, []).append({
                    "line": line,
                    "over": _safe_float(row.get("over_odds")),
                    "under": _safe_float(row.get("under_odds")),
                    "game_date": game_date,
                    "game_id": game_id,
                })
    except Exception as exc:
        logger.warning("Could not load PrizePicks overlay from %s: %s", prizepicks_path, exc)
        return {}

    overlay_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (player_id, stat_type, _game_date), prop_candidates in candidates.items():
        unique_lines = {candidate["line"] for candidate in prop_candidates}
        if len(unique_lines) != 1:
            continue

        chosen = prop_candidates[-1]
        overlay_index.setdefault(player_id, {}).setdefault(stat_type, {})["pp"] = {
            "line": chosen["line"],
            "over": chosen.get("over"),
            "under": chosen.get("under"),
            "game_date": chosen.get("game_date"),
            "game_id": chosen.get("game_id"),
        }

    return overlay_index


def _build_overlay_props_index(players_data: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    overlay_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not isinstance(players_data, dict):
        return overlay_index

    for raw_player_id, payload in players_data.items():
        if not isinstance(payload, dict):
            continue
        player_id = str(raw_player_id)
        game_date = _normalize_game_date(payload.get("game_date"))
        game_id = payload.get("game_id")
        props_tree = payload.get("props", {})
        if not isinstance(props_tree, dict):
            continue

        for raw_stat, raw_books in props_tree.items():
            stat_type = str(raw_stat or "").upper()
            if not stat_type or not isinstance(raw_books, dict):
                continue
            for raw_book, raw_prop in raw_books.items():
                book = _normalize_book_key(raw_book)
                if book not in SUPPORTED_BOOKS or not isinstance(raw_prop, dict):
                    continue
                line = _safe_float(raw_prop.get("line"))
                if line is None:
                    continue
                overlay_index.setdefault(player_id, {}).setdefault(stat_type, {})[book] = {
                    "line": line,
                    "over": _safe_float(raw_prop.get("over")),
                    "under": _safe_float(raw_prop.get("under")),
                    "game_date": game_date,
                    "game_id": game_id,
                }
    return overlay_index


def _build_line_movement_lookup(line_movements_payload: Any) -> Dict[Tuple[str, str, str, str], List[Dict[str, Any]]]:
    lookup: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    if not isinstance(line_movements_payload, dict):
        return lookup

    snapshots = line_movements_payload.get("snapshots", [])
    if not isinstance(snapshots, list):
        return lookup

    ordered_snapshots = sorted(
        [snapshot for snapshot in snapshots if isinstance(snapshot, dict)],
        key=lambda snapshot: str(snapshot.get("timestamp") or ""),
    )
    for snapshot in ordered_snapshots:
        timestamp = snapshot.get("timestamp")
        players = snapshot.get("players", {})
        if not isinstance(players, dict):
            continue

        for raw_player_id, pdata in players.items():
            if not isinstance(pdata, dict):
                continue
            player_id = str(raw_player_id)
            game_date = _normalize_game_date(pdata.get("game_date"))
            props_tree = pdata.get("props", {})
            if not isinstance(props_tree, dict):
                continue

            for raw_stat, raw_books in props_tree.items():
                stat_type = str(raw_stat or "").upper()
                if not stat_type or not isinstance(raw_books, dict):
                    continue
                for raw_book, raw_prop in raw_books.items():
                    book = _normalize_book_key(raw_book)
                    if book not in SUPPORTED_BOOKS or not isinstance(raw_prop, dict):
                        continue
                    line = _safe_float(raw_prop.get("line"))
                    if line is None:
                        continue
                    key = (player_id, stat_type, book, game_date)
                    lookup.setdefault(key, []).append({
                        "timestamp": timestamp,
                        "line": line,
                        "over": _safe_float(raw_prop.get("over")),
                        "under": _safe_float(raw_prop.get("under")),
                    })

    return lookup


def _get_zone_matchup_score(
    player_zone_data: Any,
    opponent_zone_data: Any,
    zone_filter: Optional[set] = None,
) -> Tuple[Optional[float], Dict[str, Any]]:
    if not isinstance(player_zone_data, dict) or not isinstance(opponent_zone_data, dict):
        return None, {"weights": {}}

    weighted_total = 0.0
    total_weight = 0.0
    debug_weights = {}
    for zone, pdata in player_zone_data.items():
        if zone_filter and zone not in zone_filter:
            continue
        pct = _parse_percent((pdata or {}).get("percentage"))
        rank = _rank_to_ease((opponent_zone_data.get(zone) or {}).get("rank"))
        if pct is None or rank is None or pct <= 0:
            continue
        weighted_total += rank * pct
        total_weight += pct
        debug_weights[zone] = {
            "player_pct": round(pct, 1),
            "opp_rank": _safe_float((opponent_zone_data.get(zone) or {}).get("rank")),
        }

    if total_weight <= 0:
        return None, {"weights": debug_weights}

    return weighted_total / total_weight, {"weights": debug_weights}


def _get_shot_type_matchup_score(player: Dict[str, Any]) -> Tuple[Optional[float], Dict[str, Any]]:
    analysis = player.get("shot_type_analysis")
    if not isinstance(analysis, dict):
        return None, {"weights": {}}
    player_data = analysis.get("player", {})
    opp_data = analysis.get("opp_def", {})
    if not isinstance(player_data, dict) or not isinstance(opp_data, dict):
        return None, {"weights": {}}

    weighted_total = 0.0
    total_weight = 0.0
    debug_weights = {}
    for key in ("catch_and_shoot", "pull_up", "less_than_10_ft"):
        pct = _safe_float((player_data.get(key) or {}).get("percentage"))
        rank = _rank_to_ease((opp_data.get(key) or {}).get("rank"))
        if pct is None or rank is None or pct <= 0:
            continue
        weighted_total += rank * pct
        total_weight += pct
        debug_weights[key] = {
            "player_pct": round(pct, 1),
            "opp_rank": _safe_float((opp_data.get(key) or {}).get("rank")),
        }

    if total_weight <= 0:
        return None, {"weights": debug_weights}
    return weighted_total / total_weight, {"weights": debug_weights}


def _get_play_type_matchup_score(player: Dict[str, Any]) -> Tuple[Optional[float], Dict[str, Any]]:
    play_types = player.get("play_type_analysis")
    if not isinstance(play_types, list):
        return None, {"weights": {}}

    weighted_total = 0.0
    total_weight = 0.0
    debug_weights = {}
    for item in play_types:
        if not isinstance(item, dict):
            continue
        pct = _parse_percent(item.get("percent"))
        rank = _rank_to_ease(item.get("rank"))
        label = str(item.get("type") or "")
        if pct is None or rank is None or pct <= 0:
            continue
        weighted_total += rank * pct
        total_weight += pct
        debug_weights[label] = {
            "player_pct": round(pct, 1),
            "opp_rank": _safe_float(item.get("rank")),
        }

    if total_weight <= 0:
        return None, {"weights": debug_weights}
    return weighted_total / total_weight, {"weights": debug_weights}


def _compute_matchup_context(player: Dict[str, Any], stat_type: str, side: str) -> Dict[str, Any]:
    profile = STAT_PROFILES.get(stat_type, {})
    focus = profile.get("focus")

    shooting_score, shooting_debug = _get_zone_matchup_score(
        player.get("shooting_zones"),
        player.get("opp_def_zones"),
    )
    assist_score, assist_debug = _get_zone_matchup_score(
        player.get("assist_zones"),
        player.get("opp_assist_zones"),
    )
    three_zone_score, three_zone_debug = _get_zone_matchup_score(
        player.get("shooting_zones"),
        player.get("opp_def_zones"),
        zone_filter={"left_corner", "right_corner", "top_key"},
    )
    shot_type_score, shot_type_debug = _get_shot_type_matchup_score(player)
    play_type_score, play_type_debug = _get_play_type_matchup_score(player)
    paint_rank = _rank_to_ease(((player.get("opp_def_zones") or {}).get("paint") or {}).get("rank"))

    score = None
    details = {
        "shooting_zones": shooting_debug,
        "assist_zones": assist_debug,
        "three_zones": three_zone_debug,
        "shot_type": shot_type_debug,
        "play_type": play_type_debug,
        "paint_rank": _safe_float((((player.get("opp_def_zones") or {}).get("paint") or {}).get("rank"))),
    }

    if focus == "points":
        score = _weighted_average([
            (shooting_score, 0.30),
            (shot_type_score, 0.35),
            (play_type_score, 0.35),
        ])
    elif focus == "assists":
        score = _weighted_average([
            (assist_score, 0.70),
            (play_type_score, 0.30),
        ])
    elif focus == "rebounds":
        score = _weighted_average([
            (paint_rank, 1.0),
        ])
    elif focus == "threes":
        score = _weighted_average([
            (three_zone_score, 0.45),
            (shot_type_score, 0.55),
        ])
    elif focus == "stocks":
        score = None
    elif focus == "combo":
        component_scores = []
        for component, weight in profile.get("components", {}).items():
            component_ctx = _compute_matchup_context(player, component, side)
            if component_ctx["available"]:
                component_scores.append((component_ctx["raw_score"], weight))
        score = _weighted_average(component_scores)

    if score is None:
        return {
            "available": False,
            "raw_score": 0.0,
            "score": 0.0,
            "details": details,
        }

    directional_score = score * SIDE_MULTIPLIERS[side]
    return {
        "available": True,
        "raw_score": _clamp(score, -1.0, 1.0),
        "score": _clamp(directional_score, -1.0, 1.0),
        "details": details,
    }


def _compute_recent_form_context(player: Dict[str, Any], stat_type: str, line: float, side: str) -> Dict[str, Any]:
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    logs = _extract_logs(player)
    values = [value for value in (_stat_value_from_game(game, stat_type) for game in logs) if value is not None]

    recent5 = values[:5]
    recent10 = values[:10]
    recent20 = values[:20]
    recent5_avg = _average(recent5)
    recent10_avg = _average(recent10)
    recent20_avg = _average(recent20)

    def _hit_rate(sample: List[float]) -> Optional[float]:
        if not sample:
            return None
        if side == "over":
            hits = sum(1 for value in sample if value >= line)
        else:
            hits = sum(1 for value in sample if value <= line)
        return (hits / len(sample)) * 100.0

    hit_rate_5 = _hit_rate(recent5)
    hit_rate_10 = _hit_rate(recent10)
    hit_rate_20 = _hit_rate(recent20)

    gap_5 = None if recent5_avg is None else SIDE_MULTIPLIERS[side] * (recent5_avg - line)
    gap_10 = None if recent10_avg is None else SIDE_MULTIPLIERS[side] * (recent10_avg - line)
    gap_20 = None if recent20_avg is None else SIDE_MULTIPLIERS[side] * (recent20_avg - line)
    trend = None if recent5_avg is None or recent10_avg is None else recent5_avg - recent10_avg

    score = _clamp(
        (_normalize_score_by_scale(gap_5, scale) * 0.40)
        + (_normalize_score_by_scale(gap_10, scale) * 0.25)
        + (_normalize_score_by_scale(gap_20, scale) * 0.10)
        + (_normalize_score_by_scale((hit_rate_5 or 50.0) - 50.0, 35.0) * 0.15)
        + (_normalize_score_by_scale((hit_rate_10 or 50.0) - 50.0, 40.0) * 0.10),
        -1.0,
        1.0,
    )

    available = len(recent10) >= 4
    return {
        "available": available,
        "score": score if available else 0.0,
        "raw_score": score if available else 0.0,
        "details": {
            "samples": {
                "last_5": len(recent5),
                "last_10": len(recent10),
                "last_20": len(recent20),
            },
            "averages": {
                "last_5": _round(recent5_avg),
                "last_10": _round(recent10_avg),
                "last_20": _round(recent20_avg),
            },
            "hit_rates": {
                "last_5": _round(hit_rate_5),
                "last_10": _round(hit_rate_10),
                "last_20": _round(hit_rate_20),
            },
            "trend": _round(trend),
        },
    }


def _compute_projection_context(player: Dict[str, Any], stat_type: str, line: float, side: str) -> Dict[str, Any]:
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    season_avg = _stat_value_from_stats(player.get("stats", {}), stat_type)
    logs = _extract_logs(player)
    values = [value for value in (_stat_value_from_game(game, stat_type) for game in logs[:10]) if value is not None]
    recent5_avg = _average(values[:5])
    recent10_avg = _average(values[:10])

    season_minutes = _safe_float((player.get("stats") or {}).get("MIN"))
    recent_minutes = _average([
        _safe_float(game.get("MIN")) for game in logs[:5]
    ])
    minute_delta_ratio = None
    if season_minutes and recent_minutes:
        minute_delta_ratio = (recent_minutes - season_minutes) / max(season_minutes, 1.0)

    baseline_projection = _weighted_average([
        (season_avg, 0.48),
        (recent10_avg, 0.32),
        (recent5_avg, 0.20),
    ])
    if baseline_projection is not None and minute_delta_ratio is not None:
        baseline_projection += baseline_projection * _clamp(minute_delta_ratio, -0.20, 0.20) * 0.18

    projection_gap = None if baseline_projection is None else SIDE_MULTIPLIERS[side] * (baseline_projection - line)
    score = _normalize_score_by_scale(projection_gap, scale)
    available = baseline_projection is not None
    return {
        "available": available,
        "score": score if available else 0.0,
        "raw_score": score if available else 0.0,
        "details": {
            "season_avg": _round(season_avg),
            "recent_5_avg": _round(recent5_avg),
            "recent_10_avg": _round(recent10_avg),
            "recent_minutes": _round(recent_minutes),
            "season_minutes": _round(season_minutes),
            "baseline_projection": _round(baseline_projection),
            "projection_gap": _round(projection_gap),
        },
    }


def _compute_head_to_head_context(
    player: Dict[str, Any],
    opponent: Optional[str],
    stat_type: str,
    line: float,
    side: str,
) -> Dict[str, Any]:
    if not opponent:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    values = []
    for game in _extract_logs(player):
        matchup = str(game.get("MATCHUP") or "")
        if matchup.endswith(f" {opponent}"):
            stat_value = _stat_value_from_game(game, stat_type)
            if stat_value is not None:
                values.append(stat_value)

    h2h_avg = _average(values[:5])
    h2h_gap = None if h2h_avg is None else SIDE_MULTIPLIERS[side] * (h2h_avg - line)
    available = len(values) >= 2
    score = _normalize_score_by_scale(h2h_gap, scale) if available else 0.0
    return {
        "available": available,
        "score": score,
        "raw_score": score,
        "details": {
            "sample_size": len(values),
            "average": _round(h2h_avg),
            "gap_vs_line": _round(h2h_gap),
        },
    }


def _compute_back_to_back_context(
    player: Dict[str, Any],
    stat_type: str,
    game_date: str,
    side: str,
) -> Dict[str, Any]:
    logs = _extract_logs(player)
    if not logs:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    current_game_date = _parse_date(game_date)
    latest_logged_date = _parse_date(logs[0].get("GAME_DATE"))
    if current_game_date is None or latest_logged_date is None:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    current_is_b2b = (current_game_date - latest_logged_date).days == 1
    if not current_is_b2b:
        return {
            "available": False,
            "score": 0.0,
            "raw_score": 0.0,
            "details": {
                "current_is_b2b": False,
            },
        }

    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    values = []
    b2b_values = []
    dated_logs = [(_parse_date(game.get("GAME_DATE")), game) for game in logs]
    dated_logs = [(date_value, game) for date_value, game in dated_logs if date_value is not None]
    for idx, (date_value, game) in enumerate(dated_logs):
        stat_value = _stat_value_from_game(game, stat_type)
        if stat_value is not None:
            values.append(stat_value)
        if idx + 1 >= len(dated_logs) or stat_value is None:
            continue
        next_date_value = dated_logs[idx + 1][0]
        if (date_value - next_date_value).days == 1:
            b2b_values.append(stat_value)

    overall_avg = _average(values[:20])
    b2b_avg = _average(b2b_values[:10])
    if b2b_avg is not None and overall_avg is not None and len(b2b_values) >= 2:
        delta = b2b_avg - overall_avg
        score = _normalize_score_by_scale(SIDE_MULTIPLIERS[side] * delta, scale)
        return {
            "available": True,
            "score": score,
            "raw_score": score,
            "details": {
                "current_is_b2b": True,
                "sample_size": len(b2b_values),
                "overall_avg": _round(overall_avg),
                "b2b_avg": _round(b2b_avg),
                "delta_vs_overall": _round(delta),
            },
        }

    base_bias = profile.get("b2b_bias", -0.08)
    score = _clamp(base_bias * SIDE_MULTIPLIERS[side], -0.25, 0.25)
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "current_is_b2b": True,
            "sample_size": len(b2b_values),
            "fallback_bias": _round(base_bias),
        },
    }


def _build_style_vector(player: Dict[str, Any]) -> Dict[str, float]:
    vector: Dict[str, float] = {}
    stats = player.get("stats") if isinstance(player.get("stats"), dict) else {}
    vector["min"] = (_safe_float(stats.get("MIN"), 0.0) or 0.0) / 40.0
    vector["usg"] = (_safe_float(stats.get("USG_PCT"), 0.0) or 0.0) / 100.0
    vector["fga"] = (_safe_float(stats.get("FGA"), 0.0) or 0.0) / 25.0
    vector["potential_ast"] = (_safe_float(stats.get("POTENTIAL_AST"), 0.0) or 0.0) / 18.0
    vector["reb_chances"] = (_safe_float(stats.get("REB_CHANCES"), 0.0) or 0.0) / 22.0

    shot_type_analysis = player.get("shot_type_analysis", {})
    player_shot_type = shot_type_analysis.get("player", {}) if isinstance(shot_type_analysis, dict) else {}
    for key in ("catch_and_shoot", "pull_up", "less_than_10_ft"):
        vector[f"shot:{key}"] = (_safe_float((player_shot_type.get(key) or {}).get("percentage"), 0.0) or 0.0) / 100.0

    play_type_index = {
        label: idx for idx, label in enumerate(PLAY_TYPE_LABELS)
    }
    for label in PLAY_TYPE_LABELS:
        vector[f"play:{label}"] = 0.0
    for item in player.get("play_type_analysis") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("type") or "")
        if label not in play_type_index:
            continue
        vector[f"play:{label}"] = (_parse_percent(item.get("percent")) or 0.0) / 100.0

    return vector


def _fingerprint_distance(left: Dict[str, float], right: Dict[str, float]) -> float:
    keys = set(left.keys()) | set(right.keys())
    if not keys:
        return 0.0
    deltas = [abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys]
    return sum(deltas) / len(deltas)


def _select_market_for_side(
    stat_type: str,
    book_props: Dict[str, Dict[str, Any]],
    side: str,
) -> Optional[Dict[str, Any]]:
    chosen = None
    consensus_lines = []
    implieds = []
    books_summary = {}

    for book, prop in (book_props or {}).items():
        if book not in SUPPORTED_BOOKS or not isinstance(prop, dict):
            continue
        line = _safe_float(prop.get("line"))
        if line is None:
            continue
        side_odds = _safe_float(prop.get(side))
        opposite_odds = _safe_float(prop.get("under" if side == "over" else "over"))
        implied = _american_to_implied(side_odds)
        consensus_lines.append(line)
        if implied is not None:
            implieds.append(implied)
        books_summary[book] = {
            "line": line,
            "side_odds": side_odds,
            "opposite_odds": opposite_odds,
            "game_date": _normalize_game_date(prop.get("game_date")),
            "game_id": prop.get("game_id"),
        }

        if chosen is None:
            chosen = {
                "book": book,
                "line": line,
                "odds": side_odds,
                "opposite_odds": opposite_odds,
                "game_date": _normalize_game_date(prop.get("game_date")),
                "game_id": prop.get("game_id"),
                "implied": implied,
            }
            continue

        current_tuple = (
            line,
            implied if implied is not None else 1.0,
        )
        chosen_tuple = (
            chosen["line"],
            chosen["implied"] if chosen["implied"] is not None else 1.0,
        )
        if side == "over":
            should_replace = current_tuple < chosen_tuple
        else:
            should_replace = (-current_tuple[0], current_tuple[1]) < (-chosen_tuple[0], chosen_tuple[1])
        if should_replace:
            chosen = {
                "book": book,
                "line": line,
                "odds": side_odds,
                "opposite_odds": opposite_odds,
                "game_date": _normalize_game_date(prop.get("game_date")),
                "game_id": prop.get("game_id"),
                "implied": implied,
            }

    if chosen is None:
        return None

    return {
        "stat_type": stat_type,
        "side": side,
        "chosen": chosen,
        "books": books_summary,
        "consensus_line": _average(consensus_lines),
        "average_side_implied": _average(implieds),
        "available_books": len(books_summary),
    }


def _compute_market_context(market_selection: Dict[str, Any], side: str) -> Dict[str, Any]:
    chosen = market_selection.get("chosen", {})
    chosen_line = _safe_float(chosen.get("line"))
    consensus_line = _safe_float(market_selection.get("consensus_line"))
    line_delta = None
    if chosen_line is not None and consensus_line is not None:
        if side == "over":
            line_delta = consensus_line - chosen_line
        else:
            line_delta = chosen_line - consensus_line

    chosen_implied = _american_to_implied(chosen.get("odds"))
    average_implied = _safe_float(market_selection.get("average_side_implied"))
    price_delta = None
    if chosen_implied is not None and average_implied is not None:
        price_delta = average_implied - chosen_implied

    score = _clamp(
        (_normalize_score_by_scale(line_delta, 1.0) * 0.75)
        + (_normalize_score_by_scale(price_delta, 0.10) * 0.25),
        -1.0,
        1.0,
    )
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "chosen_book": chosen.get("book"),
            "chosen_line": _round(chosen_line),
            "consensus_line": _round(consensus_line),
            "line_delta_vs_consensus": _round(line_delta),
            "chosen_implied": _round(chosen_implied, 3),
            "average_implied": _round(average_implied, 3),
            "price_delta": _round(price_delta, 3),
            "available_books": market_selection.get("available_books", 0),
            "books": market_selection.get("books", {}),
        },
    }


def _compute_line_movement_context(
    line_lookup: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]],
    player_id: str,
    stat_type: str,
    book: str,
    game_date: str,
    side: str,
    chosen_odds: Any,
    chosen_line: float,
) -> Dict[str, Any]:
    history = line_lookup.get((player_id, stat_type, book, game_date), [])
    if len(history) < 2:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    opening = history[0]
    latest = history[-1]
    open_line = _safe_float(opening.get("line"))
    latest_line = _safe_float(latest.get("line"))
    current_line = chosen_line if chosen_line is not None else latest_line
    favorable_line_change = None
    if current_line is not None and open_line is not None:
        if side == "over":
            favorable_line_change = open_line - current_line
        else:
            favorable_line_change = current_line - open_line

    opening_implied = _american_to_implied(opening.get(side))
    current_implied = _american_to_implied(chosen_odds)
    favorable_price_change = None
    if opening_implied is not None and current_implied is not None:
        favorable_price_change = opening_implied - current_implied

    score = _clamp(
        (_normalize_score_by_scale(favorable_line_change, 1.0) * 0.75)
        + (_normalize_score_by_scale(favorable_price_change, 0.08) * 0.25),
        -1.0,
        1.0,
    )
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "opening_line": _round(open_line),
            "latest_snapshot_line": _round(latest_line),
            "current_line": _round(current_line),
            "favorable_line_change": _round(favorable_line_change),
            "opening_side_implied": _round(opening_implied, 3),
            "current_side_implied": _round(current_implied, 3),
            "favorable_price_change": _round(favorable_price_change, 3),
            "snapshots_seen": len(history),
        },
    }


def _compute_similar_players_context(
    entry: Dict[str, Any],
    stat_type: str,
    side: str,
    current_line: float,
    active_entries: List[Dict[str, Any]],
    style_cache: Dict[int, Dict[str, float]],
) -> Dict[str, Any]:
    player = entry["player"]
    player_id = int(player.get("id"))
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    target_average = _stat_value_from_stats(player.get("stats", {}), stat_type)
    target_position = _simple_position(player.get("position"))
    target_style = style_cache.get(player_id, {})

    candidates = []
    for candidate_entry in active_entries:
        candidate_player = candidate_entry["player"]
        candidate_id = int(candidate_player.get("id"))
        if candidate_id == player_id:
            continue
        if stat_type not in candidate_entry["props"]:
            continue
        candidate_position = _simple_position(candidate_player.get("position"))
        if candidate_position != target_position and {candidate_position, target_position} not in [{"F", "C"}]:
            continue

        candidate_market = _select_market_for_side(stat_type, candidate_entry["props"][stat_type], side)
        if not candidate_market:
            continue
        candidate_line = _safe_float(candidate_market["chosen"].get("line"))
        candidate_average = _stat_value_from_stats(candidate_player.get("stats", {}), stat_type)
        if candidate_line is None or candidate_average is None:
            continue

        distance = (
            _normalize_score_by_scale(abs((candidate_average or 0.0) - (target_average or 0.0)), scale) * 0.35
            + _normalize_score_by_scale(abs(candidate_line - current_line), scale) * 0.30
            + _fingerprint_distance(target_style, style_cache.get(candidate_id, {})) * 0.25
            + (0.10 if candidate_position != target_position else 0.0)
        )
        candidates.append({
            "id": candidate_id,
            "name": candidate_player.get("name"),
            "team": candidate_player.get("team"),
            "line": candidate_line,
            "average": candidate_average,
            "distance": distance,
        })

    candidates.sort(key=lambda item: item["distance"])
    top_candidates = candidates[:6]
    if len(top_candidates) < 3:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {"candidates": top_candidates}}

    comp_gap = _average([
        candidate["average"] - candidate["line"]
        for candidate in top_candidates
    ])
    directional_gap = None if comp_gap is None else SIDE_MULTIPLIERS[side] * comp_gap
    score = _normalize_score_by_scale(directional_gap, scale)
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "sample_size": len(top_candidates),
            "average_gap_vs_line": _round(comp_gap),
            "candidates": [
                {
                    "id": candidate["id"],
                    "name": candidate["name"],
                    "team": candidate["team"],
                    "line": _round(candidate["line"]),
                    "average": _round(candidate["average"]),
                    "distance": round(candidate["distance"], 3),
                }
                for candidate in top_candidates
            ],
        },
    }


def _component_available_weight(components: Dict[str, Dict[str, Any]]) -> float:
    available_weight = 0.0
    for component_name, component in components.items():
        if component.get("available"):
            available_weight += COMPONENT_WEIGHTS.get(component_name, 0.0)
    return available_weight


def _build_reason_strings(
    side: str,
    line: float,
    stat_type: str,
    components: Dict[str, Dict[str, Any]],
) -> List[str]:
    reasons = []
    projection_details = components["projection"]["details"]
    projection_gap = projection_details.get("projection_gap")
    if projection_gap is not None:
        direction_word = "above" if projection_gap >= 0 else "below"
        reasons.append(
            f"Baseline projection sits {abs(projection_gap):.1f} {direction_word} the line."
        )

    recent_details = components["recent_form"]["details"]
    recent_10_avg = recent_details.get("averages", {}).get("last_10")
    recent_10_hit_rate = recent_details.get("hit_rates", {}).get("last_10")
    if recent_10_avg is not None and recent_10_hit_rate is not None:
        reasons.append(
            f"Recent form: last 10 average {recent_10_avg:.1f} with {recent_10_hit_rate:.0f}% {_side_name(side).lower()} rate."
        )

    if components["matchup"]["available"]:
        reasons.append("Opponent matchup grades favorable for this shot/profile mix.")

    market_details = components["market"]["details"]
    line_delta = market_details.get("line_delta_vs_consensus")
    chosen_book = market_details.get("chosen_book")
    if line_delta is not None and abs(line_delta) >= 0.25 and chosen_book:
        reasons.append(
            f"{BOOK_LABELS.get(chosen_book, chosen_book)} offers a {abs(line_delta):.1f}-point edge versus the market consensus."
        )

    movement_details = components["line_movement"]["details"]
    line_change = movement_details.get("favorable_line_change")
    if line_change is not None and abs(line_change) >= 0.25:
        reasons.append(f"Today’s movement improved this {_side_name(side).lower()} number by {abs(line_change):.1f}.")

    similar_details = components["similar_players"]["details"]
    average_gap = similar_details.get("average_gap_vs_line")
    if average_gap is not None:
        direction_word = "above" if average_gap >= 0 else "below"
        reasons.append(
            f"Comparable profiles average {abs(average_gap):.1f} {direction_word} their own lines."
        )

    b2b_details = components["back_to_back"]["details"]
    if b2b_details.get("current_is_b2b"):
        reasons.append("Back-to-back context is baked into the score.")

    return reasons[:5]


def _build_candidate(
    entry: Dict[str, Any],
    stat_type: str,
    side: str,
    market_selection: Dict[str, Any],
    active_entries: List[Dict[str, Any]],
    style_cache: Dict[int, Dict[str, float]],
    line_lookup: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    player = entry["player"]
    profile = STAT_PROFILES.get(stat_type)
    if not profile:
        return None

    chosen = market_selection.get("chosen", {})
    line = _safe_float(chosen.get("line"))
    if line is None:
        return None

    book = chosen.get("book")
    game_date = entry["game_date"] or chosen.get("game_date") or ""
    player_id = str(player.get("id"))
    opponent = entry.get("opponent")

    components = {
        "projection": _compute_projection_context(player, stat_type, line, side),
        "recent_form": _compute_recent_form_context(player, stat_type, line, side),
        "matchup": _compute_matchup_context(player, stat_type, side),
        "market": _compute_market_context(market_selection, side),
        "line_movement": _compute_line_movement_context(
            line_lookup,
            player_id=player_id,
            stat_type=stat_type,
            book=book,
            game_date=game_date,
            side=side,
            chosen_odds=chosen.get("odds"),
            chosen_line=line,
        ),
        "similar_players": _compute_similar_players_context(
            entry,
            stat_type,
            side,
            line,
            active_entries,
            style_cache,
        ),
        "head_to_head": _compute_head_to_head_context(player, opponent, stat_type, line, side),
        "back_to_back": _compute_back_to_back_context(player, stat_type, game_date, side),
    }

    weighted_score = 0.0
    for component_name, component in components.items():
        weighted_score += COMPONENT_WEIGHTS.get(component_name, 0.0) * component.get("score", 0.0)

    available_weight = _component_available_weight(components)
    confidence = round((available_weight / TOTAL_COMPONENT_WEIGHT) * 100.0, 1) if TOTAL_COMPONENT_WEIGHT else 0.0
    confidence_multiplier = 0.85 + (min(confidence, 100.0) / 100.0) * 0.15
    edge_score = round(_clamp(50.0 + (weighted_score * 45.0 * confidence_multiplier), 1.0, 99.0), 1)

    reasons = _build_reason_strings(side, line, stat_type, components)
    recommendation_key = f"{player_id}|{game_date}|{stat_type}|{side}"
    return {
        "recommendation_key": recommendation_key,
        "player_id": int(player.get("id")),
        "player_name": player.get("name"),
        "player_headshot_url": _player_headshot_url(player.get("id")),
        "team": player.get("team"),
        "opponent": opponent,
        "position": player.get("position"),
        "game_id": entry.get("game_id") or chosen.get("game_id"),
        "game_date": game_date,
        "game_time_et": entry.get("game_time_et"),
        "sportsbook": book,
        "sportsbook_label": BOOK_LABELS.get(book, str(book).title()),
        "stat_type": stat_type,
        "stat_label": profile.get("display"),
        "pick": side,
        "pick_label": _side_name(side),
        "line": line,
        "odds": _safe_float(chosen.get("odds")),
        "odds_display": _format_odds(chosen.get("odds")),
        "opposite_odds": _safe_float(chosen.get("opposite_odds")),
        "edge_score": edge_score,
        "confidence": confidence,
        "signal_score": round(weighted_score, 3),
        "reasons": reasons,
        "inputs": {
            "projection": components["projection"]["details"],
            "recent_form": components["recent_form"]["details"],
            "matchup": components["matchup"]["details"],
            "market": components["market"]["details"],
            "line_movement": components["line_movement"]["details"],
            "similar_players": components["similar_players"]["details"],
            "head_to_head": components["head_to_head"]["details"],
            "back_to_back": components["back_to_back"]["details"],
        },
        "component_scores": {
            component_name: round(component.get("score", 0.0), 3)
            for component_name, component in components.items()
        },
        "available_component_weights": round(available_weight, 3),
    }


def _diversify_candidates(candidates: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    output = []
    per_player_counts: Dict[int, int] = {}
    per_game_counts: Dict[str, int] = {}
    seen_keys = set()

    for candidate in candidates:
        recommendation_key = candidate.get("recommendation_key")
        if recommendation_key in seen_keys:
            continue
        player_id = int(candidate.get("player_id"))
        game_id = str(candidate.get("game_id") or "")
        if per_player_counts.get(player_id, 0) >= 2:
            continue
        if game_id and per_game_counts.get(game_id, 0) >= 5:
            continue

        seen_keys.add(recommendation_key)
        per_player_counts[player_id] = per_player_counts.get(player_id, 0) + 1
        if game_id:
            per_game_counts[game_id] = per_game_counts.get(game_id, 0) + 1
        output.append(candidate)

        if len(output) >= limit:
            break

    return output


def _build_sportsbook_boards(candidates: List[Dict[str, Any]], limit_per_book: int) -> Dict[str, Dict[str, Any]]:
    boards: Dict[str, Dict[str, Any]] = {}
    ordered_books = sorted(SUPPORTED_BOOKS, key=_book_sort_key)

    for book in ordered_books:
        book_candidates = [dict(candidate) for candidate in candidates if candidate.get("sportsbook") == book]
        book_candidates.sort(key=_candidate_sort_key, reverse=True)
        book_recommendations = _diversify_candidates(book_candidates, limit_per_book)
        for sportsbook_rank, recommendation in enumerate(book_recommendations, start=1):
            recommendation["sportsbook_rank"] = sportsbook_rank

        boards[book] = {
            "sportsbook": book,
            "sportsbook_label": BOOK_LABELS.get(book, str(book).title()),
            "count": len(book_recommendations),
            "limit": limit_per_book,
            "recommendations": book_recommendations,
        }

    return boards


def _write_history_snapshot(payload: Dict[str, Any]) -> None:
    history = _load_json(EDGE_SCORE_HISTORY_PATH, {"snapshots": []})
    if not isinstance(history, dict):
        history = {"snapshots": []}
    snapshots = history.get("snapshots", [])
    if not isinstance(snapshots, list):
        snapshots = []

    snapshots.append({
        "generated_at": payload.get("generated_at"),
        "refresh_label": payload.get("refresh_label"),
        "game_dates": payload.get("game_dates", []),
        "top_15": [
            {
                "rank": recommendation.get("rank"),
                "recommendation_key": recommendation.get("recommendation_key"),
                "player_name": recommendation.get("player_name"),
                "team": recommendation.get("team"),
                "stat_type": recommendation.get("stat_type"),
                "pick": recommendation.get("pick"),
                "sportsbook": recommendation.get("sportsbook"),
                "line": recommendation.get("line"),
                "edge_score": recommendation.get("edge_score"),
            }
            for recommendation in payload.get("recommendations", [])
        ],
    })
    history["snapshots"] = snapshots[-120:]
    _write_json_atomic(EDGE_SCORE_HISTORY_PATH, history)


def _state_snapshot_for_recommendations(recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = {}
    for recommendation in recommendations:
        snapshot[recommendation["recommendation_key"]] = {
            "player_name": recommendation.get("player_name"),
            "stat_type": recommendation.get("stat_type"),
            "pick": recommendation.get("pick"),
            "sportsbook": recommendation.get("sportsbook"),
            "line": recommendation.get("line"),
            "odds": recommendation.get("odds"),
        }
    return snapshot


def _filter_official_alert_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    per_book_counts: Dict[str, int] = {}
    filtered = []
    for recommendation in recommendations:
        signal_score = _safe_float(recommendation.get("edge_score"), 0.0) or 0.0
        book = str(recommendation.get("sportsbook") or "").strip().lower()
        if book not in SUPPORTED_BOOKS:
            continue
        if signal_score < EDGE_DISCORD_MIN_SIGNAL_SCORE:
            continue
        if per_book_counts.get(book, 0) >= EDGE_DISCORD_PER_BOOK_LIMIT:
            continue
        filtered.append(recommendation)
        per_book_counts[book] = per_book_counts.get(book, 0) + 1
    return filtered


def _filter_tracker_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = []
    for recommendation in recommendations:
        signal_score = _safe_float(recommendation.get("edge_score"), 0.0) or 0.0
        if signal_score < EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE:
            continue
        filtered.append(recommendation)
    return filtered


def _compute_notification_delta(
    recommendations: List[Dict[str, Any]],
    state: Dict[str, Any],
    refresh_label: str,
) -> Dict[str, Any]:
    official_recommendations = _filter_official_alert_recommendations(recommendations)
    current_snapshot = _state_snapshot_for_recommendations(official_recommendations)
    slate_dates = sorted({
        _normalize_game_date(recommendation.get("game_date"))
        for recommendation in official_recommendations
        if _normalize_game_date(recommendation.get("game_date"))
    })
    slate_date = slate_dates[0] if slate_dates else ""

    alert_state_by_date = state.get("discord_alert_state_by_date", {})
    if not isinstance(alert_state_by_date, dict):
        alert_state_by_date = {}
    date_state = alert_state_by_date.get(slate_date, {}) if slate_date else {}
    if not isinstance(date_state, dict):
        date_state = {}

    previous_snapshot = date_state.get("last_sent_snapshot", {})
    if not isinstance(previous_snapshot, dict):
        previous_snapshot = {}

    changes = []
    for recommendation in official_recommendations:
        key = recommendation["recommendation_key"]
        previous = previous_snapshot.get(key)
        reason_flags = []
        if previous is None:
            reason_flags.append("new entrant")
        else:
            if recommendation.get("sportsbook") != previous.get("sportsbook"):
                reason_flags.append("best book changed")
            if abs((_safe_float(recommendation.get("line"), 0.0) or 0.0) - (_safe_float(previous.get("line"), 0.0) or 0.0)) >= EDGE_DISCORD_LINE_MOVE_POINTS:
                reason_flags.append("line moved")
            if abs((_safe_float(recommendation.get("odds"), 0.0) or 0.0) - (_safe_float(previous.get("odds"), 0.0) or 0.0)) >= EDGE_DISCORD_ODDS_MOVE_AMERICAN:
                reason_flags.append("odds moved")

        if reason_flags:
            changes.append({
                "recommendation_key": key,
                "rank": recommendation.get("rank"),
                "player_name": recommendation.get("player_name"),
                "stat_type": recommendation.get("stat_type"),
                "pick": recommendation.get("pick"),
                "sportsbook": recommendation.get("sportsbook"),
                "line": recommendation.get("line"),
                "edge_score": recommendation.get("edge_score"),
                "reasons": reason_flags,
            })

    removed = [
        {
            "recommendation_key": key,
            "player_name": payload.get("player_name"),
            "stat_type": payload.get("stat_type"),
            "pick": payload.get("pick"),
        }
        for key, payload in previous_snapshot.items()
        if key not in current_snapshot
    ]

    opening_sent = bool(date_state.get("opening_sent_at"))
    pre_tip_sent = bool(date_state.get("pre_tip_sent_at"))
    last_sent_at_raw = date_state.get("last_sent_at")
    last_sent_at = _parse_dt(last_sent_at_raw)
    now = get_et_now()
    cooldown_active = False
    if last_sent_at is not None and EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS > 0:
        cooldown_active = (now - last_sent_at).total_seconds() < EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS

    alert_kind = "none"
    send_recommendations: List[Dict[str, Any]] = []
    board_changed = bool(changes or removed)
    if official_recommendations:
        if refresh_label == "pre_game" and not pre_tip_sent and (not opening_sent or board_changed):
            alert_kind = "pre_tip"
            send_recommendations = official_recommendations
        elif not opening_sent:
            alert_kind = "opening"
            send_recommendations = official_recommendations
        elif changes:
            alert_kind = "update"
            changed_keys = {
                str(change.get("recommendation_key") or "").strip()
                for change in changes
                if str(change.get("recommendation_key") or "").strip()
            }
            send_recommendations = [
                recommendation
                for recommendation in official_recommendations
                if str(recommendation.get("recommendation_key") or "").strip() in changed_keys
            ]

    should_send = bool(send_recommendations)
    if alert_kind == "pre_tip":
        cooldown_active = False
    if cooldown_active and alert_kind == "update":
        should_send = False

    return {
        "changes": changes,
        "removed": removed,
        "cooldown_active": cooldown_active,
        "should_send": should_send,
        "current_snapshot": current_snapshot,
        "official_recommendations": official_recommendations,
        "send_recommendations": send_recommendations,
        "alert_kind": alert_kind,
        "slate_date": slate_date,
        "opening_sent": opening_sent,
        "pre_tip_sent": pre_tip_sent,
    }


LONG_STAT_LABELS = {
    "PTS": "Points",
    "REB": "Rebounds",
    "AST": "Assists",
    "FG3M": "3-Pointers Made",
    "BLK": "Blocks",
    "STL": "Steals",
    "STL+BLK": "Steals + Blocks",
    "PTS+REB+AST": "Points + Rebounds + Assists",
    "PTS+REB": "Points + Rebounds",
    "PTS+AST": "Points + Assists",
    "REB+AST": "Rebounds + Assists",
}

NOTIFICATION_REASON_LABELS = {
    "new entrant": "new to the board",
    "best book changed": "best book changed",
    "line moved": "line moved",
    "odds moved": "price moved",
    "score moved": "Signal Score moved",
    "rank changed": "rank changed",
}

REFRESH_LABELS = {
    "pipeline": "Daily Refresh",
    "intraday": "Intraday Refresh",
    "pre_game": "Pre-Tip Refresh",
}

RESULT_STATUS_LABELS = {
    "cashed": "Cashed",
    "lost": "Lost",
    "push": "Pushed",
    "void": "Void",
}


def _long_stat_label(stat_type: Any, fallback: Any = None) -> str:
    normalized = str(stat_type or "").strip().upper()
    return LONG_STAT_LABELS.get(normalized) or str(fallback or stat_type or "Prop")


def _normalize_person_name(raw_value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(raw_value or "").lower()).strip()
    return " ".join(text.split())


def _discord_book_label(recommendation: Dict[str, Any]) -> str:
    sportsbook_label = recommendation.get("sportsbook_label") or BOOK_LABELS.get(
        recommendation.get("sportsbook"),
        str(recommendation.get("sportsbook") or "").title(),
    )
    odds_display = recommendation.get("odds_display") or _format_odds(recommendation.get("odds"))
    if recommendation.get("sportsbook") == "pp" or odds_display == "-":
        return f"{sportsbook_label} line"
    return f"{sportsbook_label} {odds_display}"


def _discord_signal_summary(recommendation: Dict[str, Any]) -> str:
    inputs = recommendation.get("inputs", {})
    side = str(recommendation.get("pick") or "").lower()

    snippets: List[str] = []
    line = _safe_float(recommendation.get("line"))
    baseline_projection = _safe_float(inputs.get("projection", {}).get("baseline_projection"))
    projection_gap = _safe_float(inputs.get("projection", {}).get("projection_gap"))
    projection_delta = None
    if baseline_projection is not None and line is not None:
        projection_delta = baseline_projection - line
    elif projection_gap is not None and side in SIDE_MULTIPLIERS:
        projection_delta = projection_gap * SIDE_MULTIPLIERS[side]

    if projection_delta is not None and abs(projection_delta) >= 0.5:
        direction = "above" if projection_delta >= 0 else "below"
        snippets.append(f"baseline projection sits {abs(projection_delta):.1f} {direction} the line")

    recent_avg = _safe_float(inputs.get("recent_form", {}).get("averages", {}).get("last_10"))
    recent_hit_rate = _safe_float(inputs.get("recent_form", {}).get("hit_rates", {}).get("last_10"))
    if recent_avg is not None and recent_hit_rate is not None and recent_hit_rate >= 55:
        snippets.append(
            f"last 10 average is {recent_avg:.1f} with a {recent_hit_rate:.0f}% {_side_name(side).lower()} hit rate"
        )
    elif recent_hit_rate is not None and recent_hit_rate >= 55:
        snippets.append(f"last 10 {_side_name(side).lower()} hit rate is {recent_hit_rate:.0f}%")

    chosen_book = recommendation.get("sportsbook_label") or BOOK_LABELS.get(recommendation.get("sportsbook"))
    line_delta = _safe_float(inputs.get("market", {}).get("line_delta_vs_consensus"))
    if line_delta is not None and abs(line_delta) >= 0.25:
        snippets.append(f"{chosen_book} is {abs(line_delta):.1f} better than consensus")

    average_gap = _safe_float(inputs.get("similar_players", {}).get("average_gap_vs_line"))
    comp_sample = int(_safe_float(inputs.get("similar_players", {}).get("sample_size"), 0.0) or 0)
    if comp_sample >= 3 and average_gap is not None:
        direction = "above" if average_gap >= 0 else "below"
        snippets.append(f"{comp_sample} similar-player comps averaged {abs(average_gap):.1f} {direction} their lines")

    line_change = _safe_float(inputs.get("line_movement", {}).get("favorable_line_change"))
    if line_change is not None and abs(line_change) >= 0.25:
        snippets.append(f"market moved {abs(line_change):.1f} points toward the {_side_name(side).lower()}")

    deduped_snippets: List[str] = []
    for snippet in snippets:
        if snippet not in deduped_snippets:
            deduped_snippets.append(snippet)

    if not deduped_snippets:
        return "projection, form, and matchup data are generally aligned."
    deduped_snippets = deduped_snippets[:3]
    if len(deduped_snippets) == 1:
        return f"{deduped_snippets[0]}."
    if len(deduped_snippets) == 2:
        return f"{deduped_snippets[0]}, and {deduped_snippets[1]}."
    return f"{deduped_snippets[0]}; {deduped_snippets[1]}; {deduped_snippets[2]}."


def _serialize_recommendation_for_results(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rank": recommendation.get("rank"),
        "recommendation_key": recommendation.get("recommendation_key"),
        "player_id": recommendation.get("player_id"),
        "player_name": recommendation.get("player_name"),
        "team": recommendation.get("team"),
        "opponent": recommendation.get("opponent"),
        "game_id": recommendation.get("game_id"),
        "game_date": _normalize_game_date(recommendation.get("game_date")),
        "game_time_et": recommendation.get("game_time_et"),
        "sportsbook": recommendation.get("sportsbook"),
        "sportsbook_label": recommendation.get("sportsbook_label"),
        "stat_type": recommendation.get("stat_type"),
        "stat_label": recommendation.get("stat_label"),
        "pick": recommendation.get("pick"),
        "pick_label": recommendation.get("pick_label"),
        "line": _safe_float(recommendation.get("line")),
        "odds": _safe_float(recommendation.get("odds")),
        "odds_display": recommendation.get("odds_display"),
        "edge_score": _safe_float(recommendation.get("edge_score")),
        "first_logged_at": recommendation.get("first_logged_at"),
    }


def _queue_results_recap_payload(
    state: Dict[str, Any],
    recommendations: List[Dict[str, Any]],
    *,
    generated_at: Any,
    refresh_label: str,
    alert_kind: str,
) -> None:
    pending = state.get("pending_result_recaps", {})
    if not isinstance(pending, dict):
        pending = {}

    recommendations_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            continue
        game_date = _normalize_game_date(recommendation.get("game_date"))
        if not game_date:
            continue
        recommendations_by_date.setdefault(game_date, []).append(
            _serialize_recommendation_for_results(recommendation)
        )

    for game_date, recommendations_for_date in recommendations_by_date.items():
        existing_entry = pending.get(game_date, {})
        if not isinstance(existing_entry, dict):
            existing_entry = {}
        tracked = existing_entry.get("tracked_recommendations", {})
        if not isinstance(tracked, dict):
            tracked = {}

        for recommendation in recommendations_for_date:
            recommendation_key = recommendation.get("recommendation_key")
            if not recommendation_key or recommendation_key in tracked:
                continue
            tracked[recommendation_key] = {
                **recommendation,
                "first_alerted_at": generated_at,
                "first_alert_kind": alert_kind,
            }

        ordered_recommendations = sorted(
            tracked.values(),
            key=lambda recommendation: (
                int(recommendation.get("rank") or 999),
                str(recommendation.get("first_alerted_at") or ""),
            ),
        )

        pending[game_date] = {
            "game_date": game_date,
            "source_generated_at": generated_at,
            "refresh_label": refresh_label,
            "tracked_recommendations": tracked,
            "recommendations": ordered_recommendations,
        }

    state["pending_result_recaps"] = pending


def _compute_tracker_delta(
    recommendations: List[Dict[str, Any]],
    state: Dict[str, Any],
    *,
    slate_date: str,
    generated_at: Any,
) -> Dict[str, Any]:
    tracker_candidates = _filter_tracker_recommendations(recommendations)
    pending = state.get("pending_result_recaps", {})
    if not isinstance(pending, dict):
        pending = {}
    existing_entry = pending.get(slate_date, {}) if slate_date else {}
    if not isinstance(existing_entry, dict):
        existing_entry = {}
    tracked = existing_entry.get("tracked_recommendations", {})
    if not isinstance(tracked, dict):
        tracked = {}

    new_recommendations = []
    for recommendation in tracker_candidates:
        recommendation_key = str(recommendation.get("recommendation_key") or "").strip()
        if not recommendation_key or recommendation_key in tracked:
            continue
        tracker_recommendation = dict(recommendation)
        tracker_recommendation["first_logged_at"] = generated_at
        new_recommendations.append(tracker_recommendation)

    return {
        "tracker_candidates": tracker_candidates,
        "new_recommendations": new_recommendations,
        "should_send": bool(new_recommendations),
        "slate_date": slate_date,
    }


def _write_results_recap_history(entry: Dict[str, Any]) -> None:
    history = _load_json(EDGE_SCORE_RESULTS_HISTORY_PATH, {"recaps": []})
    if not isinstance(history, dict):
        history = {"recaps": []}
    recaps = history.get("recaps", [])
    if not isinstance(recaps, list):
        recaps = []
    recaps.append(entry)
    history["recaps"] = recaps[-60:]
    _write_json_atomic(EDGE_SCORE_RESULTS_HISTORY_PATH, history)


def _find_game_log_for_date(player: Dict[str, Any], game_date: str) -> Optional[Dict[str, Any]]:
    logs = player.get("game_log") if isinstance(player, dict) else []
    if not isinstance(logs, list):
        return None
    target_date = _normalize_game_date(game_date)
    for game in logs:
        if not isinstance(game, dict):
            continue
        if _normalize_game_date(game.get("GAME_DATE")) == target_date:
            return game
    return None


def _build_dnp_lookup(master_feed: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, set]]:
    lookup: Dict[Tuple[str, str], Dict[str, set]] = {}
    for player in master_feed:
        if not isinstance(player, dict):
            continue
        team = str(player.get("team") or "").strip()
        logs = player.get("game_log")
        if not team or not isinstance(logs, list):
            continue
        for game in logs:
            if not isinstance(game, dict):
                continue
            game_date = _normalize_game_date(game.get("GAME_DATE"))
            dnps = game.get("dnps")
            if not game_date or not isinstance(dnps, list):
                continue
            bucket = lookup.setdefault((team, game_date), {"ids": set(), "names": set()})
            for dnp in dnps:
                if not isinstance(dnp, dict):
                    continue
                dnp_id = str(dnp.get("id") or "").strip()
                if dnp_id:
                    bucket["ids"].add(dnp_id)
                normalized_name = _normalize_person_name(dnp.get("name"))
                if normalized_name:
                    bucket["names"].add(normalized_name)
    return lookup


def _grade_pick_result(stat_value: float, line: float, side: str) -> str:
    if abs(stat_value - line) < 1e-9:
        return "push"
    if side == "over":
        return "cashed" if stat_value > line else "lost"
    return "cashed" if stat_value < line else "lost"


def _grade_results_recap(
    recap_payload: Dict[str, Any],
    master_feed: List[Dict[str, Any]],
) -> Dict[str, Any]:
    player_lookup = {
        str(player.get("id")): player
        for player in master_feed
        if isinstance(player, dict) and player.get("id") is not None
    }
    dnp_lookup = _build_dnp_lookup(master_feed)

    graded_recommendations = []
    unresolved = []

    for recommendation in recap_payload.get("recommendations", []):
        if not isinstance(recommendation, dict):
            continue
        player_id = str(recommendation.get("player_id") or "").strip()
        player_name = recommendation.get("player_name")
        team = str(recommendation.get("team") or "").strip()
        game_date = _normalize_game_date(recommendation.get("game_date"))
        stat_type = recommendation.get("stat_type")
        line = _safe_float(recommendation.get("line"))
        side = str(recommendation.get("pick") or "").lower()

        player = player_lookup.get(player_id)
        if player is None or not game_date or line is None or side not in SIDE_MULTIPLIERS:
            unresolved.append(recommendation.get("recommendation_key"))
            continue

        game_log = _find_game_log_for_date(player, game_date)
        if game_log is not None:
            minutes = _safe_float(game_log.get("MIN"), 0.0) or 0.0
            if minutes <= 0:
                status = "void"
                final_value = None
                result_note = "No logged minutes"
            else:
                final_value = _stat_value_from_game(game_log, stat_type)
                if final_value is None:
                    unresolved.append(recommendation.get("recommendation_key"))
                    continue
                status = _grade_pick_result(final_value, line, side)
                result_note = None
        else:
            dnp_bucket = dnp_lookup.get((team, game_date), {"ids": set(), "names": set()})
            if player_id in dnp_bucket.get("ids", set()) or _normalize_person_name(player_name) in dnp_bucket.get("names", set()):
                status = "void"
                final_value = None
                result_note = "Listed as DNP"
            else:
                unresolved.append(recommendation.get("recommendation_key"))
                continue

        graded_recommendations.append({
            **recommendation,
            "result_status": status,
            "result_label": RESULT_STATUS_LABELS.get(status, status.title()),
            "final_value": _round(final_value),
            "result_note": result_note,
        })

    summary = {
        "cashed": sum(1 for recommendation in graded_recommendations if recommendation["result_status"] == "cashed"),
        "lost": sum(1 for recommendation in graded_recommendations if recommendation["result_status"] == "lost"),
        "push": sum(1 for recommendation in graded_recommendations if recommendation["result_status"] == "push"),
        "void": sum(1 for recommendation in graded_recommendations if recommendation["result_status"] == "void"),
        "graded_count": len(graded_recommendations),
        "unresolved_count": len(unresolved),
    }

    return {
        "ready": len(unresolved) == 0 and len(graded_recommendations) > 0,
        "unresolved": unresolved,
        "summary": summary,
        "recommendations": graded_recommendations,
    }


def _results_record_text(summary: Dict[str, Any]) -> str:
    base = f"{summary.get('cashed', 0)}-{summary.get('lost', 0)}"
    extras = []
    if summary.get("push", 0):
        extras.append(f"{summary.get('push', 0)} push")
    if summary.get("void", 0):
        extras.append(f"{summary.get('void', 0)} void")
    if extras:
        return f"{base} | {', '.join(extras)}"
    return base


def _result_status_emoji(status: str) -> str:
    if status == "cashed":
        return "✅"
    if status == "lost":
        return "❌"
    if status == "push":
        return "➖"
    return "⚪"


def _send_discord_results_recap(recap_payload: Dict[str, Any], graded_results: Dict[str, Any]) -> bool:
    webhook_url = _results_recap_webhook_url()
    if not webhook_url:
        return False

    summary = graded_results.get("summary", {})
    lines = []
    for display_rank, recommendation in enumerate(graded_results.get("recommendations", [])[:EDGE_LIMIT], start=1):
        stat_label = _long_stat_label(recommendation.get("stat_type"), recommendation.get("stat_label"))
        final_value = recommendation.get("final_value")
        final_text = f"{final_value:.1f}" if isinstance(final_value, (float, int)) else (recommendation.get("result_note") or "Void")
        tracked_at = _format_tracker_time(recommendation.get("first_alerted_at") or recommendation.get("first_logged_at"))
        status_emoji = _result_status_emoji(str(recommendation.get("result_status") or ""))
        lines.append(
            f"**#{display_rank} {status_emoji} {recommendation.get('player_name')}** — "
            f"{stat_label} {recommendation.get('pick_label')} {float(recommendation.get('line') or 0.0):.1f}\n"
            f"Tracked: {tracked_at} | Final: {final_text} | {_discord_book_label(recommendation)}"
        )

    recap_date = _normalize_game_date(recap_payload.get("game_date"))
    discord_payload = {
        "username": "NBA Dashboard Prop Tracker" if EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL else "NBA Dashboard Daily Props",
        "embeds": [
            {
                "title": "Daily Prop Tracker Results",
                "description": "\n".join(lines)[:3800],
                "color": 0x3B82F6,
                "timestamp": get_et_now().isoformat(),
                "fields": [
                    {
                        "name": "Slate Date",
                        "value": recap_date or "n/a",
                        "inline": True,
                    },
                    {
                        "name": "Record",
                        "value": _results_record_text(summary),
                        "inline": True,
                    },
                    {
                        "name": "Tracked Picks",
                        "value": str(summary.get("graded_count", 0)),
                        "inline": True,
                    },
                    {
                        "name": "Recapped",
                        "value": _format_discord_timestamp(get_et_now().isoformat()),
                        "inline": False,
                    },
                ],
            }
        ],
    }

    return _post_discord_webhook(webhook_url, discord_payload)


def _group_recommendations_by_sportsbook(
    recommendations: List[Dict[str, Any]],
    *,
    limit_per_book: int,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for recommendation in recommendations:
        book = str(recommendation.get("sportsbook") or "").strip().lower()
        if not book:
            continue
        grouped.setdefault(book, []).append(recommendation)

    grouped_output = []
    for book in sorted(grouped.keys(), key=_book_sort_key):
        book_recommendations = grouped[book]
        grouped_output.append({
            "sportsbook": book,
            "sportsbook_label": BOOK_LABELS.get(book, book.title()),
            "total_count": len(book_recommendations),
            "recommendations": book_recommendations[:limit_per_book],
        })
    return grouped_output


def _changed_books_summary(groups: List[Dict[str, Any]]) -> str:
    return ", ".join(
        f"{group['sportsbook_label']} ({group['total_count']})"
        for group in groups
    ) or "No changed sportsbook groups."


def _build_discord_book_embed(
    group: Dict[str, Any],
    *,
    alert_kind: str,
    change_lookup: Dict[str, Dict[str, Any]],
    channel_variant: str,
) -> Dict[str, Any]:
    book_recommendations = group["recommendations"]
    lines = []
    for display_rank, recommendation in enumerate(book_recommendations, start=1):
        stat_label = _long_stat_label(recommendation.get("stat_type"), recommendation.get("stat_label"))
        reason_prefix = ""
        change = change_lookup.get(str(recommendation.get("recommendation_key") or "").strip())
        if alert_kind == "update" and change:
            pretty_reasons = [
                NOTIFICATION_REASON_LABELS.get(reason, reason)
                for reason in (change.get("reasons", []) or ["updated"])
            ]
            reason_prefix = f"Change: {', '.join(pretty_reasons)}\n"
        if channel_variant == "tracker":
            first_logged_at = _format_tracker_time(recommendation.get("first_logged_at"))
            reason_prefix = f"First logged: {first_logged_at}\n"
        lines.append(
            f"**{display_rank}.** {recommendation['player_name']} — "
            f"{stat_label} {recommendation['pick_label']} {recommendation['line']:.1f}\n"
            f"{_discord_book_label(recommendation)} | Signal Score {recommendation['edge_score']:.1f}\n"
            f"{reason_prefix}Why: {_discord_signal_summary(recommendation)}"
        )

    overflow_count = max(0, int(group.get("total_count", 0)) - len(book_recommendations))
    if overflow_count:
        suffix = "in this tracker batch" if channel_variant == "tracker" else "in this alert"
        lines.append(f"`+{overflow_count} more {group['sportsbook_label']} spot(s) {suffix}`")

    book_embed: Dict[str, Any] = {
        "title": group["sportsbook_label"],
        "description": "\n\n".join(lines)[:3800],
        "color": BOOK_EMBED_COLORS.get(group["sportsbook"], 0x22C55E),
    }
    thumbnail_url = _sportsbook_logo_url(group["sportsbook"])
    if thumbnail_url:
        book_embed["thumbnail"] = {"url": thumbnail_url}
    return book_embed


def _build_discord_alert_payload(
    payload: Dict[str, Any],
    notification_delta: Dict[str, Any],
    *,
    channel_variant: str,
) -> Optional[Dict[str, Any]]:
    recommendations = notification_delta.get("send_recommendations", [])
    if not recommendations:
        return None

    alert_kind = notification_delta.get("alert_kind", "update")
    changes = notification_delta.get("changes", [])
    removed = notification_delta.get("removed", [])
    grouped_books = _group_recommendations_by_sportsbook(
        recommendations,
        limit_per_book=EDGE_DISCORD_PER_BOOK_LIMIT,
    )
    if not grouped_books:
        return None

    title_map = {
        "opening": "Today's Best Props",
        "update": "Today's Best Props Update",
        "pre_tip": "Today's Best Props Final",
    }
    refresh_label_text = REFRESH_LABELS.get(
        payload.get("refresh_label"),
        str(payload.get("refresh_label") or "Refresh").replace("_", " ").title(),
    )
    threshold_text = _format_signal_score_threshold(EDGE_DISCORD_MIN_SIGNAL_SCORE)
    rules_text = (
        f"Up to {EDGE_DISCORD_PER_BOOK_LIMIT} props per sportsbook at Signal Score {threshold_text}+."
    )
    books_summary = _changed_books_summary(grouped_books)
    removed_lines = [
        (
            f"{removed_item.get('player_name')} — "
            f"{_long_stat_label(removed_item.get('stat_type'), removed_item.get('stat_type'))} "
            f"{_side_name(removed_item.get('pick')).lower()}"
        )
        for removed_item in removed[:4]
    ]
    summary_fields: List[Dict[str, Any]] = []

    if alert_kind == "opening":
        description = (
            f"Posted { _format_discord_timestamp(payload.get('generated_at')) }. "
            "Top signals are grouped by sportsbook below."
        )
        summary_fields.extend([
            {
                "name": "Why this fired",
                "value": rules_text[:1024],
                "inline": False,
            },
            {
                "name": "Books in this alert",
                "value": books_summary[:1024],
                "inline": False,
            },
            {
                "name": "Slate",
                "value": ", ".join(payload.get("game_dates", [])) or "n/a",
                "inline": True,
            },
            {
                "name": "Signal Score",
                "value": "1-99 read on how strongly the current data supports a prop.",
                "inline": False,
            },
        ])
    else:
        description = (
            f"Posted { _format_discord_timestamp(payload.get('generated_at')) }. "
            "Only props with meaningful changes are listed below."
        )
        summary_fields.extend([
            {
                "name": "Why this updated",
                "value": "Only props with a new entrant, better book, line move, or price move are shown."[:1024],
                "inline": False,
            },
            {
                "name": "Books changed",
                "value": books_summary[:1024],
                "inline": False,
            },
        ])
        if removed_lines:
            summary_fields.append({
                "name": "Dropped",
                "value": "\n".join(removed_lines)[:700],
                "inline": False,
            })

    title_label = title_map.get(alert_kind) or "Today's Best Props Update"
    change_lookup = {
        str(change.get("recommendation_key") or "").strip(): change
        for change in changes
        if str(change.get("recommendation_key") or "").strip()
    }
    embeds = [
        {
            "title": f"{title_label} • {refresh_label_text}",
            "description": description[:4096],
            "color": 0x22C55E,
            "timestamp": payload.get("generated_at"),
            "fields": summary_fields,
        }
    ]

    for group in grouped_books:
        embeds.append(
            _build_discord_book_embed(
                group,
                alert_kind=alert_kind,
                change_lookup=change_lookup,
                channel_variant=channel_variant,
            )
        )

    username = "NBA Dashboard Daily Props"
    return {
        "username": username,
        "embeds": embeds,
    }


def _build_discord_tracker_payload(
    payload: Dict[str, Any],
    tracker_delta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    recommendations = tracker_delta.get("new_recommendations", [])
    if not recommendations:
        return None

    grouped_books = _group_recommendations_by_sportsbook(
        recommendations,
        limit_per_book=max(EDGE_DISCORD_PER_BOOK_LIMIT, 1),
    )
    if not grouped_books:
        return None

    refresh_label_text = REFRESH_LABELS.get(
        payload.get("refresh_label"),
        str(payload.get("refresh_label") or "Refresh").replace("_", " ").title(),
    )
    books_summary = _changed_books_summary(grouped_books)
    summary_fields: List[Dict[str, Any]] = [
        {
            "name": "Logged",
            "value": _format_discord_timestamp(payload.get("generated_at")),
            "inline": False,
        },
        {
            "name": "Tracker threshold",
            "value": f"Signal Score {_format_signal_score_threshold(EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE)}+",
            "inline": True,
        },
        {
            "name": "Slate",
            "value": ", ".join(payload.get("game_dates", [])) or "n/a",
            "inline": True,
        },
        {
            "name": "Books in this log",
            "value": books_summary[:1024],
            "inline": False,
        },
        {
            "name": "How this works",
            "value": (
                "This tracker logs a prop once when it first clears the threshold, freezes that first book/line, "
                "and grades that exact entry in tomorrow's results recap."
            )[:1024],
            "inline": False,
        },
    ]

    embeds = [
        {
            "title": f"Daily Prop Tracker • {refresh_label_text}",
            "description": "Running ledger of the highest-signal props from the live daily props feed.",
            "color": 0x3B82F6,
            "timestamp": payload.get("generated_at"),
            "fields": summary_fields,
        }
    ]
    for group in grouped_books:
        embeds.append(
            _build_discord_book_embed(
                group,
                alert_kind="opening",
                change_lookup={},
                channel_variant="tracker",
            )
        )

    return {
        "username": "NBA Dashboard Prop Tracker",
        "embeds": embeds,
    }


def _process_results_recaps(master_feed: List[Dict[str, Any]], state: Dict[str, Any]) -> Dict[str, Any]:
    pending = state.get("pending_result_recaps", {})
    if not isinstance(pending, dict) or not pending:
        return {"sent_dates": [], "state_changed": False}

    today_str = get_et_now().strftime("%Y-%m-%d")
    sent_history = state.get("sent_result_recaps", {})
    if not isinstance(sent_history, dict):
        sent_history = {}

    sent_dates: List[str] = []
    state_changed = False
    remaining_pending = {}

    for game_date in sorted(pending.keys()):
        recap_payload = pending.get(game_date)
        if not isinstance(recap_payload, dict):
            continue
        normalized_date = _normalize_game_date(game_date)
        if not normalized_date:
            continue
        if normalized_date >= today_str:
            remaining_pending[normalized_date] = recap_payload
            continue
        if normalized_date in sent_history:
            continue

        graded_results = _grade_results_recap(recap_payload, master_feed)
        if not graded_results.get("ready"):
            remaining_pending[normalized_date] = recap_payload
            continue

        if _send_discord_results_recap(recap_payload, graded_results):
            sent_at = get_et_now().isoformat()
            sent_history[normalized_date] = {
                "sent_at": sent_at,
                "record": _results_record_text(graded_results.get("summary", {})),
                "graded_count": graded_results.get("summary", {}).get("graded_count", 0),
            }
            _write_results_recap_history({
                "game_date": normalized_date,
                "sent_at": sent_at,
                "source_generated_at": recap_payload.get("source_generated_at"),
                "refresh_label": recap_payload.get("refresh_label"),
                "summary": graded_results.get("summary", {}),
                "recommendations": graded_results.get("recommendations", []),
            })
            sent_dates.append(normalized_date)
            state_changed = True
        else:
            remaining_pending[normalized_date] = recap_payload

    if list(remaining_pending.keys()) != list(pending.keys()):
        state_changed = True

    trimmed_sent_history = dict(sorted(sent_history.items())[-30:])
    state["pending_result_recaps"] = remaining_pending
    state["sent_result_recaps"] = trimmed_sent_history
    return {"sent_dates": sent_dates, "state_changed": state_changed}


def _send_discord_webhook(
    payload: Dict[str, Any],
    notification_delta: Dict[str, Any],
    *,
    webhook_url: str,
    channel_variant: str,
) -> bool:
    if not webhook_url:
        return False
    if channel_variant == "tracker":
        discord_payload = _build_discord_tracker_payload(payload, notification_delta)
    else:
        discord_payload = _build_discord_alert_payload(
            payload,
            notification_delta,
            channel_variant=channel_variant,
        )
    if not discord_payload:
        return False
    return _post_discord_webhook(webhook_url, discord_payload)


def _is_missing_relation_error(error: Exception, table_name: str) -> bool:
    message = str(error).lower()
    return (
        ("relation" in message and "does not exist" in message and table_name.lower() in message)
        or ("could not find the table" in message and table_name.lower() in message)
        or ("schema cache" in message and table_name.lower() in message)
    )


def _sync_payload_to_supabase(payload: Dict[str, Any]) -> bool:
    try:
        from utils.supabase_client import get_supabase_client
        client = get_supabase_client()
    except Exception as exc:
        logger.warning("Supabase unavailable for edge scores: %s", exc)
        return True

    row = {
        "ranking_key": "current",
        "game_dates": payload.get("game_dates", []),
        "generated_at": payload.get("generated_at"),
        "refresh_label": payload.get("refresh_label"),
        "summary": payload.get("summary", {}),
        "top_recommendations": payload.get("recommendations", []),
        "notification": payload.get("notification", {}),
    }

    try:
        client.table(EDGE_SCORE_TABLE).upsert(
            [row],
            on_conflict="ranking_key",
        ).execute()
        return True
    except Exception as exc:
        if _is_missing_relation_error(exc, EDGE_SCORE_TABLE):
            log_status(
                logger,
                "WARN",
                "Edge Score Supabase sync skipped; table missing",
                table=EDGE_SCORE_TABLE,
            )
            return True
        logger.warning("Edge Score Supabase sync failed: %s", exc)
        return False


def run_edge_score_refresh(
    refresh_label: str = "manual",
    current_players_data: Optional[Dict[str, Any]] = None,
    master_feed_path: str = MASTER_FEED_PATH,
    schedule_path: str = SCHEDULE_PATH,
    line_movements_path: str = LINE_MOVEMENTS_PATH,
    prizepicks_path: str = PRIZEPICKS_PATH,
) -> Dict[str, Any]:
    start_time = time.time()
    master_feed = _load_json(master_feed_path, [])
    schedule_payload = _load_json(schedule_path, {"games": []})
    line_movements_payload = _load_json(line_movements_path, {"snapshots": []})
    state = _load_json(EDGE_SCORE_STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    if not isinstance(master_feed, list):
        master_feed = []

    recap_result = {"sent_dates": [], "state_changed": False}
    if refresh_label == "pipeline" and _results_recap_webhook_url():
        try:
            recap_result = _process_results_recaps(master_feed, state)
        except Exception as exc:
            logger.warning("Edge Score results recap failed: %s", exc)

    schedule_context = _build_schedule_context(schedule_payload)
    overlay_props_index = _build_overlay_props_index(current_players_data)
    overlay_props_index = _merge_overlay_indexes(
        overlay_props_index,
        _load_prizepicks_overlay(master_feed, schedule_context, prizepicks_path),
    )
    line_lookup = _build_line_movement_lookup(line_movements_payload)

    active_entries = []
    for player in master_feed:
        if not isinstance(player, dict):
            continue
        team = player.get("team")
        if schedule_context["active_teams"] and team not in schedule_context["active_teams"]:
            continue

        player_id = str(player.get("id"))
        game = schedule_context["game_by_team"].get(team, {})
        game_date = _normalize_game_date(game.get("game_date")) or ""

        base_props = _merge_props_trees(
            _normalize_props_tree_for_game_date(
                player.get("props_by_date", {}),
                target_game_date=game_date,
                active_dates=schedule_context["active_dates"],
            ),
            _normalize_props_tree_for_game_date(
                player.get("props", {}),
                target_game_date=game_date,
                active_dates=schedule_context["active_dates"],
            ),
        )
        merged_props = _merge_props_trees(
            base_props,
            _normalize_props_tree_for_game_date(
                overlay_props_index.get(player_id, {}),
                target_game_date=game_date,
                active_dates=schedule_context["active_dates"],
            ),
        )
        if not merged_props:
            continue

        if not game_date:
            for book_map in merged_props.values():
                for prop in book_map.values():
                    if prop.get("game_date"):
                        game_date = _normalize_game_date(prop.get("game_date"))
                        break
                if game_date:
                    break

        active_entries.append({
            "player": player,
            "team": team,
            "props": merged_props,
            "game_id": game.get("game_id"),
            "game_date": game_date,
            "game_time_et": game.get("game_time_et"),
            "opponent": schedule_context["opponent_by_team"].get(team),
        })

    style_cache = {
        int(entry["player"].get("id")): _build_style_vector(entry["player"])
        for entry in active_entries
    }

    candidates = []
    for entry in active_entries:
        for stat_type, book_props in entry["props"].items():
            if stat_type not in STAT_PROFILES:
                continue

            side_candidates = []
            for side in ("over", "under"):
                market_selection = _select_market_for_side(stat_type, book_props, side)
                if not market_selection:
                    continue
                candidate = _build_candidate(
                    entry,
                    stat_type,
                    side,
                    market_selection,
                    active_entries,
                    style_cache,
                    line_lookup,
                )
                if candidate is not None:
                    side_candidates.append(candidate)

            if not side_candidates:
                continue

            best_candidate = max(
                side_candidates,
                key=lambda candidate: (
                    candidate.get("edge_score", 0.0),
                    candidate.get("confidence", 0.0),
                    candidate.get("signal_score", 0.0),
                ),
            )
            candidates.append(best_candidate)

    candidates.sort(key=_candidate_sort_key, reverse=True)
    top_recommendations = _diversify_candidates(candidates, EDGE_LIMIT)
    for rank, recommendation in enumerate(top_recommendations, start=1):
        recommendation["rank"] = rank
    sportsbook_boards = _build_sportsbook_boards(candidates, EDGE_SPORTSBOOK_BOARD_LIMIT)

    notification_delta = _compute_notification_delta(top_recommendations, state, refresh_label)
    official_recommendations = notification_delta.get("official_recommendations", [])

    payload = {
        "generated_at": get_et_now().isoformat(),
        "refresh_label": refresh_label,
        "game_dates": sorted({entry["game_date"] for entry in active_entries if entry.get("game_date")}),
        "summary": {
            "active_players": len(active_entries),
            "candidate_count": len(candidates),
            "top_count": len(top_recommendations),
            "available_books": sorted(SUPPORTED_BOOKS),
            "sportsbook_board_limit": EDGE_SPORTSBOOK_BOARD_LIMIT,
            "sportsbook_boards": sportsbook_boards,
            "duration_s": round(time.time() - start_time, 2),
            "scoring_model": "Signal Score",
        },
        "recommendations": top_recommendations,
        "notification": {
            "discord_configured": _has_discord_alert_target(),
            "discord_main_configured": bool(EDGE_SCORE_DISCORD_WEBHOOK_URL),
            "discord_tracker_configured": bool(EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL),
            "should_send": bool(notification_delta.get("should_send")),
            "delivery_allowed": refresh_label in DISCORD_ALERT_REFRESH_LABELS,
            "delivery_scope": "intraday_only",
            "cooldown_active": bool(notification_delta.get("cooldown_active")),
            "alert_kind": notification_delta.get("alert_kind"),
            "official_candidate_count": len(official_recommendations),
            "tracker_min_signal_score": EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE,
            "change_count": len(notification_delta.get("changes", [])),
            "removed_count": len(notification_delta.get("removed", [])),
            "changes": notification_delta.get("changes", []),
            "removed": notification_delta.get("removed", []),
            "dedupe_rules": {
                "per_book_limit": EDGE_DISCORD_PER_BOOK_LIMIT,
                "min_signal_score": EDGE_DISCORD_MIN_SIGNAL_SCORE,
                "new_entrant": True,
                "best_book_changed": True,
                "line_move_points": EDGE_DISCORD_LINE_MOVE_POINTS,
                "odds_move_american": EDGE_DISCORD_ODDS_MOVE_AMERICAN,
                "min_interval_seconds": EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS,
            },
        },
    }
    tracker_delta = _compute_tracker_delta(
        notification_delta.get("send_recommendations", []),
        state,
        slate_date=notification_delta.get("slate_date", ""),
        generated_at=payload["generated_at"],
    )
    payload["notification"]["tracker_new_count"] = len(tracker_delta.get("new_recommendations", []))

    _write_json_atomic(EDGE_SCORE_PATH, payload)
    _write_history_snapshot(payload)

    discord_sent = False
    discord_main_sent = False
    discord_tracker_sent = False
    state_changed = bool(recap_result.get("state_changed"))
    if (
        refresh_label in DISCORD_ALERT_REFRESH_LABELS
        and notification_delta.get("should_send")
        and _has_discord_alert_target()
    ):
        if EDGE_SCORE_DISCORD_WEBHOOK_URL:
            try:
                discord_main_sent = _send_discord_webhook(
                    payload,
                    notification_delta,
                    webhook_url=EDGE_SCORE_DISCORD_WEBHOOK_URL,
                    channel_variant="main",
                )
            except Exception as exc:
                logger.warning("Discord Edge Score webhook failed: %s", exc)
        if EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL and tracker_delta.get("should_send"):
            try:
                discord_tracker_sent = _send_discord_webhook(
                    payload,
                    tracker_delta,
                    webhook_url=EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL,
                    channel_variant="tracker",
                )
            except Exception as exc:
                logger.warning("Discord Edge Score tracker webhook failed: %s", exc)
        discord_sent = bool(discord_main_sent or discord_tracker_sent)

    if discord_sent:
        slate_date = notification_delta.get("slate_date") or ""
        alert_state_by_date = state.get("discord_alert_state_by_date", {})
        if not isinstance(alert_state_by_date, dict):
            alert_state_by_date = {}
        date_state = alert_state_by_date.get(slate_date, {}) if slate_date else {}
        if not isinstance(date_state, dict):
            date_state = {}

        date_state["last_sent_at"] = payload["generated_at"]
        date_state["last_sent_snapshot"] = notification_delta.get("current_snapshot", {})
        if notification_delta.get("alert_kind") == "opening":
            date_state["opening_sent_at"] = payload["generated_at"]
        if notification_delta.get("alert_kind") == "pre_tip":
            date_state.setdefault("opening_sent_at", payload["generated_at"])
            date_state["pre_tip_sent_at"] = payload["generated_at"]

        if slate_date:
            alert_state_by_date[slate_date] = date_state
            state["discord_alert_state_by_date"] = alert_state_by_date

        tracker_visible_sent = bool(discord_tracker_sent or (not EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL and discord_main_sent))
        if tracker_visible_sent and tracker_delta.get("new_recommendations"):
            _queue_results_recap_payload(
                state,
                tracker_delta.get("new_recommendations", []),
                generated_at=payload["generated_at"],
                refresh_label=refresh_label,
                alert_kind="tracker",
            )
            state_changed = True
        elif discord_sent:
            state_changed = True

    if state_changed or (not os.path.exists(EDGE_SCORE_STATE_PATH)):
        _write_json_atomic(EDGE_SCORE_STATE_PATH, state if isinstance(state, dict) else {})

    sync_ok = _sync_payload_to_supabase(payload)
    if discord_sent:
        log_status(
            logger,
            "OK",
            "Edge Score refresh complete",
            refresh_label=refresh_label,
            top=len(top_recommendations),
            discord_sent=True,
            results_recaps_sent=len(recap_result.get("sent_dates", [])),
            supabase_sync_ok=sync_ok,
        )
    else:
        log_status(
            logger,
            "OK",
            "Edge Score refresh complete",
            refresh_label=refresh_label,
            top=len(top_recommendations),
            discord_sent=False,
            results_recaps_sent=len(recap_result.get("sent_dates", [])),
            supabase_sync_ok=sync_ok,
        )

    return payload
