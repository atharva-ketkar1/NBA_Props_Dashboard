import json
import logging
import os
from datetime import datetime

from utils.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


def upsert_line_movements_from_file(line_movements_path: str):
    """Mirror the current line movement snapshot blob into Supabase."""
    if not os.path.exists(line_movements_path):
        logger.warning("line movements file not found: %s", line_movements_path)
        return

    with open(line_movements_path, "r") as f:
        lm_data = json.load(f)

    date_str = lm_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    get_supabase_client().table("line_movements").upsert(
        {
            "game_date": date_str,
            "snapshots": lm_data.get("snapshots", []),
        },
        on_conflict="game_date",
    ).execute()


def upsert_historical_odds_from_file(historical_odds_path: str, game_date: str):
    """Mirror one game date from the local historical archive into Supabase rows."""
    if not os.path.exists(historical_odds_path):
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return

    with open(historical_odds_path, "r") as f:
        historical_odds = json.load(f)

    date_blob = historical_odds.get(game_date, {})
    if not isinstance(date_blob, dict) or not date_blob:
        logger.info("No historical odds found for %s", game_date)
        return

    rows = []
    for player_id, record in date_blob.items():
        if not isinstance(record, dict):
            continue
        rows.append(
            {
                "player_id": int(player_id),
                "player_name": record.get("name"),
                "team": record.get("team"),
                "game_date": game_date,
                "props": record.get("props", {}),
                "source": record.get("source"),
                "captured_at": record.get("captured_at"),
            }
        )

    if not rows:
        logger.info("No historical odds rows prepared for %s", game_date)
        return

    get_supabase_client().table("historical_odds").upsert(
        rows,
        on_conflict="player_id,game_date",
    ).execute()
