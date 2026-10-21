"""
roster_sync.py
Stamp current-roster membership onto the Supabase `players` table.

Why this exists
---------------
`players` is upsert-only: the daily pipeline writes every player it sees and
nothing is ever removed. So a player who retired last season stays in the live
bootstrap payload forever, and the row count only grows.

Deleting them is not an option. Archive seasons are served by swapping only a
player's game_log (see frontend/constants.ts) -- identity still comes from
`players` -- so a deleted retiree also disappears from archive browsing, and
their player_props / historical_player_props / archive_gamelogs rows are
orphaned.

So membership is stamped instead. commonallplayers with IsOnlyCurrentSeason=1
(via utils/player_index.py) returns exactly the set we want: every player on an
NBA roster for the resolved season, including rookies who have not played a
minute, excluding anyone who retired before it. Every player in that list gets
`roster_season` set to the current season. Nobody else is touched, so retirees
age out by construction -- there is no "delete retirees" step at all, and a
player who retires *mid*-season stays active until rollover, which is what the
historical half of the slate needs.

Safety
------
The sweep is only as trustworthy as the index behind it, and `get_player_index`
deliberately degrades to a stale cache or an empty list rather than raising. If
a thin or wrong-season list were stamped, the *unstamped* remainder -- i.e. most
of the league -- would read as off-roster in one run. The guards below exist for
that single failure mode, and every one of them skips the sweep rather than
writing a partial result:

  * nothing usable from the index (source "empty")
  * a cache written for a different season than the one being stamped
  * fewer than MIN_ROSTER_ROWS players (an NBA roster set is ~500)
  * a list that would shrink an existing active set by more than
    (1 - MIN_RETAINED_FRACTION) -- this is the September case, where
    resolve_season() has already rolled over but commonallplayers for the
    unstarted season can still be legitimately thin
"""

import logging
import os
from datetime import datetime, timezone

from utils.logging_utils import log_status

logger = logging.getLogger("RosterSync")

# An NBA roster set is ~500 players (519 measured on 2026-10-20). Anything far
# below that is a partial upstream response, not a league that shrank.
MIN_ROSTER_ROWS = max(1, int(os.getenv("ROSTER_SYNC_MIN_ROWS", "400")))

# How much of an already-stamped active set a new list is allowed to drop.
MIN_RETAINED_FRACTION = min(
    1.0,
    max(0.0, float(os.getenv("ROSTER_SYNC_MIN_RETAINED_FRACTION", "0.8"))),
)

# Ids per UPDATE. Keeps the ?id=in.(...) query string well clear of URL limits.
UPDATE_CHUNK_SIZE = max(1, int(os.getenv("ROSTER_SYNC_CHUNK_SIZE", "200")))


def _skip(reason, **fields):
    log_status(logger, "WARN", "Roster membership sync skipped", reason=reason, **fields)
    return {"ok": False, "skipped": True, "reason": reason, "stamped": 0, **fields}


def _count_active(sb, season):
    """How many rows already carry this season. None if the count is unavailable."""
    try:
        response = (
            sb.table("players")
            .select("id", count="exact", head=True)
            .eq("roster_season", season)
            .execute()
        )
        return response.count
    except Exception as exc:
        logger.warning("Could not count currently-active players: %s", exc)
        return None


def sync_roster_membership(season=None, dry_run=False, index_result=None, client=None):
    """Stamp `roster_season` on every player currently on an NBA roster.

    Returns a summary dict; never raises. Call it after the aggregator's player
    upsert, so the rows being stamped already exist.
    """
    from scrapers.season_type import resolve_season
    from utils.player_index import get_player_index_result

    resolved_season = season or resolve_season()

    if index_result is None:
        index_result = get_player_index_result(season=resolved_season)

    records = index_result.get("players") or []
    source = index_result.get("source")
    cache_season = index_result.get("cache_season")

    if source == "empty" or not records:
        return _skip("player_index_unavailable", season=resolved_season)

    # A cache from a previous season describes last year's league. Stamping it
    # would mark this season's rookies off-roster and keep retirees on it.
    if source == "cache" and cache_season and cache_season != resolved_season:
        return _skip(
            "player_index_cache_season_mismatch",
            season=resolved_season,
            cache_season=cache_season,
        )

    if len(records) < MIN_ROSTER_ROWS:
        return _skip(
            "player_index_too_small",
            season=resolved_season,
            players=len(records),
            min_rows=MIN_ROSTER_ROWS,
        )

    player_ids = sorted({
        int(record["PLAYER_ID"])
        for record in records
        if record.get("PLAYER_ID") is not None
    })
    if not player_ids:
        return _skip("player_index_had_no_ids", season=resolved_season)

    if client is None:
        try:
            from utils.supabase_client import get_supabase_client
            client = get_supabase_client()
        except Exception as exc:
            return _skip("supabase_unavailable", season=resolved_season, error=str(exc))

    already_active = _count_active(client, resolved_season)
    if (
        already_active
        and len(player_ids) < already_active * MIN_RETAINED_FRACTION
    ):
        return _skip(
            "player_index_would_shrink_active_set",
            season=resolved_season,
            players=len(player_ids),
            already_active=already_active,
            min_retained_fraction=MIN_RETAINED_FRACTION,
        )

    if dry_run:
        log_status(
            logger,
            "OK",
            "Roster membership sync (dry run)",
            season=resolved_season,
            source=source,
            players=len(player_ids),
            already_active=already_active,
        )
        return {
            "ok": True,
            "skipped": False,
            "dry_run": True,
            "season": resolved_season,
            "stamped": 0,
            "candidates": len(player_ids),
        }

    stamp = {
        "roster_season": resolved_season,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        from postgrest.types import ReturnMethod
        returning = ReturnMethod.minimal
    except Exception:
        returning = "minimal"

    stamped = 0
    failed_chunks = 0
    for start in range(0, len(player_ids), UPDATE_CHUNK_SIZE):
        chunk = player_ids[start:start + UPDATE_CHUNK_SIZE]
        try:
            # return=minimal matters: the default echoes back every updated row,
            # and `players` rows carry game_log / zone JSONB blobs.
            (
                client.table("players")
                .update(stamp, returning=returning)
                .in_("id", chunk)
                .execute()
            )
            stamped += len(chunk)
        except Exception as exc:
            failed_chunks += 1
            logger.error(
                "Roster stamp failed for ids %d-%d: %s",
                chunk[0], chunk[-1], exc,
            )

    ok = failed_chunks == 0
    log_status(
        logger,
        "OK" if ok else "WARN",
        "Roster membership sync complete" if ok else "Roster membership sync incomplete",
        season=resolved_season,
        source=source,
        stamped=stamped,
        candidates=len(player_ids),
        already_active=already_active,
        failed_chunks=failed_chunks or None,
    )
    return {
        "ok": ok,
        "skipped": False,
        "season": resolved_season,
        "source": source,
        "stamped": stamped,
        "candidates": len(player_ids),
        "already_active": already_active,
        "failed_chunks": failed_chunks,
    }


if __name__ == "__main__":
    import argparse

    from utils.logging_utils import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description="Stamp current-roster membership on players")
    parser.add_argument("--season", help="Override the resolved season, e.g. 2026-27")
    parser.add_argument("--dry-run", action="store_true", help="Run the guards without writing")
    args = parser.parse_args()

    print(sync_roster_membership(season=args.season, dry_run=args.dry_run))
