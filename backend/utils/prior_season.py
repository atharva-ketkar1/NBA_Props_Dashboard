"""
prior_season.py
Prior-season game history and season aggregates, for players whose current
season is too short to project from.

Why this exists
---------------
season_stats.csv comes from leaguedashplayerstats and therefore only contains
players with GP > 0. On opening night it holds 68 rows while the slate needs
~171, so aggregator.py gives every other rostered player a zero-filled skeleton
stats dict (see "Give roster-only players (GP == 0) a skeleton entry") just so
their props have somewhere to attach.

edge_score then reads those zeros as a real season average, because 0.0 is not
None:

    _stat_value_from_stats(stats, "PTS+REB+AST")  -> 0.0
    baseline_projection                           -> 0.0, available=True
    projection_gap for an under                   -> -1 * (0.0 - 36.5) = +36.5

which clamps the projection component to its maximum for whichever side is the
under. Measured on 2026-10-21, all 15 recommendations were unders with
baseline_projection=0.0 and edge_score between 63.9 and 65.2 -- the board was
ranking line size, not edge.

What this module provides
-------------------------
Per player, from the most recent season in which they actually played:

    profile(pid)  exact full-season per-game aggregates, same keys as the
                  master_feed stats dict, so it can be blended straight into it
    logs(pid)     the tail of that season's game logs, same row shape as
                  master_feed game_log, so _extract_logs and the ML feature
                  builder consume them unchanged

Coverage measured on 2026-10-21, against the 171 players with props:

    163 had GP == 0 (zero-filled skeleton)
    151 of those have 2025-26 logs
      4 more have 2024-25 logs only
      8 have no history at all -- all genuine rookies

Source and cost
---------------
Supabase `archive_gamelogs(player_id, season) -> game_log` is the primary
source: it is already populated (582 players for 2025-26, 569 for 2024-25),
already in master_feed game_log shape, and unlike backend/data/archive it is
not gitignored, so it is reachable from a freshly deployed VM.

The full pull is expensive -- 117MB with `dnps`, 29MB without -- and the
pipeline runs every 5 minutes. But a completed season never changes, so it is
fetched once and cached on disk. The cache strips `dnps` (75% of the bytes;
last season's absences must not drive tonight's lineup adjustment, and every
reader guards the field with isinstance) and keeps the last
PRIOR_LOG_GAMES games, which covers the widest rolling window any consumer
asks for (recent20_* in the regression model) with headroom. Exact season
aggregates are computed before trimming, so `profile` is never a tail average.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PriorSeason")

BACKEND_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BACKEND_DIR / "data" / "prior"
ARCHIVE_DIR = BACKEND_DIR / "data" / "archive"
CURRENT_DIR = BACKEND_DIR / "data" / "current"

# The widest rolling window any consumer asks for is recent20_*; keep headroom.
PRIOR_LOG_GAMES = int(os.environ.get("PRIOR_SEASON_LOG_GAMES", "25"))

# How many prior seasons back to look for a player's most recent real season.
PRIOR_SEASON_DEPTH = int(os.environ.get("PRIOR_SEASON_DEPTH", "2"))

# A season is only usable as a prior if the player logged at least this many
# games in it. Below that the "prior" is itself a small sample, and we would
# rather fall back a season than anchor on 2 games.
MIN_PRIOR_GAMES = int(os.environ.get("PRIOR_SEASON_MIN_GAMES", "5"))

SUPABASE_TABLE = "archive_gamelogs"
SUPABASE_PAGE_SIZE = int(os.environ.get("PRIOR_SEASON_PAGE_SIZE", "40"))

# Keys the master_feed stats dict carries (aggregator.DISPLAY_STATS), minus the
# identity fields. These are what edge_score reads off player["stats"].
_PER_GAME_KEYS = [
    "MIN",
    "PTS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "PLUS_MINUS",
    "REB", "OREB", "DREB", "AST", "TOV", "STL", "BLK", "PF",
    "POTENTIAL_AST", "DRIVES", "DRIVE_PTS", "REB_CHANCES",
]

# Rate stats: a mean of per-game percentages, not a ratio of totals. These are
# already per-possession or per-game normalised in the source rows.
_MEAN_RATE_KEYS = ["USG_PCT", "AST_PCT", "REB_PCT", "TS_PCT"]

# The two sources disagree on scale for USG_PCT: season_stats.csv stores it as
# a fraction (0.194) and the game logs as a percentage (19.4) -- verified on
# 2026-10-21 across every player with GP > 0. This profile gets blended into a
# season_stats-shaped dict, so percentage-scale keys have to be converted or
# the blend averages 0.194 with 19.4 and lands two orders of magnitude out.
# FG_PCT, FG3_PCT, FT_PCT and TS_PCT are fractions in both and need no change.
_PERCENT_TO_FRACTION_KEYS = {"USG_PCT"}

# Shooting percentages must come from summed makes over summed attempts, since
# a mean of per-game percentages over-weights low-volume games.
_RATIO_KEYS = {
    "FG_PCT": ("FGM", "FGA"),
    "FG3_PCT": ("FG3M", "FG3A"),
    "FT_PCT": ("FTM", "FTA"),
}

_COMBO_KEYS = {
    "PTS+REB+AST": ("PTS", "REB", "AST"),
    "PTS+REB": ("PTS", "REB"),
    "PTS+AST": ("PTS", "AST"),
    "REB+AST": ("REB", "AST"),
    "STL+BLK": ("STL", "BLK"),
}


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _mean(values: List[Optional[float]]) -> Optional[float]:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def prior_season_strings(current_season: str, depth: int = PRIOR_SEASON_DEPTH) -> List[str]:
    """["2025-26", "2024-25"] for a current season of "2026-27"."""
    try:
        start_year = int(str(current_season)[:4])
    except (TypeError, ValueError):
        return []
    seasons = []
    for step in range(1, depth + 1):
        year = start_year - step
        seasons.append(f"{year}-{str(year + 1)[-2:]}")
    return seasons


def _resolve_current_season() -> str:
    try:
        from scrapers.season_type import resolve_season
        return resolve_season()
    except Exception:  # pragma: no cover - resolve_season has its own fallbacks
        from datetime import date
        today = date.today()
        start_year = today.year if today.month >= 9 else today.year - 1
        return f"{start_year}-{str(start_year + 1)[-2:]}"


def _season_aggregates(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Full-season per-game aggregates, in master_feed stats-dict shape.

    Computed over every game in the season, before any trimming, so this stays
    a true season average rather than a tail average.
    """
    played = [log for log in logs if (_safe_float(log.get("MIN")) or 0.0) > 0]
    if not played:
        return {}

    stats: Dict[str, Any] = {"GP": len(played)}

    for key in _PER_GAME_KEYS:
        value = _mean([_safe_float(log.get(key)) for log in played])
        if value is not None:
            stats[key] = value

    for key in _MEAN_RATE_KEYS:
        value = _mean([_safe_float(log.get(key)) for log in played])
        if value is not None:
            stats[key] = value / 100.0 if key in _PERCENT_TO_FRACTION_KEYS else value

    for key, (made_key, attempted_key) in _RATIO_KEYS.items():
        made = [_safe_float(log.get(made_key)) for log in played]
        attempted = [_safe_float(log.get(attempted_key)) for log in played]
        made_total = sum(value for value in made if value is not None)
        attempted_total = sum(value for value in attempted if value is not None)
        if attempted_total > 0:
            stats[key] = made_total / attempted_total

    # Derive combos from the component means rather than from a per-game combo
    # column, so a missing column in one season cannot zero the combo.
    for key, components in _COMBO_KEYS.items():
        parts = [stats.get(component) for component in components]
        if all(part is not None for part in parts):
            stats[key] = sum(parts)

    return stats


