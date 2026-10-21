import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List

from utils.logging_utils import log_status
from utils.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

TABLE = "game_markets_closing"


def _is_missing_relation_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        ("relation" in message and "does not exist" in message and TABLE in message)
        or ("could not find the table" in message and TABLE in message)
        or ("schema cache" in message and TABLE in message)
    )


def capture_closing_game_markets(game_ids: Iterable[str]) -> bool:
    """Snapshot game_markets_current (spread/total/moneyline) for the given
    game_ids into game_markets_closing -- once per game, right around tip-off.

    This is intentionally a single-row-per-game closing snapshot, not a running
    history: game lines rarely move outside of a major injury/news event, so
    continuous intraday snapshotting wouldn't add value 99% of the time and
    would add real recurring write I/O. Called from master_cron's existing
    pre-tip closing-line window, so it adds no new fetch and no new cron cadence.
    """
    normalized_ids = sorted({str(gid).strip() for gid in game_ids if str(gid or "").strip()})
    if not normalized_ids:
        return True

    try:
        sb = get_supabase_client()
        resp = (
            sb.table("game_markets_current")
            .select("*")
            .in_("game_id", normalized_ids)
            .execute()
        )
        current_rows: List[Dict[str, Any]] = resp.data or []
    except Exception as exc:
        log_status(logger, "WARN", "Could not read game_markets_current for closing capture", error=exc)
        return False

    if not current_rows:
        log_status(logger, "SKIP", "No current game markets to capture as closing lines", game_ids=normalized_ids)
        return True

    captured_at = datetime.now().isoformat()
    rows = []
    for row in current_rows:
        if not row.get("markets"):
            continue
        rows.append(
            {
                "game_id": row.get("game_id"),
                "game_date": row.get("game_date"),
                "matchup": row.get("matchup"),
                "away_team_id": row.get("away_team_id"),
                "away_team_tricode": row.get("away_team_tricode"),
                "away_team_name": row.get("away_team_name"),
                "home_team_id": row.get("home_team_id"),
                "home_team_tricode": row.get("home_team_tricode"),
                "home_team_name": row.get("home_team_name"),
                "game_time_utc": row.get("game_time_utc"),
                "game_time_et": row.get("game_time_et"),
                "markets": row.get("markets"),
                "source": row.get("source"),
                "captured_at": captured_at,
            }
        )

    if not rows:
        log_status(logger, "SKIP", "No game markets with odds to capture as closing lines")
        return True

    try:
        sb.table(TABLE).upsert(rows, on_conflict="game_id").execute()
        log_status(logger, "OK", "Closing game markets captured", rows=len(rows))
        return True
    except Exception as exc:
        if _is_missing_relation_error(exc):
            log_status(logger, "WARN", "game_markets_closing sync skipped; table missing", table=TABLE)
            return True
        log_status(logger, "WARN", "Closing game markets upsert failed", error=exc)
        return False
