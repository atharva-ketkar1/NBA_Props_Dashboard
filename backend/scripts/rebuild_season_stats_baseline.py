#!/usr/bin/env python3
"""Rebuild season_stats.csv at the start of a season, anchored to CURRENT rosters.

Why this exists
---------------
`leaguedashplayerstats` returns nothing for a season with no completed games, so on
opening day run_pipeline's run_stats() falls back to "preserve the previous file".
That fallback is silently wrong in two ways:

  1. The preserved file was last written in May, when resolve_season_type() returns
     "Playoffs" -- so the baseline is 230 *playoff* participants, not the league.
  2. Teams are last season's. Anthony Davis reads DAL, not WAS.

Because season_stats.csv is also the player universe used to match sportsbook props,
anything outside those 230 names is dropped: on 2026-10-20 that was 79 of 162
players with props (49%), including every rookie and every player whose team missed
the playoffs.

What it does
------------
  * universe + team + position  <- playerindex for the CURRENT season (works at 0 GP)
  * statistical baseline        <- leaguedashplayerstats for the PRIOR season,
                                   Regular Season (not playoffs), joined on PLAYER_ID
  * players with no prior season (rookies) are kept with empty stats, so their props
    still match even though they have no history.

The result keeps season_stats.csv's exact 47-column schema and adds
STATS_SOURCE_SEASON so the provenance of the baseline is visible rather than implied.
Once real games are played, the normal scrape returns rows and overwrites this.

Usage:
    python backend/scripts/rebuild_season_stats_baseline.py [--dry-run]
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

import pandas as pd  # noqa: E402

from scrapers.season_type import resolve_season  # noqa: E402

OUT_PATH = BACKEND / "data" / "current" / "season_stats.csv"
REQUIRED_COLUMNS = {"PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION"}


def prior_season(season):
    """'2026-27' -> '2025-26'"""
    start = int(season[:4]) - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def fetch_prior_season_stats(season):
    """Reuse NBAStatsEngine so the 47-column schema is produced identically.

    The engine reads the season off the environment, so it is set before import-time
    helpers run inside get_player_data().
    """
    os.environ["NBA_SEASON"] = season
    os.environ["NBA_SEASON_TYPE"] = "regular"
    # gamelogs-specific override, kept consistent so any shared helper agrees
    os.environ["GAMELOGS_SEASON_TYPE"] = "regular"

    from scrapers.season_stats_scrape import NBAStatsEngine

    engine = NBAStatsEngine()
    return engine.get_player_data()


def fetch_current_rosters(season):
    from scrapers.season_stats_scrape import NBAStatsEngine

    engine = NBAStatsEngine()
    url = (
        "https://stats.nba.com/stats/playerindex"
        f"?Historical=0&LeagueID=00&Season={season}&SeasonType=Regular%20Season"
    )
    _tag, df = engine._fetch_url(url, "Index")
    if df.empty:
        raise SystemExit(f"playerindex returned no rows for {season}; aborting.")

    df = df[df.get("IS_DEFUNCT", 0).astype(str).isin(["0", "0.0", "False", "nan"])] \
        if "IS_DEFUNCT" in df.columns else df

    rosters = pd.DataFrame({
        "PLAYER_ID": df["PERSON_ID"].astype("int64"),
        "PLAYER_NAME": (
            df["PLAYER_FIRST_NAME"].fillna("").astype(str).str.strip()
            + " "
            + df["PLAYER_LAST_NAME"].fillna("").astype(str).str.strip()
        ).str.strip(),
        "TEAM_ABBREVIATION": df["TEAM_ABBREVIATION"],
        "TEAM_ID": df["TEAM_ID"],
        "POSITION": df.get("POSITION"),
    })
    # Players not on a roster right now cannot have props; drop them.
    rosters = rosters[rosters["TEAM_ABBREVIATION"].notna()]
    rosters = rosters[rosters["TEAM_ABBREVIATION"].astype(str).str.strip() != ""]
    return rosters.drop_duplicates(subset=["PLAYER_ID"])


def build(current_season, dry_run=False):
    base_season = prior_season(current_season)
    print(f"current season : {current_season}")
    print(f"baseline season: {base_season} (Regular Season)")

    rosters = fetch_current_rosters(current_season)
    print(f"  current rosters      : {len(rosters)} players")

    stats = fetch_prior_season_stats(base_season)
    if not isinstance(stats, pd.DataFrame) or stats.empty:
        raise SystemExit(f"prior-season scrape returned no rows for {base_season}; aborting.")
    print(f"  {base_season} regular season: {len(stats)} players, {len(stats.columns)} columns")

    # Current roster identity wins; prior-season numbers are only a baseline.
    identity_cols = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "TEAM_ID", "POSITION"]
    stat_cols = [c for c in stats.columns if c not in identity_cols]

    merged = rosters.merge(
        stats[["PLAYER_ID"] + stat_cols], on="PLAYER_ID", how="left", suffixes=("", "_prior")
    )
    merged["STATS_SOURCE_SEASON"] = base_season
    # Rookies matched nothing: mark the baseline as absent rather than as 2025-26.
    no_history = merged["GP"].isna() if "GP" in merged.columns else merged.index == -1
    merged.loc[no_history, "STATS_SOURCE_SEASON"] = ""

    # Preserve the original column order, then append anything new.
    if OUT_PATH.exists():
        original_order = list(pd.read_csv(OUT_PATH, nrows=0).columns)
        ordered = [c for c in original_order if c in merged.columns]
        ordered += [c for c in merged.columns if c not in ordered]
        merged = merged[ordered]

    missing = REQUIRED_COLUMNS - set(merged.columns)
    if missing:
        raise SystemExit(f"refusing to write: missing required columns {sorted(missing)}")

    with_history = int((~no_history).sum())
    print(f"\n  rows written         : {len(merged)}")
    print(f"  with {base_season} baseline : {with_history}")
    print(f"  no prior history     : {len(merged) - with_history} (rookies / returnees)")

    if dry_run:
        print("\n--dry-run: not writing.")
        return merged

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".tmp", prefix=f".{OUT_PATH.name}.",
        dir=str(OUT_PATH.parent), delete=False, newline="", encoding="utf-8",
    )
    tmp.close()
    try:
        merged.to_csv(tmp.name, index=False)
        os.replace(tmp.name, OUT_PATH)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    print(f"\nwrote {OUT_PATH}")
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    # Resolve before the env is mutated for the prior-season fetch.
    build(resolve_season(), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
