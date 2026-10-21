#!/usr/bin/env python3
"""Backfill boxscores.json (margins + DNPs) for a past, completed season.

Why this exists
----------------
`scrapers/boxscores.py` only ever scraped the last MAX_HISTORY_DAYS of games,
so there is no boxscore file for 2024-25 (or any season before the current
one) -- which is also why the hover tooltip's "DID NOT PLAY" block has
nothing to show for archive seasons: `aggregator.py` attaches `dnps` to a
game log row from this exact file, keyed by game id, and a past season was
never in it.

The NBA CDN boxscore endpoint `fetch_and_parse_boxscore` already hits
(backend/scrapers/boxscores.py) takes a game id, not a date window, so this
script just supplies a different list of ids -- every GAME_ID that appears in
that season's gamelogs CSV -- and reuses the same fetch/retry/pacing path
(`utils.http_client.proxied_get`, which already handles 403/429 with
exponential backoff and Retry-After).

Incremental and safe to re-run: `run_scrape_for_ids` skips any game id already
present at the output path, and checkpoints to disk periodically so an
interrupted run doesn't lose progress.

Usage:
    python backend/scripts/build_season_boxscores.py --season 2024-25
    python backend/scripts/build_season_boxscores.py --season 2024-25 --dry-run
"""

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pandas as pd  # noqa: E402

from scrapers.boxscores import run_scrape_for_ids  # noqa: E402

ARCHIVE = BACKEND / "data" / "archive"


def game_ids_for_season(csv_path):
    df = pd.read_csv(csv_path, dtype={"GAME_ID": str})
    if "GAME_ID" not in df.columns:
        raise SystemExit(f"{csv_path} has no GAME_ID column")
    # Matches the zero-padded 10-digit form aggregator.py and boxscores.json
    # both key on (CSV stores it unpadded, e.g. "22401185").
    ids = sorted({str(gid).zfill(10) for gid in df["GAME_ID"].dropna().unique()})
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True, help="e.g. 2024-25")
    ap.add_argument("--csv", help="defaults to backend/data/archive/gamelogs_<season>.csv")
    ap.add_argument("--output", help="defaults to backend/data/archive/season_<season>/boxscores.json")
    ap.add_argument("--dry-run", action="store_true", help="only report the game-id count, fetch nothing")
    args = ap.parse_args()

    csv_path = Path(args.csv) if args.csv else ARCHIVE / f"gamelogs_{args.season}.csv"
    output_path = Path(args.output) if args.output else ARCHIVE / f"season_{args.season}" / "boxscores.json"

    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    ids = game_ids_for_season(csv_path)
    print(f"season : {args.season}")
    print(f"source : {csv_path}")
    print(f"output : {output_path}")
    print(f"  game ids in season: {len(ids)}")

    if args.dry_run:
        print("\n--dry-run: nothing fetched.")
        return 0

    run_scrape_for_ids(ids, str(output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
