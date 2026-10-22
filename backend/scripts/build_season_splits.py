#!/usr/bin/env python3
"""Fill public.player_season_splits / public.team_season_defense for one season.

These are the four panels the dashboard calls "season splits": Shooting Zones,
Assist Zones, Shot Type Analysis and Play Type Analysis. The live copies live on
the public.players row and are overwritten in place every pipeline run, so the
only way to offer "view 25/26 instead" is to keep a second, season-keyed copy.
This script produces it.

Why it re-scrapes instead of reading the archive
------------------------------------------------
backend/data/archive/season_2025-26/ looks like the obvious source and is the
wrong one. It was captured 2026-05-09, in May, when resolve_season_type() returns
"Playoffs" -- so it holds 16 teams, a subset of players, and a shooting_zones.json
that is 2 bytes of "{}" (check its MANIFEST.txt, which records the file as empty).
Serving that as "the 25/26 season" would quietly show playoff-only numbers for the
teams that made it and nothing for the other 14.

stats.nba.com still answers for a completed season, so the honest source is a
fresh pull with Season pinned to the target and SeasonType pinned to Regular
Season. That is what resolve_season()/resolve_season_type() already read out of
the environment, which is why the env is set before the scrapers are imported:
scrapers/shooting_zones.py builds its PARAMS dict at module scope, so an env set
after the import would be read too late and silently pull the live season.

The scrape takes a few minutes and is paced by utils/http_client. Results are
cached to backend/data/archive/season_splits_<season>.json, so re-running to fix
a composition bug costs nothing. Pass --refresh to force a new pull.

Usage:
    python backend/scripts/build_season_splits.py --season 2025-26
    python backend/scripts/build_season_splits.py --season 2025-26 --refresh
    python backend/scripts/build_season_splits.py --season 2025-26 --dry-run
"""

import argparse
import json
import logging
import os
import sys
import unicodedata
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

logger = logging.getLogger("build_season_splits")

# Mirrors utils/aggregator.py. Order matters only as a tiebreak -- the array is
# sorted by points before it is stored -- but the labels are what the frontend
# prints, so they have to match the live panel exactly or the two seasons would
# disagree on what a play type is called.
PLAY_TYPES_CONFIG = [
    ("Free Throws", "freeThrows"),
    ("Post Up", "postUp"),
    ("PNR Roll Man", "pickAndRollRollMan"),
    ("Putback", "putback"),
    ("Spot Up", "spotUp"),
    ("Cut", "cut"),
    ("Isolation", "isolation"),
    ("Transition", "transition"),
    ("PNR Ball Handler", "pickAndRollBallHandler"),
    ("Handoff", "handoff"),
    ("Off Screen", "offScreen"),
    ("Misc", "misc"),
]

SHOT_TYPE_RANK_KEYS = {
    "catch_and_shoot": "catchAndShoot",
    "pull_up": "pullups",
    "less_than_10_ft": "lessThanTenFeet",
}


