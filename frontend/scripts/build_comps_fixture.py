#!/usr/bin/env python3
"""Rebuild the committed comparable-player fixture.

    cd frontend && python3 scripts/build_comps_fixture.py

Why a committed fixture exists at all: `backend/data` is gitignored, so
`master_feed.json` can never be present on a CI runner, and
`verify_similar_players.ts` would otherwise have nothing to check. The
alternative was to skip the check in CI, which would have made it green
without testing anything -- the ranking would be unguarded exactly where a
regression is most likely to slip through (someone else's push).

What is kept, and what is deliberately dropped:

  kept    id, name, team, position, height_inches, weight_lbs, stats subset,
          play_type_analysis, shot_type_analysis.player, opponents_played
  dropped game_log, historical_odds, props, props_by_date

The dropped fields are the large ones, and the ranking does not read them --
`rankSimilarPlayers` scores on the archetype vector plus the opponent list.
Dropping `game_log` has a useful side effect: `getOpponentsFaced` then has to
fall back to `opponents_played`, which is exactly the DB-mode code path, so
the fixture exercises the shape production actually serves rather than the
local JSON one.

Only players above the comp floor (MIN >= 12, GP >= 5) are included, since
nothing below it can be a candidate. That is what keeps this ~330KB rather
than ~18MB.

Regenerate when: the archetype feature set changes, the comp floor changes, or
the labelled sets in verify_similar_players.ts are updated to players not in
the current pool. After regenerating, re-run the verifier and update the
floors there if the numbers legitimately moved -- do not regenerate simply to
make a failing check pass, which would be deleting the evidence.
"""

import json
import os
import sys
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = FRONTEND_DIR.parent / "backend" / "data" / "current" / "master_feed.json"
OUTPUT = FRONTEND_DIR / "scripts" / "fixtures" / "comps_pool.json"

# Every stat the archetype vector reads, and nothing else.
STAT_KEYS = [
    "MIN", "GP", "PTS", "AST", "REB", "FG3A", "FG3M", "FGA", "FTA",
    "DRIVES", "POTENTIAL_AST", "STL", "BLK", "TOV", "USG_PCT", "REB_CHANCES",
]

# Mirrors MIN_MINUTES_FOR_COMP / MIN_GAMES_FOR_COMP in similarPlayers.ts.
MIN_MINUTES = 12
MIN_GAMES = 5


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opponents(player):
    """Distinct opponent tricodes from the game log, as the aggregator emits."""
    seen = set()
    for game in player.get("game_log") or []:
        matchup = str((game or {}).get("MATCHUP") or "").strip()
        if not matchup:
            continue
        token = matchup.split()[-1].strip().upper()
        if token.isalpha() and len(token) <= 4:
            seen.add(token)
    return sorted(seen)


def main():
    source = Path(os.environ.get("MASTER_FEED_PATH") or DEFAULT_SOURCE)
    if not source.exists():
        print(
            f"source feed not found at {source}\n"
            "Run the pipeline or `python3 backend/scripts/sync_all_data.py` first, "
            "or set MASTER_FEED_PATH.",
            file=sys.stderr,
        )
        return 2

    players = json.loads(source.read_text())
    slim = []
    missing_size = 0

    for player in players:
        stats = player.get("stats") or {}
        minutes = _num(stats.get("MIN")) or 0
        games = _num(stats.get("GP")) or 0
        if minutes < MIN_MINUTES or games < MIN_GAMES:
            continue
        if not player.get("height_inches"):
            missing_size += 1

        slim.append({
            "id": player["id"],
            "name": player["name"],
            "team": player.get("team"),
            "position": player.get("position"),
            "height_inches": player.get("height_inches"),
            "weight_lbs": player.get("weight_lbs"),
            "opponents_played": _opponents(player),
            "stats": {k: stats[k] for k in STAT_KEYS if stats.get(k) is not None},
            "play_type_analysis": player.get("play_type_analysis") or [],
            "shot_type_analysis": {
                "player": (player.get("shot_type_analysis") or {}).get("player") or {},
            },
            # Present and empty on purpose -- see the module docstring.
            "game_log": [],
            "props": {},
            "props_by_date": {},
            "historical_odds": {},
        })

    # Refuse rather than warn. This already happened once: regenerating from a
    # local feed that predated the measurements migration produced a fixture
    # with no height at all, which would have quietly replaced a good one.
    # Height is the largest single contributor to the ranking, so a pool
    # without it does not fail the verifier loudly -- it just makes every
    # comparison worse, and the recorded floors would be re-tuned downward to
    # match a fixture that was broken rather than an algorithm that regressed.
    if slim and missing_size > len(slim) * 0.5:
        print(
            f"refusing to write: {missing_size}/{len(slim)} players have no "
            f"height_inches.\n"
            f"The source feed at {source} predates the measurements migration, "
            f"so this fixture would silently under-test the ranking.\n"
            f"Sync fresh data (python3 backend/scripts/sync_all_data.py) and "
            f"retry, or set ALLOW_SIZELESS_FIXTURE=1 if you genuinely want a "
            f"size-blind pool.",
            file=sys.stderr,
        )
        if not os.environ.get("ALLOW_SIZELESS_FIXTURE"):
            return 1

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(slim, separators=(",", ":")))

    size_kb = OUTPUT.stat().st_size / 1024
    print(f"wrote {OUTPUT.relative_to(FRONTEND_DIR)}: {len(slim)} players, {size_kb:.0f} KB")
    with_size = len(slim) - missing_size
    print(f"  height present for {with_size}/{len(slim)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
