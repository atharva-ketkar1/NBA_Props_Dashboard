import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set
from zoneinfo import ZoneInfo

import pandas as pd

try:
    from utils.player_matcher import PlayerMatcher
except ModuleNotFoundError:  # pragma: no cover - repo-root import fallback
    from backend.utils.player_matcher import PlayerMatcher

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STATS_PATH = BASE_DIR / "data" / "current" / "season_stats.csv"
DEFAULT_ARCHIVE_DIR = BASE_DIR / "data" / "archive" / "prizepicks"
ET_ZONE = ZoneInfo("America/New_York")

PP_PROP_MAP = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "threes": "FG3M",
    "blocks": "BLK",
    "steals": "STL",
    "pra": "PTS+REB+AST",
    "pr": "PTS+REB",
    "pa": "PTS+AST",
    "ra": "REB+AST",
    "stocks": "STL+BLK",
}


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)


def _load_matcher(stats_path: Path) -> PlayerMatcher:
    df_stats = pd.read_csv(stats_path, usecols=["PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION"])
    stats_records = df_stats.to_dict("records")
    return PlayerMatcher(stats_records)


def _merge_props_tree(existing_props: Any, incoming_props: Any) -> Dict[str, Any]:
    existing = existing_props if isinstance(existing_props, dict) else {}
    incoming = incoming_props if isinstance(incoming_props, dict) else {}
    merged = dict(existing)

    for stat_key, book_map in incoming.items():
        if not isinstance(book_map, dict):
            continue
        merged_books = dict(merged.get(stat_key) or {})
        for book_key, line in book_map.items():
            if not isinstance(line, dict):
                continue
            merged_books[book_key] = {
                **(merged_books.get(book_key) or {}),
                **line,
            }
        merged[stat_key] = merged_books

    return merged


def _merge_record(existing_record: Any, incoming_record: Dict[str, Any]) -> Dict[str, Any]:
    existing = existing_record if isinstance(existing_record, dict) else {}
    return {
        "name": incoming_record.get("name") or existing.get("name"),
        "team": incoming_record.get("team") or existing.get("team"),
        "game_id": incoming_record.get("game_id") or existing.get("game_id"),
        "props": _merge_props_tree(existing.get("props", {}), incoming_record.get("props", {})),
        "source": incoming_record.get("source") or existing.get("source"),
        "captured_at": incoming_record.get("captured_at") or existing.get("captured_at"),
    }


def archive_prizepicks_rows(
    rows: List[Dict[str, Any]],
    *,
    allowed_game_ids: Optional[Iterable[str]] = None,
    archive_dir: Path = DEFAULT_ARCHIVE_DIR,
    stats_path: Path = DEFAULT_STATS_PATH,
    captured_at: Optional[str] = None,
) -> Dict[str, int]:
    matcher = _load_matcher(stats_path)
    allowed_ids: Set[str] = {str(game_id) for game_id in (allowed_game_ids or []) if game_id}
    captured_at_value = captured_at or datetime.now(ET_ZONE).isoformat()
    grouped_rows: Dict[str, Dict[str, Dict[str, Any]]] = {}

    diagnostics = {
        "rows_seen": 0,
        "rows_archived": 0,
        "dates_written": 0,
        "skipped_not_target_game": 0,
        "skipped_missing_game_date": 0,
        "skipped_missing_line": 0,
        "skipped_unmatched_player": 0,
        "skipped_unsupported_prop": 0,
    }

    for row in rows or []:
        diagnostics["rows_seen"] += 1

        game_date = str(row.get("game_date") or "").strip()
        if not game_date:
            diagnostics["skipped_missing_game_date"] += 1
            continue

        game_id = str(row.get("game_id") or "").strip()
        if allowed_ids and game_id not in allowed_ids:
            diagnostics["skipped_not_target_game"] += 1
            continue

        line = row.get("line")
        if line in (None, ""):
            diagnostics["skipped_missing_line"] += 1
            continue

        prop_key = PP_PROP_MAP.get(str(row.get("prop_type") or "").strip().lower())
        if not prop_key:
            diagnostics["skipped_unsupported_prop"] += 1
            continue

        raw_player_name = str(row.get("raw_player_name") or row.get("player") or "").strip()
        team = str(row.get("team") or "").strip()
        player_id = matcher.match_player(raw_player_name, team)
        if not player_id:
            diagnostics["skipped_unmatched_player"] += 1
            continue

        player_key = str(player_id)
        date_bucket = grouped_rows.setdefault(game_date, {})
        player_record = date_bucket.setdefault(
            player_key,
            {
                "name": raw_player_name,
                "team": team,
                "game_id": game_id,
                "props": {},
                "source": "prizepicks_closing_line",
                "captured_at": captured_at_value,
            },
        )

        player_record["props"].setdefault(prop_key, {})
        player_record["props"][prop_key]["pp"] = {
            "line": float(line),
            "over": row.get("over_odds"),
            "under": row.get("under_odds"),
        }
        diagnostics["rows_archived"] += 1

    for game_date, date_rows in grouped_rows.items():
        archive_path = archive_dir / f"{game_date}.json"
        existing = _read_json(archive_path)
        merged = dict(existing)

        for player_id, incoming_record in date_rows.items():
            merged[player_id] = _merge_record(existing.get(player_id), incoming_record)

        _write_json(archive_path, merged)
        diagnostics["dates_written"] += 1

    return diagnostics
