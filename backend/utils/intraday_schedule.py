import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from prop_modeling.injury_feature_config import (
    INJURY_FRESHNESS_LOCK_SENSITIVE_MAX_AGE_MINUTES,
    INJURY_FRESHNESS_LOCK_SENSITIVE_START_HOUR_ET,
)

ET_ZONE = ZoneInfo("America/New_York")
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def get_et_now():
    return datetime.now(ET_ZONE)


def _parse_env_int(name: str, fallback: int, minimum: int) -> int:
    raw_value = os.getenv(name, str(fallback))
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, parsed)


def get_fixed_intraday_interval_seconds() -> int:
    return _parse_env_int("INTRADAY_INTERVAL_SECONDS", 900, 300)


def get_intraday_window_config() -> dict:
    fixed_seconds = get_fixed_intraday_interval_seconds()
    return {
        "active_seconds": _parse_env_int("INTRADAY_INTERVAL_ACTIVE_SECONDS", fixed_seconds, 300),
        "quiet_seconds": _parse_env_int("INTRADAY_INTERVAL_QUIET_SECONDS", fixed_seconds, 300),
        "pretip_minutes": _parse_env_int("INTRADAY_ACTIVE_PRETIP_MINUTES", 180, 0),
        "posttip_minutes": _parse_env_int("INTRADAY_ACTIVE_POSTTIP_MINUTES", 150, 0),
    }


def _parse_schedule_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ET_ZONE)
    return parsed.astimezone(ET_ZONE)


def _load_schedule_games(schedule_path: str) -> list:
    if not schedule_path or not os.path.exists(schedule_path):
        return []
    try:
        with open(schedule_path, "r") as handle:
            payload = json.load(handle)
    except Exception:
        return []
    games = payload.get("games", []) if isinstance(payload, dict) else []
    return games if isinstance(games, list) else []


def _is_recent_live_game(game: dict, now: datetime) -> bool:
    deadline_dt = _parse_schedule_datetime(game.get("closing_scrape_deadline"))
    if deadline_dt is not None:
        # Ignore stale LIVE flags from old schedule payloads.
        return deadline_dt >= (now - timedelta(hours=8))

    game_date = str(game.get("game_date") or "").strip()
    return game_date == now.strftime("%Y-%m-%d")


def is_active_intraday_window(schedule_path: str, now=None) -> bool:
    config = get_intraday_window_config()
    if config["active_seconds"] == config["quiet_seconds"]:
        return True

    now = now or get_et_now()
    games = _load_schedule_games(schedule_path)
    if not games:
        # Prefer the more responsive interval if schedule context is missing.
        return True

    for game in games:
        if not isinstance(game, dict):
            continue

        if bool(game.get("is_live")) and not bool(game.get("is_final")) and _is_recent_live_game(game, now):
            return True

        deadline_dt = _parse_schedule_datetime(game.get("closing_scrape_deadline"))
        if deadline_dt is None:
            continue

        active_start = deadline_dt - timedelta(minutes=config["pretip_minutes"])
        active_end = deadline_dt + timedelta(minutes=config["posttip_minutes"])
        if active_start <= now <= active_end:
            return True

    return False


def _has_same_day_games(games: list, now: datetime) -> bool:
    today_key = now.strftime("%Y-%m-%d")
    for game in games:
        if not isinstance(game, dict):
            continue
        game_date = str(game.get("game_date") or "").strip()
        if game_date == today_key:
            return True
        deadline_dt = _parse_schedule_datetime(game.get("closing_scrape_deadline"))
        if deadline_dt is not None and deadline_dt.date() == now.date():
            return True
    return False


def get_schedule_aware_intraday_interval_seconds(schedule_path: str, now=None) -> int:
    now = now or get_et_now()
    config = get_intraday_window_config()
    interval_seconds = (
        config["active_seconds"]
        if is_active_intraday_window(schedule_path, now=now)
        else config["quiet_seconds"]
    )
    games = _load_schedule_games(schedule_path)
    if (
        games
        and now.hour >= INJURY_FRESHNESS_LOCK_SENSITIVE_START_HOUR_ET
        and _has_same_day_games(games, now)
    ):
        freshness_cap_seconds = max(300, INJURY_FRESHNESS_LOCK_SENSITIVE_MAX_AGE_MINUTES * 60)
        return min(interval_seconds, freshness_cap_seconds)
    return interval_seconds
