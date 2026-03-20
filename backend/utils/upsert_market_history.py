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
        return False

    with open(line_movements_path, "r") as f:
        lm_data = json.load(f)

    default_date = lm_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    snapshots_by_date = {}

    for snapshot in lm_data.get("snapshots", []):
        players = snapshot.get("players", {}) if isinstance(snapshot, dict) else {}
        dated_players = {}

        for player_id, pdata in players.items():
            game_date = (pdata or {}).get("game_date") or default_date
            dated_players.setdefault(game_date, {})[player_id] = pdata

        for game_date, players_blob in dated_players.items():
            snapshots_by_date.setdefault(game_date, []).append({
                "timestamp": snapshot.get("timestamp"),
                "label": snapshot.get("label"),
                "players": players_blob,
            })

    rows = [
        {"game_date": game_date, "snapshots": snapshots}
        for game_date, snapshots in snapshots_by_date.items()
    ]

    if not rows:
        rows = [{"game_date": default_date, "snapshots": lm_data.get("snapshots", [])}]

    get_supabase_client().table("line_movements").upsert(
        rows,
        on_conflict="game_date",
    ).execute()
    return True


def upsert_historical_odds_from_file(historical_odds_path: str, game_date: str):
    """Mirror one game date from the local historical archive into Supabase rows."""
    if not os.path.exists(historical_odds_path):
        logger.warning("historical odds file not found: %s", historical_odds_path)
        return False

    with open(historical_odds_path, "r") as f:
        historical_odds = json.load(f)

    date_blob = historical_odds.get(game_date, {})
    if not isinstance(date_blob, dict) or not date_blob:
        logger.info("No historical odds found for %s", game_date)
        return False

    rows = []
    skipped_keys = []
    for player_id, record in date_blob.items():
        if not isinstance(record, dict):
            continue
        try:
            normalized_player_id = int(player_id)
        except (TypeError, ValueError):
            skipped_keys.append(str(player_id))
            continue
        rows.append(
            {
                "player_id": normalized_player_id,
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
        return False

    if skipped_keys:
        logger.warning(
            "Skipping %d historical odds rows with unresolved player ids for %s: %s",
            len(skipped_keys),
            game_date,
            ", ".join(skipped_keys[:10]),
        )

    get_supabase_client().table("historical_odds").upsert(
        rows,
        on_conflict="player_id,game_date",
    ).execute()
    return True
