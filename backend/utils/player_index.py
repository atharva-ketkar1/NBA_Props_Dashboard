"""
player_index.py
League-wide player roster for PlayerMatcher, independent of games played.

Why this exists
---------------
PlayerMatcher was built only from season_stats.csv, which comes from
leaguedashplayerstats and therefore only contains players with GP > 0. That is
fine mid-season and catastrophic at the start of one. Measured on 2026-10-20,
two days into the 2026-27 season:

    season_stats.csv      41 players   (only those who had already played)
    draftkings.csv       147 distinct players (the next slate, other teams)
    overlap                0

Zero overlap means PlayerMatcher cannot resolve a single prop, upsert_props logs
"No rows resolved", props_ok=False, and the whole pipeline is marked failed --
every run, until enough games accumulate to cover the slate.

commonallplayers with IsOnlyCurrentSeason=1 returns every rostered player
regardless of GP (519 on the same date), which lifted overlap with DraftKings
from 0 to 124 of 147; the remainder are name-format differences the existing
RapidFuzz pass in PlayerMatcher already handles.

The roster changes rarely, so it is cached on disk and refreshed once a day.
Failures are non-fatal: callers fall back to whatever season stats provide,
which is the old behaviour rather than a regression.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("PlayerIndex")

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = BACKEND_DIR / "data" / "current" / "player_index.json"
CACHE_TTL_SECONDS = int(os.environ.get("PLAYER_INDEX_TTL_SECONDS", str(24 * 3600)))

_ENDPOINT = "https://stats.nba.com/stats/commonallplayers"


def _fetch_roster(season):
    """Pull the full current-season roster through the Cloudflare Worker.

    stats.nba.com does not answer datacenter IPs directly, so this must use
    proxied_get_json. Imported lazily to keep this module importable in contexts
    that never hit the network.
    """
    from utils.http_client import proxied_get_json

    payload = proxied_get_json(
        _ENDPOINT,
        params={"LeagueID": "00", "Season": season, "IsOnlyCurrentSeason": "1"},
        label="player-index",
    )
    result_sets = payload.get("resultSets") or []
    if not result_sets:
        raise ValueError("commonallplayers returned no resultSets")

    headers = result_sets[0]["headers"]
    rows = result_sets[0]["rowSet"]
    col = {name: i for i, name in enumerate(headers)}

    required = ("PERSON_ID", "DISPLAY_FIRST_LAST", "TEAM_ABBREVIATION")
    missing = [c for c in required if c not in col]
    if missing:
        raise ValueError(f"commonallplayers missing columns: {missing}")

    records = []
    for row in rows:
        pid = row[col["PERSON_ID"]]
        name = str(row[col["DISPLAY_FIRST_LAST"]] or "").strip()
        team = str(row[col["TEAM_ABBREVIATION"]] or "").strip().upper()
        if not pid or not name:
            continue
        # Translate into the shape PlayerMatcher documents it wants:
        # {'PLAYER_ID': int, 'PLAYER_NAME': str, 'TEAM_ABBREVIATION': str}
        records.append({
            "PLAYER_ID": int(pid),
            "PLAYER_NAME": name,
            "TEAM_ABBREVIATION": team or "UNK",
        })
    return records


def _read_cache(cache_path):
    try:
        with open(cache_path) as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return None, 0, None
    records = blob.get("players") or []
    fetched_at = float(blob.get("fetched_at") or 0)
    cached_season = blob.get("season") or None
    return (records or None), fetched_at, cached_season


def _write_cache(cache_path, season, records):
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            json.dump({"season": season, "fetched_at": time.time(),
                       "players": records}, fh)
        tmp.replace(cache_path)
    except OSError as exc:
        logger.warning("Could not cache player index: %s", exc)


def get_player_index_result(season=None, cache_path=None, force_refresh=False):
    """Roster records plus where they came from.

    Returns a dict:
        players       list of records (possibly empty)
        source        "fetch" (live upstream), "cache" (disk fallback) or
                      "empty" (nothing usable)
        season        the season that was asked for
        cache_season  the season the cache was written for, when served from cache
        fetched_at    cache write time, when served from cache

    `get_player_index` deliberately degrades to a stale cache or an empty list
    rather than raising, because the matcher only needs best-effort coverage.
    Callers that *write* roster state need to tell those cases apart -- stamping
    "who is on a roster" from an empty or previous-season list would age out the
    entire league in one run -- so the provenance is reported here rather than
    inferred from the row count.
    """
    if season is None:
        from scrapers.season_type import resolve_season
        season = resolve_season()
    cache_path = Path(cache_path or DEFAULT_CACHE_PATH)

    cached, fetched_at, cached_season = _read_cache(cache_path)
    # A cache written for a different season is not "fresh" no matter how
    # recently it was written -- every real caller so far has only ever asked
    # for the live season, so this never mattered until a caller (the
    # archive backfill script) started asking for a past one explicitly.
    fresh = (
        cached
        and cached_season == season
        and (time.time() - fetched_at) < CACHE_TTL_SECONDS
    )
    if fresh and not force_refresh:
        return {
            "players": cached,
            "source": "cache",
            "season": season,
            "cache_season": cached_season,
            "fetched_at": fetched_at,
        }

    try:
        records = _fetch_roster(season)
        if records:
            _write_cache(cache_path, season, records)
            logger.info("Player index refreshed: %d players for %s",
                        len(records), season)
            return {
                "players": records,
                "source": "fetch",
                "season": season,
                "cache_season": season,
                "fetched_at": time.time(),
            }
        raise ValueError("commonallplayers returned 0 usable rows")
    except Exception as exc:
        if cached:
            logger.warning("Player index refresh failed (%s); using cache of %d players",
                           exc, len(cached))
            return {
                "players": cached,
                "source": "cache",
                "season": season,
                "cache_season": cached_season,
                "fetched_at": fetched_at,
            }
        logger.warning("Player index unavailable (%s); matcher will fall back to "
                       "season stats only, which resolves poorly early in a season", exc)
        return {
            "players": [],
            "source": "empty",
            "season": season,
            "cache_season": None,
            "fetched_at": 0,
        }


def get_player_index(season=None, cache_path=None, force_refresh=False):
    """Return roster records for PlayerMatcher, or [] if unavailable.

    Never raises: an empty list leaves the caller with its previous behaviour.
    """
    return get_player_index_result(
        season=season,
        cache_path=cache_path,
        force_refresh=force_refresh,
    )["players"]


ALL_TIME_CACHE_PATH = BACKEND_DIR / "data" / "current" / "player_index_all_time.json"
# All-time data changes only when someone debuts/returns, so there is no
# reason to re-fetch it every day the way the live roster is.
ALL_TIME_CACHE_TTL_SECONDS = int(os.environ.get("PLAYER_INDEX_ALL_TIME_TTL_SECONDS", str(7 * 24 * 3600)))
_ALL_TIME_SEASON_KEY = "__all_time__"


def get_full_player_history(cache_path=None, force_refresh=False):
    """Every player the league has ever rostered, for matching DNP names from
    a PAST season.

    `get_player_index(season=...)` is unusable for this: it calls
    commonallplayers with IsOnlyCurrentSeason=1, and that flag filters to
    players on a roster RIGHT NOW, regardless of what `Season` value is
    passed -- so asking it for season='2024-25' quietly returns today's
    roster filtered to names with 2024-25 history (138 players, missing
    anyone who has since left the league, e.g. Maxi Kleber). Passing
    IsOnlyCurrentSeason=0 instead drops that filter and returns the league's
    full all-time list (~5200 players, active or not), which is what a
    historical-season name lookup actually needs. This is intentionally a
    separate function and cache file rather than a parameter on
    get_player_index, so the live pipeline's current-season path is
    untouched.
    """
    from utils.http_client import proxied_get_json

    cache_path = Path(cache_path or ALL_TIME_CACHE_PATH)
    cached, fetched_at, cached_season = _read_cache(cache_path)
    fresh = (
        cached
        and cached_season == _ALL_TIME_SEASON_KEY
        and (time.time() - fetched_at) < ALL_TIME_CACHE_TTL_SECONDS
    )
    if fresh and not force_refresh:
        return cached

    try:
        payload = proxied_get_json(
            _ENDPOINT,
            params={"LeagueID": "00", "Season": "2024-25", "IsOnlyCurrentSeason": "0"},
            label="player-index-all-time",
        )
        result_sets = payload.get("resultSets") or []
        if not result_sets:
            raise ValueError("commonallplayers returned no resultSets")
        headers = result_sets[0]["headers"]
        rows = result_sets[0]["rowSet"]
        col = {name: i for i, name in enumerate(headers)}
        required = ("PERSON_ID", "DISPLAY_FIRST_LAST", "TEAM_ABBREVIATION")
        missing = [c for c in required if c not in col]
        if missing:
            raise ValueError(f"commonallplayers missing columns: {missing}")

        records = []
        for row in rows:
            pid = row[col["PERSON_ID"]]
            name = str(row[col["DISPLAY_FIRST_LAST"]] or "").strip()
            team = str(row[col["TEAM_ABBREVIATION"]] or "").strip().upper()
            if not pid or not name:
                continue
            records.append({
                "PLAYER_ID": int(pid),
                "PLAYER_NAME": name,
                "TEAM_ABBREVIATION": team or "UNK",
            })

        if records:
            _write_cache(cache_path, _ALL_TIME_SEASON_KEY, records)
            logger.info("All-time player index refreshed: %d players", len(records))
            return records
        raise ValueError("commonallplayers returned 0 usable rows")
    except Exception as exc:
        if cached:
            logger.warning("All-time player index refresh failed (%s); using cache of %d players",
                           exc, len(cached))
            return cached
        logger.warning("All-time player index unavailable (%s); historical DNP names will not resolve", exc)
        return []
