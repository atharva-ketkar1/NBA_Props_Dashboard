import os
import urllib.parse
from datetime import date, datetime


REGULAR_SEASON = "Regular Season"
PLAYOFFS = "Playoffs"
PRE_SEASON = "Pre Season"
ALL_STAR = "All Star"
PLAY_IN = "PlayIn"

# An NBA GAME_ID is ten characters: "00", then one digit for the season type,
# then a two-digit season year, then a five-digit sequence. 0022500001 is the
# first regular-season game of 2025-26; 0042500101 is a playoff game.
#
# The leading zeros matter and are easy to lose: the game logs in
# data/archive/ have been round-tripped through CSV as integers, so the same
# file holds both "0022400455" and "22400001". Every lookup here pads back to
# ten characters first, which is why `season_type_from_game_id` must be used
# in preference to slicing an id by hand.
_GAME_ID_SEASON_TYPES = {
    "1": PRE_SEASON,
    "2": REGULAR_SEASON,
    "3": ALL_STAR,
    "4": PLAYOFFS,
    "5": PLAY_IN,
}

GAME_ID_LENGTH = 10


def season_type_from_game_id(game_id):
    """The season type encoded in an NBA GAME_ID, or None if it cannot be read.

    None is returned rather than guessing, and callers must decide what an
    unreadable id means for them. Defaulting it to REGULAR_SEASON would let
    playoff rows through a regular-season filter, and defaulting it to
    PLAYOFFS would silently delete data; neither is safe to do quietly.
    """
    if game_id is None:
        return None
    clean = str(game_id).strip()
    if not clean or not clean.isdigit():
        return None
    if len(clean) > GAME_ID_LENGTH:
        return None
    return _GAME_ID_SEASON_TYPES.get(clean.zfill(GAME_ID_LENGTH)[2])


def is_regular_season_game_id(game_id):
    """True only when the id positively reads as a regular-season game.

    An unreadable id is False here, so a caller filtering on this keeps only
    what it can vouch for. Pair it with `season_type_from_game_id` when the
    unreadable ones need counting rather than dropping.
    """
    return season_type_from_game_id(game_id) == REGULAR_SEASON


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