def normalize_name(name):
    """Fold a player name to a comparable key.

    Both sides of this join come from stats.nba.com for the same season, so exact
    matches are the rule. The fold is for the exceptions: a name that gained or
    lost a suffix between endpoints, or lost its accents in one of them.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in stripped.lower() if ch.isalnum())


def simple_position(position):
    """Collapse a listed position to the G/F/C bucket the defensive splits use."""
    text = str(position or "").upper()
    if "G" in text:
        return "G"
    if "C" in text:
        return "C"
    if "F" in text:
        return "F"
    return "G"


def safe_float(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return default if result != result else result  # NaN


def scrape_season(season, season_type):
    """Pull every season aggregate for `season`, with the env pinned first."""
    os.environ["NBA_SEASON"] = season
    os.environ["NBA_SEASON_TYPE"] = season_type

    # Imported here, after the env is set: several of these read resolve_season()
    # at module scope and would otherwise bake in the live season.
    from scrapers import assist_zones, opp_assist_zones, opp_def_zones
    from scrapers import opp_shot_type_analysis, play_type_analysis
    from scrapers import season_stats_scrape, shooting_zones, shot_type_analysis

    if shooting_zones.PARAMS.get("Season") != season:
        raise RuntimeError(
            f"shooting_zones pinned to {shooting_zones.PARAMS.get('Season')!r}, wanted {season!r}. "
            "Something imported the scrapers before the environment was set."
        )

    logger.info("Scraping %s (%s) -- this takes a few minutes.", season, season_type)

    logger.info("  season stats...")
    stats_df = season_stats_scrape.NBAStatsEngine().get_player_data()
    if stats_df.empty:
        raise RuntimeError(f"Season stats came back empty for {season}; aborting.")

    logger.info("  shooting zones...")
    shooting = shooting_zones.get_shooting_zones_data()
    # Explicit team list: assist_zones otherwise reads the live season_stats.csv,
    # which in the opening week of a new season names only the teams that have
    # already tipped off. The season being scraped is the one that defines its
    # own teams.
    season_team_ids = sorted({
        int(value) for value in stats_df.get("TEAM_ID", []).dropna().unique()
        if int(value) > 0
    })
    logger.info("  assist zones across %d teams...", len(season_team_ids))
    assists = assist_zones.get_assist_zones_data(team_ids=season_team_ids)
    logger.info("  opponent defensive zones...")
    opp_zones = opp_def_zones.get_opp_def_zones_data()
    logger.info("  opponent assist zones...")
    opp_assists = opp_assist_zones.get_opp_assist_zones_data()
    logger.info("  shot types...")
    shot_types = shot_type_analysis.get_shot_type_data()
    logger.info("  opponent shot-type ranks...")
    opp_shot_types = opp_shot_type_analysis.process_defensive_rankings()
    logger.info("  play types...")
    play_types = play_type_analysis.get_play_type_data()

    return {
        "season": season,
        "season_type": season_type,
        "stats": stats_df.to_dict(orient="records"),
        "shooting_zones": shooting,
        "assist_zones": assists,
        "opp_def_zones": opp_zones,
        "opp_assist_zones": opp_assists,
        "shot_type_analysis": shot_types,
        "opp_shot_type_ranks": opp_shot_types,
        "play_type_analysis": play_types,
    }


def build_player_rows(raw, season):
    """One row per player: their own season profile, with no opponent baked in."""
    shooting_by_key = {normalize_name(k): v for k, v in (raw.get("shooting_zones") or {}).items()}
    assists_by_key = {normalize_name(k): v for k, v in (raw.get("assist_zones") or {}).items()}
    shot_players = (raw.get("shot_type_analysis") or {}).get("players", {})
    shot_by_key = {normalize_name(k): v for k, v in shot_players.items()}
    play_players = (raw.get("play_type_analysis") or {}).get("players", {})
    play_by_key = {normalize_name(k): v for k, v in play_players.items()}

    rows = []
    for record in raw.get("stats") or []:
        player_id = record.get("PLAYER_ID")
        name = record.get("PLAYER_NAME")
        if not player_id or not name:
            continue
        key = normalize_name(name)

        shooting = shooting_by_key.get(key)
        assists = assists_by_key.get(key)
        shot_points = build_shot_type_points(record, shot_by_key.get(key))
        play_points = build_play_type_points(play_by_key.get(key))

        # A player with no aggregate anywhere contributes nothing but a row the
        # panels would have to special-case as empty. Skip them: a missing row
        # and an all-empty row mean the same thing, and only one costs storage.
        if not any([shooting, assists, shot_points, play_points]):
            continue

        rows.append({
            "player_id": int(player_id),
            "season": season,
            "player_name": name,
            "team": str(record.get("TEAM_ABBREVIATION") or "") or None,
            "position": str(record.get("POSITION") or "") or None,
            "stats": {
                "FG_PCT": safe_float(record.get("FG_PCT")),
                "PTS": safe_float(record.get("PTS")),
                "FTM": safe_float(record.get("FTM")),
                "GP": safe_float(record.get("GP")),
            },
            "shooting_zones": shooting,
            "assist_zones": assists,
            "shot_type_points": shot_points,
            "play_type_points": play_points,
        })

    return rows


def build_shot_type_points(stats_record, shot_record):
    """Split a player's field-goal points into C&S / pull-up / under-10ft.

    Same derivation as utils/aggregator.py: the tracking endpoint reports catch
    and shoot and pull up totals, free throws are removed from the season points,
    and whatever is left over is attributed to shots inside ten feet. It is a
    remainder, not a measurement, which is why it cannot go negative.
    """
    if not shot_record:
        return None

    by_shot = shot_record.get("pointsByShotType") or {}
    matches = safe_float(by_shot.get("matchesPlayed"), 0) or 1

    floor_pts = safe_float(stats_record.get("PTS")) - safe_float(stats_record.get("FTM"))
    cs_pts = safe_float(by_shot.get("catchAndShoot")) / matches
    pu_pts = safe_float(by_shot.get("pullups")) / matches
    lt10_pts = max(0.0, floor_pts - cs_pts - pu_pts)

    cs_pct = pu_pct = lt10_pct = 0
    if floor_pts > 0:
        cs_pct = round((cs_pts / floor_pts) * 100)
        pu_pct = round((pu_pts / floor_pts) * 100)
        lt10_pct = 100 - cs_pct - pu_pct

    if cs_pts <= 0 and pu_pts <= 0 and lt10_pts <= 0:
        return None

    return {
        "catch_and_shoot": {"points": round(cs_pts, 1), "percentage": cs_pct},
        "pull_up": {"points": round(pu_pts, 1), "percentage": pu_pct},
        "less_than_10_ft": {"points": round(lt10_pts, 1), "percentage": lt10_pct},
    }


def build_play_type_points(play_record):
    """Per-game points by play type, highest first, with the opponent rank left out.

    The rank belongs to whichever team the player faces next, which is not known
    until render time, so it is filled in by the API rather than stored here.
    """
    if not play_record:
        return None

    points = play_record.get("points") or {}
    total = sum(safe_float(points.get(key, {}).get("perMatch")) for _, key in PLAY_TYPES_CONFIG)

    rows = []
    for label, key in PLAY_TYPES_CONFIG:
        per_match = safe_float(points.get(key, {}).get("perMatch"))
        if per_match <= 0:
            continue
        pct = round((per_match / total) * 100) if total > 0 else 0
        rows.append({
            "type": label,
            "key": key,
            "points": f"{per_match} ({pct}%)",
            "percent": f"{pct}%",
            "raw_pts": per_match,
        })

    if not rows:
        return None

    rows.sort(key=lambda item: item["raw_pts"], reverse=True)
    return rows


def build_team_rows(raw, season):
    """One row per team: everything the panels need about that defense."""
    from utils.team_names import FULL_TO_ABBR

    opp_zones = raw.get("opp_def_zones") or {}
    opp_assists = raw.get("opp_assist_zones") or {}
    opp_shot_ranks = raw.get("opp_shot_type_ranks") or {}
    play_teams = (raw.get("play_type_analysis") or {}).get("teams", {})

    team_names = set(opp_zones) | set(opp_assists) | set(opp_shot_ranks) | set(play_teams)

    rows = []
    for full_name in sorted(team_names):
        tricode = FULL_TO_ABBR.get(full_name)
        if not tricode:
            logger.warning("No tricode for team name %r -- skipping.", full_name)
            continue

        rows.append({
            "team": tricode,
            "season": season,
            "team_name": full_name,
            "opp_def_zones": opp_zones.get(full_name),
            "opp_assist_zones": opp_assists.get(full_name),
            "shot_type_ranks": (opp_shot_ranks.get(full_name) or {}).get("rankings"),
            "play_type_ranks": (play_teams.get(full_name) or {}).get("rankings"),
        })

    return rows


def upsert(table, rows, chunk_size=200):
    from utils.supabase_client import get_supabase_client

    client = get_supabase_client()
    for index in range(0, len(rows), chunk_size):
        chunk = rows[index:index + chunk_size]
        client.table(table).upsert(chunk).execute()
        logger.info("  %s: %d/%d", table, min(index + chunk_size, len(rows)), len(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", required=True, help="NBA-format season, e.g. 2025-26")
    parser.add_argument("--season-type", default="Regular Season",
                        help="Defaults to Regular Season, which is what the panels mean by a season.")
    parser.add_argument("--refresh", action="store_true", help="Re-scrape even if a cached pull exists.")
    parser.add_argument("--dry-run", action="store_true", help="Build the rows and report, but do not write to Supabase.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND / ".env")
    except ImportError:
        pass

    cache_path = BACKEND / "data" / "archive" / f"season_splits_{args.season}.json"

    if cache_path.exists() and not args.refresh:
        logger.info("Reading cached scrape from %s (--refresh to re-pull).", cache_path)
        raw = json.loads(cache_path.read_text())
    else:
        raw = scrape_season(args.season, args.season_type)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(raw))
        logger.info("Cached raw scrape to %s", cache_path)

    player_rows = build_player_rows(raw, args.season)
    team_rows = build_team_rows(raw, args.season)

    logger.info("Built %d player rows and %d team rows for %s.", len(player_rows), len(team_rows), args.season)
    for label, field in (
        ("shooting zones", "shooting_zones"),
        ("assist zones", "assist_zones"),
        ("shot types", "shot_type_points"),
        ("play types", "play_type_points"),
    ):
        covered = sum(1 for row in player_rows if row.get(field))
        logger.info("  %-14s %d players", label, covered)

    if len(team_rows) < 30:
        logger.warning(
            "Only %d of 30 teams have defensive splits. A Playoffs scrape looks exactly "
            "like this -- check --season-type.", len(team_rows),
        )

    if args.dry_run:
        logger.info("Dry run: nothing written.")
        return 0

    upsert("player_season_splits", player_rows)
    upsert("team_season_defense", team_rows)
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
