import os
import urllib.parse
from datetime import date, datetime


REGULAR_SEASON = "Regular Season"
PLAYOFFS = "Playoffs"

_ALIASES = {
    "regular": REGULAR_SEASON,
    "regular season": REGULAR_SEASON,
    "reg": REGULAR_SEASON,
    "playoff": PLAYOFFS,
    "playoffs": PLAYOFFS,
    "postseason": PLAYOFFS,
    "post season": PLAYOFFS,
    "play-in": "PlayIn",
    "playin": "PlayIn",
    "play in": "PlayIn",
    "preseason": "Pre Season",
    "pre season": "Pre Season",
}


def normalize_season_type(value):
    if value is None:
        return None
    clean = str(value).strip()
    if not clean:
        return None
    return _ALIASES.get(clean.lower(), clean)


def _normalize_season_string(value):
    if value is None:
        return None
    clean = str(value).strip()
    if not clean:
        return None
    if len(clean) == 7 and clean[4] == "-":
        return clean
    return None


def resolve_season(*, today=None, env_names=None):
    """
    Resolve the active NBA season string like 2025-26.

    Env overrides win first. Without an override, the season rolls over in
    September so preseason/training-camp automation can start targeting the
    upcoming season before opening night.
    """
    names = env_names or (
        "NBA_SEASON",
        "NBA_STATS_SEASON",
        "SEASON",
    )
    for name in names:
        override = _normalize_season_string(os.environ.get(name))
        if override:
            return override

    if today is None:
        today = date.today()
    elif isinstance(today, datetime):
        today = today.date()

    start_year = today.year if today.month >= 9 else today.year - 1
    end_suffix = str((start_year + 1) % 100).zfill(2)
    return f"{start_year}-{end_suffix}"


def parse_season_start_year(season):
    clean = _normalize_season_string(season)
    if not clean:
        raise ValueError(f"Invalid season string: {season!r}")
    return int(clean[:4])


def is_completed_season(season, *, today=None):
    return parse_season_start_year(season) < parse_season_start_year(resolve_season(today=today))


def resolve_season_type(*, today=None, env_names=None):
    """
    Resolve the NBA stats SeasonType dynamically.

    Env overrides win first. Use NBA_SEASON_TYPE=regular or playoffs when the
    league calendar needs manual correction. With no override, playoff mode is
    used from mid-April through June and regular season mode otherwise.
    """
    names = env_names or (
        "NBA_SEASON_TYPE",
        "NBA_STATS_SEASON_TYPE",
        "SEASON_TYPE",
    )
    for name in names:
        override = normalize_season_type(os.environ.get(name))
        if override:
            return override

    if today is None:
        today = date.today()
    elif isinstance(today, datetime):
        today = today.date()

    if today.month in {5, 6} or (today.month == 4 and today.day >= 15):
        return PLAYOFFS
    return REGULAR_SEASON


def encoded_season_type(**kwargs):
    return urllib.parse.quote(resolve_season_type(**kwargs), safe="")
