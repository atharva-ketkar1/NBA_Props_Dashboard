import csv
import copy
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from prop_modeling.injury_feature_config import (
    CREATION_BENEFIT_POTENTIAL_AST_THRESHOLD,
    INJURY_FRESHNESS_LOCK_SENSITIVE_MAX_AGE_MINUTES,
    INJURY_FRESHNESS_LOCK_SENSITIVE_START_HOUR_ET,
    INJURY_FRESHNESS_NORMAL_MAX_AGE_MINUTES,
    ONBALL_BENEFIT_DRIVES_THRESHOLD,
    OVERLAY_MAX_MULTIPLIER,
    OVERLAY_MIN_RECENT5_MINUTES,
    OVERLAY_MULTI_BENEFIT_MULTIPLIER,
    OVERLAY_SINGLE_BENEFIT_MULTIPLIER,
    PROMOTION_GUARDRAIL_SUPPORTED_STAT_TYPES,
    SAME_POS_BENEFIT_MINUTES_THRESHOLD,
    USAGE_BENEFIT_USAGE_THRESHOLD,
    resolve_promotion_guardrail_config,
)
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
ACTION_NETWORK_PATH = os.path.join(CURRENT_DIR, "action_network_odds.json")
LINE_MOVEMENTS_PATH = os.path.join(CURRENT_DIR, "line_movements_today.json")
PRIZEPICKS_PATH = os.path.join(CURRENT_DIR, "prizepicks.csv")
EDGE_SCORE_PATH = os.path.join(CURRENT_DIR, "edge_scores_top15.json")
EDGE_SCORE_STATE_PATH = os.path.join(CURRENT_DIR, "edge_score_notification_state.json")
EDGE_SCORE_HISTORY_PATH = os.path.join(ARCHIVE_DIR, "edge_scores_history.json")
EDGE_SCORE_RESULTS_HISTORY_PATH = os.path.join(ARCHIVE_DIR, "edge_score_results_history.json")
BOXSCORE_CDN_BASE_URL = "https://cdn.nba.com/static/json/liveData/boxscore"
BOXSCORE_CDN_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.nba.com",
    "referer": "https://www.nba.com/",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
}
ISO8601_DURATION_RE = re.compile(
    r"^PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?$"
)
DISCORD_WEBHOOK_MESSAGE_PATH_RE = re.compile(r"/messages/\d+$")

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
EDGE_DISCORD_PER_PLAYER_LIMIT = max(1, _env_int("EDGE_SCORE_DISCORD_PER_PLAYER_LIMIT", 1))
EDGE_TOP_PER_PLAYER_LIMIT = max(1, _env_int("EDGE_SCORE_TOP_PER_PLAYER_LIMIT", 1))
EDGE_TOP_PER_GAME_LIMIT = max(1, _env_int("EDGE_SCORE_TOP_PER_GAME_LIMIT", 5))
EDGE_TRACKER_PER_PLAYER_LIMIT = max(1, _env_int("EDGE_SCORE_TRACKER_PER_PLAYER_LIMIT", 1))
EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE = max(
    1.0,
    min(99.0, _env_float("EDGE_SCORE_DISCORD_TRACKER_MIN_SIGNAL_SCORE", 72.5)),
)
EDGE_SPORTSBOOK_BOARD_LIMIT = max(1, _env_int("EDGE_SPORTSBOOK_BOARD_LIMIT", 10))
EDGE_DISCORD_COMPONENT_OVERLAP_THRESHOLD = max(
    0.0,
    min(1.0, _env_float("EDGE_SCORE_DISCORD_COMPONENT_OVERLAP_THRESHOLD", 0.45)),
)
EDGE_DISCORD_LINE_MOVE_POINTS = max(0.25, _env_float("EDGE_SCORE_DISCORD_LINE_MOVE_POINTS", 1.0))
EDGE_DISCORD_ODDS_MOVE_AMERICAN = max(5, _env_int("EDGE_SCORE_DISCORD_ODDS_MOVE_AMERICAN", 25))
EDGE_DISCORD_SCORE_DELTA = max(1.0, _env_float("EDGE_SCORE_DISCORD_SCORE_DELTA", 6.0))
EDGE_DISCORD_RANK_DELTA = max(1, _env_int("EDGE_SCORE_DISCORD_RANK_DELTA", 4))
EDGE_DISCORD_HTTP_MAX_ATTEMPTS = max(1, _env_int("EDGE_SCORE_DISCORD_HTTP_MAX_ATTEMPTS", 5))
EDGE_DISCORD_RATE_LIMIT_FALLBACK_SECONDS = max(
    0.25,
    _env_float("EDGE_SCORE_DISCORD_RATE_LIMIT_FALLBACK_SECONDS", 1.5),
)
EDGE_DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS = max(
    0.25,
    _env_float("EDGE_SCORE_DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS", 30.0),
)
_DISCORD_WEBHOOK_RATE_LIMIT_UNTIL: Dict[str, float] = {}
UNDATED_PROP_KEY = "__undated__"
TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
EDGE_SCORE_ENABLE_PRIZEPICKS = (
    str(os.getenv("EDGE_SCORE_ENABLE_PRIZEPICKS", "false")).strip().lower() in TRUE_ENV_VALUES
)

