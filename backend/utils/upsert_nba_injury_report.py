import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from utils.logging_utils import log_status
from utils.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

NBA_INJURY_REPORT_TABLE = "nba_injury_reports_current"


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


def build_nba_injury_report_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    source = str(payload.get("source") or "nba_official_injury_report").strip()
    source = source or "nba_official_injury_report"
    query_date = str(payload.get("query_date") or "").strip() or None
    generated_at = str(payload.get("generated_at") or "").strip() or None
    report_timestamp_et = str(payload.get("report_timestamp_et") or "").strip() or None
    report_page_url = str(payload.get("report_page_url") or "").strip() or None
    report_pdf_url = str(payload.get("report_pdf_url") or "").strip() or None
    games = payload.get("games") if isinstance(payload.get("games"), list) else []

    rows = []
    for game in games:
        if not isinstance(game, dict):
            continue
        game_id = str(game.get("game_id") or "").strip()
        if not game_id:
            continue

        teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
        player_row_count = sum(
            len(team_payload.get("players") or [])
            for team_payload in teams.values()
            if isinstance(team_payload, dict)
        )
        not_submitted_team_count = sum(
            1
            for team_payload in teams.values()
            if isinstance(team_payload, dict)
            and team_payload.get("report_status") == "not_submitted"
        )

        rows.append(
            {
                "game_id": game_id,
                "game_date": game.get("game_date") or query_date,
                "matchup": game.get("matchup") or game.get("report_matchup"),
                "report_matchup": game.get("report_matchup"),
                "away_team_tricode": game.get("away_team_tricode"),
                "away_team_name": game.get("away_team_name"),
                "home_team_tricode": game.get("home_team_tricode"),
                "home_team_name": game.get("home_team_name"),
                "game_time_utc": game.get("game_time_utc"),
                "game_time_et": game.get("game_time_et"),
                "closing_scrape_deadline": game.get("closing_scrape_deadline"),
                "has_injury_report": bool(teams),
                "player_row_count": _safe_int(player_row_count) or 0,
                "not_submitted_team_count": _safe_int(not_submitted_team_count) or 0,
                "teams": teams,
                "source": source,
                "source_query_date": query_date,
                "report_timestamp_et": report_timestamp_et,
                "report_page_url": report_page_url,
                "report_pdf_url": report_pdf_url,
                "source_generated_at": generated_at,
                "updated_at": datetime.now().isoformat(),
            }
        )
    return rows


def upsert_nba_injury_report_from_file(injury_report_path: str) -> bool:
    if not os.path.exists(injury_report_path):
        logger.warning("NBA injury report artifact not found: %s", injury_report_path)
        return False

    try:
        with open(injury_report_path, "r") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("Could not read NBA injury report artifact %s: %s", injury_report_path, exc)
        return False

    rows = build_nba_injury_report_rows(payload)
    if not rows:
        log_status(logger, "SKIP", "No NBA injury report rows prepared")
        return True

    try:
        sb = get_supabase_client()
        sb.table(NBA_INJURY_REPORT_TABLE).upsert(rows, on_conflict="game_id").execute()
        log_status(logger, "OK", "nba_injury_reports_current sync complete", rows=len(rows))
        return True
    except Exception as exc:
        if _is_missing_relation_error(exc, NBA_INJURY_REPORT_TABLE):
            log_status(
                logger,
                "WARN",
                "nba_injury_reports_current sync skipped; table missing",
                table=NBA_INJURY_REPORT_TABLE,
            )
            return True
        log_status(logger, "WARN", "nba_injury_reports_current sync failed", error=exc)
        return False
