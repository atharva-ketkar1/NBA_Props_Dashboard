#!/usr/bin/env python3
"""Keep a local, on-disk archive of a completed season under backend/data/archive/.

Complements scripts/snapshot.py rather than replacing it. snapshot.py uploads one
zip to a private GitHub Release; this keeps the season's files browsable on disk, so
last season's numbers can be opened without downloading and unzipping an 18MB asset.

Why it is needed
----------------
Most of backend/data/current/ is scratch that the next pipeline run overwrites. On
opening day 2026-10-20 that cost real data: the season-aggregate scrapers correctly
returned nothing for a season with no games played, and the pipeline wrote those
empty results straight over 2025-26's zones, shot types and play types. They were
recoverable only because a GitHub Release happened to predate the run.

Layout (follows the existing convention in backend/data/archive/)
-----------------------------------------------------------------
    archive/gamelogs_<season>.csv      flat at root, beside gamelogs_2024-25.csv
    archive/season_<season>/           the season-level aggregates
    archive/season_<season>/MANIFEST.txt
    archive/season_<season>/SHA256SUMS  verify with: shasum -a 256 -c SHA256SUMS

Usage:
    # from the live data dir (normal end-of-season case)
    python backend/scripts/archive_season_locally.py --season 2025-26

    # from an extracted snapshot, when current/ has already been overwritten
    python backend/scripts/archive_season_locally.py --season 2025-26 \
        --from /path/to/nba_bk_stage/local_data/current

    python backend/scripts/archive_season_locally.py --season 2025-26 --verify
"""

import argparse
import datetime as dt
import hashlib
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CURRENT = BACKEND / "data" / "current"
ARCHIVE = BACKEND / "data" / "archive"

# Season-scoped by filename -> stays flat at the archive root.
GAMELOGS = "gamelogs_{season}.csv"

# Fixed-filename season aggregates. These are the files the pipeline overwrites on
# the first run of a new season, so they are the whole reason this script exists.
AGGREGATES = [
    "season_stats.csv",
    "shooting_zones.json",
    "assist_zones.json",
    "opp_assist_zones.json",
    "opp_def_zones.json",
    "opponent_defensive_ranks.json",
    "shot_type_analysis.json",
    "play_type_analysis.json",
    "boxscores.json",
]

# Copied when present, but their absence is not worth warning about: these are
# day-scoped files whose value in a season archive is incidental.
OPTIONAL = [
    "nba_injury_report.json",
    "action_network_odds.json",
    "line_movements_today.json",
    "draftkings.csv",
    "fanduel.csv",
    "prizepicks.csv",
]

EMPTY_BYTES = 64  # '{}' or '{"players": {}, "teams": {}}' -> nothing worth archiving


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def season_dir(season):
    return ARCHIVE / f"season_{season}"


def copy(src, dst, label, results):
    if not src.exists():
        results.append((label, "absent", 0))
        return False
    size = src.stat().st_size
    if size <= EMPTY_BYTES:
        # Refuse to archive an already-emptied file over a good one.
        if dst.exists() and dst.stat().st_size > EMPTY_BYTES:
            results.append((label, f"SKIPPED (source empty, {size}B; kept existing archive)", size))
            return False
        results.append((label, f"empty ({size}B)", size))
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if size > EMPTY_BYTES:
        results.append((label, "copied", size))
    return True


def write_metadata(target, season, results, source):
    lines = [
        f"NBA Dashboard local season archive: {season}",
        f"created: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        f"source : {source}",
        "",
        "Season-scoped game logs live at ../gamelogs_<season>.csv (root convention).",
        "This directory holds the fixed-filename season aggregates, which the",
        "pipeline overwrites on the first run of a new season.",
        "",
        "Verify: shasum -a 256 -c SHA256SUMS",
        "",
        "== contents ==",
    ]
    for label, status, size in results:
        lines.append(f"  {label:36} {status:<52} {size // 1024:>7}KB")
    (target / "MANIFEST.txt").write_text("\n".join(lines) + "\n")

    sums = []
    for path in sorted(target.rglob("*")):
        if path.is_file() and path.name not in ("SHA256SUMS", "MANIFEST.txt"):
            sums.append(f"{sha256(path)}  {path.relative_to(target)}")
    (target / "SHA256SUMS").write_text("\n".join(sums) + "\n")


def verify(season):
    target = season_dir(season)
    sums_file = target / "SHA256SUMS"
    if not sums_file.exists():
        print(f"no archive at {target}")
        return False
    ok = bad = 0
    for line in sums_file.read_text().splitlines():
        if not line.strip():
            continue
        expected, rel = line.split("  ", 1)
        path = target / rel
        if not path.exists():
            print(f"  MISSING  {rel}")
            bad += 1
        elif sha256(path) == expected:
            ok += 1
        else:
            print(f"  MISMATCH {rel}")
            bad += 1
    gamelogs = ARCHIVE / GAMELOGS.format(season=season)
    print(f"\n{target}")
    print(f"  {ok} file(s) verified, {bad} problem(s)")
    print(f"  gamelogs: {'present' if gamelogs.exists() else 'ABSENT'} "
          f"({gamelogs.stat().st_size // 1024 if gamelogs.exists() else 0}KB)")
    return bad == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True, help="e.g. 2025-26")
    ap.add_argument("--from", dest="source", help="source dir (default: backend/data/current)")
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    if args.verify:
        return 0 if verify(args.season) else 1

    source = Path(args.source) if args.source else CURRENT
    if not source.is_dir():
        raise SystemExit(f"source dir not found: {source}")

    target = season_dir(args.season)
    target.mkdir(parents=True, exist_ok=True)
    print(f"season : {args.season}")
    print(f"source : {source}")
    print(f"target : {target}\n")

    results = []

    # 1. Game logs -> archive root, matching gamelogs_2024-25.csv.
    name = GAMELOGS.format(season=args.season)
    copy(source / name, ARCHIVE / name, f"{name} (-> archive root)", results)

    # 2. Season aggregates -> archive/season_<season>/
    for fname in AGGREGATES:
        copy(source / fname, target / fname, fname, results)
    for fname in OPTIONAL:
        if (source / fname).exists():
            copy(source / fname, target / fname, f"{fname} (optional)", results)

    write_metadata(target, args.season, results, str(source))

    copied = [r for r in results if r[1] == "copied"]
    skipped = [r for r in results if r[1].startswith("SKIPPED")]
    empty = [r for r in results if r[1].startswith("empty")]
    absent = [r for r in results if r[1] == "absent"]

    print("\n".join(f"  {label:36} {status}" for label, status, _ in results))
    print(f"\n  copied={len(copied)} empty={len(empty)} skipped={len(skipped)} absent={len(absent)}")
    total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"  archive size: {total / 1048576:.1f} MB  ({target})")
    if skipped:
        print("\n  NOTE: source files were already empty; existing archive copies kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