def _trim_log(log: Dict[str, Any]) -> Dict[str, Any]:
    """Drop `dnps`, which is ~75% of the payload and must not influence tonight."""
    return {key: value for key, value in log.items() if key != "dnps"}


def _sorted_desc(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def sort_key(log: Dict[str, Any]) -> str:
        return str(log.get("GAME_DATE") or "")
    return sorted(logs, key=sort_key, reverse=True)


def _load_season_from_supabase(season: str) -> Dict[int, List[Dict[str, Any]]]:
    """Page the whole season out of archive_gamelogs.

    The row count is read first and verified at the end. The cache this feeds
    is written once and then trusted forever, so a short read -- a dropped
    connection or an early empty page part-way through pagination -- must raise
    rather than quietly persist a truncated season.
    """
    from utils.supabase_client import get_supabase_client

    client = get_supabase_client()
    head = (
        client.table(SUPABASE_TABLE)
        .select("player_id", count="exact")
        .eq("season", season)
        .limit(1)
        .execute()
    )
    expected = head.count
    if not expected:
        return {}

    by_player: Dict[int, List[Dict[str, Any]]] = {}
    seen_rows = 0
    offset = 0
    while offset < expected:
        response = (
            client.table(SUPABASE_TABLE)
            .select("player_id,game_log")
            .eq("season", season)
            .order("player_id")
            .range(offset, offset + SUPABASE_PAGE_SIZE - 1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            break
        seen_rows += len(rows)
        for row in rows:
            try:
                player_id = int(row.get("player_id"))
            except (TypeError, ValueError):
                continue
            game_log = row.get("game_log")
            if isinstance(game_log, list) and game_log:
                by_player[player_id] = game_log
        offset += len(rows)

    if seen_rows < expected:
        raise RuntimeError(
            f"{SUPABASE_TABLE} short read for {season}: got {seen_rows} of {expected} rows"
        )
    return by_player


def _load_season_from_csv(season: str) -> Dict[int, List[Dict[str, Any]]]:
    """Fallback for a machine that still has the on-disk archive.

    backend/data is gitignored, so this cannot be relied on after a fresh
    deploy -- it exists so local runs and backfills do not need the network.
    """
    import csv

    candidates = [
        ARCHIVE_DIR / f"gamelogs_{season}.csv",
        CURRENT_DIR / f"gamelogs_{season}.csv",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return {}

    by_player: Dict[int, List[Dict[str, Any]]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                player_id = int(str(row.get("PLAYER_ID") or "").strip())
            except (TypeError, ValueError):
                continue
            # CSV gives every cell as a string; coerce the numerics so the rows
            # match the JSON shape that Supabase and master_feed use.
            coerced: Dict[str, Any] = {}
            for key, value in row.items():
                if key in ("TEAM_ABBREVIATION", "MATCHUP", "WL", "GAME_DATE", "DATE_STR"):
                    coerced[key] = value
                    continue
                number = _safe_float(value)
                coerced[key] = number if number is not None else value
            by_player.setdefault(player_id, []).append(coerced)
    return by_player


def _build_season_payload(season: str) -> Dict[str, Any]:
    by_player: Dict[int, List[Dict[str, Any]]] = {}
    source = "none"
    try:
        by_player = _load_season_from_supabase(season)
        source = "supabase"
    except Exception as exc:
        logger.warning("archive_gamelogs fetch failed for %s (%s); trying local CSV", season, exc)
    if not by_player:
        by_player = _load_season_from_csv(season)
        source = "csv" if by_player else source

    profiles: Dict[str, Any] = {}
    logs: Dict[str, Any] = {}
    for player_id, game_log in by_player.items():
        aggregates = _season_aggregates(game_log)
        if not aggregates or aggregates.get("GP", 0) < MIN_PRIOR_GAMES:
            continue
        played = [log for log in game_log if (_safe_float(log.get("MIN")) or 0.0) > 0]
        profiles[str(player_id)] = aggregates
        logs[str(player_id)] = [
            _trim_log(log) for log in _sorted_desc(played)[:PRIOR_LOG_GAMES]
        ]

    return {"season": season, "source": source, "profiles": profiles, "logs": logs}


def _cache_path(season: str) -> Path:
    return CACHE_DIR / f"prior_history_{season}.json"


def build_cache(season: str, *, force: bool = False) -> Dict[str, Any]:
    """Fetch and cache one prior season. Safe to call repeatedly."""
    path = _cache_path(season)
    if path.exists() and not force:
        try:
            with path.open() as handle:
                return json.load(handle)
        except Exception as exc:
            logger.warning("Prior-season cache unreadable at %s (%s); rebuilding", path, exc)

    payload = _build_season_payload(season)
    if not payload.get("profiles"):
        logger.warning("No prior-season history available for %s", season)
        return payload

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # run_pipeline warms this, but cron_line_movement and cron_closing_lines
    # can also trigger a lazy build, so two processes may write at once. A
    # per-process temp name keeps them from interleaving into one file; the
    # os.replace then makes whichever finishes last the winner, and both wrote
    # the same immutable season anyway.
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        with tmp.open("w") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    logger.info(
        "Prior-season cache written | season=%s source=%s players=%d path=%s",
        season, payload.get("source"), len(payload["profiles"]), path,
    )
    return payload


class PriorHistory:
    """Most-recent-season-played history, resolved across several prior seasons.

    A player is looked up in the newest prior season first and falls back to
    older ones, so a player who missed all of last season is still anchored on
    the last season they actually played.
    """

    def __init__(self, seasons: List[str]) -> None:
        self.seasons = seasons
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._logs: Dict[str, List[Dict[str, Any]]] = {}
        self._season_by_player: Dict[str, str] = {}
        self._loaded = False

    def load(self) -> "PriorHistory":
        if self._loaded:
            return self
        for season in self.seasons:
            try:
                payload = build_cache(season)
            except Exception as exc:
                logger.warning("Prior-season load failed for %s: %s", season, exc)
                continue
            for player_id, profile in (payload.get("profiles") or {}).items():
                # Newest season wins; do not let an older season overwrite it.
                if player_id in self._profiles:
                    continue
                self._profiles[player_id] = profile
                self._logs[player_id] = (payload.get("logs") or {}).get(player_id, [])
                self._season_by_player[player_id] = season
        self._loaded = True
        logger.info(
            "Prior-season history ready | seasons=%s players=%d",
            ",".join(self.seasons), len(self._profiles),
        )
        return self

    def profile(self, player_id: Any) -> Dict[str, Any]:
        return self._profiles.get(str(player_id), {})

    def logs(self, player_id: Any) -> List[Dict[str, Any]]:
        return self._logs.get(str(player_id), [])

    def season_for(self, player_id: Any) -> Optional[str]:
        return self._season_by_player.get(str(player_id))

    def __len__(self) -> int:
        return len(self._profiles)


_history: Optional[PriorHistory] = None


def get_prior_history() -> PriorHistory:
    """Process-wide singleton. A completed season never changes, so this is
    loaded once and reused for the life of the process."""
    global _history
    if _history is None:
        seasons = prior_season_strings(_resolve_current_season())
        _history = PriorHistory(seasons).load()
    return _history


def reset_prior_history() -> None:
    """Drop the singleton. For tests and for long-lived processes that cross a
    season boundary."""
    global _history
    _history = None


if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(BACKEND_DIR))

    parser = argparse.ArgumentParser(description="Build the prior-season history cache.")
    parser.add_argument("--season", action="append", dest="seasons",
                        help="Season to build, e.g. 2025-26. Repeatable. "
                             "Defaults to the seasons before the current one.")
    parser.add_argument("--force", action="store_true", help="Refetch even if cached.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    targets = args.seasons or prior_season_strings(_resolve_current_season())
    for target in targets:
        payload = build_cache(target, force=args.force)
        print(f"{target}: source={payload.get('source')} players={len(payload.get('profiles') or {})}")
