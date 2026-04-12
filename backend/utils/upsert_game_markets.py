import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logging_utils import log_status
from utils.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

GAME_MARKETS_TABLE = "game_markets_current"


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_missing_relation_error(error: Exception, table_name: str) -> bool:
    message = str(error).lower()
    table_name = str(table_name).lower()
    return (
        ("relation" in message and "does not exist" in message and table_name in message)
        or ("could not find the table" in message and table_name in message)
        or ("schema cache" in message and table_name in message)
    )


def build_game_market_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    source = str(payload.get("source") or "action_network").strip() or "action_network"
    query_date = str(payload.get("query_date") or "").strip() or None
    generated_at = str(payload.get("generated_at") or "").strip() or None
    games = payload.get("games") if isinstance(payload.get("games"), list) else []

    rows = []
    for game in games:
        if not isinstance(game, dict):
            continue
        game_id = str(game.get("game_id") or "").strip()
        if not game_id:
            continue

        rows.append(
            {
                "game_id": game_id,
                "game_date": game.get("game_date") or query_date,
                "matchup": game.get("matchup"),
                "away_team_id": _safe_int(game.get("away_team_id")),
                "away_team_tricode": game.get("away_team_tricode"),
                "away_team_name": game.get("away_team_name"),
                "home_team_id": _safe_int(game.get("home_team_id")),
                "home_team_tricode": game.get("home_team_tricode"),
                "home_team_name": game.get("home_team_name"),
                "game_time_utc": game.get("game_time_utc"),
                "game_time_et": game.get("game_time_et"),
                "closing_scrape_deadline": game.get("closing_scrape_deadline"),
                "action_network_game_id": _safe_int(game.get("action_network_game_id")),
                "has_action_network_markets": bool(game.get("has_action_network_markets")),
                "markets": game.get("markets") if isinstance(game.get("markets"), dict) else {},
                "source": source,
                "source_query_date": query_date,
                "source_generated_at": generated_at,
                "updated_at": datetime.now().isoformat(),
            }
        )
    return rows


def upsert_game_markets_from_file(action_network_path: str) -> bool:
    if not os.path.exists(action_network_path):
        logger.warning("Action Network artifact not found: %s", action_network_path)
        return False

    try:
        with open(action_network_path, "r") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("Could not read Action Network artifact %s: %s", action_network_path, exc)
        return False

    rows = build_game_market_rows(payload)
    if not rows:
        log_status(logger, "SKIP", "No Action Network game markets rows prepared")
        return True

    try:
        sb = get_supabase_client()
        sb.table(GAME_MARKETS_TABLE).upsert(rows, on_conflict="game_id").execute()
        log_status(logger, "OK", "game_markets_current sync complete", rows=len(rows))
        return True
    except Exception as exc:
        if _is_missing_relation_error(exc, GAME_MARKETS_TABLE):
            log_status(
                logger,
                "WARN",
                "game_markets_current sync skipped; table missing",
                table=GAME_MARKETS_TABLE,
            )
            return True
        log_status(logger, "WARN", "game_markets_current sync failed", error=exc)
        return False
