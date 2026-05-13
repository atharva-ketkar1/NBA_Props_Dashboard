import math
import re
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo("America/New_York")

_MISSING_DATE_VALUES = {"", "nan", "nat", "none", "null", "tbd"}
_STARTED_STATUS_TEXT_RE = re.compile(
    r"\b(live|in[-_\s]?progress|final|halftime|half\s*time|q[1-4]|"
    r"1st|2nd|3rd|4th|ot|overtime|end of)\b",
    re.IGNORECASE,
)


def get_et_now() -> datetime:
    return datetime.now(ET_ZONE)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def normalize_game_date(raw_value: Any) -> str:
    if not raw_value:
        return ""
    if isinstance(raw_value, float) and math.isnan(raw_value):
        return ""
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped.lower() in _MISSING_DATE_VALUES:
            return ""
        if len(stripped) >= 10 and stripped[4] == "-" and stripped[7] == "-":
            if len(stripped) == 10:
                return stripped
            parsed = parse_et_datetime(stripped)
            if parsed is not None:
                return parsed.strftime("%Y-%m-%d")
            return stripped[:10]
    return str(raw_value)


def parse_et_datetime(raw_value: Any) -> Optional[datetime]:
    if not raw_value:
        return None
    text = str(raw_value).strip()
    if not text or text.lower() in _MISSING_DATE_VALUES:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ET_ZONE)
    return parsed.astimezone(ET_ZONE)


def get_schedule_start_time(game: Mapping[str, Any]) -> Optional[datetime]:
    for key in (
        "closing_scrape_deadline",
        "game_time_utc",
        "start_time",
        "start_time_utc",
        "commence_time",
    ):
        parsed = parse_et_datetime(game.get(key))
        if parsed is not None:
            return parsed
    return None


def has_started_status(game: Mapping[str, Any]) -> bool:
    if bool(game.get("is_live")) or bool(game.get("is_final")):
        return True

    game_status = _safe_float(game.get("game_status", game.get("gameStatus")), 0.0) or 0.0
    if int(game_status) >= 2:
        return True

    status_text = " ".join(
        str(game.get(key) or "")
        for key in ("game_status_text", "gameStatusText", "status_text", "status")
    ).strip()
    return bool(status_text and _STARTED_STATUS_TEXT_RE.search(status_text))


def has_game_started(game: Optional[Mapping[str, Any]], now_et: Optional[datetime] = None) -> bool:
    if not isinstance(game, Mapping):
        return False

    now = (now_et or get_et_now()).astimezone(ET_ZONE)
    if has_started_status(game):
        return True

    start_dt = get_schedule_start_time(game)
    if start_dt is not None:
        return now >= start_dt

    game_date = normalize_game_date(game.get("game_date") or game.get("gameDateEst"))
    if not game_date:
        return False
    try:
        game_day = datetime.strptime(game_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    # If today's tip time is unknown, treat it as unsafe rather than assuming
    # the game is still pregame. Future TBD games remain eligible once dated.
    return game_day <= now.date()


def is_pregame_schedule_game(game: Optional[Mapping[str, Any]], now_et: Optional[datetime] = None) -> bool:
    return isinstance(game, Mapping) and not has_game_started(game, now_et=now_et)


def is_prop_pregame_by_time(
    start_time: Any,
    game_date: Any = None,
    now_et: Optional[datetime] = None,
) -> bool:
    now = (now_et or get_et_now()).astimezone(ET_ZONE)
    start_dt = parse_et_datetime(start_time)
    if start_dt is not None:
        return now < start_dt

    normalized_date = normalize_game_date(game_date)
    if not normalized_date:
        return False
    try:
        game_day = datetime.strptime(normalized_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    return game_day > now.date()