SUPPORTED_BOOKS = {"dk", "fd"} | ({"pp"} if EDGE_SCORE_ENABLE_PRIZEPICKS else set())
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
ACTION_NETWORK_CONTEXT_BOOK_IDS = (
    "15",    # DraftKings
    "30",    # FanDuel
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
)
DISCORD_ALERT_REFRESH_LABELS = {"intraday"}
SIDE_MULTIPLIERS = {
    "over": 1.0,
    "under": -1.0,
}
COMPONENT_WEIGHTS = {
    "projection": 0.19,
    "recent_form": 0.15,
    "matchup": 0.13,
    "market": 0.09,
    "ml_regression": 0.25,
    "line_movement": 0.06,
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

BOOK_DISPLAY_ORDER = [book for book in ("dk", "fd", "pp") if book in SUPPORTED_BOOKS]
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


def _safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _webhook_url_with_query(webhook_url: str, extra_query: Optional[Dict[str, str]] = None) -> str:
    if not extra_query:
        return webhook_url
    split = urlsplit(webhook_url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update({key: value for key, value in extra_query.items() if value is not None})
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _webhook_message_url(webhook_url: str, message_id: str) -> str:
    split = urlsplit(webhook_url)
    base_path = split.path.rstrip("/")
    message_path = f"{base_path}/messages/{message_id}"
    return urlunsplit((split.scheme, split.netloc, message_path, split.query, split.fragment))


def _discord_retry_after_seconds(response: requests.Response) -> float:
    retry_after = _safe_float(response.headers.get("Retry-After"))
    if retry_after is not None and retry_after > 0:
        return min(retry_after, EDGE_DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS)

    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        retry_after = _safe_float(body.get("retry_after"))
        if retry_after is not None and retry_after > 0:
            return min(retry_after, EDGE_DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS)

    return EDGE_DISCORD_RATE_LIMIT_FALLBACK_SECONDS


def _discord_rate_limit_bucket_key(method: str, url: str) -> str:
    split = urlsplit(url)
    normalized_path = DISCORD_WEBHOOK_MESSAGE_PATH_RE.sub("", split.path.rstrip("/"))
    return f"{method.upper()} {split.scheme}://{split.netloc}{normalized_path}"


def _wait_for_discord_rate_limit_window(method: str, url: str) -> None:
    bucket_key = _discord_rate_limit_bucket_key(method, url)
    wait_until = _DISCORD_WEBHOOK_RATE_LIMIT_UNTIL.get(bucket_key)
    if wait_until is None:
        return

    now = time.monotonic()
    if wait_until <= now:
        _DISCORD_WEBHOOK_RATE_LIMIT_UNTIL.pop(bucket_key, None)
        return

    time.sleep(wait_until - now)


def _record_discord_rate_limit_window(method: str, url: str, response: requests.Response) -> None:
    bucket_key = _discord_rate_limit_bucket_key(method, url)
    now = time.monotonic()
    cooldown_seconds: Optional[float] = None

    if response.status_code == 429:
        cooldown_seconds = _discord_retry_after_seconds(response)
    else:
        remaining = str(response.headers.get("X-RateLimit-Remaining") or "").strip()
        if remaining == "0":
            cooldown_seconds = _safe_float(response.headers.get("X-RateLimit-Reset-After"))

    if cooldown_seconds is None or cooldown_seconds <= 0:
        if _DISCORD_WEBHOOK_RATE_LIMIT_UNTIL.get(bucket_key, 0.0) <= now:
            _DISCORD_WEBHOOK_RATE_LIMIT_UNTIL.pop(bucket_key, None)
        return

    _DISCORD_WEBHOOK_RATE_LIMIT_UNTIL[bucket_key] = max(
        _DISCORD_WEBHOOK_RATE_LIMIT_UNTIL.get(bucket_key, 0.0),
        now + min(cooldown_seconds, EDGE_DISCORD_RATE_LIMIT_MAX_SLEEP_SECONDS),
    )


def _discord_webhook_request(method: str, url: str, *, payload: Optional[Dict[str, Any]] = None) -> requests.Response:
    last_response: Optional[requests.Response] = None
    for attempt_index in range(EDGE_DISCORD_HTTP_MAX_ATTEMPTS):
        _wait_for_discord_rate_limit_window(method, url)
        response = requests.request(method, url, json=payload, timeout=10)
        _record_discord_rate_limit_window(method, url, response)
        if response.status_code != 429:
            return response

        last_response = response
        if attempt_index >= EDGE_DISCORD_HTTP_MAX_ATTEMPTS - 1:
            break

        sleep_seconds = _discord_retry_after_seconds(response)
        logger.warning(
            "Discord webhook rate limited; retrying | method=%s path=%s sleep_s=%.3f attempt=%s/%s",
            method,
            urlsplit(url).path,
            sleep_seconds,
            attempt_index + 1,
            EDGE_DISCORD_HTTP_MAX_ATTEMPTS,
        )

    if last_response is not None:
        return last_response
    return requests.request(method, url, json=payload, timeout=10)


def _execute_discord_webhook(webhook_url: str, payload: Dict[str, Any], *, wait: bool = False) -> Optional[Dict[str, Any]]:
    if not webhook_url:
        return None
    response = _discord_webhook_request(
        "POST",
        _webhook_url_with_query(webhook_url, {"wait": "true"} if wait else None),
        payload=payload,
    )
    response.raise_for_status()
    if not wait:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


def _post_discord_webhook(webhook_url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _execute_discord_webhook(webhook_url, payload, wait=True)


def _edit_discord_webhook_message(
    webhook_url: str,
    message_id: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not webhook_url or not message_id:
        return None
    edit_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"username", "avatar_url"}
    }
    response = _discord_webhook_request(
        "PATCH",
        _webhook_message_url(webhook_url, message_id),
        payload=edit_payload,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {}


def _delete_discord_webhook_message(webhook_url: str, message_id: str) -> str:
    if not webhook_url or not message_id:
        return "missing"
    response = _discord_webhook_request(
        "DELETE",
        _webhook_message_url(webhook_url, message_id),
    )
    if response.status_code == 404:
        return "missing"
    response.raise_for_status()
    return "deleted"


def _format_discord_timestamp(raw_value: Any) -> str:
    dt = _parse_dt(raw_value) or get_et_now()
    month = dt.strftime("%b")
    time_text = dt.strftime("%I:%M %p").lstrip("0")
    return f"{month} {dt.day}, {dt.year} • {time_text} ET"


def _format_tracker_time(raw_value: Any) -> str:
    dt = _parse_dt(raw_value)
    if dt is None:
        return "Unknown ET"
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
        _ranking_edge_score(candidate),
        _ranking_confidence(candidate),
        candidate.get("signal_score", 0.0),
    )


def _ranking_edge_score(candidate: Dict[str, Any]) -> float:
    return _safe_float(candidate.get("display_edge_score"), _safe_float(candidate.get("edge_score"), 0.0)) or 0.0


def _ranking_confidence(candidate: Dict[str, Any]) -> float:
    return _safe_float(
        candidate.get("display_confidence"),
        _safe_float(candidate.get("confidence"), 0.0),
    ) or 0.0


def _stat_component_weights(stat_type: Any) -> Dict[str, float]:
    profile = STAT_PROFILES.get(str(stat_type or "").strip().upper(), {})
    if not isinstance(profile, dict):
        return {}
    components = profile.get("components", {})
    if not isinstance(components, dict):
        return {}

    normalized: Dict[str, float] = {}
    total_weight = 0.0
    for raw_component, raw_weight in components.items():
        weight = _safe_float(raw_weight)
        if weight is None or weight <= 0:
            continue
        component = str(raw_component or "").strip().upper()
        if not component:
            continue
        normalized[component] = float(weight)
        total_weight += float(weight)

    if total_weight <= 0:
        return {}
    if abs(total_weight - 1.0) < 1e-9:
        return normalized
    return {
        component: weight / total_weight
        for component, weight in normalized.items()
    }


def _stat_component_overlap(left_stat_type: Any, right_stat_type: Any) -> float:
    left = _stat_component_weights(left_stat_type)
    right = _stat_component_weights(right_stat_type)
    if not left or not right:
        return 0.0
    components = set(left.keys()) | set(right.keys())
    return sum(min(left.get(component, 0.0), right.get(component, 0.0)) for component in components)


def _discord_recommendations_conflict(
    candidate: Dict[str, Any],
    existing: Dict[str, Any],
) -> bool:
    candidate_player = str(candidate.get("player_id") or "").strip()
    existing_player = str(existing.get("player_id") or "").strip()
    if not candidate_player or candidate_player != existing_player:
        return False

    candidate_pick = str(candidate.get("pick") or "").strip().lower()
    existing_pick = str(existing.get("pick") or "").strip().lower()
    if candidate_pick != existing_pick:
        return False

    candidate_stat = str(candidate.get("stat_type") or "").strip().upper()
    existing_stat = str(existing.get("stat_type") or "").strip().upper()
    if candidate_stat == existing_stat:
        return True

    return _stat_component_overlap(candidate_stat, existing_stat) >= EDGE_DISCORD_COMPONENT_OVERLAP_THRESHOLD


def _filter_discord_recommendations(
    recommendations: List[Dict[str, Any]],
    *,
    min_signal_score: float,
    per_book_limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    per_book_counts: Dict[str, int] = {}
    per_player_counts: Dict[str, int] = {}
    selected_by_player: Dict[str, List[Dict[str, Any]]] = {}
    filtered = []

    for recommendation in recommendations:
        if recommendation.get("eligibility_blocked"):
            continue
        signal_score = _ranking_edge_score(recommendation)
        if signal_score < min_signal_score:
            continue

        book = str(recommendation.get("sportsbook") or "").strip().lower()
        if book not in SUPPORTED_BOOKS:
            continue

        rank = _safe_int(recommendation.get("rank"))
        if rank is not None and rank > EDGE_DISCORD_MAX_RANK:
            continue

        if per_book_limit is not None and per_book_counts.get(book, 0) >= per_book_limit:
            continue

        player_key = str(recommendation.get("player_id") or "").strip()
        selected_for_player = selected_by_player.get(player_key, [])
        if player_key and per_player_counts.get(player_key, 0) >= EDGE_DISCORD_PER_PLAYER_LIMIT:
            continue
        if player_key and any(
            _discord_recommendations_conflict(recommendation, existing)
            for existing in selected_for_player
        ):
            continue

        filtered.append(recommendation)
        if per_book_limit is not None:
            per_book_counts[book] = per_book_counts.get(book, 0) + 1
        if player_key:
            per_player_counts[player_key] = per_player_counts.get(player_key, 0) + 1
            selected_by_player.setdefault(player_key, []).append(recommendation)

    return filtered


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


def _safe_divide(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or abs(denominator) < 1e-9:
        return None
    return numerator / denominator


def _normalize_fraction(raw_value: Any) -> Optional[float]:
    value = _safe_float(raw_value)
    if value is None:
        return None
    if value > 1.5:
        value /= 100.0
    return _clamp(value, 0.0, 2.0)


def _clamp_optional(value: Optional[float], low: float, high: float) -> Optional[float]:
    if value is None:
        return None
    return _clamp(value, low, high)


def _average_log_metric(
    logs: List[Dict[str, Any]],
    key: str,
    limit: int,
    *,
    percent: bool = False,
) -> Optional[float]:
    values: List[Optional[float]] = []
    for game in logs[:limit]:
        raw_value = game.get(key)
        value = _normalize_fraction(raw_value) if percent else _safe_float(raw_value)
        if value is not None:
            values.append(value)
    return _average(values)


def _sum_log_metric(logs: List[Dict[str, Any]], key: str, limit: int) -> Optional[float]:
    total = 0.0
    seen = False
    for game in logs[:limit]:
        value = _safe_float(game.get(key))
        if value is None:
            continue
        total += value
        seen = True
    return total if seen else None


def _sum_log_minutes(logs: List[Dict[str, Any]], limit: int) -> Optional[float]:
    total = 0.0
    seen = False
    for game in logs[:limit]:
        minutes = _safe_float(game.get("MIN"))
        if minutes is None or minutes <= 0:
            continue
        total += minutes
        seen = True
    return total if seen else None


def _paired_metric_sums(
    logs: List[Dict[str, Any]],
    numerator_key: str,
    denominator_key: str,
    limit: int,
) -> Tuple[Optional[float], Optional[float]]:
    numerator_total = 0.0
    denominator_total = 0.0
    seen = False
    for game in logs[:limit]:
        numerator_value = _safe_float(game.get(numerator_key))
        denominator_value = _safe_float(game.get(denominator_key))
        if numerator_value is None or denominator_value is None or denominator_value <= 0:
            continue
        numerator_total += numerator_value
        denominator_total += denominator_value
        seen = True
    if not seen:
        return None, None
    return numerator_total, denominator_total


def _metric_rate_from_logs(logs: List[Dict[str, Any]], key: str, limit: int) -> Optional[float]:
    numerator_total, denominator_total = _paired_metric_sums(logs, key, "MIN", limit)
    return _safe_divide(numerator_total, denominator_total)


def _stat_rate_from_logs(logs: List[Dict[str, Any]], stat_type: str, limit: int) -> Optional[float]:
    numerator = 0.0
    denominator = 0.0
    seen = False
    for game in logs[:limit]:
        stat_value = _stat_value_from_game(game, stat_type)
        minutes = _safe_float(game.get("MIN"))
        if stat_value is None or minutes is None or minutes <= 0:
            continue
        numerator += stat_value
        denominator += minutes
        seen = True
    if not seen:
        return None
    return _safe_divide(numerator, denominator)


def _win_pct(wins: Any, losses: Any) -> Optional[float]:
    wins_value = _safe_float(wins)
    losses_value = _safe_float(losses)
    if wins_value is None or losses_value is None:
        return None
    games_played = wins_value + losses_value
    if games_played <= 0:
        return None
    return wins_value / games_played


def _pick_action_network_context_market(markets: Any) -> Dict[str, Any]:
    if not isinstance(markets, dict):
        return {}

    for book_id in ACTION_NETWORK_CONTEXT_BOOK_IDS:
        market = markets.get(book_id)
        if market is None and book_id.isdigit():
            market = markets.get(int(book_id))
        if isinstance(market, dict):
            return market

    for book_id in sorted(str(key) for key in markets.keys()):
        market = markets.get(book_id)
        if market is None and book_id.isdigit():
            market = markets.get(int(book_id))
        if isinstance(market, dict):
            return market
    return {}


def _market_total_line(market: Dict[str, Any]) -> Optional[float]:
    total_market = market.get("total") if isinstance(market.get("total"), dict) else {}
    total_line = _safe_float(total_market.get("line"))
    if total_line is not None:
        return total_line
    over_line = _safe_float((total_market.get("over") or {}).get("line"))
    under_line = _safe_float((total_market.get("under") or {}).get("line"))
    return over_line if over_line is not None else under_line


def _team_total_line_from_market(market: Dict[str, Any], team: str) -> Optional[float]:
    team_total_market = (
        market.get("team_total") if isinstance(market.get("team_total"), dict) else {}
    )
    team_offer = team_total_market.get(team)
    if not isinstance(team_offer, dict):
        return None
    return _safe_float(team_offer.get("line"))


def _resolve_game_team_context(game: Any, team: Any) -> Dict[str, Any]:
    if not isinstance(game, dict):
        return {}

    team_code = str(team or "").strip().upper()
    home_team = str(game.get("home_team_tricode") or "").strip().upper()
    away_team = str(game.get("away_team_tricode") or "").strip().upper()
    if not team_code or team_code not in {home_team, away_team}:
        return {}

    is_home = team_code == home_team
    opponent_team = away_team if is_home else home_team
    team_side = "home" if is_home else "away"
    market = _pick_action_network_context_market(
        game.get("action_network_markets") or game.get("markets")
    )
    spread_market = market.get("spread") if isinstance(market.get("spread"), dict) else {}
    team_spread_line = _safe_float((spread_market.get(team_side) or {}).get("line"))
    raw_game_spread = _safe_float(game.get("spread"))
    game_total_line = _market_total_line(market) if market else None
    if game_total_line is None:
        game_total_line = _safe_float(game.get("total"))

    team_total_line = _team_total_line_from_market(market, team_code) if market else None
    opponent_total_line = _team_total_line_from_market(market, opponent_team) if market else None
    team_implied_total = team_total_line
    opponent_implied_total = opponent_total_line
    if (
        team_implied_total is None
        and team_spread_line is not None
        and game_total_line is not None
    ):
        team_implied_total = (game_total_line - team_spread_line) / 2.0
    if (
        opponent_implied_total is None
        and team_spread_line is not None
        and game_total_line is not None
    ):
        opponent_implied_total = (game_total_line + team_spread_line) / 2.0

    vegas_spread = team_spread_line if team_spread_line is not None else raw_game_spread
    return {
        "is_home": is_home,
        "team_win_pct": _win_pct(
            game.get("home_team_wins") if is_home else game.get("away_team_wins"),
            game.get("home_team_losses") if is_home else game.get("away_team_losses"),
        ),
        "opponent_win_pct": _win_pct(
            game.get("away_team_wins") if is_home else game.get("home_team_wins"),
            game.get("away_team_losses") if is_home else game.get("home_team_losses"),
        ),
        "current_score_differential": _safe_float(game.get("score_differential")),
        "is_live": bool(game.get("is_live")),
        "game_status": game.get("game_status"),
        "vegas_spread": vegas_spread,
        "vegas_total": game_total_line,
        "team_spread_line": team_spread_line,
        "spread_abs": abs(vegas_spread) if vegas_spread is not None else None,
        "team_is_favorite": None if team_spread_line is None else team_spread_line < 0,
        "team_total_line": team_total_line,
        "opponent_team_total_line": opponent_total_line,
        "team_implied_total": team_implied_total,
        "opponent_implied_total": opponent_implied_total,
        "market_book_id": market.get("book_id") if isinstance(market, dict) else None,
        "market_book_label": market.get("book_label") if isinstance(market, dict) else None,
    }


def _resolve_entry_game_team_context(entry: Dict[str, Any]) -> Dict[str, Any]:
    game = entry.get("game_context")
    return _resolve_game_team_context(game, entry.get("team"))


def _legacy_projection_baseline(
    season_avg: Optional[float],
    recent10_avg: Optional[float],
    recent5_avg: Optional[float],
    season_minutes: Optional[float],
    recent_minutes: Optional[float],
) -> Optional[float]:
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
    return baseline_projection


def _compute_expected_minutes_context(entry: Dict[str, Any], logs: List[Dict[str, Any]], blowout_risk_boost: bool = False) -> Dict[str, Any]:
    player = entry.get("player") if isinstance(entry, dict) else {}
    stats = player.get("stats") if isinstance(player, dict) and isinstance(player.get("stats"), dict) else {}
    season_minutes = _safe_float(stats.get("MIN"))
    recent_5_minutes = _average([_safe_float(game.get("MIN")) for game in logs[:5]])
    recent_10_minutes = _average([_safe_float(game.get("MIN")) for game in logs[:10]])
    minutes_baseline = _weighted_average([
        (season_minutes, 0.48),
        (recent_10_minutes, 0.32),
        (recent_5_minutes, 0.20),
    ])

    game_team_context = _resolve_entry_game_team_context(entry)
    team_win_pct = _safe_float(game_team_context.get("team_win_pct"))
    opponent_win_pct = _safe_float(game_team_context.get("opponent_win_pct"))
    win_pct_gap = None
    if team_win_pct is not None and opponent_win_pct is not None:
        win_pct_gap = abs(team_win_pct - opponent_win_pct)

    competitive_minutes: List[float] = []
    blowout_minutes: List[float] = []
    for game in logs[:25]:
        minutes = _safe_float(game.get("MIN"))
        margin = _safe_float(game.get("margin"))
        if minutes is None or minutes <= 0 or margin is None:
            continue
        if abs(margin) >= 15:
            blowout_minutes.append(minutes)
        elif abs(margin) <= 8:
            competitive_minutes.append(minutes)

    competitive_avg = _average(competitive_minutes)
    blowout_avg = _average(blowout_minutes)
    historical_adjustment_ratio = None
    if (
        competitive_avg is not None
        and competitive_avg > 0
        and blowout_avg is not None
        and len(competitive_minutes) >= 5
        and len(blowout_minutes) >= 3
    ):
        historical_adjustment_ratio = (blowout_avg - competitive_avg) / competitive_avg

    blowout_risk = 0.0
    vegas_spread = game_team_context.get("vegas_spread")
    if vegas_spread is not None:
        blowout_risk = _clamp((abs(vegas_spread) - 8.0) / 7.0, 0.0, 1.0)
    elif win_pct_gap is not None:
        blowout_risk = _clamp((win_pct_gap - 0.08) / 0.28, 0.0, 1.0)

    if blowout_risk_boost:
        blowout_risk = min(1.0, blowout_risk + 0.20)

    live_score_differential = _safe_float(game_team_context.get("current_score_differential"))
    if game_team_context.get("is_live") and live_score_differential is not None:
        blowout_risk = max(blowout_risk, _clamp((abs(live_score_differential) - 10.0) / 12.0, 0.0, 1.0))

    generic_adjustment_ratio = None
    role_minutes = minutes_baseline or recent_10_minutes or season_minutes or recent_5_minutes
    if blowout_risk > 0 and role_minutes is not None:
        if role_minutes >= 34.0:
            generic_adjustment_ratio = -0.04
        elif role_minutes >= 28.0:
            generic_adjustment_ratio = -0.025

    raw_adjustment_ratio = _weighted_average([
        (historical_adjustment_ratio, 0.70),
        (generic_adjustment_ratio, 0.30),
    ])
    minutes_adjustment_ratio = None
    if raw_adjustment_ratio is not None and blowout_risk > 0:
        minutes_adjustment_ratio = _clamp(raw_adjustment_ratio * blowout_risk, -0.12, 0.03)

    expected_minutes = minutes_baseline
    if expected_minutes is not None and minutes_adjustment_ratio is not None:
        expected_minutes *= (1.0 + minutes_adjustment_ratio)
    if expected_minutes is not None:
        expected_minutes = _clamp(expected_minutes, 4.0, 42.0)

    return {
        "expected_minutes": expected_minutes,
        "season_minutes": season_minutes,
        "recent_5_minutes": recent_5_minutes,
        "recent_10_minutes": recent_10_minutes,
        "minutes_baseline": minutes_baseline,
        "minutes_adjustment_ratio": minutes_adjustment_ratio,
        "blowout_risk": blowout_risk if blowout_risk > 0 else None,
        "team_win_pct": team_win_pct,
        "opponent_win_pct": opponent_win_pct,
        "competitive_minutes_avg": competitive_avg,
        "blowout_minutes_avg": blowout_avg,
        "blowout_sample_size": len(blowout_minutes),
        "competitive_sample_size": len(competitive_minutes),
    }


def _estimate_stat_rate_context(player: Dict[str, Any], logs: List[Dict[str, Any]], stat_type: str) -> Dict[str, Any]:
    stats = player.get("stats") if isinstance(player.get("stats"), dict) else {}
    season_minutes = _safe_float(stats.get("MIN"))
    season_stat = _stat_value_from_stats(stats, stat_type)
    season_rate = _safe_divide(season_stat, season_minutes)
    recent_10_rate = _stat_rate_from_logs(logs, stat_type, 10)
    recent_5_rate = _stat_rate_from_logs(logs, stat_type, 5)
    base_rate = _weighted_average([
        (season_rate, 0.48),
        (recent_10_rate, 0.32),
        (recent_5_rate, 0.20),
    ])

    profile = STAT_PROFILES.get(stat_type, {})
    component_rates: Dict[str, Any] = {}
    expected_rate = base_rate
    rate_model = "per_minute_blend"

    if stat_type in {"PTS+REB+AST", "PTS+REB", "PTS+AST", "REB+AST"}:
        component_rate_total = 0.0
        components_available = 0
        for component_stat, _ in (profile.get("components") or {}).items():
            component_context = _estimate_stat_rate_context(player, logs, component_stat)
            component_rates[component_stat] = component_context.get("details", {})
            component_rate = _safe_float(component_context.get("expected_rate"))
            if component_rate is not None:
                component_rate_total += component_rate
                components_available += 1
        component_rate_projection = component_rate_total if components_available > 0 else None
        expected_rate = _weighted_average([
            (component_rate_projection, 0.85),
            (base_rate, 0.15),
        ])
        rate_model = "component_sum"
    elif stat_type == "PTS":
        season_usage = _normalize_fraction(stats.get("USG_PCT"))
        recent_10_usage = _average_log_metric(logs, "USG_PCT", 10, percent=True)
        recent_5_usage = _average_log_metric(logs, "USG_PCT", 5, percent=True)
        expected_usage = _weighted_average([
            (season_usage, 0.42),
            (recent_10_usage, 0.35),
            (recent_5_usage, 0.23),
        ])

        season_drive_rate = _safe_divide(_safe_float(stats.get("DRIVES")), season_minutes)
        recent_10_drive_rate = _metric_rate_from_logs(logs, "DRIVES", 10)
        recent_5_drive_rate = _metric_rate_from_logs(logs, "DRIVES", 5)
        expected_drive_rate = _weighted_average([
            (season_drive_rate, 0.45),
            (recent_10_drive_rate, 0.35),
            (recent_5_drive_rate, 0.20),
        ])

        recent_10_ts = _average_log_metric(logs, "TS_PCT", 10, percent=True)
        usage_shift = None
        if expected_usage is not None and season_usage is not None and season_usage > 0:
            usage_shift = (expected_usage - season_usage) / max(season_usage, 0.12)
        drive_shift = None
        if expected_drive_rate is not None and season_drive_rate is not None and season_drive_rate > 0:
            drive_shift = (expected_drive_rate - season_drive_rate) / max(season_drive_rate, 0.05)

        opportunity_rate = base_rate
        if opportunity_rate is not None:
            if usage_shift is not None:
                opportunity_rate *= 1.0 + (_clamp(usage_shift, -0.30, 0.30) * 0.22)
            if drive_shift is not None:
                opportunity_rate *= 1.0 + (_clamp(drive_shift, -0.30, 0.30) * 0.14)
            if recent_10_ts is not None:
                opportunity_rate *= 1.0 + (_clamp((recent_10_ts - 0.57) / 0.12, -0.10, 0.10) * 0.10)

        expected_rate = _weighted_average([
            (base_rate, 0.60),
            (opportunity_rate, 0.40),
        ])
        rate_model = "usage_drive_rate"
        component_rates = {
            "season_usage": _round(season_usage, 3),
            "recent_10_usage": _round(recent_10_usage, 3),
            "recent_5_usage": _round(recent_5_usage, 3),
            "expected_usage": _round(expected_usage, 3),
            "season_drive_rate": _round(season_drive_rate, 3),
            "recent_10_drive_rate": _round(recent_10_drive_rate, 3),
            "recent_5_drive_rate": _round(recent_5_drive_rate, 3),
            "expected_drive_rate": _round(expected_drive_rate, 3),
            "recent_10_ts_pct": _round(recent_10_ts, 3),
        }
    elif stat_type == "AST":
        season_potential_rate = _safe_divide(_safe_float(stats.get("POTENTIAL_AST")), season_minutes)
        recent_10_potential_rate = _metric_rate_from_logs(logs, "POTENTIAL_AST", 10)
        recent_5_potential_rate = _metric_rate_from_logs(logs, "POTENTIAL_AST", 5)
        expected_potential_rate = _weighted_average([
            (season_potential_rate, 0.45),
            (recent_10_potential_rate, 0.33),
            (recent_5_potential_rate, 0.22),
        ])

        season_conversion = _clamp_optional(_safe_divide(season_stat, _safe_float(stats.get("POTENTIAL_AST"))), 0.10, 0.65)
        recent_10_ast_total, recent_10_potential_total = _paired_metric_sums(logs, "AST", "POTENTIAL_AST", 10)
        recent_5_ast_total, recent_5_potential_total = _paired_metric_sums(logs, "AST", "POTENTIAL_AST", 5)
        recent_10_conversion = _clamp_optional(_safe_divide(recent_10_ast_total, recent_10_potential_total), 0.10, 0.65)
        recent_5_conversion = _clamp_optional(_safe_divide(recent_5_ast_total, recent_5_potential_total), 0.10, 0.65)
        expected_conversion = _weighted_average([
            (season_conversion, 0.45),
            (recent_10_conversion, 0.35),
            (recent_5_conversion, 0.20),
        ])

        opportunity_rate = None
        if expected_potential_rate is not None and expected_conversion is not None:
            opportunity_rate = expected_potential_rate * _clamp(expected_conversion, 0.05, 0.55)

        recent_drive_pass_rate = _metric_rate_from_logs(logs, "DRIVE_PASSES", 10)
        if opportunity_rate is not None and recent_drive_pass_rate is not None:
            opportunity_rate *= 1.0 + (_clamp((recent_drive_pass_rate - 0.10) / 0.10, -0.10, 0.10) * 0.08)

        expected_rate = _weighted_average([
            (base_rate, 0.35),
            (opportunity_rate, 0.65),
        ])
        rate_model = "potential_assists"
        component_rates = {
            "season_potential_rate": _round(season_potential_rate, 3),
            "recent_10_potential_rate": _round(recent_10_potential_rate, 3),
            "recent_5_potential_rate": _round(recent_5_potential_rate, 3),
            "expected_potential_rate": _round(expected_potential_rate, 3),
            "season_conversion": _round(season_conversion, 3),
            "recent_10_conversion": _round(recent_10_conversion, 3),
            "recent_5_conversion": _round(recent_5_conversion, 3),
            "expected_conversion": _round(expected_conversion, 3),
        }
    elif stat_type == "REB":
        season_chance_rate = _safe_divide(_safe_float(stats.get("REB_CHANCES")), season_minutes)
        recent_10_chance_rate = _metric_rate_from_logs(logs, "REB_CHANCES", 10)
        recent_5_chance_rate = _metric_rate_from_logs(logs, "REB_CHANCES", 5)
        expected_chance_rate = _weighted_average([
            (season_chance_rate, 0.45),
            (recent_10_chance_rate, 0.33),
            (recent_5_chance_rate, 0.22),
        ])

        season_conversion = _clamp_optional(_safe_divide(season_stat, _safe_float(stats.get("REB_CHANCES"))), 0.10, 0.75)
        recent_10_reb_total, recent_10_chances_total = _paired_metric_sums(logs, "REB", "REB_CHANCES", 10)
        recent_5_reb_total, recent_5_chances_total = _paired_metric_sums(logs, "REB", "REB_CHANCES", 5)
        recent_10_conversion = _clamp_optional(_safe_divide(recent_10_reb_total, recent_10_chances_total), 0.10, 0.75)
        recent_5_conversion = _clamp_optional(_safe_divide(recent_5_reb_total, recent_5_chances_total), 0.10, 0.75)
        expected_conversion = _weighted_average([
            (season_conversion, 0.45),
            (recent_10_conversion, 0.35),
            (recent_5_conversion, 0.20),
        ])

        opportunity_rate = None
        if expected_chance_rate is not None and expected_conversion is not None:
            opportunity_rate = expected_chance_rate * _clamp(expected_conversion, 0.08, 0.55)

        recent_reb_pct = _average_log_metric(logs, "REB_PCT", 10, percent=True)
        if opportunity_rate is not None and recent_reb_pct is not None:
            opportunity_rate *= 1.0 + (_clamp((recent_reb_pct - 0.15) / 0.10, -0.10, 0.10) * 0.08)

        expected_rate = _weighted_average([
            (base_rate, 0.30),
            (opportunity_rate, 0.70),
        ])
        rate_model = "rebound_chances"
        component_rates = {
            "season_rebound_chance_rate": _round(season_chance_rate, 3),
            "recent_10_rebound_chance_rate": _round(recent_10_chance_rate, 3),
            "recent_5_rebound_chance_rate": _round(recent_5_chance_rate, 3),
            "expected_rebound_chance_rate": _round(expected_chance_rate, 3),
            "season_conversion": _round(season_conversion, 3),
            "recent_10_conversion": _round(recent_10_conversion, 3),
            "recent_5_conversion": _round(recent_5_conversion, 3),
            "expected_conversion": _round(expected_conversion, 3),
        }
    elif stat_type == "FG3M":
        season_attempt_rate = _safe_divide(_safe_float(stats.get("FG3A")), season_minutes)
        recent_10_attempt_rate = _metric_rate_from_logs(logs, "FG3A", 10)
        recent_5_attempt_rate = _metric_rate_from_logs(logs, "FG3A", 5)
        expected_attempt_rate = _weighted_average([
            (season_attempt_rate, 0.45),
            (recent_10_attempt_rate, 0.33),
            (recent_5_attempt_rate, 0.22),
        ])

        season_conversion = _clamp_optional(_normalize_fraction(stats.get("FG3_PCT")), 0.15, 0.60)
        recent_10_fg3m_total, recent_10_fg3a_total = _paired_metric_sums(logs, "FG3M", "FG3A", 10)
        recent_5_fg3m_total, recent_5_fg3a_total = _paired_metric_sums(logs, "FG3M", "FG3A", 5)
        recent_10_conversion = _clamp_optional(_safe_divide(recent_10_fg3m_total, recent_10_fg3a_total), 0.15, 0.60)
        recent_5_conversion = _clamp_optional(_safe_divide(recent_5_fg3m_total, recent_5_fg3a_total), 0.15, 0.60)
        expected_conversion = _weighted_average([
            (season_conversion, 0.42),
            (recent_10_conversion, 0.35),
            (recent_5_conversion, 0.23),
        ])

        opportunity_rate = None
        if expected_attempt_rate is not None and expected_conversion is not None:
            opportunity_rate = expected_attempt_rate * _clamp(expected_conversion, 0.08, 0.60)

        expected_rate = _weighted_average([
            (base_rate, 0.25),
            (opportunity_rate, 0.75),
        ])
        rate_model = "three_point_volume"
        component_rates = {
            "season_attempt_rate": _round(season_attempt_rate, 3),
            "recent_10_attempt_rate": _round(recent_10_attempt_rate, 3),
            "recent_5_attempt_rate": _round(recent_5_attempt_rate, 3),
            "expected_attempt_rate": _round(expected_attempt_rate, 3),
            "season_conversion": _round(season_conversion, 3),
            "recent_10_conversion": _round(recent_10_conversion, 3),
            "recent_5_conversion": _round(recent_5_conversion, 3),
            "expected_conversion": _round(expected_conversion, 3),
        }

    return {
        "expected_rate": expected_rate,
        "details": {
            "rate_model": rate_model,
            "season_rate": _round(season_rate, 3),
            "recent_10_rate": _round(recent_10_rate, 3),
            "recent_5_rate": _round(recent_5_rate, 3),
            "base_rate": _round(base_rate, 3),
            "expected_rate": _round(expected_rate, 3),
            "components": component_rates,
        },
    }


def _is_live_or_final_schedule_game(game: Dict[str, Any]) -> bool:
    if bool(game.get("is_live")) or bool(game.get("is_final")):
        return True
    game_status = int(_safe_float(game.get("game_status"), 0.0) or 0.0)
    return game_status >= 2


def _build_schedule_context(schedule_payload: Any, action_network_payload: Any = None) -> Dict[str, Any]:
    if isinstance(schedule_payload, dict):
        games = schedule_payload.get("games", [])
    elif isinstance(schedule_payload, list):
        games = schedule_payload
    else:
        games = []

    games = [game for game in games if isinstance(game, dict)]
    now = get_et_now()
    today_str = now.strftime("%Y-%m-%d")
    pregame_games = [game for game in games if not _is_live_or_final_schedule_game(game)]

    active_candidates = []
    for game in pregame_games:
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
        for game in pregame_games:
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

    an_games = action_network_payload.get("games", []) if isinstance(action_network_payload, dict) else []
    for game in active_games:
        an_match = next((g for g in an_games if g.get("game_id") == game.get("game_id") or (
            g.get("home_team_tricode") == game.get("home_team_tricode") and
            g.get("away_team_tricode") == game.get("away_team_tricode")
        )), None)
        if an_match:
            game["action_network_markets"] = an_match.get("markets")
            game["has_action_network_markets"] = an_match.get("has_action_network_markets")
            context_market = _pick_action_network_context_market(an_match.get("markets"))
            game["spread"] = an_match.get("spread")
            game["total"] = _market_total_line(context_market) or an_match.get("total")

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


def _classify_player_role(season_minutes: Optional[float], season_usage_pct: Optional[float]) -> str:
    mins = season_minutes or 0.0
    usg = season_usage_pct or 0.0
    if mins >= 30.0 and usg >= 0.24:
        return "star"
    if mins >= 24.0:
        return "starter"
    if mins >= 16.0:
        return "rotation"
    return "bench"


def _load_tonight_dnps(master_feed: Any, injury_report: Any, schedule_context: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    if not isinstance(injury_report, dict):
        return {}
    
    matcher, id_to_team = _build_master_feed_matcher(master_feed)
    if not matcher:
        return {}
        
    master_stats = {}
    if isinstance(master_feed, list):
        for player in master_feed:
            if isinstance(player, dict) and player.get("id"):
                master_stats[str(player["id"])] = player
            
    dnps_by_team: Dict[str, List[Dict[str, Any]]] = {}
    
    games = injury_report.get("games", [])
    if isinstance(games, list):
        for game in games:
            if not isinstance(game, dict):
                continue
            team_player_entries: List[Tuple[str, Dict[str, Any]]] = []
            teams_payload = game.get("teams", {})
            if isinstance(teams_payload, dict):
                for team_abbrev, team_data in teams_payload.items():
                    if not isinstance(team_data, dict):
                        continue
                    for player_entry in team_data.get("players", []):
                        if isinstance(player_entry, dict):
                            team_player_entries.append((str(team_abbrev or "").strip().upper(), player_entry))

            for player_entry in game.get("players", []):
                if isinstance(player_entry, dict):
                    team_player_entries.append(("", player_entry))

            for team_context, player_entry in team_player_entries:
                status = str(player_entry.get("current_status") or "").lower()
                if status not in {"out", "doubtful"}:
                    continue

                raw_name = player_entry.get("player_name") or player_entry.get("report_player_name") or ""

                player_id = matcher.match_player(raw_name, team_context or "UNK")
                if not player_id:
                    continue

                canonical_team = team_context or id_to_team.get(str(player_id))
                if not canonical_team:
                    continue

                master_player = master_stats.get(str(player_id), {})
                stats = master_player.get("stats", {})
                season_minutes = _safe_float(stats.get("MIN"))
                season_usage = _normalize_fraction(stats.get("USG_PCT"))

                role = _classify_player_role(season_minutes, season_usage)

                dnps_by_team.setdefault(canonical_team, []).append({
                    "player_id": str(player_id),
                    "player_name": master_player.get("name", raw_name),
                    "season_usage_pct": season_usage * 100.0 if season_usage is not None else None,
                    "season_minutes": season_minutes,
                    "position": master_player.get("position"),
                    "role": role,
                })
                
    return dnps_by_team


def _compute_lineup_adjustment(
    player: Dict[str, Any],
    stat_type: str,
    side: str,
    tonight_dnps: Optional[Dict[str, List[Dict[str, Any]]]],
    tonight_opponent_dnps: Optional[List[Dict[str, Any]]],
    logs: List[Dict[str, Any]],
    team_recent_games: Dict[str, str],
    game_context: Optional[Dict[str, Any]] = None,
    feature_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    adjustment = {
        "q50_multiplier": 1.0,
        "adjustment_type": "none",
        "reason": "",
        "freed_usage_pct": 0.0,
        "absent_stars": [],
        "benefit_flags": [],
        "context_multipliers": {},
        "modeled_minutes_delta": None,
        "severe_vacancy": False,
        "returning_teammate_drag": False,
        "vegas_context": {},
        "blowout_risk_boost": False,
        "b2b_rest_flip": False,
        "opponent_reb_context": None,
    }

    team = player.get("team")
    if not team:
        return adjustment

    stats = player.get("stats", {}) if isinstance(player, dict) else {}
    player_season_minutes = _safe_float(stats.get("MIN"))
    player_position = str(player.get("position") or "").upper()
    player_position_group = "G" if "G" in player_position else "C" if "C" in player_position else "F" if "F" in player_position else ""
    feature_snapshot = feature_snapshot if isinstance(feature_snapshot, dict) else {}
    absent_teammates = tonight_dnps.get(team, []) if isinstance(tonight_dnps, dict) else []
    
    team_recent_date_str = team_recent_games.get(team)
    if team_recent_date_str:
        team_recent_date = _parse_date(team_recent_date_str)
        now = get_et_now()
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        if team_recent_date_str == yesterday and logs:
            latest_logged_date = _parse_date(logs[0].get("GAME_DATE"))
            if team_recent_date and latest_logged_date and (team_recent_date - latest_logged_date).days >= 1:
                adjustment["b2b_rest_flip"] = True
                
    if tonight_opponent_dnps and (player_season_minutes is None or player_season_minutes < 28.0):
        if any(dnp.get("role") == "star" for dnp in tonight_opponent_dnps):
            adjustment["blowout_risk_boost"] = True

    absent_stars = []
    freed_usg = _safe_float(feature_snapshot.get("missing_team_usage_pct"), 0.0) or 0.0
    opponent_frontcourt_freed = 0.0
    opponent_reb_context = None

    for dnp in absent_teammates:
        role = dnp.get("role")
        if role in {"star", "starter", "rotation"}:
            if role in {"star", "starter"}:
                absent_stars.append(dnp.get("player_name") or "Unknown")

    adjustment["freed_usage_pct"] = freed_usg
    adjustment["absent_stars"] = absent_stars

    q50_multiplier = 1.0
    reason_parts: List[str] = []
    context_multipliers: Dict[str, float] = {}
    if isinstance(game_context, dict) and (
        "vegas_total" in game_context
        or "team_implied_total" in game_context
        or "team_spread_line" in game_context
    ):
        team_game_context = game_context
    else:
        team_game_context = _resolve_game_team_context(game_context, team)

    def apply_context_multiplier(key: str, multiplier: float, reason: str) -> None:
        nonlocal q50_multiplier
        if multiplier <= 0:
            return
        q50_multiplier *= multiplier
        context_multipliers[key] = round(multiplier, 4)
        if reason:
            reason_parts.append(reason)

    benefit_flags: List[str] = []
    recent5_minutes_avg = _safe_float(feature_snapshot.get("recent5_minutes_avg"))
    if recent5_minutes_avg is not None and recent5_minutes_avg >= OVERLAY_MIN_RECENT5_MINUTES:
        missing_same_pos_minutes = _safe_float(feature_snapshot.get("missing_same_pos_minutes"), 0.0) or 0.0
        missing_playmaker_potential_ast_pg = _safe_float(
            feature_snapshot.get("missing_playmaker_potential_ast_pg"),
            0.0,
        ) or 0.0
        playmaker_interaction = _safe_float(
            feature_snapshot.get("playmaker_vacuum_x_player_ast_rate"),
            0.0,
        ) or 0.0
        missing_onball_drives_pg = _safe_float(feature_snapshot.get("missing_onball_drives_pg"), 0.0) or 0.0
        onball_interaction = _safe_float(
            feature_snapshot.get("onball_vacuum_x_player_drive_rate"),
            0.0,
        ) or 0.0
        missing_high_usage_usage_pct = _safe_float(
            feature_snapshot.get("missing_high_usage_usage_pct"),
            0.0,
        ) or 0.0
        usage_interaction = _safe_float(
            feature_snapshot.get("usage_vacuum_x_player_usage_pct"),
            0.0,
        ) or 0.0

        if missing_same_pos_minutes >= SAME_POS_BENEFIT_MINUTES_THRESHOLD:
            benefit_flags.append("same_pos_benefit")
        if (
            missing_playmaker_potential_ast_pg >= CREATION_BENEFIT_POTENTIAL_AST_THRESHOLD
            and playmaker_interaction > 0.0
        ):
            benefit_flags.append("creation_benefit")
        if missing_onball_drives_pg >= ONBALL_BENEFIT_DRIVES_THRESHOLD and onball_interaction > 0.0:
            benefit_flags.append("onball_benefit")
        if missing_high_usage_usage_pct >= USAGE_BENEFIT_USAGE_THRESHOLD and usage_interaction > 0.0:
            benefit_flags.append("usage_benefit")

    adjustment["benefit_flags"] = benefit_flags
    if len(benefit_flags) == 1:
        apply_context_multiplier(
            "role_single_benefit",
            OVERLAY_SINGLE_BENEFIT_MULTIPLIER,
            benefit_flags[0].replace("_", " "),
        )
        adjustment["adjustment_type"] = "beneficiary_boost"
    elif len(benefit_flags) >= 2:
        apply_context_multiplier(
            "role_multi_benefit",
            OVERLAY_MULTI_BENEFIT_MULTIPLIER,
            ", ".join(flag.replace("_", " ") for flag in benefit_flags),
        )
        adjustment["adjustment_type"] = "beneficiary_boost"

    missing_team_minutes = _safe_float(feature_snapshot.get("missing_team_minutes"), 0.0) or 0.0
    missing_same_pos_minutes = _safe_float(feature_snapshot.get("missing_same_pos_minutes"), 0.0) or 0.0
    missing_key_teammate_count = _safe_float(feature_snapshot.get("missing_key_teammate_count"), 0.0) or 0.0
    modeled_minutes_delta = _safe_float(feature_snapshot.get("modeled_minutes_delta_vs_recent5"), 0.0) or 0.0
    modeled_minutes_q50 = _safe_float(feature_snapshot.get("modeled_minutes_q50"))
    returning_key_teammate_count = _safe_float(feature_snapshot.get("returning_key_teammate_count"), 0.0) or 0.0
    returning_same_pos_key_count = _safe_float(feature_snapshot.get("returning_same_pos_key_count"), 0.0) or 0.0
    adjustment["modeled_minutes_delta"] = round(modeled_minutes_delta, 2)

    scoring_context_stats = {"PTS", "FG3M", "PTS+AST", "PTS+REB", "PTS+REB+AST"}
    creation_context_stats = {"AST", "PTS+AST", "REB+AST", "PTS+REB+AST"}
    rebound_context_stats = {"REB", "PTS+REB", "REB+AST", "PTS+REB+AST"}
    defensive_context_stats = {"STL", "BLK", "STL+BLK"}
    role_sensitive_stats = scoring_context_stats | creation_context_stats | rebound_context_stats

    if (
        modeled_minutes_delta >= 2.0
        and (benefit_flags or missing_key_teammate_count >= 1.0)
        and (recent5_minutes_avg is None or recent5_minutes_avg >= 8.0)
    ):
        max_minutes_boost = 0.04 if stat_type in defensive_context_stats else 0.065
        minutes_boost_pct = min(max_minutes_boost, modeled_minutes_delta * 0.008)
        if minutes_boost_pct > 0.0:
            apply_context_multiplier(
                "modeled_minutes_promotion",
                1.0 + minutes_boost_pct,
                f"modeled minutes +{modeled_minutes_delta:.1f}",
            )
            if adjustment["adjustment_type"] == "none":
                adjustment["adjustment_type"] = "minutes_promotion_boost"

    vacancy_pressure = max(
        freed_usg / 75.0,
        missing_team_minutes / 90.0,
        missing_key_teammate_count / 3.0,
    )
    if benefit_flags and vacancy_pressure >= 1.0 and stat_type not in defensive_context_stats:
        severe_boost_pct = min(0.05, 0.02 + (vacancy_pressure - 1.0) * 0.025)
        apply_context_multiplier(
            "severe_vacancy",
            1.0 + severe_boost_pct,
            "severe teammate vacancy",
        )
        adjustment["severe_vacancy"] = True
        if adjustment["adjustment_type"] == "none":
            adjustment["adjustment_type"] = "severe_vacancy_boost"

    if (
        "same_pos_benefit" in benefit_flags
        and missing_same_pos_minutes >= 36.0
        and stat_type in role_sensitive_stats
    ):
        same_pos_boost_pct = min(0.035, (missing_same_pos_minutes - 30.0) * 0.0015)
        if same_pos_boost_pct > 0.0:
            apply_context_multiplier(
                "same_position_rotation_shock",
                1.0 + same_pos_boost_pct,
                "same-position rotation shock",
            )

    if returning_key_teammate_count > 0.0:
        returning_drag_pct = 0.015 + min(0.025, returning_key_teammate_count * 0.01)
        if returning_same_pos_key_count > 0.0 and stat_type in role_sensitive_stats:
            returning_drag_pct += 0.01
        if benefit_flags or modeled_minutes_delta >= 2.0:
            returning_drag_pct *= 0.45
        if returning_drag_pct > 0.0:
            apply_context_multiplier(
                "returning_key_teammate_drag",
                1.0 - min(0.05, returning_drag_pct),
                "returning key teammate tempers role",
            )
            adjustment["returning_teammate_drag"] = True

    if stat_type in {"REB", "PTS+REB+AST", "PTS+REB", "REB+AST"}:
        for dnp in tonight_opponent_dnps or []:
            role = dnp.get("role")
            pos = str(dnp.get("position") or "").upper()
            if role in {"star", "starter"} and ("C" in pos or "F" in pos):
                opponent_frontcourt_freed += (dnp.get("season_minutes") or 0.0)
                if role == "star":
                    opponent_reb_context = "star_out"
                elif role == "starter" and opponent_reb_context != "star_out":
                    opponent_reb_context = "starter_out"
                    
        if opponent_frontcourt_freed >= 24.0:
            opponent_reb_multiplier = 1.07 if opponent_reb_context == "star_out" else 1.04
            apply_context_multiplier(
                "opponent_frontcourt_absence",
                opponent_reb_multiplier,
                "easier rebounding context",
            )
            adjustment["opponent_reb_context"] = opponent_reb_context
            if adjustment["adjustment_type"] == "none":
                adjustment["adjustment_type"] = "opponent_reb_boost"

    vegas_total = _safe_float(team_game_context.get("vegas_total")) if team_game_context else None
    team_implied_total = _safe_float(team_game_context.get("team_implied_total")) if team_game_context else None
    spread_abs = _safe_float(team_game_context.get("spread_abs")) if team_game_context else None
    if vegas_total is not None:
        if vegas_total >= 230.0 and (
            stat_type in scoring_context_stats
            or stat_type in creation_context_stats
        ):
            total_boost_pct = min(0.04, (vegas_total - 228.0) * 0.003)
            apply_context_multiplier(
                "high_game_total",
                1.0 + total_boost_pct,
                f"high game total {vegas_total:.1f}",
            )
        elif vegas_total <= 214.0 and stat_type in scoring_context_stats:
            total_drag_pct = min(0.045, (216.0 - vegas_total) * 0.004)
            apply_context_multiplier(
                "low_game_total",
                1.0 - max(0.0, total_drag_pct),
                f"low game total {vegas_total:.1f}",
            )

        if vegas_total <= 216.0 and player_position_group in {"C", "F"} and stat_type in rebound_context_stats:
            rebound_total_boost_pct = min(0.035, (218.0 - vegas_total) * 0.003)
            apply_context_multiplier(
                "low_total_rebounding",
                1.0 + max(0.0, rebound_total_boost_pct),
                "low total favors rebound volume",
            )

    if team_implied_total is not None:
        if team_implied_total >= 118.0 and (
            stat_type in scoring_context_stats
            or stat_type in creation_context_stats
        ):
            implied_boost_pct = min(0.035, (team_implied_total - 116.0) * 0.003)
            apply_context_multiplier(
                "high_team_implied_total",
                1.0 + implied_boost_pct,
                f"team implied total {team_implied_total:.1f}",
            )
        elif team_implied_total <= 106.0 and stat_type in scoring_context_stats:
            implied_drag_pct = min(0.04, (108.0 - team_implied_total) * 0.004)
            apply_context_multiplier(
                "low_team_implied_total",
                1.0 - max(0.0, implied_drag_pct),
                f"low team implied total {team_implied_total:.1f}",
            )

    minutes_reference = modeled_minutes_q50 or player_season_minutes or recent5_minutes_avg
    if spread_abs is not None and spread_abs >= 12.0 and minutes_reference is not None:
        protected_promotion = bool(benefit_flags and modeled_minutes_delta >= 3.0)
        if minutes_reference >= 26.0 and not protected_promotion:
            blowout_drag_pct = min(0.055, (spread_abs - 10.0) * 0.006)
            apply_context_multiplier(
                "blowout_minutes_drag",
                1.0 - max(0.0, blowout_drag_pct),
                f"spread {spread_abs:.1f} adds blowout risk",
            )
            adjustment["blowout_risk_boost"] = True

    adjustment["context_multipliers"] = context_multipliers
    adjustment["vegas_context"] = {
        "vegas_total": vegas_total,
        "team_implied_total": team_implied_total,
        "spread_abs": spread_abs,
        "team_spread_line": _safe_float(team_game_context.get("team_spread_line")) if team_game_context else None,
        "market_book_label": team_game_context.get("market_book_label") if team_game_context else None,
    }
    adjustment["reason"] = " | ".join(dict.fromkeys(reason_parts))
    adjustment["q50_multiplier"] = _clamp(q50_multiplier, 0.70, OVERLAY_MAX_MULTIPLIER)
    return adjustment


def _injury_refresh_target_age_minutes(schedule_payload: Dict[str, Any]) -> int:
    now = get_et_now()
    rows = schedule_payload.get("games", []) if isinstance(schedule_payload, dict) else []
    today_str = now.date().isoformat()
    has_today_games = any(
        isinstance(row, dict) and _normalize_game_date(row.get("game_date")) == today_str
        for row in rows
    )
    if has_today_games and now.hour >= INJURY_FRESHNESS_LOCK_SENSITIVE_START_HOUR_ET:
        return INJURY_FRESHNESS_LOCK_SENSITIVE_MAX_AGE_MINUTES
    return INJURY_FRESHNESS_NORMAL_MAX_AGE_MINUTES


def _refresh_injury_report_for_scoring(
    schedule_payload: Dict[str, Any],
    *,
    refresh_label: str,
) -> None:
    target_age_minutes = _injury_refresh_target_age_minutes(schedule_payload)
    try:
        from pathlib import Path
        from scrapers import fetch_nba_injury_report

        result = fetch_nba_injury_report.refresh_nba_injury_report_if_needed(
            output_path=Path(os.path.join(CURRENT_DIR, "nba_injury_report.json")),
            schedule_path=Path(SCHEDULE_PATH),
            min_refresh_interval_seconds=int(target_age_minutes * 60),
        )
        payload = result.get("payload") if isinstance(result, dict) else {}
        logger.info(
            "Edge Score injury refresh | label=%s refreshed=%s age_target_min=%s games=%s players=%s",
            refresh_label,
            bool(result.get("refreshed")) if isinstance(result, dict) else False,
            target_age_minutes,
            (payload or {}).get("game_count", 0),
            (payload or {}).get("player_row_count", 0),
        )
    except Exception as exc:
        logger.warning("Edge Score injury pre-refresh failed: %s", exc)


def _injury_artifact_freshness(
    injury_report: Dict[str, Any],
    *,
    schedule_payload: Dict[str, Any],
) -> Dict[str, Any]:
    generated_at = str((injury_report or {}).get("generated_at") or "").strip()
    age_minutes = None
    if generated_at:
        try:
            generated_dt = datetime.fromisoformat(generated_at)
            if generated_dt.tzinfo is None:
                generated_dt = generated_dt.replace(tzinfo=ET_ZONE)
            age_minutes = round(
                max(0.0, (get_et_now() - generated_dt.astimezone(ET_ZONE)).total_seconds() / 60.0),
                2,
            )
        except ValueError:
            age_minutes = None
    max_age_minutes = _injury_refresh_target_age_minutes(schedule_payload)
    return {
        "generated_at": generated_at or None,
        "report_timestamp_et": (injury_report or {}).get("report_timestamp_et"),
        "query_date": (injury_report or {}).get("query_date"),
        "age_minutes": age_minutes,
        "max_age_minutes": float(max_age_minutes),
        "is_stale": age_minutes is None or age_minutes > max_age_minutes,
    }


def _get_promotion_guardrail_config() -> Dict[str, float]:
    global _ml_predictor
    raw_config = {}
    if _ml_predictor is not None and isinstance(getattr(_ml_predictor, "meta", None), dict):
        raw_config = _ml_predictor.meta.get("promotion_guardrail", {}) or {}
    return resolve_promotion_guardrail_config(raw_config)


def _is_combo_stat_type(stat_type: str) -> bool:
    return "+" in str(stat_type or "")


def _has_positive_creator_interaction(feature_snapshot: Dict[str, Any]) -> bool:
    return any(
        (_safe_float(feature_snapshot.get(key), 0.0) or 0.0) > 0.0
        for key in (
            "playmaker_vacuum_x_player_ast_rate",
            "onball_vacuum_x_player_drive_rate",
            "missing_playmaker_potential_ast_pg_x_player_ast_rate",
            "missing_onball_drives_pg_x_player_drive_rate",
        )
    )


def _is_injury_sensitive_recommendation(feature_snapshot: Dict[str, Any]) -> bool:
    return any(
        (
            (_safe_float(feature_snapshot.get("missing_key_teammate_count"), 0.0) or 0.0) > 0.0,
            (_safe_float(feature_snapshot.get("returning_key_teammate_count"), 0.0) or 0.0) > 0.0,
            (_safe_float(feature_snapshot.get("missing_team_usage_pct"), 0.0) or 0.0) >= 25.0,
            (_safe_float(feature_snapshot.get("missing_team_minutes"), 0.0) or 0.0) >= 30.0,
        )
    )


def _compute_recommendation_eligibility(
    *,
    side: str,
    ml_details: Dict[str, Any],
) -> Dict[str, Any]:
    feature_snapshot = ml_details.get("injury_feature_snapshot") or {}
    injury_sensitive = _is_injury_sensitive_recommendation(feature_snapshot)
    injury_freshness = ml_details.get("injury_report_freshness") or {}
    if injury_sensitive and injury_freshness.get("is_stale"):
        return {
            "injury_sensitive": True,
            "eligibility_blocked": True,
            "eligibility_block_reason": "blocked_stale_injury_context",
        }

    missing_team_usage_pct = _safe_float(feature_snapshot.get("missing_team_usage_pct"), 0.0) or 0.0
    missing_team_minutes = _safe_float(feature_snapshot.get("missing_team_minutes"), 0.0) or 0.0
    missing_key_teammate_count = _safe_float(feature_snapshot.get("missing_key_teammate_count"), 0.0) or 0.0
    returning_key_teammate_count = _safe_float(feature_snapshot.get("returning_key_teammate_count"), 0.0) or 0.0
    modeled_minutes_delta = _safe_float(feature_snapshot.get("modeled_minutes_delta_vs_recent5"), 0.0) or 0.0
    teammate_minutes_delta = _safe_float(
        feature_snapshot.get("missing_key_teammates_player_minutes_delta"),
        0.0,
    ) or 0.0
    teammate_stat_delta = _safe_float(
        feature_snapshot.get("missing_key_teammates_player_stat_delta"),
        0.0,
    ) or 0.0

    if (
        side == "under"
        and injury_sensitive
        and missing_team_usage_pct >= 45.0
        and missing_team_minutes >= 60.0
        and (
            missing_key_teammate_count >= 2.0
            or returning_key_teammate_count >= 2.0
        )
        and (
            modeled_minutes_delta >= 3.0
            or teammate_minutes_delta >= 2.0
        )
        and (
            teammate_stat_delta > 0.0
            or _has_positive_creator_interaction(feature_snapshot)
        )
    ):
        return {
            "injury_sensitive": True,
            "eligibility_blocked": True,
            "eligibility_block_reason": "blocked_promotion_under",
        }

    return {
        "injury_sensitive": injury_sensitive,
        "eligibility_blocked": False,
        "eligibility_block_reason": "",
    }


def _compute_promotion_guardrail(
    *,
    player: Dict[str, Any],
    stat_type: str,
    side: str,
    line: float,
    ml_details: Dict[str, Any],
    edge_score: float,
    confidence: float,
) -> Dict[str, Any]:
    guardrail = {
        "active": False,
        "reason": "",
        "edge_score_penalty": 0.0,
        "confidence_penalty": 0.0,
        "display_edge_score": edge_score,
        "display_confidence": confidence,
        "gap_pct": None,
        "gap_threshold_pct": None,
    }
    if side != "under" or stat_type not in PROMOTION_GUARDRAIL_SUPPORTED_STAT_TYPES:
        return guardrail

    injury_freshness = ml_details.get("injury_report_freshness") or {}
    if injury_freshness.get("is_stale"):
        guardrail["reason"] = "injury_context_stale"
        return guardrail

    feature_snapshot = ml_details.get("injury_feature_snapshot") or {}
    q50 = _safe_float(ml_details.get("q50"), _safe_float(ml_details.get("prediction_val")))
    if q50 is None or q50 >= line:
        return guardrail

    config = _get_promotion_guardrail_config()
    missing_team_usage_pct = _safe_float(feature_snapshot.get("missing_team_usage_pct"), 0.0) or 0.0
    missing_team_minutes = _safe_float(feature_snapshot.get("missing_team_minutes"), 0.0) or 0.0
    recent5_minutes_avg = _safe_float(feature_snapshot.get("recent5_minutes_avg"), 0.0) or 0.0
    modeled_minutes_delta = _safe_float(feature_snapshot.get("modeled_minutes_delta_vs_recent5"), 0.0) or 0.0
    teammate_minutes_delta = _safe_float(
        feature_snapshot.get("missing_key_teammates_player_minutes_delta"),
        0.0,
    ) or 0.0
    if (
        missing_team_usage_pct < config["min_missing_team_usage_pct"]
        or missing_team_minutes < config["min_missing_team_minutes"]
        or recent5_minutes_avg < config["min_recent5_minutes_avg"]
        or (
            modeled_minutes_delta < config["min_modeled_minutes_delta_vs_recent5"]
            and teammate_minutes_delta < config["min_missing_key_teammates_player_minutes_delta"]
        )
    ):
        return guardrail

    pos_group = str(
        feature_snapshot.get("resolved_pos_group")
        or _simple_position(player.get("position"))
        or "G"
    ).upper()
    missing_same_pos_minutes = _safe_float(feature_snapshot.get("missing_same_pos_minutes"), 0.0) or 0.0
    missing_guard_minutes = _safe_float(feature_snapshot.get("missing_guard_minutes"), 0.0) or 0.0
    missing_playmaker_potential_ast_pg = _safe_float(
        feature_snapshot.get("missing_playmaker_potential_ast_pg"),
        0.0,
    ) or 0.0
    missing_onball_drives_pg = _safe_float(feature_snapshot.get("missing_onball_drives_pg"), 0.0) or 0.0
    playmaker_interaction = _safe_float(
        feature_snapshot.get("missing_playmaker_potential_ast_pg_x_player_ast_rate"),
        _safe_float(feature_snapshot.get("playmaker_vacuum_x_player_ast_rate"), 0.0),
    ) or 0.0
    onball_interaction = _safe_float(
        feature_snapshot.get("missing_onball_drives_pg_x_player_drive_rate"),
        _safe_float(feature_snapshot.get("onball_vacuum_x_player_drive_rate"), 0.0),
    ) or 0.0

    role_reasons: List[str] = []
    role_aligned = False
    if pos_group == "G":
        if (
            missing_guard_minutes >= config["min_missing_guard_minutes"]
            or missing_same_pos_minutes >= config["min_missing_same_pos_minutes"]
        ):
            role_aligned = True
            role_reasons.append("guard vacancy")
    else:
        if missing_same_pos_minutes >= config["min_missing_same_pos_minutes"]:
            role_aligned = True
            role_reasons.append("same-position vacancy")
        elif (
            (
                missing_playmaker_potential_ast_pg >= config["min_cross_position_creator_metric"]
                and playmaker_interaction > 0.0
            )
            or (
                missing_onball_drives_pg >= config["min_cross_position_creator_metric"]
                and onball_interaction > 0.0
            )
        ):
            role_aligned = True
            role_reasons.append("cross-position creator vacancy")

    if not role_aligned:
        return guardrail

    gap_pct = max(0.0, (line - q50) / max(abs(line), 1.0))
    gap_threshold_pct = (
        config["combo_stat_gap_pct"]
        if _is_combo_stat_type(stat_type)
        else config["single_stat_gap_pct"]
    )
    guardrail["gap_pct"] = round(gap_pct, 4)
    guardrail["gap_threshold_pct"] = round(gap_threshold_pct, 4)
    if gap_pct >= gap_threshold_pct:
        return guardrail

    display_edge_score = min(
        edge_score - config["edge_score_penalty_points"],
        config["display_edge_score_cap"],
    )
    display_confidence = max(0.0, confidence - config["confidence_penalty_points"])
    guardrail.update({
        "active": True,
        "reason": ", ".join(role_reasons),
        "edge_score_penalty": round(max(0.0, edge_score - display_edge_score), 2),
        "confidence_penalty": round(max(0.0, confidence - display_confidence), 2),
        "display_edge_score": round(max(1.0, display_edge_score), 1),
        "display_confidence": round(display_confidence, 1),
    })
    return guardrail


_ml_predictor = None

def _compute_ml_regression_context(
    player: Dict[str, Any],
    opponent: str,
    stat_type: str,
    line: float,
    side: str,
    tonight_dnps: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    tonight_opponent_dnps: Optional[List[Dict[str, Any]]] = None,
    team_recent_games: Optional[Dict[str, str]] = None,
    game_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute the ML regression component score for a player/stat/line/side combination.

    Consumes quantile outputs (q25, q50, q75) from the predictor when available,
    falling back to the legacy (prediction + std_dev) interface if the predictor
    has not yet been updated to emit quantiles.

    The quantile spread is used to derive p_over via a simple linear interpolation
    across the [q25, q75] interval, which avoids the Gaussian assumption of the old
    norm.cdf approach and is robust to skewed distributions.

    p_over is exposed in the returned details dict so it flows through to the
    candidate output and can be used by the edge score boost logic.
    """
    global _ml_predictor
    if _ml_predictor is None:
        from utils.ml_inference import get_ml_predictor
        _ml_predictor = get_ml_predictor()

    logs = _extract_logs(player)
    if not logs:
        return {"available": False, "score": 0.0, "p_over": None, "details": {}}

    player_info = {
        "player_id": player.get("id"),
        "player_name": player.get("name"),
        "team": player.get("team"),
        "opponent_abbrev": opponent,
        "position": player.get("position"),
    }

    if isinstance(game_context, dict) and (
        "vegas_total" in game_context
        or "team_implied_total" in game_context
        or "team_spread_line" in game_context
    ):
        resolved_game_context = game_context
    else:
        resolved_game_context = _resolve_game_team_context(game_context, player.get("team"))
    res = _ml_predictor.predict(
        player_info,
        logs,
        stat_type,
        include_features=True,
        game_context=resolved_game_context,
    )
    if not res:
        return {"available": False, "score": 0.0, "p_over": None, "details": {}}
    feature_snapshot = res.get("feature_snapshot", {}) if isinstance(res, dict) else {}
    injury_report_freshness = res.get("injury_report_freshness", {}) if isinstance(res, dict) else {}
    position_resolution_summary = res.get("position_resolution_summary", {}) if isinstance(res, dict) else {}
    runtime_context = res.get("runtime_context", {}) if isinstance(res, dict) else {}

    # ── Quantile path (preferred) ────────────────────────────────────────────
    q25 = _safe_float(res.get("q25"))
    q50 = _safe_float(res.get("q50"))
    q75 = _safe_float(res.get("q75"))

    if q25 is not None and q50 is not None and q75 is not None:
        adjustment = _compute_lineup_adjustment(
            player=player,
            stat_type=stat_type,
            side=side,
            tonight_dnps=tonight_dnps,
            tonight_opponent_dnps=tonight_opponent_dnps,
            logs=logs,
            team_recent_games=team_recent_games or {},
            game_context=resolved_game_context,
            feature_snapshot=feature_snapshot,
        )
        mult = adjustment.get("q50_multiplier", 1.0)
        if mult != 1.0:
            q25 *= mult
            q50 *= mult
            q75 *= mult
            logger.debug(
                "Lineup adjustment applied | player=%s stat=%s mult=%.3f reason=%s",
                player.get("name"), stat_type, mult, adjustment.get("reason"),
            )
            
        # Linear interpolation across [q25, q75] to estimate p_over.
        # - line <= q25  → ~75% of outcomes are above  → p_over ≈ 0.75
        # - line == q50  → 50% of outcomes are above   → p_over = 0.50
        # - line >= q75  → ~25% of outcomes are above  → p_over ≈ 0.25
        # We clamp to [0.10, 0.90] to avoid extreme scores from extrapolation.
        iqr = q75 - q25
        if iqr > 1e-6:
            # Fraction of the IQR that the line sits above q25
            frac = _clamp((line - q25) / iqr, 0.0, 1.0)
            # frac=0 → line is at/below q25 → p_over=0.75; frac=1 → p_over=0.25
            p_over = _clamp(0.75 - frac * 0.50, 0.10, 0.90)
        else:
            # Zero-width IQR (degenerate prediction) — fall back to median comparison
            p_over = 0.60 if q50 > line else 0.40

        prediction_val = q50
        spread = round(q75 - q25, 2)
        details = {
            "prediction_val": round(q50, 2),
            "q25": round(q25, 2),
            "q50": round(q50, 2),
            "q75": round(q75, 2),
            "quantile_spread": spread,
            "hit_probability": round(p_over * 100.0, 1),
            "p_over": round(p_over, 4),
            "calibration": "quantile_iqr",
            "ml_lineup_adjustment": adjustment,
            "injury_report_freshness": injury_report_freshness,
            "position_resolution_summary": position_resolution_summary,
            "runtime_context": runtime_context,
            "injury_feature_snapshot": {
                key: feature_snapshot.get(key)
                for key in (
                    "resolved_pos_group",
                    "position_resolution_tier",
                    "recent5_minutes_avg",
                    "predicted_minutes",
                    "modeled_minutes_q50",
                    "modeled_minutes_iqr",
                    "modeled_minutes_delta_vs_recent5",
                    "modeled_minutes_x_recent10_target_per_min",
                    "recent10_target_per_min",
                    "same_team_current_season_games",
                    "recent_team_games_missed_10",
                    "inactive_streak_team_games",
                    "games_since_return",
                    "previous_absence_streak_team_games",
                    "missing_team_usage_pct",
                    "missing_team_minutes",
                    "missing_same_pos_usage_pct",
                    "missing_same_pos_minutes",
                    "missing_guard_usage_pct",
                    "missing_guard_minutes",
                    "missing_high_usage_usage_pct",
                    "missing_high_usage_minutes",
                    "missing_playmaker_potential_ast_pg",
                    "missing_playmaker_minutes",
                    "missing_onball_drives_pg",
                    "missing_onball_minutes",
                    "playmaker_vacuum_x_player_ast_rate",
                    "onball_vacuum_x_player_drive_rate",
                    "usage_vacuum_x_player_usage_pct",
                    "missing_playmaker_potential_ast_pg_x_player_ast_rate",
                    "missing_onball_drives_pg_x_player_drive_rate",
                    "missing_high_usage_usage_pct_x_player_usage_rate",
                    "missing_same_pos_minutes_x_player_target_per_min",
                    "missing_playmaker_potential_ast_pg_x_player_target_per_min",
                    "missing_onball_drives_pg_x_player_target_per_min",
                    "missing_key_teammates_player_stat_delta",
                    "missing_key_teammates_player_minutes_delta",
                    "missing_key_teammates_player_usage_pct_delta",
                    "missing_key_teammates_player_potential_ast_rate_delta",
                    "missing_key_teammates_player_drive_rate_delta",
                    "missing_key_teammates_player_target_per_min_delta",
                    "missing_key_teammates_effective_support",
                    "missing_key_teammate_count",
                    "missing_same_pos_key_count",
                    "missing_guard_key_count",
                    "missing_playmaker_key_count",
                    "returning_key_teammates_player_stat_delta",
                    "returning_key_teammates_player_minutes_delta",
                    "returning_key_teammates_player_usage_pct_delta",
                    "returning_key_teammates_player_potential_ast_rate_delta",
                    "returning_key_teammates_player_drive_rate_delta",
                    "returning_key_teammates_player_target_per_min_delta",
                    "returning_key_teammates_effective_support",
                    "returning_key_teammate_count",
                    "returning_same_pos_key_count",
                    "returning_guard_key_count",
                    "returning_playmaker_key_count",
                    "modeled_minutes_q50_x_missing_key_teammates_player_target_per_min_delta",
                    "modeled_minutes_q50_x_returning_key_teammates_player_target_per_min_delta",
                )
            },
            "vegas_total": _safe_float(resolved_game_context.get("vegas_total")),
            "vegas_spread": _safe_float(resolved_game_context.get("vegas_spread")),
            "team_implied_total": _safe_float(resolved_game_context.get("team_implied_total")),
            "team_spread_line": _safe_float(resolved_game_context.get("team_spread_line")),
            "spread_abs": _safe_float(resolved_game_context.get("spread_abs")),
        }

    # ── Legacy path (std_dev / Gaussian fallback) ────────────────────────────
    else:
        prediction_val = _safe_float(res.get("prediction"))
        std_dev = _safe_float(res.get("std_dev"))
        if prediction_val is None:
            return {"available": False, "score": 0.0, "p_over": None, "details": {}}

        p_over = _ml_predictor.hit_probability(prediction_val, std_dev, line, "over")
        p_over = _clamp(p_over, 0.10, 0.90)
        details = {
            "prediction_val": round(prediction_val, 2),
            "hit_probability": round(p_over * 100.0, 1),
            "p_over": round(p_over, 4),
            "std_dev": round(std_dev, 2) if std_dev is not None else None,
            "calibration": "gaussian_legacy",
            "injury_report_freshness": injury_report_freshness,
            "position_resolution_summary": position_resolution_summary,
            "runtime_context": runtime_context,
            "vegas_total": _safe_float(resolved_game_context.get("vegas_total")),
            "vegas_spread": _safe_float(resolved_game_context.get("vegas_spread")),
            "team_implied_total": _safe_float(resolved_game_context.get("team_implied_total")),
            "team_spread_line": _safe_float(resolved_game_context.get("team_spread_line")),
            "spread_abs": _safe_float(resolved_game_context.get("spread_abs")),
        }

    # ── Score mapping: p_over → [-1, 1] ─────────────────────────────────────
    # p_over > 0.5 leans towards +1.0 (over signal)
    # p_over < 0.5 leans towards -1.0 (under signal)
    # We use the requested side to orient the score correctly.
    if side == "over":
        prob_for_side = p_over
    else:
        prob_for_side = 1.0 - p_over

    score = _clamp((prob_for_side - 0.5) * 2.0, -1.0, 1.0)

    return {
        "available": True,
        "raw_score": score,
        "score": score,
        "p_over": round(p_over, 4),
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


def _compute_projection_context(
    entry: Dict[str, Any],
    stat_type: str,
    line: float,
    side: str,
    blowout_risk_boost: bool = False,
) -> Dict[str, Any]:
    player = entry.get("player") if isinstance(entry, dict) else {}
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    season_avg = _stat_value_from_stats(player.get("stats", {}), stat_type)
    logs = _extract_logs(player)
    values = [value for value in (_stat_value_from_game(game, stat_type) for game in logs[:10]) if value is not None]
    recent5_avg = _average(values[:5])
    recent10_avg = _average(values[:10])

    minutes_context = _compute_expected_minutes_context(entry, logs, blowout_risk_boost=blowout_risk_boost)
    expected_minutes = _safe_float(minutes_context.get("expected_minutes"))
    season_minutes = _safe_float(minutes_context.get("season_minutes"))
    recent_minutes = _safe_float(minutes_context.get("recent_5_minutes"))
    rate_context = _estimate_stat_rate_context(player, logs, stat_type)
    expected_rate = _safe_float(rate_context.get("expected_rate"))

    opportunity_projection = None
    if expected_minutes is not None and expected_rate is not None:
        opportunity_projection = expected_minutes * expected_rate

    legacy_projection = _legacy_projection_baseline(
        season_avg,
        recent10_avg,
        recent5_avg,
        season_minutes,
        recent_minutes,
    )
    baseline_projection = _weighted_average([
        (opportunity_projection, 0.78),
        (legacy_projection, 0.22),
    ])
    projection_method = "opportunity_v1" if opportunity_projection is not None else "legacy_average"

    projection_gap = None if baseline_projection is None else SIDE_MULTIPLIERS[side] * (baseline_projection - line)
    score = _normalize_score_by_scale(projection_gap, scale)
    available = baseline_projection is not None
    return {
        "available": available,
        "score": score if available else 0.0,
        "raw_score": score if available else 0.0,
        "details": {
            "model_type": projection_method,
            "season_avg": _round(season_avg),
            "recent_5_avg": _round(recent5_avg),
            "recent_10_avg": _round(recent10_avg),
            "recent_minutes": _round(recent_minutes),
            "season_minutes": _round(season_minutes),
            "expected_minutes": _round(expected_minutes),
            "expected_rate": _round(expected_rate, 3),
            "opportunity_projection": _round(opportunity_projection),
            "legacy_projection": _round(legacy_projection),
            "baseline_projection": _round(baseline_projection),
            "projection_gap": _round(projection_gap),
            "minutes_context": {
                "minutes_baseline": _round(minutes_context.get("minutes_baseline")),
                "minutes_adjustment_ratio": _round(minutes_context.get("minutes_adjustment_ratio"), 3),
                "blowout_risk": _round(minutes_context.get("blowout_risk"), 3),
                "team_win_pct": _round(minutes_context.get("team_win_pct"), 3),
                "opponent_win_pct": _round(minutes_context.get("opponent_win_pct"), 3),
                "competitive_minutes_avg": _round(minutes_context.get("competitive_minutes_avg")),
                "blowout_minutes_avg": _round(minutes_context.get("blowout_minutes_avg")),
                "competitive_sample_size": minutes_context.get("competitive_sample_size"),
                "blowout_sample_size": minutes_context.get("blowout_sample_size"),
            },
            "rate_context": rate_context.get("details", {}),
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
    b2b_rest_flip: bool = False,
) -> Dict[str, Any]:
    logs = _extract_logs(player)
    if not logs:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    current_game_date = _parse_date(game_date)
    latest_logged_date = _parse_date(logs[0].get("GAME_DATE"))
    if current_game_date is None or latest_logged_date is None:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    profile = STAT_PROFILES.get(stat_type, {})
    if b2b_rest_flip:
        base_bias = profile.get("b2b_bias", -0.08)
        score = _clamp(abs(base_bias) * 0.5 * SIDE_MULTIPLIERS[side], -0.25, 0.25)
        return {
            "available": True,
            "score": score,
            "raw_score": score,
            "details": {
                "current_is_b2b": False,
                "b2b_rest_flip": True,
                "fallback_bias": _round(abs(base_bias) * 0.5),
            },
        }

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
    profile = STAT_PROFILES.get(stat_type, {})
    projection_details = components["projection"]["details"]
    projection_gap = projection_details.get("projection_gap")
    if projection_gap is not None:
        direction_word = "above" if projection_gap >= 0 else "below"
        model_label = "Opportunity-based baseline" if projection_details.get("model_type") == "opportunity_v1" else "Baseline projection"
        reasons.append(
            f"{model_label} sits {abs(projection_gap):.1f} {direction_word} the line."
        )

    expected_minutes = _safe_float(projection_details.get("expected_minutes"))
    expected_rate = _safe_float(projection_details.get("expected_rate"))
    if expected_minutes is not None and expected_rate is not None:
        rate_label = str(profile.get("display") or stat_type).lower()
        reasons.append(
            f"Expected role is about {expected_minutes:.1f} minutes at {expected_rate:.2f} {rate_label} per minute."
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
    tonight_dnps: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    team_recent_games: Optional[Dict[str, str]] = None,
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
        "projection": _compute_projection_context(entry, stat_type, line, side),
        "recent_form": _compute_recent_form_context(player, stat_type, line, side),
        "matchup": _compute_matchup_context(player, stat_type, side),
        "market": _compute_market_context(market_selection, side),
        "ml_regression": _compute_ml_regression_context(
            player=player,
            opponent=opponent,
            stat_type=stat_type,
            line=line,
            side=side,
            tonight_dnps=tonight_dnps,
            tonight_opponent_dnps=tonight_dnps.get(opponent, []) if tonight_dnps and opponent else [],
            team_recent_games=team_recent_games,
            game_context=entry.get("game_context"),
        ),
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

    # ── ML p_over soft boost ─────────────────────────────────────────────────
    # When the ML model has high directional conviction (p_over >= 0.60 for an
    # over pick, or p_over <= 0.40 for an under pick) we apply a small additive
    # boost to the weighted score before it is converted to an edge_score.
    # The boost is intentionally modest (+0.04 max) so it cannot override weak
    # signals from other components — it only amplifies already-strong edges.
    # We also apply a soft penalty (-0.03) when the ML model disagrees with
    # the pick direction, to discount edges where the ML and other signals diverge.
    p_over = _safe_float(components["ml_regression"].get("p_over"))
    ml_boost = 0.0
    if p_over is not None and components["ml_regression"].get("available"):
        if side == "over":
            if p_over >= 0.60:
                ml_boost = _clamp((p_over - 0.60) / 0.30 * 0.04, 0.0, 0.04)
            elif p_over <= 0.40:
                ml_boost = _clamp((p_over - 0.40) / 0.30 * 0.03, -0.03, 0.0)
        else:  # under
            p_under = 1.0 - p_over
            if p_under >= 0.60:
                ml_boost = _clamp((p_under - 0.60) / 0.30 * 0.04, 0.0, 0.04)
            elif p_under <= 0.40:
                ml_boost = _clamp((p_under - 0.40) / 0.30 * 0.03, -0.03, 0.0)
    weighted_score = _clamp(weighted_score + ml_boost, -1.0, 1.0)
    confidence_multiplier = 0.85 + (min(confidence, 100.0) / 100.0) * 0.15
    edge_score = round(_clamp(50.0 + (weighted_score * 45.0 * confidence_multiplier), 1.0, 99.0), 1)
    promotion_guardrail = _compute_promotion_guardrail(
        player=player,
        stat_type=stat_type,
        side=side,
        line=line,
        ml_details=components["ml_regression"].get("details", {}) or {},
        edge_score=edge_score,
        confidence=confidence,
    )
    eligibility = _compute_recommendation_eligibility(
        side=side,
        ml_details=components["ml_regression"].get("details", {}) or {},
    )
    display_edge_score = promotion_guardrail.get("display_edge_score", edge_score)
    display_confidence = promotion_guardrail.get("display_confidence", confidence)

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
        "display_edge_score": round(float(display_edge_score), 1),
        "confidence": confidence,
        "display_confidence": round(float(display_confidence), 1),
        "signal_score": round(weighted_score, 3),
        "ml_p_over": p_over,
        "ml_boost_applied": round(ml_boost, 4) if ml_boost != 0.0 else None,
        "guardrail_active": bool(promotion_guardrail.get("active")),
        "guardrail_reason": promotion_guardrail.get("reason"),
        "guardrail_confidence_penalty": promotion_guardrail.get("confidence_penalty"),
        "guardrail_edge_score_penalty": promotion_guardrail.get("edge_score_penalty"),
        "injury_sensitive": bool(eligibility.get("injury_sensitive")),
        "eligibility_blocked": bool(eligibility.get("eligibility_blocked")),
        "eligibility_block_reason": str(eligibility.get("eligibility_block_reason") or ""),
        "reasons": reasons,
        "inputs": {
            "projection": components["projection"]["details"],
            "recent_form": components["recent_form"]["details"],
            "matchup": components["matchup"]["details"],
            "market": components["market"]["details"],
            "ml_regression": components["ml_regression"]["details"],
            "promotion_guardrail": promotion_guardrail,
            "eligibility": eligibility,
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


def _diversify_candidates(
    candidates: List[Dict[str, Any]],
    limit: int,
    *,
    per_player_limit: int = EDGE_TOP_PER_PLAYER_LIMIT,
    per_game_limit: int = EDGE_TOP_PER_GAME_LIMIT,
    prevent_component_overlap: bool = True,
) -> List[Dict[str, Any]]:
    output = []
    per_player_counts: Dict[int, int] = {}
    selected_by_player: Dict[int, List[Dict[str, Any]]] = {}
    per_game_counts: Dict[str, int] = {}
    seen_keys = set()

    for candidate in candidates:
        recommendation_key = candidate.get("recommendation_key")
        if recommendation_key in seen_keys:
            continue
        player_id = int(candidate.get("player_id"))
        selected_for_player = selected_by_player.get(player_id, [])
        game_id = str(candidate.get("game_id") or "")
        if per_player_counts.get(player_id, 0) >= per_player_limit:
            continue
        if prevent_component_overlap and any(
            _discord_recommendations_conflict(candidate, existing)
            for existing in selected_for_player
        ):
            continue
        if game_id and per_game_counts.get(game_id, 0) >= per_game_limit:
            continue

        seen_keys.add(recommendation_key)
        per_player_counts[player_id] = per_player_counts.get(player_id, 0) + 1
        selected_by_player.setdefault(player_id, []).append(candidate)
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
        book_recommendations = _diversify_candidates(
            book_candidates,
            limit_per_book,
            per_player_limit=EDGE_TOP_PER_PLAYER_LIMIT,
            per_game_limit=EDGE_TOP_PER_GAME_LIMIT,
            prevent_component_overlap=True,
        )
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
                "display_edge_score": recommendation.get("display_edge_score"),
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
            "display_edge_score": recommendation.get("display_edge_score"),
            "rank": recommendation.get("rank"),
        }
    return snapshot


def _load_preservable_previous_board(
    previous_payload: Dict[str, Any],
    *,
    current_game_dates: List[str],
) -> Optional[Dict[str, Any]]:
    if not isinstance(previous_payload, dict):
        return None
    previous_recommendations = previous_payload.get("recommendations", [])
    if not isinstance(previous_recommendations, list) or not previous_recommendations:
        return None

    normalized_current_dates = sorted({
        _normalize_game_date(game_date)
        for game_date in current_game_dates
        if _normalize_game_date(game_date)
    })
    normalized_previous_dates = sorted({
        _normalize_game_date(game_date)
        for game_date in previous_payload.get("game_dates", [])
        if _normalize_game_date(game_date)
    })
    if normalized_current_dates and normalized_previous_dates != normalized_current_dates:
        return None

    previous_summary = previous_payload.get("summary", {})
    if not isinstance(previous_summary, dict):
        previous_summary = {}
    previous_freshness = previous_summary.get("injury_report_freshness", {})
    if isinstance(previous_freshness, dict) and previous_freshness.get("is_stale"):
        return None

    preserved_recommendations = copy.deepcopy(previous_recommendations)
    for rank, recommendation in enumerate(preserved_recommendations, start=1):
        recommendation["rank"] = rank

    preserved_boards = previous_summary.get("sportsbook_boards", {})
    if not isinstance(preserved_boards, dict):
        preserved_boards = {}

    return {
        "generated_at": previous_payload.get("generated_at"),
        "recommendations": preserved_recommendations,
        "sportsbook_boards": copy.deepcopy(preserved_boards),
    }


def _tracker_state_snapshot_for_recommendations(recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = {}
    for recommendation in recommendations:
        snapshot[recommendation["recommendation_key"]] = {
            "recommendation_key": recommendation.get("recommendation_key"),
            "player_name": recommendation.get("player_name"),
            "player_id": recommendation.get("player_id"),
            "stat_type": recommendation.get("stat_type"),
            "stat_label": recommendation.get("stat_label"),
            "pick": recommendation.get("pick"),
            "pick_label": recommendation.get("pick_label"),
            "sportsbook": recommendation.get("sportsbook"),
            "sportsbook_label": recommendation.get("sportsbook_label"),
            "line": recommendation.get("line"),
            "odds": recommendation.get("odds"),
            "odds_display": recommendation.get("odds_display"),
            "edge_score": recommendation.get("edge_score"),
            "display_edge_score": recommendation.get("display_edge_score"),
            "guardrail_active": recommendation.get("guardrail_active"),
            "game_date": _normalize_game_date(recommendation.get("game_date")),
            "first_logged_at": recommendation.get("first_logged_at"),
            "why_summary": recommendation.get("why_summary") or _discord_signal_summary(recommendation),
        }
    return snapshot


def _filter_official_alert_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _filter_discord_recommendations(
        recommendations,
        min_signal_score=EDGE_DISCORD_MIN_SIGNAL_SCORE,
        per_book_limit=EDGE_DISCORD_PER_BOOK_LIMIT,
    )


def _filter_tracker_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _filter_discord_recommendations(
        recommendations,
        min_signal_score=EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE,
        per_book_limit=None,
    )


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
            if abs((_safe_float(recommendation.get("line"), 0.0) or 0.0) - (_safe_float(previous.get("line"), 0.0) or 0.0)) >= EDGE_DISCORD_LINE_MOVE_POINTS:
                reason_flags.append("line moved")
            if abs((_safe_float(recommendation.get("odds"), 0.0) or 0.0) - (_safe_float(previous.get("odds"), 0.0) or 0.0)) >= EDGE_DISCORD_ODDS_MOVE_AMERICAN:
                reason_flags.append("odds moved")
            current_display_edge_score = _safe_float(recommendation.get("display_edge_score"))
            previous_display_edge_score = _safe_float(previous.get("display_edge_score"))
            if (
                current_display_edge_score is not None
                and previous_display_edge_score is not None
                and abs(current_display_edge_score - previous_display_edge_score) >= EDGE_DISCORD_SCORE_DELTA
            ):
                reason_flags.append("score moved")
            current_rank = _safe_int(recommendation.get("rank"))
            previous_rank = _safe_int(previous.get("rank"))
            if (
                current_rank is not None
                and previous_rank is not None
                and abs(current_rank - previous_rank) >= EDGE_DISCORD_RANK_DELTA
            ):
                reason_flags.append("rank changed")

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
                "previous_rank": previous.get("rank") if isinstance(previous, dict) else None,
                "current_rank": recommendation.get("rank"),
                "previous_display_edge_score": previous.get("display_edge_score") if isinstance(previous, dict) else None,
                "current_display_edge_score": recommendation.get("display_edge_score"),
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

REASON_LABELS = {
    "restricted_area": "rim",
    "paint": "paint",
    "mid_range": "mid-range",
    "left_corner": "left corner",
    "right_corner": "right corner",
    "top_key": "above the break",
    "catch_and_shoot": "catch-and-shoot",
    "pull_up": "pull-up",
    "less_than_10_ft": "inside 10 feet",
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
    if str(odds_display).strip().lower().startswith(str(sportsbook_label).strip().lower()):
        return str(odds_display)
    return f"{sportsbook_label} {odds_display}"


def _discord_tracker_odds_label(recommendation: Dict[str, Any]) -> str:
    odds_display = str(recommendation.get("odds_display") or _format_odds(recommendation.get("odds")) or "").strip()
    sportsbook_label = str(recommendation.get("sportsbook_label") or BOOK_LABELS.get(
        recommendation.get("sportsbook"),
        "",
    )).strip()
    if recommendation.get("sportsbook") == "pp" or odds_display in {"", "-"}:
        return "line"
    if sportsbook_label and odds_display.lower().startswith(sportsbook_label.lower()):
        odds_display = odds_display[len(sportsbook_label):].strip()
    return odds_display or "line"


def _reason_label(raw_key: Any) -> str:
    key = str(raw_key or "").strip()
    if not key:
        return "this area"
    return REASON_LABELS.get(key, key.replace("_", " "))


def _top_weight_detail(weights: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(weights, dict):
        return None
    best_entry = None
    for raw_key, entry in weights.items():
        if not isinstance(entry, dict):
            continue
        player_pct = _safe_float(entry.get("player_pct") or entry.get("percentage"))
        opp_rank = _safe_float(entry.get("opp_rank") or entry.get("rank"))
        if player_pct is None or player_pct <= 0:
            continue
        candidate = {
            "key": str(raw_key),
            "label": _reason_label(raw_key),
            "player_pct": player_pct,
            "opp_rank": opp_rank,
        }
        if best_entry is None or player_pct > best_entry["player_pct"]:
            best_entry = candidate
    return best_entry


def _component_reason_snippet(recommendation: Dict[str, Any], component_name: str) -> Optional[str]:
    stat_type = str(recommendation.get("stat_type") or "")
    side = str(recommendation.get("pick") or "").lower()
    line = _safe_float(recommendation.get("line"))
    inputs = recommendation.get("inputs", {}) or {}

    if component_name == "projection":
        projection = inputs.get("projection", {}) or {}
        baseline_projection = _safe_float(projection.get("baseline_projection"))
        expected_minutes = _safe_float(projection.get("expected_minutes"))
        expected_rate = _safe_float(projection.get("expected_rate"))
        rate_context = projection.get("rate_context", {}) or {}
        rate_model = str(rate_context.get("rate_model") or "")
        components = rate_context.get("components", {}) or {}
        if baseline_projection is None or line is None:
            return None
        direction = "above" if baseline_projection >= line else "below"
        if stat_type == "PTS" and rate_model == "usage_drive_rate":
            expected_usage = _safe_float(components.get("expected_usage"))
            expected_drive_rate = _safe_float(components.get("expected_drive_rate"))
            if expected_minutes is not None and expected_usage is not None and expected_drive_rate is not None:
                return (
                    f"usage ({expected_usage * 100:.1f}%) and drive volume ({expected_drive_rate:.2f}/min) "
                    f"point to about {expected_minutes:.1f} minutes and a {baseline_projection:.1f} baseline {direction} {line:.1f}"
                )
        if stat_type == "AST" and rate_model == "potential_assists":
            potential_rate = _safe_float(components.get("expected_potential_rate"))
            if expected_minutes is not None and potential_rate is not None:
                return (
                    f"potential assists are supporting about {expected_minutes:.1f} minutes "
                    f"and a {baseline_projection:.1f} baseline {direction} {line:.1f}"
                )
        if stat_type == "REB" and rate_model == "rebound_chances":
            chance_rate = _safe_float(components.get("expected_rebound_chance_rate"))
            if expected_minutes is not None and chance_rate is not None:
                return (
                    f"rebound chances are supporting about {expected_minutes:.1f} minutes "
                    f"and a {baseline_projection:.1f} baseline {direction} {line:.1f}"
                )
        if stat_type == "FG3M" and rate_model == "three_point_volume":
            attempt_rate = _safe_float(components.get("expected_attempt_rate"))
            if expected_minutes is not None and attempt_rate is not None:
                return (
                    f"3-point volume ({attempt_rate:.2f} attempts/min) supports about {expected_minutes:.1f} minutes "
                    f"and a {baseline_projection:.1f} baseline {direction} {line:.1f}"
                )
        if rate_model == "component_sum" and isinstance(components, dict):
            component_names = [
                _long_stat_label(component_stat).lower()
                for component_stat, component_detail in components.items()
                if _safe_float((component_detail or {}).get("expected_rate")) is not None
            ][:2]
            if component_names and expected_minutes is not None:
                joined = " and ".join(component_names)
                return (
                    f"{joined} are carrying about {expected_minutes:.1f} minutes "
                    f"and a {baseline_projection:.1f} baseline {direction} {line:.1f}"
                )
        if expected_minutes is not None and expected_rate is not None:
            rate_label = _long_stat_label(stat_type).lower()
            return (
                f"expected role is about {expected_minutes:.1f} minutes at {expected_rate:.2f} {rate_label} per minute, "
                f"which puts the baseline {direction} {line:.1f}"
            )
        return f"baseline projection still lands {direction} the line"

    if component_name == "ml_regression":
        ml_inputs = inputs.get("ml_regression", {})
        prediction_val = ml_inputs.get("prediction_val")
        if prediction_val is not None:
            adj = ml_inputs.get("ml_lineup_adjustment", {})
            adj_type = adj.get("adjustment_type", "none")
            
            vegas_total = _safe_float(ml_inputs.get("vegas_total"))
            vegas_spread = _safe_float(ml_inputs.get("vegas_spread"))
            
            if vegas_total is not None and vegas_total >= 235.0 and stat_type == "PTS":
                return f"the massive total ({vegas_total}) implies a fast-paced game script, shifting the regression projection to {prediction_val:.1f}"
            if vegas_spread is not None and abs(vegas_spread) >= 12.0:
                return f"the point spread ({vegas_spread}) signals massive blowout risk, threatening 4th-quarter minutes and capping the regression projection to {prediction_val:.1f}"

            if adj_type != "none":
                pct_boost = (adj.get("q50_multiplier", 1.0) - 1.0) * 100.0
                if adj.get("absent_stars", []):
                    stars_out = ", ".join(adj.get("absent_stars"))
                    return f"the regression model projects {prediction_val:.1f} (lineup-adjusted +{pct_boost:.0f}%: {stars_out} out)"
                if adj.get("reason"):
                    return f"the regression model projects {prediction_val:.1f} (lineup-adjusted +{pct_boost:.0f}%: {adj.get('reason')})"
                
            return f"the regression model projects {prediction_val:.1f}"

    if component_name == "recent_form":
        recent_form = inputs.get("recent_form", {}) or {}
        averages = recent_form.get("averages", {}) or {}
        hit_rates = recent_form.get("hit_rates", {}) or {}
        recent_10_avg = _safe_float(averages.get("last_10"))
        recent_5_avg = _safe_float(averages.get("last_5"))
        recent_10_hit_rate = _safe_float(hit_rates.get("last_10"))
        trend = _safe_float(recent_form.get("trend"))
        if recent_10_avg is not None and recent_10_hit_rate is not None and line is not None and recent_10_hit_rate >= 65:
            return (
                f"last 10 is averaging {recent_10_avg:.1f} against a {line:.1f} line "
                f"with a {recent_10_hit_rate:.0f}% {_side_name(side).lower()} hit rate"
            )
        if recent_5_avg is not None and recent_10_avg is not None and trend is not None:
            if side == "over" and trend > 0.4:
                return f"recent form is rising too: last 5 is {recent_5_avg:.1f} versus {recent_10_avg:.1f} over the last 10"
            if side == "under" and trend < -0.4:
                return f"recent form is cooling too: last 5 is {recent_5_avg:.1f} versus {recent_10_avg:.1f} over the last 10"
        return None

    if component_name == "matchup":
        matchup = inputs.get("matchup", {}) or {}
        focus = (STAT_PROFILES.get(stat_type) or {}).get("focus")
        if focus == "points":
            shot_leader = _top_weight_detail((matchup.get("shot_type") or {}).get("weights"))
            play_leader = _top_weight_detail((matchup.get("play_type") or {}).get("weights"))
            zone_leader = _top_weight_detail((matchup.get("shooting_zones") or {}).get("weights"))
            leader = shot_leader or play_leader or zone_leader
            if leader and leader.get("opp_rank") is not None:
                return f"{leader['label']} volume ({leader['player_pct']:.0f}%) lines up with an opponent rank of {leader['opp_rank']:.0f} there"
        if focus == "assists":
            assist_leader = _top_weight_detail((matchup.get("assist_zones") or {}).get("weights"))
            play_leader = _top_weight_detail((matchup.get("play_type") or {}).get("weights"))
            leader = assist_leader or play_leader
            if leader and leader.get("opp_rank") is not None:
                return f"assist creation leans through {leader['label']} ({leader['player_pct']:.0f}%), where the opponent ranks {leader['opp_rank']:.0f}"
        if focus == "rebounds":
            paint_rank = _safe_float(matchup.get("paint_rank"))
            if paint_rank is not None:
                return f"the interior matchup is notable here too, with the opponent ranking {paint_rank:.0f} in the paint"
        if focus == "threes":
            zone_leader = _top_weight_detail((matchup.get("three_zones") or {}).get("weights"))
            shot_leader = _top_weight_detail((matchup.get("shot_type") or {}).get("weights"))
            leader = zone_leader or shot_leader
            if leader and leader.get("opp_rank") is not None:
                return f"{leader['label']} 3-point volume ({leader['player_pct']:.0f}%) lines up with an opponent rank of {leader['opp_rank']:.0f}"
        if focus == "combo":
            assist_leader = _top_weight_detail((matchup.get("assist_zones") or {}).get("weights"))
            shot_leader = _top_weight_detail((matchup.get("shot_type") or {}).get("weights"))
            play_leader = _top_weight_detail((matchup.get("play_type") or {}).get("weights"))
            leader = assist_leader or shot_leader or play_leader
            if leader and leader.get("opp_rank") is not None:
                return f"the matchup fits this combo through {leader['label']} usage ({leader['player_pct']:.0f}%) and an opponent rank of {leader['opp_rank']:.0f}"
        return None

    if component_name == "market":
        market = inputs.get("market", {}) or {}
        line_delta = _safe_float(market.get("line_delta_vs_consensus"))
        price_delta = _safe_float(market.get("price_delta"))
        chosen_book = recommendation.get("sportsbook_label") or BOOK_LABELS.get(recommendation.get("sportsbook"))
        if line_delta is not None and line_delta >= 0.25:
            return f"{chosen_book} is hanging a line that is {line_delta:.1f} better than consensus"
        if price_delta is not None and price_delta >= 0.015:
            return f"{chosen_book} is also offering a friendlier price than the market average"
        return None

    if component_name == "line_movement":
        movement = inputs.get("line_movement", {}) or {}
        line_change = _safe_float(movement.get("favorable_line_change"))
        price_change = _safe_float(movement.get("favorable_price_change"))
        if line_change is not None and line_change >= 0.5:
            return f"the market has already moved {line_change:.1f} points toward the {_side_name(side).lower()}"
        if price_change is not None and price_change >= 0.02:
            return f"the price has already moved toward this {_side_name(side).lower()} side"
        return None

    if component_name == "similar_players":
        similar = inputs.get("similar_players", {}) or {}
        comp_sample = int(_safe_float(similar.get("sample_size"), 0.0) or 0)
        average_gap = _safe_float(similar.get("average_gap_vs_line"))
        directional_gap = None
        if average_gap is not None:
            directional_gap = average_gap if side == "over" else -average_gap
        if comp_sample >= 3 and directional_gap is not None and directional_gap >= 0.2:
            if side == "over":
                return f"{comp_sample} similar-player comps cleared their lines by {directional_gap:.1f} on average"
            return f"{comp_sample} similar-player comps stayed {directional_gap:.1f} below their lines on average"
        return None

    if component_name == "head_to_head":
        h2h = inputs.get("head_to_head", {}) or {}
        sample_size = int(_safe_float(h2h.get("sample_size"), 0.0) or 0)
        average = _safe_float(h2h.get("average"))
        if sample_size >= 2 and average is not None and line is not None:
            direction = "above" if average >= line else "below"
            return f"in {sample_size} recent meetings, this matchup has averaged {average:.1f}, which is {direction} the line"
        return None

    if component_name == "back_to_back":
        b2b = inputs.get("back_to_back", {}) or {}
        if stat_type == "STL" or stat_type == "BLK" or stat_type == "STL+BLK":
            return None
        if b2b.get("current_is_b2b"):
            delta = _safe_float(b2b.get("delta_vs_overall"))
            if delta is not None:
                direction = "up" if delta >= 0 else "down"
                return f"back-to-back history matters here too, with this stat trending {direction} by {abs(delta):.1f}"
            return "back-to-back context is part of the read here"
        return None

    if component_name == "ml_regression":
        ml = inputs.get("ml_regression", {}) or {}
        p_over_val = _safe_float(ml.get("p_over"))
        prediction_val = _safe_float(ml.get("prediction_val"))
        hit_prob = _safe_float(ml.get("hit_probability"))
        if p_over_val is None or prediction_val is None or hit_prob is None:
            return None
        if side == "over" and p_over_val >= 0.60:
            return (
                f"the regression model projects {prediction_val:.1f} with a "
                f"{hit_prob:.0f}% over probability"
            )
        if side == "under" and p_over_val <= 0.40:
            under_prob = round((1.0 - p_over_val) * 100.0, 0)
            return (
                f"the regression model projects {prediction_val:.1f} with a "
                f"{under_prob:.0f}% under probability"
            )
        return None

    return None


def _discord_signal_summary(recommendation: Dict[str, Any]) -> str:
    component_scores = recommendation.get("component_scores", {}) or {}
    ranked_components = [
        component_name
        for component_name, score in sorted(
            component_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        if _safe_float(score, 0.0) is not None and (_safe_float(score, 0.0) or 0.0) > 0.04
    ]

    deduped_snippets: List[str] = []
    for component_name in ranked_components:
        snippet = _component_reason_snippet(recommendation, component_name)
        if snippet and snippet not in deduped_snippets:
            deduped_snippets.append(snippet)
        if len(deduped_snippets) >= 3:
            break

    if not deduped_snippets:
        fallback_projection = _component_reason_snippet(recommendation, "projection")
        fallback_market = _component_reason_snippet(recommendation, "market")
        fallback_recent = _component_reason_snippet(recommendation, "recent_form")
        for snippet in (fallback_projection, fallback_market, fallback_recent):
            if snippet and snippet not in deduped_snippets:
                deduped_snippets.append(snippet)
            if len(deduped_snippets) >= 3:
                break

    if not deduped_snippets:
        return "projection, form, and matchup data are generally aligned."
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
        "edge_score": _safe_float(recommendation.get("display_edge_score"), _safe_float(recommendation.get("edge_score"))),
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
            if not recommendation_key:
                continue
            existing_recommendation = tracked.get(recommendation_key)
            if not isinstance(existing_recommendation, dict):
                tracked[recommendation_key] = {
                    **recommendation,
                    "first_alerted_at": generated_at,
                    "first_alert_kind": alert_kind,
                }
                continue

            if alert_kind == "tracker" and not existing_recommendation.get("first_logged_at"):
                tracked[recommendation_key] = {
                    **existing_recommendation,
                    **recommendation,
                    "first_logged_at": recommendation.get("first_logged_at") or generated_at,
                    "first_alert_kind": "tracker",
                }

        ordered_recommendations = sorted(
            tracked.values(),
            key=lambda recommendation: (
                int(recommendation.get("rank") or 999),
                str(recommendation.get("first_logged_at") or recommendation.get("first_alerted_at") or ""),
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
    tracker_state_by_date = state.get("discord_tracker_state_by_date", {})
    if not isinstance(tracker_state_by_date, dict):
        tracker_state_by_date = {}
    existing_entry = tracker_state_by_date.get(slate_date, {}) if slate_date else {}
    if not isinstance(existing_entry, dict):
        existing_entry = {}
    sent_snapshot = existing_entry.get("sent_snapshot", {})
    if not isinstance(sent_snapshot, dict):
        sent_snapshot = {}
    existing_by_player: Dict[str, List[Dict[str, Any]]] = {}
    for tracked in sent_snapshot.values():
        if not isinstance(tracked, dict):
            continue
        tracked_player_key = str(tracked.get("player_id") or "").strip()
        if tracked_player_key:
            existing_by_player.setdefault(tracked_player_key, []).append(tracked)

    new_recommendations = []
    for recommendation in tracker_candidates:
        recommendation_key = str(recommendation.get("recommendation_key") or "").strip()
        if not recommendation_key or recommendation_key in sent_snapshot:
            continue
        player_key = str(recommendation.get("player_id") or "").strip()
        existing_for_player = existing_by_player.get(player_key, [])
        if player_key and len(existing_for_player) >= EDGE_TRACKER_PER_PLAYER_LIMIT:
            continue
        if player_key and any(
            _discord_recommendations_conflict(recommendation, existing)
            for existing in existing_for_player
        ):
            continue
        tracker_recommendation = dict(recommendation)
        tracker_recommendation["first_logged_at"] = generated_at
        new_recommendations.append(tracker_recommendation)
        if player_key:
            existing_by_player.setdefault(player_key, []).append(tracker_recommendation)

    return {
        "tracker_candidates": tracker_candidates,
        "new_recommendations": new_recommendations,
        "should_send": bool(new_recommendations),
        "slate_date": slate_date,
        "current_snapshot": _tracker_state_snapshot_for_recommendations(new_recommendations),
    }


def _tracker_running_recommendations(
    state: Dict[str, Any],
    *,
    slate_date: str,
    tracker_delta: Dict[str, Any],
) -> List[Dict[str, Any]]:
    tracker_state_by_date = state.get("discord_tracker_state_by_date", {})
    if not isinstance(tracker_state_by_date, dict):
        tracker_state_by_date = {}
    existing_entry = tracker_state_by_date.get(slate_date, {}) if slate_date else {}
    if not isinstance(existing_entry, dict):
        existing_entry = {}
    sent_snapshot = existing_entry.get("sent_snapshot", {})
    if not isinstance(sent_snapshot, dict):
        sent_snapshot = {}

    pending_recaps = state.get("pending_result_recaps", {})
    if not isinstance(pending_recaps, dict):
        pending_recaps = {}
    pending_entry = pending_recaps.get(slate_date, {}) if slate_date else {}
    if not isinstance(pending_entry, dict):
        pending_entry = {}
    pending_tracked = pending_entry.get("tracked_recommendations", {})
    if not isinstance(pending_tracked, dict):
        pending_tracked = {}
    pending_snapshot = _tracker_state_snapshot_for_recommendations([
        recommendation
        for recommendation in pending_tracked.values()
        if isinstance(recommendation, dict)
    ])
    candidate_snapshot = _tracker_state_snapshot_for_recommendations(
        tracker_delta.get("tracker_candidates", [])
    )

    merged_snapshot = {}
    for recommendation_key, existing_recommendation in sent_snapshot.items():
        key = str(recommendation_key or "").strip()
        if not key or not isinstance(existing_recommendation, dict):
            continue
        merged_snapshot[key] = {
            **pending_snapshot.get(key, {}),
            **candidate_snapshot.get(key, {}),
            **existing_recommendation,
            "recommendation_key": key,
        }
    merged_snapshot.update(_tracker_state_snapshot_for_recommendations(tracker_delta.get("new_recommendations", [])))
    recommendations = list(merged_snapshot.values())
    first_sent_at = existing_entry.get("first_sent_at") or existing_entry.get("last_sent_at")
    for recommendation in recommendations:
        if isinstance(recommendation, dict) and not recommendation.get("first_logged_at") and first_sent_at:
            recommendation["first_logged_at"] = first_sent_at
    recommendations.sort(
        key=lambda recommendation: (
            str(recommendation.get("first_logged_at") or ""),
            -_ranking_edge_score(recommendation),
            str(recommendation.get("player_name") or ""),
        ),
    )
    return recommendations


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


def _iso8601_duration_minutes(raw_value: Any) -> float:
    if raw_value is None:
        return 0.0
    if isinstance(raw_value, (int, float)):
        return max(0.0, float(raw_value))

    raw_text = str(raw_value).strip()
    if not raw_text:
        return 0.0

    parsed_direct = _safe_float(raw_text)
    if parsed_direct is not None:
        return max(0.0, parsed_direct)

    match = ISO8601_DURATION_RE.match(raw_text)
    if not match:
        return 0.0

    hours = float(match.group("hours") or 0.0)
    minutes = float(match.group("minutes") or 0.0)
    seconds = float(match.group("seconds") or 0.0)
    return max(0.0, hours * 60.0 + minutes + (seconds / 60.0))


def _boxscore_player_to_game_log(player_entry: Dict[str, Any]) -> Dict[str, Any]:
    statistics = player_entry.get("statistics", {})
    if not isinstance(statistics, dict):
        statistics = {}

    minutes = _iso8601_duration_minutes(
        statistics.get("minutes") or statistics.get("minutesCalculated")
    )
    game_log = {
        "MIN": minutes,
        "PTS": _safe_float(statistics.get("points"), 0.0) or 0.0,
        "REB": _safe_float(statistics.get("reboundsTotal"), 0.0) or 0.0,
        "AST": _safe_float(statistics.get("assists"), 0.0) or 0.0,
        "FG3M": _safe_float(statistics.get("threePointersMade"), 0.0) or 0.0,
        "BLK": _safe_float(statistics.get("blocks"), 0.0) or 0.0,
        "STL": _safe_float(statistics.get("steals"), 0.0) or 0.0,
        "TOV": _safe_float(statistics.get("turnovers"), 0.0) or 0.0,
    }
    not_playing_reason = (
        player_entry.get("notPlayingDescription")
        or player_entry.get("notPlayingReason")
        or (
            player_entry.get("status")
            if str(player_entry.get("played") or "").strip() != "1"
            else None
        )
    )
    if not_playing_reason:
        game_log["_not_playing_reason"] = str(not_playing_reason)
    return game_log


def _boxscore_cache_for_game(
    game_id: str,
    boxscore_cache: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    cached = boxscore_cache.get(game_id)
    if isinstance(cached, dict):
        return cached

    cache_entry: Dict[str, Dict[str, Dict[str, Any]]] = {
        "by_player_id": {},
        "by_name": {},
    }
    boxscore_cache[game_id] = cache_entry

    if not game_id:
        return cache_entry

    url = f"{BOXSCORE_CDN_BASE_URL}/boxscore_{game_id}.json"
    try:
        response = requests.get(url, headers=BOXSCORE_CDN_HEADERS, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Boxscore fallback fetch failed for %s: %s", game_id, exc)
        return cache_entry

    game_payload = payload.get("game", {})
    if not isinstance(game_payload, dict):
        return cache_entry

    game_status = int(_safe_float(game_payload.get("gameStatus"), 0.0) or 0.0)
    if game_status < 3:
        return cache_entry

    for team_key in ("homeTeam", "awayTeam", "gameTeam"):
        team_payload = game_payload.get(team_key, {})
        if not isinstance(team_payload, dict):
            continue
        for player_entry in team_payload.get("players", []):
            if not isinstance(player_entry, dict):
                continue
            player_id = str(player_entry.get("personId") or "").strip()
            player_name = (
                player_entry.get("name")
                or " ".join(
                    part
                    for part in (
                        str(player_entry.get("firstName") or "").strip(),
                        str(player_entry.get("familyName") or "").strip(),
                    )
                    if part
                )
            )
            game_log = _boxscore_player_to_game_log(player_entry)
            if player_id:
                cache_entry["by_player_id"][player_id] = game_log
            normalized_name = _normalize_person_name(player_name)
            if normalized_name and normalized_name not in cache_entry["by_name"]:
                cache_entry["by_name"][normalized_name] = game_log

    return cache_entry


def _find_boxscore_player_game_log(
    recommendation: Dict[str, Any],
    boxscore_cache: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
) -> Optional[Dict[str, Any]]:
    game_id = str(recommendation.get("game_id") or "").strip()
    if not game_id:
        return None

    cache_entry = _boxscore_cache_for_game(game_id, boxscore_cache)
    by_player_id = cache_entry.get("by_player_id", {})
    if isinstance(by_player_id, dict):
        player_id = str(recommendation.get("player_id") or "").strip()
        if player_id:
            player_game = by_player_id.get(player_id)
            if isinstance(player_game, dict):
                return dict(player_game)

    by_name = cache_entry.get("by_name", {})
    if isinstance(by_name, dict):
        player_name = _normalize_person_name(recommendation.get("player_name"))
        if player_name:
            player_game = by_name.get(player_name)
            if isinstance(player_game, dict):
                return dict(player_game)

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
    boxscore_cache: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}

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

        if not game_date or line is None or side not in SIDE_MULTIPLIERS:
            unresolved.append(recommendation.get("recommendation_key"))
            continue

        player = player_lookup.get(player_id)
        game_log = _find_game_log_for_date(player, game_date) if player is not None else None
        if game_log is None:
            game_log = _find_boxscore_player_game_log(recommendation, boxscore_cache)
        if game_log is not None:
            minutes = _safe_float(game_log.get("MIN"), 0.0) or 0.0
            if minutes <= 0:
                status = "void"
                final_value = None
                result_note = str(game_log.get("_not_playing_reason") or "No logged minutes")
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


def _send_discord_results_recap(recap_payload: Dict[str, Any], graded_results: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    webhook_url = _results_recap_webhook_url()
    if not webhook_url:
        return None

    summary = graded_results.get("summary", {})
    lines = []
    for display_rank, recommendation in enumerate(graded_results.get("recommendations", [])[:EDGE_LIMIT], start=1):
        stat_label = _long_stat_label(recommendation.get("stat_type"), recommendation.get("stat_label"))
        final_value = recommendation.get("final_value")
        final_text = f"{final_value:.1f}" if isinstance(final_value, (float, int)) else (recommendation.get("result_note") or "Void")
        tracked_at = _format_tracker_time(recommendation.get("first_logged_at") or recommendation.get("first_alerted_at"))
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


def _book_labels_summary(book_keys: List[str]) -> str:
    labels = [
        BOOK_LABELS.get(book, str(book).title())
        for book in sorted({str(book or "").strip().lower() for book in book_keys if str(book or "").strip()}, key=_book_sort_key)
    ]
    return ", ".join(labels) or "None"


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
        player_name = recommendation.get("player_name") or "Unknown Player"
        stat_label = _long_stat_label(recommendation.get("stat_type"), recommendation.get("stat_label"))
        pick_label = recommendation.get("pick_label") or _side_name(str(recommendation.get("pick") or "").lower())
        line_value = _safe_float(recommendation.get("line"))
        line_text = f"{line_value:.1f}" if line_value is not None else "-"
        edge_score = _safe_float(recommendation.get("display_edge_score"), _safe_float(recommendation.get("edge_score")))
        edge_text = f"{edge_score:.1f}" if edge_score is not None else "n/a"

        if channel_variant == "tracker":
            first_logged_at = _format_tracker_time(recommendation.get("first_logged_at"))
            stat_token = str(recommendation.get("stat_type") or stat_label or "PROP").strip().upper()
            side_token = "O" if str(recommendation.get("pick") or "").lower() == "over" else "U"
            odds_text = _discord_tracker_odds_label(recommendation)
            lines.append(
                f"**{display_rank}. [{first_logged_at}] {player_name}** — "
                f"{stat_token} {side_token}{line_text} | {odds_text} | SS {edge_text}"
            )
            continue

        reason_prefix = ""
        change = change_lookup.get(str(recommendation.get("recommendation_key") or "").strip())
        if alert_kind == "update" and change:
            pretty_reasons = [
                NOTIFICATION_REASON_LABELS.get(reason, reason)
                for reason in (change.get("reasons", []) or ["updated"])
            ]
            reason_prefix = f"Change: {', '.join(pretty_reasons)}\n"
        why_summary = str(recommendation.get("why_summary") or "").strip()
        if not why_summary:
            why_summary = _discord_signal_summary(recommendation)
        lines.append(
            f"**{display_rank}.** {player_name} — "
            f"{stat_label} {pick_label} {line_text}\n"
            f"{_discord_book_label(recommendation)} | Signal Score {edge_text}\n"
            f"{reason_prefix}Why: {why_summary}"
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
    official_recommendations = notification_delta.get("official_recommendations", [])
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
        f"Top {EDGE_DISCORD_MAX_RANK} props only, max {EDGE_DISCORD_PER_BOOK_LIMIT} per sportsbook, "
        f"and max {EDGE_DISCORD_PER_PLAYER_LIMIT} per player at Signal Score {threshold_text}+."
    )
    books_summary = _changed_books_summary(grouped_books)
    visible_books = {
        str(group.get("sportsbook") or "").strip().lower()
        for group in grouped_books
        if str(group.get("sportsbook") or "").strip()
    }
    official_books = {
        str(recommendation.get("sportsbook") or "").strip().lower()
        for recommendation in official_recommendations
        if str(recommendation.get("sportsbook") or "").strip()
    }
    omitted_books = sorted(official_books - visible_books, key=_book_sort_key)
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
                "value": "Only props with a new entrant, line move, price move, or meaningful score/rank change are shown."[:1024],
                "inline": False,
            },
            {
                "name": "Books changed",
                "value": books_summary[:1024],
                "inline": False,
            },
        ])
        if omitted_books:
            summary_fields.append({
                "name": "Unchanged books omitted",
                "value": _book_labels_summary(omitted_books)[:1024],
                "inline": False,
            })
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
    *,
    running_recommendations: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    recommendations = running_recommendations or tracker_delta.get("new_recommendations", [])
    if not recommendations:
        return None

    grouped_books = _group_recommendations_by_sportsbook(
        recommendations,
        limit_per_book=max(len(recommendations), EDGE_DISCORD_PER_BOOK_LIMIT, 1),
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
            "name": "Updated",
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
            "name": "Tracked picks",
            "value": str(len(recommendations)),
            "inline": True,
        },
        {
            "name": "Books in this log",
            "value": books_summary[:1024],
            "inline": False,
        },
    ]

    embeds = [
        {
            "title": f"Daily Prop Tracker • {refresh_label_text}",
            "description": (
                "Running ledger of the day’s tracked props. This message updates in place as new plays are logged, "
                "and each play keeps the time it first entered the tracker."
            ),
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


def _recommendation_game_ids(recommendations: Any) -> List[str]:
    game_ids: List[str] = []
    seen_ids = set()
    if not isinstance(recommendations, list):
        return game_ids
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            continue
        game_id = str(recommendation.get("game_id") or "").strip()
        if not game_id or game_id in seen_ids:
            continue
        seen_ids.add(game_id)
        game_ids.append(game_id)
    return game_ids


def _normalize_ref_game_ids(raw_game_ids: Any) -> List[str]:
    normalized_ids = []
    seen_ids = set()
    if not isinstance(raw_game_ids, list):
        return normalized_ids
    for raw_game_id in raw_game_ids:
        game_id = str(raw_game_id or "").strip()
        if not game_id or game_id in seen_ids:
            continue
        seen_ids.add(game_id)
        normalized_ids.append(game_id)
    return normalized_ids


def _store_discord_message_ref(
    state: Dict[str, Any],
    *,
    slate_date: str,
    channel: str,
    message: Dict[str, Any],
    webhook_url: str,
    alert_kind: str,
    sent_at: Any,
    game_ids: Optional[List[str]] = None,
) -> bool:
    message_id = str((message or {}).get("id") or "").strip()
    if not slate_date or not channel or not message_id:
        return False
    normalized_game_ids = _normalize_ref_game_ids(game_ids or [])
    messages_by_date = state.get("discord_messages_by_date", {})
    if not isinstance(messages_by_date, dict):
        messages_by_date = {}
    date_bucket = messages_by_date.get(slate_date, {})
    if not isinstance(date_bucket, dict):
        date_bucket = {}
    channel_bucket = date_bucket.get(channel, [])
    if not isinstance(channel_bucket, list):
        channel_bucket = []
    updated_bucket = []
    state_changed = False
    current_ref_found = False

    for ref in channel_bucket:
        if not isinstance(ref, dict):
            state_changed = True
            continue
        ref_message_id = str(ref.get("message_id") or "").strip()
        if ref_message_id != message_id:
            updated_bucket.append(ref)
            continue
        if current_ref_found:
            state_changed = True
            continue

        current_ref_found = True
        existing_game_ids = _normalize_ref_game_ids(ref.get("game_ids", []))
        merged_game_ids = _normalize_ref_game_ids([*existing_game_ids, *normalized_game_ids])
        if merged_game_ids != existing_game_ids:
            ref["game_ids"] = merged_game_ids
            state_changed = True
        if webhook_url and not str(ref.get("webhook_url") or "").strip():
            ref["webhook_url"] = webhook_url
            state_changed = True
        if alert_kind and str(ref.get("alert_kind") or "").strip() != alert_kind:
            ref["alert_kind"] = alert_kind
            state_changed = True
        if sent_at and str(ref.get("sent_at") or "").strip() != str(sent_at):
            ref["sent_at"] = sent_at
            state_changed = True
        updated_bucket.append(ref)

    if not current_ref_found:
        new_ref = {
            "message_id": message_id,
            "webhook_url": webhook_url,
            "alert_kind": alert_kind,
            "sent_at": sent_at,
        }
        if normalized_game_ids:
            new_ref["game_ids"] = normalized_game_ids
        updated_bucket.append(new_ref)
        state_changed = True

    channel_bucket = updated_bucket
    date_bucket[channel] = channel_bucket[-60:]
    messages_by_date[slate_date] = date_bucket
    state["discord_messages_by_date"] = messages_by_date
    return state_changed


def _latest_discord_message_ref(
    state: Dict[str, Any],
    *,
    slate_date: str,
    channel: str,
) -> Optional[Dict[str, Any]]:
    if not slate_date or not channel:
        return None
    messages_by_date = state.get("discord_messages_by_date", {})
    if not isinstance(messages_by_date, dict):
        return None
    date_bucket = messages_by_date.get(slate_date, {})
    if not isinstance(date_bucket, dict):
        return None
    channel_bucket = date_bucket.get(channel, [])
    if not isinstance(channel_bucket, list) or not channel_bucket:
        return None
    for entry in reversed(channel_bucket):
        if isinstance(entry, dict) and str(entry.get("message_id") or "").strip():
            return entry
    return None


def _completed_schedule_game_ids(schedule_payload: Any) -> set:
    if isinstance(schedule_payload, dict):
        games = schedule_payload.get("games", [])
    elif isinstance(schedule_payload, list):
        games = schedule_payload
    else:
        games = []

    completed_game_ids = set()
    for game in games:
        if not isinstance(game, dict):
            continue
        game_id = str(game.get("game_id") or "").strip()
        if not game_id:
            continue
        game_status = int(_safe_float(game.get("game_status"), 0.0) or 0.0)
        if bool(game.get("is_final")) or game_status >= 3:
            completed_game_ids.add(game_id)
    return completed_game_ids


def _should_cleanup_main_message_ref(
    ref: Dict[str, Any],
    *,
    slate_date: str,
    today_str: str,
    completed_game_ids: set,
) -> bool:
    if slate_date < today_str:
        return True
    ref_game_ids = _normalize_ref_game_ids(ref.get("game_ids", []))
    return bool(ref_game_ids) and all(game_id in completed_game_ids for game_id in ref_game_ids)


def _cleanup_completed_discord_messages(state: Dict[str, Any], schedule_payload: Optional[Any] = None) -> Dict[str, Any]:
    messages_by_date = state.get("discord_messages_by_date", {})
    if not isinstance(messages_by_date, dict) or not messages_by_date:
        messages_by_date = {}

    now = get_et_now()
    today_str = now.strftime("%Y-%m-%d")
    completed_game_ids = _completed_schedule_game_ids(schedule_payload)
    deleted_count = 0
    state_changed = False
    remaining_messages_by_date: Dict[str, Any] = {}

    for slate_date, channel_map in messages_by_date.items():
        normalized_date = _normalize_game_date(slate_date)
        if not normalized_date:
            continue
        if not isinstance(channel_map, dict):
            state_changed = True
            continue

        remaining_channel_map: Dict[str, Any] = {}
        for channel, refs in channel_map.items():
            if not isinstance(refs, list):
                state_changed = True
                continue
            if channel != "main":
                remaining_channel_map[channel] = [
                    ref for ref in refs if isinstance(ref, dict)
                ]
                if len(remaining_channel_map[channel]) != len(refs):
                    state_changed = True
                continue

            default_webhook_url = EDGE_SCORE_DISCORD_WEBHOOK_URL
            remaining_refs = []
            for ref in refs:
                if not isinstance(ref, dict):
                    state_changed = True
                    continue
                message_id = str(ref.get("message_id") or "").strip()
                webhook_url = str(ref.get("webhook_url") or default_webhook_url or "").strip()
                should_delete_ref = _should_cleanup_main_message_ref(
                    ref,
                    slate_date=normalized_date,
                    today_str=today_str,
                    completed_game_ids=completed_game_ids,
                )

                if not should_delete_ref:
                    remaining_refs.append(ref)
                    continue
                if not webhook_url or not message_id:
                    state_changed = True
                    continue
                try:
                    delete_status = _delete_discord_webhook_message(webhook_url, message_id)
                except Exception as exc:
                    logger.warning("Discord message cleanup failed for %s/%s: %s", normalized_date, channel, exc)
                    remaining_refs.append(ref)
                    continue
                if delete_status in {"deleted", "missing"}:
                    deleted_count += 1
                    state_changed = True
                else:
                    remaining_refs.append(ref)
            if remaining_refs:
                remaining_channel_map[channel] = remaining_refs
        if remaining_channel_map:
            remaining_messages_by_date[normalized_date] = remaining_channel_map
        elif normalized_date in messages_by_date:
            state_changed = True
    if state_changed:
        state["discord_messages_by_date"] = remaining_messages_by_date
    return {"deleted": deleted_count, "state_changed": state_changed}


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

        recap_message = _send_discord_results_recap(recap_payload, graded_results)
        if recap_message:
            sent_at = get_et_now().isoformat()
            sent_history[normalized_date] = {
                "sent_at": sent_at,
                "record": _results_record_text(graded_results.get("summary", {})),
                "graded_count": graded_results.get("summary", {}).get("graded_count", 0),
                "message_id": str(recap_message.get("id") or "").strip(),
                "webhook_url": _results_recap_webhook_url(),
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
    existing_message_id: Optional[str] = None,
    existing_message_webhook_url: Optional[str] = None,
    running_recommendations: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not webhook_url:
        return None
    if channel_variant == "tracker":
        discord_payload = _build_discord_tracker_payload(
            payload,
            notification_delta,
            running_recommendations=running_recommendations,
        )
    else:
        discord_payload = _build_discord_alert_payload(
            payload,
            notification_delta,
            channel_variant=channel_variant,
        )
    if not discord_payload:
        return None
    if existing_message_id and channel_variant == "tracker":
        edit_webhook_url = str(existing_message_webhook_url or webhook_url or "").strip()
        edited = _edit_discord_webhook_message(edit_webhook_url, existing_message_id, discord_payload)
        if edited is not None:
            return edited
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
    action_network_path: str = ACTION_NETWORK_PATH,
) -> Dict[str, Any]:
    start_time = time.time()
    previous_payload = _load_json(EDGE_SCORE_PATH, {})
    if not isinstance(previous_payload, dict):
        previous_payload = {}
    master_feed = _load_json(master_feed_path, [])
    schedule_payload = _load_json(schedule_path, {"games": []})
    action_network_payload = _load_json(action_network_path, {"games": []})
    line_movements_payload = _load_json(line_movements_path, {"snapshots": []})
    state = _load_json(EDGE_SCORE_STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}

    if not isinstance(master_feed, list):
        master_feed = []

    team_recent_games = {}
    for player in master_feed:
        if not isinstance(player, dict): continue
        t = player.get("team")
        logs = _extract_logs(player)
        if t and logs:
            ld = _normalize_game_date(logs[0].get("GAME_DATE"))
            if ld and (t not in team_recent_games or ld > team_recent_games[t]):
                team_recent_games[t] = ld

    schedule_context = _build_schedule_context(schedule_payload, action_network_payload)

    _refresh_injury_report_for_scoring(schedule_payload, refresh_label=refresh_label)

    injury_report = _load_json(os.path.join(CURRENT_DIR, "nba_injury_report.json"), {})
    injury_report_freshness = _injury_artifact_freshness(
        injury_report if isinstance(injury_report, dict) else {},
        schedule_payload=schedule_payload if isinstance(schedule_payload, dict) else {},
    )
    tonight_dnps = _load_tonight_dnps(master_feed, injury_report, schedule_context)
    if tonight_dnps:
        logger.info("Tonight DNPs loaded | teams_with_absences=%d", len(tonight_dnps))
    if injury_report_freshness.get("is_stale"):
        logger.warning(
            "Edge Score using stale injury artifact | age_minutes=%s max_age_minutes=%s generated_at=%s",
            injury_report_freshness.get("age_minutes"),
            injury_report_freshness.get("max_age_minutes"),
            injury_report_freshness.get("generated_at"),
        )

    recap_result = {"sent_dates": [], "state_changed": False}
    if _results_recap_webhook_url():
        try:
            recap_result = _process_results_recaps(master_feed, state)
        except Exception as exc:
            logger.warning("Edge Score results recap failed: %s", exc)
    try:
        cleanup_result = _cleanup_completed_discord_messages(state, schedule_payload=schedule_payload)
        recap_result["deleted_messages"] = cleanup_result.get("deleted", 0)
        recap_result["state_changed"] = bool(
            recap_result.get("state_changed") or cleanup_result.get("state_changed")
        )
    except Exception as exc:
        logger.warning("Discord message cleanup failed: %s", exc)

    schedule_context = _build_schedule_context(schedule_payload, action_network_payload)
    overlay_props_index = _build_overlay_props_index(current_players_data)
    if EDGE_SCORE_ENABLE_PRIZEPICKS:
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
            "game_context": game,
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
                    tonight_dnps=tonight_dnps,
                    team_recent_games=team_recent_games,
                )
                if candidate is not None:
                    side_candidates.append(candidate)

            if not side_candidates:
                continue

            best_candidate = max(
                side_candidates,
                key=lambda candidate: (
                    _ranking_edge_score(candidate),
                    _ranking_confidence(candidate),
                    candidate.get("signal_score", 0.0),
                ),
            )
            candidates.append(best_candidate)

    candidates.sort(key=_candidate_sort_key, reverse=True)
    eligible_candidates = [
        candidate for candidate in candidates
        if not candidate.get("eligibility_blocked")
    ]
    blocked_candidates = [
        candidate for candidate in candidates
        if candidate.get("eligibility_blocked")
    ]
    top_recommendations = _diversify_candidates(
        eligible_candidates,
        EDGE_LIMIT,
        per_player_limit=EDGE_TOP_PER_PLAYER_LIMIT,
        per_game_limit=EDGE_TOP_PER_GAME_LIMIT,
        prevent_component_overlap=True,
    )
    blocked_recommendations = _diversify_candidates(
        blocked_candidates,
        EDGE_LIMIT,
        per_player_limit=EDGE_TOP_PER_PLAYER_LIMIT,
        per_game_limit=EDGE_TOP_PER_GAME_LIMIT,
        prevent_component_overlap=True,
    )
    for rank, recommendation in enumerate(top_recommendations, start=1):
        recommendation["rank"] = rank
    for blocked_rank, recommendation in enumerate(blocked_recommendations, start=1):
        recommendation["blocked_rank"] = blocked_rank
    sportsbook_boards = _build_sportsbook_boards(eligible_candidates, EDGE_SPORTSBOOK_BOARD_LIMIT)
    blocked_stale_count = sum(
        1
        for candidate in blocked_candidates
        if candidate.get("eligibility_block_reason") == "blocked_stale_injury_context"
    )
    blocked_promotion_under_count = sum(
        1
        for candidate in blocked_candidates
        if candidate.get("eligibility_block_reason") == "blocked_promotion_under"
    )

    notification_delta = _compute_notification_delta(top_recommendations, state, refresh_label)
    official_recommendations = notification_delta.get("official_recommendations", [])
    display_recommendations = top_recommendations
    display_sportsbook_boards = sportsbook_boards
    preserved_board_generated_at = None
    board_preserved_from_previous = False
    if not top_recommendations and blocked_stale_count > 0:
        preserved_board = _load_preservable_previous_board(
            previous_payload,
            current_game_dates=sorted({
                entry["game_date"]
                for entry in active_entries
                if entry.get("game_date")
            }),
        )
        if preserved_board:
            display_recommendations = preserved_board["recommendations"]
            display_sportsbook_boards = preserved_board["sportsbook_boards"]
            preserved_board_generated_at = preserved_board.get("generated_at")
            board_preserved_from_previous = True

    payload = {
        "generated_at": get_et_now().isoformat(),
        "refresh_label": refresh_label,
        "game_dates": sorted({entry["game_date"] for entry in active_entries if entry.get("game_date")}),
        "summary": {
            "active_players": len(active_entries),
            "candidate_count": len(candidates),
            "eligible_candidate_count": len(eligible_candidates),
            "blocked_candidate_count": len(blocked_candidates),
            "top_count": len(display_recommendations),
            "actual_top_count": len(top_recommendations),
            "blocked_top_count": len(blocked_recommendations),
            "available_books": sorted(SUPPORTED_BOOKS),
            "sportsbook_board_limit": EDGE_SPORTSBOOK_BOARD_LIMIT,
            "sportsbook_boards": display_sportsbook_boards,
            "injury_report_freshness": injury_report_freshness,
            "blocked_stale_injury_context_count": blocked_stale_count,
            "blocked_promotion_under_count": blocked_promotion_under_count,
            "board_preserved_from_previous": board_preserved_from_previous,
            "preserved_board_generated_at": preserved_board_generated_at,
            "duration_s": round(time.time() - start_time, 2),
            "scoring_model": "Signal Score",
        },
        "recommendations": display_recommendations,
        "blocked_recommendations": blocked_recommendations,
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
                "per_player_limit": EDGE_DISCORD_PER_PLAYER_LIMIT,
                "top_per_player_limit": EDGE_TOP_PER_PLAYER_LIMIT,
                "top_per_game_limit": EDGE_TOP_PER_GAME_LIMIT,
                "tracker_per_player_limit": EDGE_TRACKER_PER_PLAYER_LIMIT,
                "max_rank": EDGE_DISCORD_MAX_RANK,
                "component_overlap_threshold": EDGE_DISCORD_COMPONENT_OVERLAP_THRESHOLD,
                "min_signal_score": EDGE_DISCORD_MIN_SIGNAL_SCORE,
                "new_entrant": True,
                "line_move_points": EDGE_DISCORD_LINE_MOVE_POINTS,
                "odds_move_american": EDGE_DISCORD_ODDS_MOVE_AMERICAN,
                "signal_score_delta": EDGE_DISCORD_SCORE_DELTA,
                "rank_delta": EDGE_DISCORD_RANK_DELTA,
                "min_interval_seconds": EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS,
            },
        },
    }
    # The tracker intentionally backfills from the current official daily-props board,
    # not from hidden overflow candidates outside the surfaced alert set.
    tracker_delta = _compute_tracker_delta(
        notification_delta.get("official_recommendations", []),
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
    main_message_response: Optional[Dict[str, Any]] = None
    tracker_message_response: Optional[Dict[str, Any]] = None
    state_changed = bool(recap_result.get("state_changed"))
    slate_date = notification_delta.get("slate_date") or ""
    tracker_running_recommendations = _tracker_running_recommendations(
        state,
        slate_date=slate_date,
        tracker_delta=tracker_delta,
    )
    tracker_running_recommendations = _filter_tracker_recommendations(tracker_running_recommendations)
    tracker_message_ref = _latest_discord_message_ref(
        state,
        slate_date=slate_date,
        channel="tracker",
    )
    tracker_existing_message_id = str((tracker_message_ref or {}).get("message_id") or "").strip() or None
    tracker_existing_webhook_url = str((tracker_message_ref or {}).get("webhook_url") or "").strip() or None
    tracker_message_missing = bool(
        EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL
        and slate_date
        and tracker_running_recommendations
        and not tracker_existing_message_id
    )
    if refresh_label in DISCORD_ALERT_REFRESH_LABELS and _has_discord_alert_target():
        if EDGE_SCORE_DISCORD_WEBHOOK_URL and notification_delta.get("should_send"):
            try:
                main_message_response = _send_discord_webhook(
                    payload,
                    notification_delta,
                    webhook_url=EDGE_SCORE_DISCORD_WEBHOOK_URL,
                    channel_variant="main",
                )
                discord_main_sent = bool(main_message_response)
            except Exception as exc:
                logger.warning("Discord Edge Score webhook failed: %s", exc)
        if EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL and (
            tracker_delta.get("should_send") or tracker_message_missing
        ):
            try:
                tracker_message_response = _send_discord_webhook(
                    payload,
                    tracker_delta,
                    webhook_url=EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL,
                    channel_variant="tracker",
                    existing_message_id=tracker_existing_message_id,
                    existing_message_webhook_url=tracker_existing_webhook_url,
                    running_recommendations=tracker_running_recommendations,
                )
                discord_tracker_sent = bool(tracker_message_response)
            except Exception as exc:
                logger.warning("Discord Edge Score tracker webhook failed: %s", exc)
        discord_sent = bool(discord_main_sent or discord_tracker_sent)

    if discord_main_sent:
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
            state_changed = True

        if slate_date and discord_main_sent and isinstance(main_message_response, dict):
            if _store_discord_message_ref(
                state,
                slate_date=slate_date,
                channel="main",
                message=main_message_response,
                webhook_url=EDGE_SCORE_DISCORD_WEBHOOK_URL,
                alert_kind=notification_delta.get("alert_kind", "update"),
                sent_at=payload["generated_at"],
                game_ids=_recommendation_game_ids(notification_delta.get("send_recommendations", [])),
            ):
                state_changed = True

    if discord_tracker_sent and slate_date:
        tracker_state_by_date = state.get("discord_tracker_state_by_date", {})
        if not isinstance(tracker_state_by_date, dict):
            tracker_state_by_date = {}
        tracker_date_state = tracker_state_by_date.get(slate_date, {})
        if not isinstance(tracker_date_state, dict):
            tracker_date_state = {}
        sent_snapshot = _tracker_state_snapshot_for_recommendations(tracker_running_recommendations)
        tracker_date_state["sent_snapshot"] = sent_snapshot
        tracker_date_state.setdefault("first_sent_at", payload["generated_at"])
        tracker_date_state["last_sent_at"] = payload["generated_at"]
        tracker_state_by_date[slate_date] = tracker_date_state
        state["discord_tracker_state_by_date"] = tracker_state_by_date
        state_changed = True

    if slate_date and discord_tracker_sent and isinstance(tracker_message_response, dict):
        if _store_discord_message_ref(
            state,
            slate_date=slate_date,
            channel="tracker",
            message=tracker_message_response,
            webhook_url=EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL,
            alert_kind="tracker",
            sent_at=payload["generated_at"],
            game_ids=_recommendation_game_ids(tracker_running_recommendations),
        ):
            state_changed = True

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

    if state_changed or (not os.path.exists(EDGE_SCORE_STATE_PATH)):
        _write_json_atomic(EDGE_SCORE_STATE_PATH, state if isinstance(state, dict) else {})

    sync_ok = _sync_payload_to_supabase(payload)
    if discord_sent:
        log_status(
            logger,
            "OK",
            "Edge Score refresh complete",
            refresh_label=refresh_label,
            active_players=len(active_entries),
            candidates=len(candidates),
            eligible=len(eligible_candidates),
            blocked=len(blocked_candidates),
            top=len(top_recommendations),
            discord_sent=True,
            results_recaps_sent=len(recap_result.get("sent_dates", [])),
            deleted_discord_messages=recap_result.get("deleted_messages", 0),
            supabase_sync_ok=sync_ok,
        )
    else:
        log_status(
            logger,
            "OK",
            "Edge Score refresh complete",
            refresh_label=refresh_label,
            active_players=len(active_entries),
            candidates=len(candidates),
            eligible=len(eligible_candidates),
            blocked=len(blocked_candidates),
            top=len(top_recommendations),
            discord_sent=False,
            results_recaps_sent=len(recap_result.get("sent_dates", [])),
            deleted_discord_messages=recap_result.get("deleted_messages", 0),
            supabase_sync_ok=sync_ok,
        )

    return payload
