#!/usr/bin/env python3
"""Populate archive_gamelogs for a completed season from its gamelogs CSV.

Nothing in the repo wrote this table before: the 2024-25 rows were loaded by some
process that was never committed, so each season rollover was a manual step with no
record of how it was done. This script is that step, versioned.

The frontend serves an archive season by swapping ONLY the player's game_log
(App.tsx keeps the live row for everything else), and api/_lib/dashboardData.ts
reads `archive_gamelogs(player_id, season) -> game_log`. So the job is exactly:
group the season's game rows per player and store them as the same list of
per-game dicts the live feed uses. gamelogs_<season>.csv already carries precisely
the 61 columns the existing 2024-25 rows contain, so no reshaping is needed.

Usage:
    python backend/scripts/build_season_archive.py --season 2025-26
    python backend/scripts/build_season_archive.py --season 2025-26 --dry-run
    python backend/scripts/build_season_archive.py --season 2025-26 --verify
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from utils.aggregator import DNP_STAT_KEYS, safe_float  # noqa: E402
from utils.player_index import get_full_player_history  # noqa: E402
from utils.player_matcher import PlayerMatcher  # noqa: E402

TABLE = "archive_gamelogs"
ARCHIVE = BACKEND / "data" / "archive"
# Players per request. The payload is large (~100KB of JSON per player), so this is
# deliberately small to stay inside PostgREST's request size and timeout limits.
CHUNK = 15
SORT_KEY = "GAME_DATE"


def env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing {name} (expected in backend/.env)")
    return value


def clean(value):
    """JSON-safe scalar. NaN/inf are not valid JSON and Postgres rejects them."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    if isinstance(value, (pd.Timestamp,)):
        return str(value)
    if hasattr(value, "item"):          # numpy scalar
        try:
            return clean(value.item())
        except Exception:
            return str(value)
    return value


def build_dnp_stats_map(df):
    """Per-player averages for THIS season, straight from its own gamelogs CSV.

    There is no season_stats.csv for a past season (only the live one gets
    rewritten each season), but the gamelogs CSV already carries every raw
    per-game column DNP_STAT_KEYS needs, so grouping/averaging it directly is
    both sufficient and more accurate than reaching for a current-season file.
    """
    present = [k for k in DNP_STAT_KEYS if k in df.columns]
    means = df.groupby("PLAYER_ID")[present].mean() if present else pd.DataFrame()
    stats_map = {}
    for player_id, row in means.iterrows():
        stats_map[int(player_id)] = {
            k: (safe_float(row[k]) if k in present else 0.0) for k in DNP_STAT_KEYS
        }
    return stats_map


def load_boxscores(path):
    if not path or not Path(path).exists():
        return {}
    with open(path) as f:
        return json.load(f)


def attach_dnps(games, boxscores_data, stats_map, matcher):
    """Mutates each game dict in place, mirroring aggregator.py's live-season
    logic exactly: a DNP'd teammate's `stats` is their season average for
    the stat the hover tooltip is currently showing, resolved by name via the
    same PlayerMatcher the live pipeline uses."""
    if not boxscores_data:
        return
    for game in games:
        game_id = str(game.get("GAME_ID", "")).zfill(10)
        b_data = boxscores_data.get(game_id)
        if not b_data:
            continue

        team = str(game.get("TEAM_ABBREVIATION", ""))
        if not team or team == "0" or team == "0.0":
            matchup = str(game.get("MATCHUP", ""))
            if " @ " in matchup:
                team = matchup.split(" @ ")[0]
            elif " vs. " in matchup:
                team = matchup.split(" vs. ")[0]

        game["margin"] = b_data.get("margins", {}).get(team, 0)

        team_dnps = []
        for dnp in b_data.get("dnps", []):
            if dnp.get("team") != team:
                continue
            dnp_pid = matcher.match_player(dnp["name"], team, [])
            d_stats = stats_map.get(dnp_pid) if dnp_pid else None
            if not d_stats:
                d_stats = {k: 0.0 for k in DNP_STAT_KEYS}
            team_dnps.append({
                "name": dnp["name"],
                "reason": dnp.get("reason"),
                "stats": d_stats,
                "id": dnp_pid,
            })
        game["dnps"] = team_dnps


def build_rows(season, csv_path, boxscores_path=None):
    df = pd.read_csv(csv_path)
    if "PLAYER_ID" not in df.columns:
        raise SystemExit(f"{csv_path} has no PLAYER_ID column")

    if SORT_KEY in df.columns:
        # DESCENDING (newest game first). Verified against both the live
        # `players.game_log` and the existing 2024-25 archive rows, which are
        # newest-first. The frontend's "last N games" takes from the head of the
        # list, so ascending order would silently show each player's FIRST N
        # games of the season instead of their most recent.
        df = df.sort_values(SORT_KEY, ascending=False)

    boxscores_data = load_boxscores(boxscores_path)
    stats_map = build_dnp_stats_map(df) if boxscores_data else {}
    matcher = None
    if boxscores_data:
        # gamelogs_<season>.csv has no player-name column (PLAYER_ID only), so
        # the DNP name->id matcher needs a name->id lookup covering everyone
        # who played in this (possibly long-past) season -- including anyone
        # since retired or out of the league, whom a "current roster" source
        # would drop. get_full_player_history is exactly that: the league's
        # full all-time player list, not filtered by current activity.
        roster = get_full_player_history()
        if not roster:
            print(f"  WARNING: no player history available; DNP names will not resolve to IDs.")
        matcher = PlayerMatcher(roster)

    rows = []
    total_games = 0
    for player_id, group in df.groupby("PLAYER_ID", sort=True):
        games = [
            {col: clean(val) for col, val in record.items()}
            for record in group.to_dict(orient="records")
        ]
        if boxscores_data:
            attach_dnps(games, boxscores_data, stats_map, matcher)
        total_games += len(games)
        rows.append({
            "player_id": int(player_id),
            "season": season,
            "game_log": games,
        })
    return rows, total_games, list(df.columns)


RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504,
                             520, 521, 522, 523, 524, 525, 526, 530})
MAX_CHUNK_ATTEMPTS = 8


def upsert(rows, url, key):
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    endpoint = f"{url}/rest/v1/{TABLE}?on_conflict=player_id,season"
    written = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        body = json.dumps(chunk, allow_nan=False)

        # Transient 5xx/520s and raw connection timeouts happen right after a
        # project restart or under pool pressure -- not a sign the data
        # itself is bad -- so retry a few times with backoff before giving
        # up on this chunk. A timeout raises before any response/status code
        # exists, so it needs its own except clause, not just a status check.
        for attempt in range(1, MAX_CHUNK_ATTEMPTS + 1):
            try:
                resp = requests.post(endpoint, headers=headers, data=body, timeout=180)
            except requests.exceptions.RequestException as exc:
                if attempt == MAX_CHUNK_ATTEMPTS:
                    raise SystemExit(
                        f"upsert failed at rows {i}-{i + len(chunk)}: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                delay = min(2 ** attempt, 30)
                print(f"\n  chunk {i}-{i + len(chunk)}: {type(exc).__name__}, "
                      f"retrying in {delay}s (attempt {attempt}/{MAX_CHUNK_ATTEMPTS})")
                time.sleep(delay)
                continue

            if resp.status_code in (200, 201, 204):
                break
            if resp.status_code not in RETRY_STATUSES or attempt == MAX_CHUNK_ATTEMPTS:
                raise SystemExit(
                    f"upsert failed at rows {i}-{i + len(chunk)}: "
                    f"HTTP {resp.status_code} {resp.text[:400]}"
                )
            delay = min(2 ** attempt, 30)
            print(f"\n  chunk {i}-{i + len(chunk)}: HTTP {resp.status_code}, "
                  f"retrying in {delay}s (attempt {attempt}/{MAX_CHUNK_ATTEMPTS})")
            time.sleep(delay)

        written += len(chunk)
        print(f"  upserted {written}/{len(rows)} players", end="\r", flush=True)
    print(f"  upserted {written}/{len(rows)} players    ")
    return written


def verify(season, expected_players, expected_games, url, key):
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    resp = requests.get(
        f"{url}/rest/v1/{TABLE}?select=player_id,game_log&season=eq.{season}",
        headers=headers, timeout=300,
    )
    if resp.status_code != 200:
        raise SystemExit(f"verify failed: HTTP {resp.status_code} {resp.text[:300]}")
    stored = resp.json()
    games = sum(len(r.get("game_log") or []) for r in stored)
    print(f"\nverify  season={season}")
    print(f"  players in DB : {len(stored):>6}   (expected {expected_players})")
    print(f"  games in DB   : {games:>6}   (expected {expected_games})")
    ok = len(stored) == expected_players and games == expected_games
    print("  " + ("MATCH" if ok else "MISMATCH"))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", required=True, help="e.g. 2025-26")
    ap.add_argument("--csv", help="defaults to backend/data/current/gamelogs_<season>.csv")
    ap.add_argument(
        "--boxscores",
        help=(
            "Path to that season's boxscores.json (margins + DNPs), used to attach "
            "a `dnps` list to each game row like the live aggregator does. Defaults "
            "to backend/data/archive/season_<season>/boxscores.json if present; "
            "omit/missing means archived rows get no `dnps` field, same as today."
        ),
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true", help="only re-check what is stored")
    args = ap.parse_args()

    url, key = env("SUPABASE_URL"), env("SUPABASE_SERVICE_KEY")
    csv_path = Path(args.csv) if args.csv else BACKEND / "data" / "current" / f"gamelogs_{args.season}.csv"
    boxscores_path = Path(args.boxscores) if args.boxscores else ARCHIVE / f"season_{args.season}" / "boxscores.json"
    if not boxscores_path.exists():
        boxscores_path = None

    if args.verify:
        rows, total_games, _ = build_rows(args.season, csv_path, boxscores_path)
        return 0 if verify(args.season, len(rows), total_games, url, key) else 1

    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    print(f"season : {args.season}")
    print(f"source : {csv_path}")
    print(f"boxscores: {boxscores_path or '(none found -- dnps will be omitted)'}")
    rows, total_games, columns = build_rows(args.season, csv_path, boxscores_path)
    payload_mb = len(json.dumps(rows, allow_nan=False)) / 1048576
    print(f"  players     : {len(rows)}")
    print(f"  games       : {total_games}")
    print(f"  per-game cols: {len(columns)}")
    print(f"  payload     : {payload_mb:.1f} MB")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        sample = rows[0]
        print(f"sample player_id={sample['player_id']} games={len(sample['game_log'])}")
        return 0

    print(f"\nupserting into {TABLE} (on_conflict=player_id,season) ...")
    upsert(rows, url, key)
    return 0 if verify(args.season, len(rows), total_games, url, key) else 1


if __name__ == "__main__":
    sys.exit(main())
