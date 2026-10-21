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
        return None, 0
    records = blob.get("players") or []
    fetched_at = float(blob.get("fetched_at") or 0)
    return (records or None), fetched_at


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


def get_player_index(season=None, cache_path=None, force_refresh=False):
    """Return roster records for PlayerMatcher, or [] if unavailable.

    Never raises: an empty list leaves the caller with its previous behaviour.
    """
    if season is None:
        from scrapers.season_type import resolve_season
        season = resolve_season()
    cache_path = Path(cache_path or DEFAULT_CACHE_PATH)

    cached, fetched_at = _read_cache(cache_path)
    fresh = cached and (time.time() - fetched_at) < CACHE_TTL_SECONDS
    if fresh and not force_refresh:
        return cached

    try:
        records = _fetch_roster(season)
        if records:
            _write_cache(cache_path, season, records)
            logger.info("Player index refreshed: %d players for %s",
                        len(records), season)
            return records
        raise ValueError("commonallplayers returned 0 usable rows")
    except Exception as exc:
        if cached:
            logger.warning("Player index refresh failed (%s); using cache of %d players",
                           exc, len(cached))
            return cached
        logger.warning("Player index unavailable (%s); matcher will fall back to "
                       "season stats only, which resolves poorly early in a season", exc)
        return []
