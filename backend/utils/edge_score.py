import csv
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from utils.logging_utils import log_status
from utils.player_matcher import PlayerMatcher

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logger = logging.getLogger("EdgeScore")

ET_ZONE = ZoneInfo("America/New_York")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURRENT_DIR = os.path.join(BASE_DIR, "data", "current")
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "archive")

MASTER_FEED_PATH = os.path.join(CURRENT_DIR, "master_feed.json")
SCHEDULE_PATH = os.path.join(CURRENT_DIR, "today_schedule.json")
LINE_MOVEMENTS_PATH = os.path.join(CURRENT_DIR, "line_movements_today.json")
PRIZEPICKS_PATH = os.path.join(CURRENT_DIR, "prizepicks.csv")
EDGE_SCORE_PATH = os.path.join(CURRENT_DIR, "edge_scores_top15.json")
EDGE_SCORE_STATE_PATH = os.path.join(CURRENT_DIR, "edge_score_notification_state.json")
EDGE_SCORE_HISTORY_PATH = os.path.join(ARCHIVE_DIR, "edge_scores_history.json")

EDGE_SCORE_TABLE = "edge_scores_current"
EDGE_LIMIT = max(1, int(os.getenv("EDGE_SCORE_LIMIT", "15")))
EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS = max(
    0,
    int(os.getenv("EDGE_SCORE_NOTIFICATION_MIN_INTERVAL_SECONDS", "900")),
)
EDGE_SCORE_DISCORD_WEBHOOK_URL = os.getenv("EDGE_SCORE_DISCORD_WEBHOOK_URL", "").strip()
UNDATED_PROP_KEY = "__undated__"

SUPPORTED_BOOKS = {"dk", "fd", "pp"}
BOOK_ALIASES = {
    "dk": "dk",
    "draftkings": "dk",
    "fd": "fd",
    "fanduel": "fd",
    "pp": "pp",
    "prizepicks": "pp",
}
BOOK_LABELS = {
    "dk": "DraftKings",
    "fd": "FanDuel",
    "pp": "PrizePicks",
}
SIDE_MULTIPLIERS = {
    "over": 1.0,
    "under": -1.0,
}
COMPONENT_WEIGHTS = {
    "projection": 0.34,
    "recent_form": 0.18,
    "matchup": 0.16,
    "market": 0.12,
    "line_movement": 0.07,
    "similar_players": 0.07,
    "head_to_head": 0.04,
    "back_to_back": 0.02,
}
TOTAL_COMPONENT_WEIGHT = sum(COMPONENT_WEIGHTS.values())

STAT_PROFILES = {
    "PTS": {
        "display": "Points",
        "scale": 6.0,
        "focus": "points",
        "components": {"PTS": 1.0},
        "b2b_bias": -0.12,
    },
    "AST": {
        "display": "Assists",
        "scale": 2.8,
        "focus": "assists",
        "components": {"AST": 1.0},
        "b2b_bias": -0.08,
    },
    "REB": {
        "display": "Rebounds",
        "scale": 3.0,
        "focus": "rebounds",
        "components": {"REB": 1.0},
        "b2b_bias": -0.04,
    },
    "FG3M": {
        "display": "Threes",
        "scale": 1.6,
        "focus": "threes",
        "components": {"FG3M": 1.0},
        "b2b_bias": -0.10,
    },
    "BLK": {
        "display": "Blocks",
        "scale": 1.2,
        "focus": "stocks",
        "components": {"BLK": 1.0},
        "b2b_bias": -0.03,
    },
    "STL": {
        "display": "Steals",
        "scale": 1.2,
        "focus": "stocks",
        "components": {"STL": 1.0},
        "b2b_bias": -0.03,
    },
    "STL+BLK": {
        "display": "Stocks",
        "scale": 1.8,
        "focus": "stocks",
        "components": {"STL": 0.5, "BLK": 0.5},
        "b2b_bias": -0.03,
    },
    "PTS+REB+AST": {
        "display": "PRA",
        "scale": 7.5,
        "focus": "combo",
        "components": {"PTS": 0.45, "REB": 0.28, "AST": 0.27},
        "b2b_bias": -0.10,
    },
    "PTS+REB": {
        "display": "PR",
        "scale": 6.5,
        "focus": "combo",
        "components": {"PTS": 0.62, "REB": 0.38},
        "b2b_bias": -0.08,
    },
    "PTS+AST": {
        "display": "PA",
        "scale": 6.5,
        "focus": "combo",
        "components": {"PTS": 0.62, "AST": 0.38},
        "b2b_bias": -0.10,
    },
    "REB+AST": {
        "display": "RA",
        "scale": 4.8,
        "focus": "combo",
        "components": {"REB": 0.55, "AST": 0.45},
        "b2b_bias": -0.06,
    },
}
PLAY_TYPE_LABELS = [
    "Free Throws",
    "Post Up",
    "PNR Roll Man",
    "Putback",
    "Spot Up",
    "Cut",
    "Isolation",
    "Transition",
    "PNR Ball Handler",
    "Handoff",
    "Off Screen",
    "Misc",
]
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
PP_LABEL_PROP_MAP = {
    "3PTM": "FG3M",
    "3-PT MADE": "FG3M",
    "ASSISTS": "AST",
    "BLKS+STLS": "STL+BLK",
    "BLOCKED SHOTS": "BLK",
    "BLOCKS": "BLK",
    "POINTS": "PTS",
    "PRA": "PTS+REB+AST",
    "PTS+ASTS": "PTS+AST",
    "PTS+REBS": "PTS+REB",
    "PTS+REBS+ASTS": "PTS+REB+AST",
    "RA": "REB+AST",
    "REBOUNDS": "REB",
    "REBS+ASTS": "REB+AST",
    "STEALS": "STL",
    "STEALS+BLOCKS": "STL+BLK",
}


def get_et_now() -> datetime:
    return datetime.now(ET_ZONE)


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.warning("Could not load JSON from %s: %s", path, exc)
        return default


def _write_json_atomic(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w") as handle:
        json.dump(payload, handle, indent=2, default=str)
    os.replace(temp_path, path)


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _average(values: List[Optional[float]]) -> Optional[float]:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def _normalize_book_key(raw_book: Any) -> str:
    return BOOK_ALIASES.get(str(raw_book or "").strip().lower(), "")


def _normalize_pp_stat_type(raw_prop_type: Any, raw_prop_label: Any) -> str:
    raw_key = str(raw_prop_type or "").strip().lower()
    mapped = PP_PROP_MAP.get(raw_key)
    if mapped:
        return mapped

    if isinstance(raw_prop_label, str):
        cleaned = re.sub(r"\s+", " ", raw_prop_label).upper().strip()
        return PP_LABEL_PROP_MAP.get(cleaned, "")

    return ""


def _normalize_game_date(raw_value: Any) -> str:
    if not raw_value:
        return ""
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if len(stripped) >= 10 and stripped[4] == "-" and stripped[7] == "-":
            if len(stripped) == 10:
                return stripped
            try:
                dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                return dt.astimezone(ET_ZONE).strftime("%Y-%m-%d")
            except ValueError:
                return stripped[:10]
    return str(raw_value)


def _parse_date(raw_value: Any) -> Optional[datetime]:
    normalized = _normalize_game_date(raw_value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").replace(tzinfo=ET_ZONE)
    except ValueError:
        return None


def _parse_dt(raw_value: Any) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET_ZONE)
    return dt.astimezone(ET_ZONE)


def _american_to_implied(raw_odds: Any) -> Optional[float]:
    odds = _safe_float(raw_odds)
    if odds is None or odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def _format_odds(raw_odds: Any) -> str:
    odds = _safe_float(raw_odds)
    if odds is None:
        return "-"
    odds_int = int(round(odds))
    return f"+{odds_int}" if odds_int > 0 else str(odds_int)


def _stat_value_from_stats(stats: Dict[str, Any], stat_type: str) -> Optional[float]:
    if not isinstance(stats, dict):
        return None

    if stat_type in {"PTS", "REB", "AST", "FG3M", "BLK", "STL"}:
        return _safe_float(stats.get(stat_type))
    if stat_type == "STL+BLK":
        return _average([
            _safe_float(stats.get("STL+BLK")),
            (_safe_float(stats.get("STL"), 0.0) or 0.0) + (_safe_float(stats.get("BLK"), 0.0) or 0.0),
        ])
    if stat_type == "PTS+REB+AST":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("PTS", "REB", "AST"))
    if stat_type == "PTS+REB":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("PTS", "REB"))
    if stat_type == "PTS+AST":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("PTS", "AST"))
    if stat_type == "REB+AST":
        if stats.get(stat_type) is not None:
            return _safe_float(stats.get(stat_type))
        return sum((_safe_float(stats.get(key), 0.0) or 0.0) for key in ("REB", "AST"))
    return None


def _stat_value_from_game(game: Dict[str, Any], stat_type: str) -> Optional[float]:
    if not isinstance(game, dict):
        return None

    if stat_type in {"PTS", "REB", "AST", "FG3M", "BLK", "STL"}:
        return _safe_float(game.get(stat_type))
    if stat_type == "STL+BLK":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        steals = _safe_float(game.get("STL"), 0.0) or 0.0
        blocks = _safe_float(game.get("BLK"), 0.0) or 0.0
        return steals + blocks
    if stat_type == "PTS+REB+AST":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("PTS", "REB", "AST"))
    if stat_type == "PTS+REB":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("PTS", "REB"))
    if stat_type == "PTS+AST":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("PTS", "AST"))
    if stat_type == "REB+AST":
        if game.get(stat_type) is not None:
            return _safe_float(game.get(stat_type))
        return sum((_safe_float(game.get(key), 0.0) or 0.0) for key in ("REB", "AST"))
    return None


def _normalize_score_by_scale(value: Optional[float], scale: float) -> float:
    if value is None:
        return 0.0
    return _clamp(value / max(scale, 0.5), -1.0, 1.0)


def _parse_percent(raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    text = str(raw_value).strip().replace("%", "")
    return _safe_float(text)


def _rank_to_ease(raw_rank: Any) -> Optional[float]:
    rank = _safe_float(raw_rank)
    if rank is None:
        return None
    rank = _clamp(rank, 1.0, 30.0)
    return (rank - 15.5) / 14.5


def _side_name(side: str) -> str:
    return "Over" if side == "over" else "Under"


def _simple_position(position: Any) -> str:
    text = str(position or "").upper()
    if "G" in text:
        return "G"
    if "C" in text:
        return "C"
    if "F" in text:
        return "F"
    return "G"


def _extract_logs(player: Dict[str, Any]) -> List[Dict[str, Any]]:
    logs = player.get("game_log") if isinstance(player, dict) else []
    if not isinstance(logs, list):
        return []

    ordered = []
    for game in logs:
        if not isinstance(game, dict):
            continue
        minutes = _safe_float(game.get("MIN"), 0.0) or 0.0
        if minutes <= 0:
            continue
        game_date = _parse_date(game.get("GAME_DATE"))
        if game_date is None:
            continue
        ordered.append((game_date, game))
    ordered.sort(key=lambda item: item[0], reverse=True)
    return [game for _, game in ordered]


def _weighted_average(pairs: List[Tuple[Optional[float], float]]) -> Optional[float]:
    numerator = 0.0
    denominator = 0.0
    for value, weight in pairs:
        if value is None or weight <= 0:
            continue
        numerator += value * weight
        denominator += weight
    if denominator <= 0:
        return None
    return numerator / denominator


def _build_schedule_context(schedule_payload: Any) -> Dict[str, Any]:
    if isinstance(schedule_payload, dict):
        games = schedule_payload.get("games", [])
    elif isinstance(schedule_payload, list):
        games = schedule_payload
    else:
        games = []

    games = [game for game in games if isinstance(game, dict)]
    now = get_et_now()
    today_str = now.strftime("%Y-%m-%d")
    non_final_games = [game for game in games if not game.get("is_final")]

    active_candidates = []
    for game in non_final_games:
        deadline_dt = _parse_dt(game.get("closing_scrape_deadline"))
        if deadline_dt is not None and deadline_dt >= (now - timedelta(minutes=15)):
            active_candidates.append(game)

    active_games = []
    if active_candidates:
        target_date = min(
            _normalize_game_date(game.get("game_date"))
            for game in active_candidates
            if _normalize_game_date(game.get("game_date"))
        )
        active_games = [
            game for game in active_candidates
            if _normalize_game_date(game.get("game_date")) == target_date
        ]

    if not active_games:
        future_games = []
        for game in non_final_games:
            game_date = _normalize_game_date(game.get("game_date"))
            deadline_dt = _parse_dt(game.get("closing_scrape_deadline"))
            if deadline_dt and deadline_dt >= (now - timedelta(hours=2)):
                future_games.append((deadline_dt, game))
            elif game_date >= today_str:
                future_games.append((datetime.max.replace(tzinfo=ET_ZONE), game))

        future_games.sort(key=lambda item: (item[0], _normalize_game_date(item[1].get("game_date"))))
        if future_games:
            target_date = _normalize_game_date(future_games[0][1].get("game_date"))
            active_games = [
                game for _, game in future_games
                if _normalize_game_date(game.get("game_date")) == target_date
            ]

    active_teams = set()
    active_dates = set()
    opponent_by_team = {}
    game_by_team = {}
    for game in active_games:
        home_team = game.get("home_team_tricode")
        away_team = game.get("away_team_tricode")
        game_date = _normalize_game_date(game.get("game_date"))
        if home_team:
            active_teams.add(home_team)
            game_by_team[home_team] = game
            if away_team:
                opponent_by_team[home_team] = away_team
        if away_team:
            active_teams.add(away_team)
            game_by_team[away_team] = game
            if home_team:
                opponent_by_team[away_team] = home_team
        if game_date:
            active_dates.add(game_date)

    return {
        "games": active_games,
        "active_teams": active_teams,
        "active_dates": active_dates,
        "game_by_team": game_by_team,
        "opponent_by_team": opponent_by_team,
    }


def _normalize_props_tree(props_tree: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    return _normalize_props_tree_for_game_date(props_tree)


def _resolve_dated_prop_bucket(
    prop_bucket: Any,
    target_game_date: Optional[str] = None,
    active_dates: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(prop_bucket, dict):
        return None

    normalized_target = _normalize_game_date(target_game_date)
    normalized_active_dates = {
        _normalize_game_date(game_date)
        for game_date in (active_dates or set())
        if _normalize_game_date(game_date)
    }

    if normalized_target:
        target_match = prop_bucket.get(normalized_target)
        if isinstance(target_match, dict):
            return target_match

    dated_candidates: List[Tuple[str, Dict[str, Any]]] = []
    undated_candidate = prop_bucket.get(UNDATED_PROP_KEY)
    if not isinstance(undated_candidate, dict):
        undated_candidate = None

    for raw_date, raw_prop in prop_bucket.items():
        if raw_date == UNDATED_PROP_KEY or not isinstance(raw_prop, dict):
            continue
        normalized_date = _normalize_game_date(raw_date)
        if not normalized_date:
            continue
        dated_candidates.append((normalized_date, raw_prop))

    if normalized_active_dates:
        active_matches = [
            (game_date, prop)
            for game_date, prop in dated_candidates
            if game_date in normalized_active_dates
        ]
        if active_matches:
            active_matches.sort(key=lambda item: item[0], reverse=True)
            return active_matches[0][1]

    if undated_candidate and not dated_candidates:
        return undated_candidate

    if dated_candidates and not normalized_target:
        dated_candidates.sort(key=lambda item: item[0], reverse=True)
        return dated_candidates[0][1]

    return undated_candidate


def _normalize_props_tree_for_game_date(
    props_tree: Any,
    target_game_date: Optional[str] = None,
    active_dates: Optional[set] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    normalized: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not isinstance(props_tree, dict):
        return normalized

    normalized_target_game_date = _normalize_game_date(target_game_date)
    normalized_active_dates = {
        _normalize_game_date(game_date)
        for game_date in (active_dates or set())
        if _normalize_game_date(game_date)
    }

    for raw_stat, raw_books in props_tree.items():
        stat_type = str(raw_stat or "").upper()
        if not stat_type or not isinstance(raw_books, dict):
            continue
        for raw_book, raw_prop in raw_books.items():
            book = _normalize_book_key(raw_book)
            if book not in SUPPORTED_BOOKS or not isinstance(raw_prop, dict):
                continue

            if any(key in raw_prop for key in ("line", "over", "under", "game_date")):
                candidate_prop = raw_prop
            else:
                candidate_prop = _resolve_dated_prop_bucket(
                    raw_prop,
                    target_game_date=normalized_target_game_date,
                    active_dates=normalized_active_dates,
                )

            if not isinstance(candidate_prop, dict):
                continue

            prop_game_date = _normalize_game_date(candidate_prop.get("game_date")) or normalized_target_game_date
            if normalized_target_game_date and prop_game_date and prop_game_date != normalized_target_game_date:
                continue
            if normalized_active_dates and prop_game_date and prop_game_date not in normalized_active_dates:
                continue

            line = _safe_float(candidate_prop.get("line"))
            if line is None:
                continue
            normalized.setdefault(stat_type, {})[book] = {
                "line": line,
                "over": _safe_float(candidate_prop.get("over")),
                "under": _safe_float(candidate_prop.get("under")),
                "game_date": prop_game_date,
                "game_id": candidate_prop.get("game_id"),
            }
    return normalized


def _merge_props_trees(
    base_props: Dict[str, Dict[str, Dict[str, Any]]],
    overlay_props: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    merged: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for stat_type, book_map in base_props.items():
        merged[stat_type] = {book: dict(prop) for book, prop in book_map.items()}
    for stat_type, book_map in overlay_props.items():
        merged.setdefault(stat_type, {})
        for book, prop in book_map.items():
            merged[stat_type][book] = dict(prop)
    return merged


def _build_master_feed_matcher(master_feed: Any) -> Tuple[Optional[PlayerMatcher], Dict[str, str]]:
    players_metadata = []
    id_to_team: Dict[str, str] = {}

    if not isinstance(master_feed, list):
        return None, id_to_team

    for player in master_feed:
        if not isinstance(player, dict):
            continue
        player_id = player.get("id")
        player_name = player.get("name")
        team = player.get("team", "UNK")
        if player_id and player_name:
            players_metadata.append({
                "PLAYER_ID": player_id,
                "PLAYER_NAME": player_name,
                "TEAM_ABBREVIATION": team,
            })
        if player_id and team:
            id_to_team[str(player_id)] = team

    if not players_metadata:
        return None, id_to_team

    return PlayerMatcher(players_metadata), id_to_team


def _merge_overlay_indexes(
    base_index: Dict[str, Dict[str, Dict[str, Any]]],
    incoming_index: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    merged = {
        player_id: {
            stat_type: {book: dict(prop) for book, prop in stat_map.items()}
            for stat_type, stat_map in player_map.items()
        }
        for player_id, player_map in base_index.items()
    }

    for player_id, player_map in incoming_index.items():
        merged.setdefault(player_id, {})
        for stat_type, stat_map in player_map.items():
            merged[player_id].setdefault(stat_type, {})
            for book, prop in stat_map.items():
                merged[player_id][stat_type][book] = dict(prop)

    return merged


def _load_prizepicks_overlay(
    master_feed: Any,
    schedule_context: Dict[str, Any],
    prizepicks_path: str,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if not prizepicks_path or not os.path.exists(prizepicks_path):
        return {}

    matcher, id_to_team = _build_master_feed_matcher(master_feed)
    if matcher is None:
        return {}

    active_teams = set(schedule_context.get("active_teams") or [])
    game_by_team = schedule_context.get("game_by_team") or {}
    active_dates = set(schedule_context.get("active_dates") or [])

    candidates: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    try:
        with open(prizepicks_path, "r", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not isinstance(row, dict):
                    continue

                raw_team = str(row.get("team") or "").strip()
                if active_teams and raw_team not in active_teams:
                    continue

                raw_name = row.get("raw_player_name") or row.get("player") or ""
                player_id = matcher.match_player(raw_name, raw_team or "UNK")
                if not player_id:
                    continue

                canonical_team = id_to_team.get(str(player_id), raw_team)
                schedule_game = game_by_team.get(canonical_team, {}) if canonical_team else {}
                schedule_game_date = _normalize_game_date(schedule_game.get("game_date"))
                row_game_date = _normalize_game_date(row.get("game_date"))
                if schedule_game_date:
                    if row_game_date and row_game_date != schedule_game_date:
                        continue
                    game_date = schedule_game_date
                else:
                    game_date = row_game_date

                if active_dates and game_date and game_date not in active_dates:
                    continue

                stat_type = _normalize_pp_stat_type(row.get("prop_type"), row.get("prop_label"))
                if not stat_type or stat_type not in STAT_PROFILES:
                    continue

                line = _safe_float(row.get("line"))
                if line is None:
                    continue

                game_id = row.get("game_id") or schedule_game.get("game_id")
                candidate_key = (str(player_id), stat_type, game_date or "")
                candidates.setdefault(candidate_key, []).append({
                    "line": line,
                    "over": _safe_float(row.get("over_odds")),
                    "under": _safe_float(row.get("under_odds")),
                    "game_date": game_date,
                    "game_id": game_id,
                })
    except Exception as exc:
        logger.warning("Could not load PrizePicks overlay from %s: %s", prizepicks_path, exc)
        return {}

    overlay_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (player_id, stat_type, _game_date), prop_candidates in candidates.items():
        unique_lines = {candidate["line"] for candidate in prop_candidates}
        if len(unique_lines) != 1:
            continue

        chosen = prop_candidates[-1]
        overlay_index.setdefault(player_id, {}).setdefault(stat_type, {})["pp"] = {
            "line": chosen["line"],
            "over": chosen.get("over"),
            "under": chosen.get("under"),
            "game_date": chosen.get("game_date"),
            "game_id": chosen.get("game_id"),
        }

    return overlay_index


def _build_overlay_props_index(players_data: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    overlay_index: Dict[str, Dict[str, Dict[str, Any]]] = {}
    if not isinstance(players_data, dict):
        return overlay_index

    for raw_player_id, payload in players_data.items():
        if not isinstance(payload, dict):
            continue
        player_id = str(raw_player_id)
        game_date = _normalize_game_date(payload.get("game_date"))
        game_id = payload.get("game_id")
        props_tree = payload.get("props", {})
        if not isinstance(props_tree, dict):
            continue

        for raw_stat, raw_books in props_tree.items():
            stat_type = str(raw_stat or "").upper()
            if not stat_type or not isinstance(raw_books, dict):
                continue
            for raw_book, raw_prop in raw_books.items():
                book = _normalize_book_key(raw_book)
                if book not in SUPPORTED_BOOKS or not isinstance(raw_prop, dict):
                    continue
                line = _safe_float(raw_prop.get("line"))
                if line is None:
                    continue
                overlay_index.setdefault(player_id, {}).setdefault(stat_type, {})[book] = {
                    "line": line,
                    "over": _safe_float(raw_prop.get("over")),
                    "under": _safe_float(raw_prop.get("under")),
                    "game_date": game_date,
                    "game_id": game_id,
                }
    return overlay_index


def _build_line_movement_lookup(line_movements_payload: Any) -> Dict[Tuple[str, str, str, str], List[Dict[str, Any]]]:
    lookup: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    if not isinstance(line_movements_payload, dict):
        return lookup

    snapshots = line_movements_payload.get("snapshots", [])
    if not isinstance(snapshots, list):
        return lookup

    ordered_snapshots = sorted(
        [snapshot for snapshot in snapshots if isinstance(snapshot, dict)],
        key=lambda snapshot: str(snapshot.get("timestamp") or ""),
    )
    for snapshot in ordered_snapshots:
        timestamp = snapshot.get("timestamp")
        players = snapshot.get("players", {})
        if not isinstance(players, dict):
            continue

        for raw_player_id, pdata in players.items():
            if not isinstance(pdata, dict):
                continue
            player_id = str(raw_player_id)
            game_date = _normalize_game_date(pdata.get("game_date"))
            props_tree = pdata.get("props", {})
            if not isinstance(props_tree, dict):
                continue

            for raw_stat, raw_books in props_tree.items():
                stat_type = str(raw_stat or "").upper()
                if not stat_type or not isinstance(raw_books, dict):
                    continue
                for raw_book, raw_prop in raw_books.items():
                    book = _normalize_book_key(raw_book)
                    if book not in SUPPORTED_BOOKS or not isinstance(raw_prop, dict):
                        continue
                    line = _safe_float(raw_prop.get("line"))
                    if line is None:
                        continue
                    key = (player_id, stat_type, book, game_date)
                    lookup.setdefault(key, []).append({
                        "timestamp": timestamp,
                        "line": line,
                        "over": _safe_float(raw_prop.get("over")),
                        "under": _safe_float(raw_prop.get("under")),
                    })

    return lookup


def _get_zone_matchup_score(
    player_zone_data: Any,
    opponent_zone_data: Any,
    zone_filter: Optional[set] = None,
) -> Tuple[Optional[float], Dict[str, Any]]:
    if not isinstance(player_zone_data, dict) or not isinstance(opponent_zone_data, dict):
        return None, {"weights": {}}

    weighted_total = 0.0
    total_weight = 0.0
    debug_weights = {}
    for zone, pdata in player_zone_data.items():
        if zone_filter and zone not in zone_filter:
            continue
        pct = _parse_percent((pdata or {}).get("percentage"))
        rank = _rank_to_ease((opponent_zone_data.get(zone) or {}).get("rank"))
        if pct is None or rank is None or pct <= 0:
            continue
        weighted_total += rank * pct
        total_weight += pct
        debug_weights[zone] = {
            "player_pct": round(pct, 1),
            "opp_rank": _safe_float((opponent_zone_data.get(zone) or {}).get("rank")),
        }

    if total_weight <= 0:
        return None, {"weights": debug_weights}

    return weighted_total / total_weight, {"weights": debug_weights}


def _get_shot_type_matchup_score(player: Dict[str, Any]) -> Tuple[Optional[float], Dict[str, Any]]:
    analysis = player.get("shot_type_analysis")
    if not isinstance(analysis, dict):
        return None, {"weights": {}}
    player_data = analysis.get("player", {})
    opp_data = analysis.get("opp_def", {})
    if not isinstance(player_data, dict) or not isinstance(opp_data, dict):
        return None, {"weights": {}}

    weighted_total = 0.0
    total_weight = 0.0
    debug_weights = {}
    for key in ("catch_and_shoot", "pull_up", "less_than_10_ft"):
        pct = _safe_float((player_data.get(key) or {}).get("percentage"))
        rank = _rank_to_ease((opp_data.get(key) or {}).get("rank"))
        if pct is None or rank is None or pct <= 0:
            continue
        weighted_total += rank * pct
        total_weight += pct
        debug_weights[key] = {
            "player_pct": round(pct, 1),
            "opp_rank": _safe_float((opp_data.get(key) or {}).get("rank")),
        }

    if total_weight <= 0:
        return None, {"weights": debug_weights}
    return weighted_total / total_weight, {"weights": debug_weights}


def _get_play_type_matchup_score(player: Dict[str, Any]) -> Tuple[Optional[float], Dict[str, Any]]:
    play_types = player.get("play_type_analysis")
    if not isinstance(play_types, list):
        return None, {"weights": {}}

    weighted_total = 0.0
    total_weight = 0.0
    debug_weights = {}
    for item in play_types:
        if not isinstance(item, dict):
            continue
        pct = _parse_percent(item.get("percent"))
        rank = _rank_to_ease(item.get("rank"))
        label = str(item.get("type") or "")
        if pct is None or rank is None or pct <= 0:
            continue
        weighted_total += rank * pct
        total_weight += pct
        debug_weights[label] = {
            "player_pct": round(pct, 1),
            "opp_rank": _safe_float(item.get("rank")),
        }

    if total_weight <= 0:
        return None, {"weights": debug_weights}
    return weighted_total / total_weight, {"weights": debug_weights}


def _compute_matchup_context(player: Dict[str, Any], stat_type: str, side: str) -> Dict[str, Any]:
    profile = STAT_PROFILES.get(stat_type, {})
    focus = profile.get("focus")

    shooting_score, shooting_debug = _get_zone_matchup_score(
        player.get("shooting_zones"),
        player.get("opp_def_zones"),
    )
    assist_score, assist_debug = _get_zone_matchup_score(
        player.get("assist_zones"),
        player.get("opp_assist_zones"),
    )
    three_zone_score, three_zone_debug = _get_zone_matchup_score(
        player.get("shooting_zones"),
        player.get("opp_def_zones"),
        zone_filter={"left_corner", "right_corner", "top_key"},
    )
    shot_type_score, shot_type_debug = _get_shot_type_matchup_score(player)
    play_type_score, play_type_debug = _get_play_type_matchup_score(player)
    paint_rank = _rank_to_ease(((player.get("opp_def_zones") or {}).get("paint") or {}).get("rank"))

    score = None
    details = {
        "shooting_zones": shooting_debug,
        "assist_zones": assist_debug,
        "three_zones": three_zone_debug,
        "shot_type": shot_type_debug,
        "play_type": play_type_debug,
        "paint_rank": _safe_float((((player.get("opp_def_zones") or {}).get("paint") or {}).get("rank"))),
    }

    if focus == "points":
        score = _weighted_average([
            (shooting_score, 0.30),
            (shot_type_score, 0.35),
            (play_type_score, 0.35),
        ])
    elif focus == "assists":
        score = _weighted_average([
            (assist_score, 0.70),
            (play_type_score, 0.30),
        ])
    elif focus == "rebounds":
        score = _weighted_average([
            (paint_rank, 1.0),
        ])
    elif focus == "threes":
        score = _weighted_average([
            (three_zone_score, 0.45),
            (shot_type_score, 0.55),
        ])
    elif focus == "stocks":
        score = None
    elif focus == "combo":
        component_scores = []
        for component, weight in profile.get("components", {}).items():
            component_ctx = _compute_matchup_context(player, component, side)
            if component_ctx["available"]:
                component_scores.append((component_ctx["raw_score"], weight))
        score = _weighted_average(component_scores)

    if score is None:
        return {
            "available": False,
            "raw_score": 0.0,
            "score": 0.0,
            "details": details,
        }

    directional_score = score * SIDE_MULTIPLIERS[side]
    return {
        "available": True,
        "raw_score": _clamp(score, -1.0, 1.0),
        "score": _clamp(directional_score, -1.0, 1.0),
        "details": details,
    }


def _compute_recent_form_context(player: Dict[str, Any], stat_type: str, line: float, side: str) -> Dict[str, Any]:
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    logs = _extract_logs(player)
    values = [value for value in (_stat_value_from_game(game, stat_type) for game in logs) if value is not None]

    recent5 = values[:5]
    recent10 = values[:10]
    recent20 = values[:20]
    recent5_avg = _average(recent5)
    recent10_avg = _average(recent10)
    recent20_avg = _average(recent20)

    def _hit_rate(sample: List[float]) -> Optional[float]:
        if not sample:
            return None
        if side == "over":
            hits = sum(1 for value in sample if value >= line)
        else:
            hits = sum(1 for value in sample if value <= line)
        return (hits / len(sample)) * 100.0

    hit_rate_5 = _hit_rate(recent5)
    hit_rate_10 = _hit_rate(recent10)
    hit_rate_20 = _hit_rate(recent20)

    gap_5 = None if recent5_avg is None else SIDE_MULTIPLIERS[side] * (recent5_avg - line)
    gap_10 = None if recent10_avg is None else SIDE_MULTIPLIERS[side] * (recent10_avg - line)
    gap_20 = None if recent20_avg is None else SIDE_MULTIPLIERS[side] * (recent20_avg - line)
    trend = None if recent5_avg is None or recent10_avg is None else recent5_avg - recent10_avg

    score = _clamp(
        (_normalize_score_by_scale(gap_5, scale) * 0.40)
        + (_normalize_score_by_scale(gap_10, scale) * 0.25)
        + (_normalize_score_by_scale(gap_20, scale) * 0.10)
        + (_normalize_score_by_scale((hit_rate_5 or 50.0) - 50.0, 35.0) * 0.15)
        + (_normalize_score_by_scale((hit_rate_10 or 50.0) - 50.0, 40.0) * 0.10),
        -1.0,
        1.0,
    )

    available = len(recent10) >= 4
    return {
        "available": available,
        "score": score if available else 0.0,
        "raw_score": score if available else 0.0,
        "details": {
            "samples": {
                "last_5": len(recent5),
                "last_10": len(recent10),
                "last_20": len(recent20),
            },
            "averages": {
                "last_5": _round(recent5_avg),
                "last_10": _round(recent10_avg),
                "last_20": _round(recent20_avg),
            },
            "hit_rates": {
                "last_5": _round(hit_rate_5),
                "last_10": _round(hit_rate_10),
                "last_20": _round(hit_rate_20),
            },
            "trend": _round(trend),
        },
    }


def _compute_projection_context(player: Dict[str, Any], stat_type: str, line: float, side: str) -> Dict[str, Any]:
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    season_avg = _stat_value_from_stats(player.get("stats", {}), stat_type)
    logs = _extract_logs(player)
    values = [value for value in (_stat_value_from_game(game, stat_type) for game in logs[:10]) if value is not None]
    recent5_avg = _average(values[:5])
    recent10_avg = _average(values[:10])

    season_minutes = _safe_float((player.get("stats") or {}).get("MIN"))
    recent_minutes = _average([
        _safe_float(game.get("MIN")) for game in logs[:5]
    ])
    minute_delta_ratio = None
    if season_minutes and recent_minutes:
        minute_delta_ratio = (recent_minutes - season_minutes) / max(season_minutes, 1.0)

    baseline_projection = _weighted_average([
        (season_avg, 0.48),
        (recent10_avg, 0.32),
        (recent5_avg, 0.20),
    ])
    if baseline_projection is not None and minute_delta_ratio is not None:
        baseline_projection += baseline_projection * _clamp(minute_delta_ratio, -0.20, 0.20) * 0.18

    projection_gap = None if baseline_projection is None else SIDE_MULTIPLIERS[side] * (baseline_projection - line)
    score = _normalize_score_by_scale(projection_gap, scale)
    available = baseline_projection is not None
    return {
        "available": available,
        "score": score if available else 0.0,
        "raw_score": score if available else 0.0,
        "details": {
            "season_avg": _round(season_avg),
            "recent_5_avg": _round(recent5_avg),
            "recent_10_avg": _round(recent10_avg),
            "recent_minutes": _round(recent_minutes),
            "season_minutes": _round(season_minutes),
            "baseline_projection": _round(baseline_projection),
            "projection_gap": _round(projection_gap),
        },
    }


def _compute_head_to_head_context(
    player: Dict[str, Any],
    opponent: Optional[str],
    stat_type: str,
    line: float,
    side: str,
) -> Dict[str, Any]:
    if not opponent:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    values = []
    for game in _extract_logs(player):
        matchup = str(game.get("MATCHUP") or "")
        if matchup.endswith(f" {opponent}"):
            stat_value = _stat_value_from_game(game, stat_type)
            if stat_value is not None:
                values.append(stat_value)

    h2h_avg = _average(values[:5])
    h2h_gap = None if h2h_avg is None else SIDE_MULTIPLIERS[side] * (h2h_avg - line)
    available = len(values) >= 2
    score = _normalize_score_by_scale(h2h_gap, scale) if available else 0.0
    return {
        "available": available,
        "score": score,
        "raw_score": score,
        "details": {
            "sample_size": len(values),
            "average": _round(h2h_avg),
            "gap_vs_line": _round(h2h_gap),
        },
    }


def _compute_back_to_back_context(
    player: Dict[str, Any],
    stat_type: str,
    game_date: str,
    side: str,
) -> Dict[str, Any]:
    logs = _extract_logs(player)
    if not logs:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    current_game_date = _parse_date(game_date)
    latest_logged_date = _parse_date(logs[0].get("GAME_DATE"))
    if current_game_date is None or latest_logged_date is None:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    current_is_b2b = (current_game_date - latest_logged_date).days == 1
    if not current_is_b2b:
        return {
            "available": False,
            "score": 0.0,
            "raw_score": 0.0,
            "details": {
                "current_is_b2b": False,
            },
        }

    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    values = []
    b2b_values = []
    dated_logs = [(_parse_date(game.get("GAME_DATE")), game) for game in logs]
    dated_logs = [(date_value, game) for date_value, game in dated_logs if date_value is not None]
    for idx, (date_value, game) in enumerate(dated_logs):
        stat_value = _stat_value_from_game(game, stat_type)
        if stat_value is not None:
            values.append(stat_value)
        if idx + 1 >= len(dated_logs) or stat_value is None:
            continue
        next_date_value = dated_logs[idx + 1][0]
        if (date_value - next_date_value).days == 1:
            b2b_values.append(stat_value)

    overall_avg = _average(values[:20])
    b2b_avg = _average(b2b_values[:10])
    if b2b_avg is not None and overall_avg is not None and len(b2b_values) >= 2:
        delta = b2b_avg - overall_avg
        score = _normalize_score_by_scale(SIDE_MULTIPLIERS[side] * delta, scale)
        return {
            "available": True,
            "score": score,
            "raw_score": score,
            "details": {
                "current_is_b2b": True,
                "sample_size": len(b2b_values),
                "overall_avg": _round(overall_avg),
                "b2b_avg": _round(b2b_avg),
                "delta_vs_overall": _round(delta),
            },
        }

    base_bias = profile.get("b2b_bias", -0.08)
    score = _clamp(base_bias * SIDE_MULTIPLIERS[side], -0.25, 0.25)
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "current_is_b2b": True,
            "sample_size": len(b2b_values),
            "fallback_bias": _round(base_bias),
        },
    }


def _build_style_vector(player: Dict[str, Any]) -> Dict[str, float]:
    vector: Dict[str, float] = {}
    stats = player.get("stats") if isinstance(player.get("stats"), dict) else {}
    vector["min"] = (_safe_float(stats.get("MIN"), 0.0) or 0.0) / 40.0
    vector["usg"] = (_safe_float(stats.get("USG_PCT"), 0.0) or 0.0) / 100.0
    vector["fga"] = (_safe_float(stats.get("FGA"), 0.0) or 0.0) / 25.0
    vector["potential_ast"] = (_safe_float(stats.get("POTENTIAL_AST"), 0.0) or 0.0) / 18.0
    vector["reb_chances"] = (_safe_float(stats.get("REB_CHANCES"), 0.0) or 0.0) / 22.0

    shot_type_analysis = player.get("shot_type_analysis", {})
    player_shot_type = shot_type_analysis.get("player", {}) if isinstance(shot_type_analysis, dict) else {}
    for key in ("catch_and_shoot", "pull_up", "less_than_10_ft"):
        vector[f"shot:{key}"] = (_safe_float((player_shot_type.get(key) or {}).get("percentage"), 0.0) or 0.0) / 100.0

    play_type_index = {
        label: idx for idx, label in enumerate(PLAY_TYPE_LABELS)
    }
    for label in PLAY_TYPE_LABELS:
        vector[f"play:{label}"] = 0.0
    for item in player.get("play_type_analysis") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("type") or "")
        if label not in play_type_index:
            continue
        vector[f"play:{label}"] = (_parse_percent(item.get("percent")) or 0.0) / 100.0

    return vector


def _fingerprint_distance(left: Dict[str, float], right: Dict[str, float]) -> float:
    keys = set(left.keys()) | set(right.keys())
    if not keys:
        return 0.0
    deltas = [abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys]
    return sum(deltas) / len(deltas)


def _select_market_for_side(
    stat_type: str,
    book_props: Dict[str, Dict[str, Any]],
    side: str,
) -> Optional[Dict[str, Any]]:
    chosen = None
    consensus_lines = []
    implieds = []
    books_summary = {}

    for book, prop in (book_props or {}).items():
        if book not in SUPPORTED_BOOKS or not isinstance(prop, dict):
            continue
        line = _safe_float(prop.get("line"))
        if line is None:
            continue
        side_odds = _safe_float(prop.get(side))
        opposite_odds = _safe_float(prop.get("under" if side == "over" else "over"))
        implied = _american_to_implied(side_odds)
        consensus_lines.append(line)
        if implied is not None:
            implieds.append(implied)
        books_summary[book] = {
            "line": line,
            "side_odds": side_odds,
            "opposite_odds": opposite_odds,
            "game_date": _normalize_game_date(prop.get("game_date")),
            "game_id": prop.get("game_id"),
        }

        if chosen is None:
            chosen = {
                "book": book,
                "line": line,
                "odds": side_odds,
                "opposite_odds": opposite_odds,
                "game_date": _normalize_game_date(prop.get("game_date")),
                "game_id": prop.get("game_id"),
                "implied": implied,
            }
            continue

        current_tuple = (
            line,
            implied if implied is not None else 1.0,
        )
        chosen_tuple = (
            chosen["line"],
            chosen["implied"] if chosen["implied"] is not None else 1.0,
        )
        if side == "over":
            should_replace = current_tuple < chosen_tuple
        else:
            should_replace = (-current_tuple[0], current_tuple[1]) < (-chosen_tuple[0], chosen_tuple[1])
        if should_replace:
            chosen = {
                "book": book,
                "line": line,
                "odds": side_odds,
                "opposite_odds": opposite_odds,
                "game_date": _normalize_game_date(prop.get("game_date")),
                "game_id": prop.get("game_id"),
                "implied": implied,
            }

    if chosen is None:
        return None

    return {
        "stat_type": stat_type,
        "side": side,
        "chosen": chosen,
        "books": books_summary,
        "consensus_line": _average(consensus_lines),
        "average_side_implied": _average(implieds),
        "available_books": len(books_summary),
    }


def _compute_market_context(market_selection: Dict[str, Any], side: str) -> Dict[str, Any]:
    chosen = market_selection.get("chosen", {})
    chosen_line = _safe_float(chosen.get("line"))
    consensus_line = _safe_float(market_selection.get("consensus_line"))
    line_delta = None
    if chosen_line is not None and consensus_line is not None:
        if side == "over":
            line_delta = consensus_line - chosen_line
        else:
            line_delta = chosen_line - consensus_line

    chosen_implied = _american_to_implied(chosen.get("odds"))
    average_implied = _safe_float(market_selection.get("average_side_implied"))
    price_delta = None
    if chosen_implied is not None and average_implied is not None:
        price_delta = average_implied - chosen_implied

    score = _clamp(
        (_normalize_score_by_scale(line_delta, 1.0) * 0.75)
        + (_normalize_score_by_scale(price_delta, 0.10) * 0.25),
        -1.0,
        1.0,
    )
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "chosen_book": chosen.get("book"),
            "chosen_line": _round(chosen_line),
            "consensus_line": _round(consensus_line),
            "line_delta_vs_consensus": _round(line_delta),
            "chosen_implied": _round(chosen_implied, 3),
            "average_implied": _round(average_implied, 3),
            "price_delta": _round(price_delta, 3),
            "available_books": market_selection.get("available_books", 0),
            "books": market_selection.get("books", {}),
        },
    }


def _compute_line_movement_context(
    line_lookup: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]],
    player_id: str,
    stat_type: str,
    book: str,
    game_date: str,
    side: str,
    chosen_odds: Any,
    chosen_line: float,
) -> Dict[str, Any]:
    history = line_lookup.get((player_id, stat_type, book, game_date), [])
    if len(history) < 2:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {}}

    opening = history[0]
    latest = history[-1]
    open_line = _safe_float(opening.get("line"))
    latest_line = _safe_float(latest.get("line"))
    current_line = chosen_line if chosen_line is not None else latest_line
    favorable_line_change = None
    if current_line is not None and open_line is not None:
        if side == "over":
            favorable_line_change = open_line - current_line
        else:
            favorable_line_change = current_line - open_line

    opening_implied = _american_to_implied(opening.get(side))
    current_implied = _american_to_implied(chosen_odds)
    favorable_price_change = None
    if opening_implied is not None and current_implied is not None:
        favorable_price_change = opening_implied - current_implied

    score = _clamp(
        (_normalize_score_by_scale(favorable_line_change, 1.0) * 0.75)
        + (_normalize_score_by_scale(favorable_price_change, 0.08) * 0.25),
        -1.0,
        1.0,
    )
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "opening_line": _round(open_line),
            "latest_snapshot_line": _round(latest_line),
            "current_line": _round(current_line),
            "favorable_line_change": _round(favorable_line_change),
            "opening_side_implied": _round(opening_implied, 3),
            "current_side_implied": _round(current_implied, 3),
            "favorable_price_change": _round(favorable_price_change, 3),
            "snapshots_seen": len(history),
        },
    }


def _compute_similar_players_context(
    entry: Dict[str, Any],
    stat_type: str,
    side: str,
    current_line: float,
    active_entries: List[Dict[str, Any]],
    style_cache: Dict[int, Dict[str, float]],
) -> Dict[str, Any]:
    player = entry["player"]
    player_id = int(player.get("id"))
    profile = STAT_PROFILES.get(stat_type, {})
    scale = profile.get("scale", 5.0)
    target_average = _stat_value_from_stats(player.get("stats", {}), stat_type)
    target_position = _simple_position(player.get("position"))
    target_style = style_cache.get(player_id, {})

    candidates = []
    for candidate_entry in active_entries:
        candidate_player = candidate_entry["player"]
        candidate_id = int(candidate_player.get("id"))
        if candidate_id == player_id:
            continue
        if stat_type not in candidate_entry["props"]:
            continue
        candidate_position = _simple_position(candidate_player.get("position"))
        if candidate_position != target_position and {candidate_position, target_position} not in [{"F", "C"}]:
            continue

        candidate_market = _select_market_for_side(stat_type, candidate_entry["props"][stat_type], side)
        if not candidate_market:
            continue
        candidate_line = _safe_float(candidate_market["chosen"].get("line"))
        candidate_average = _stat_value_from_stats(candidate_player.get("stats", {}), stat_type)
        if candidate_line is None or candidate_average is None:
            continue

        distance = (
            _normalize_score_by_scale(abs((candidate_average or 0.0) - (target_average or 0.0)), scale) * 0.35
            + _normalize_score_by_scale(abs(candidate_line - current_line), scale) * 0.30
            + _fingerprint_distance(target_style, style_cache.get(candidate_id, {})) * 0.25
            + (0.10 if candidate_position != target_position else 0.0)
        )
        candidates.append({
            "id": candidate_id,
            "name": candidate_player.get("name"),
            "team": candidate_player.get("team"),
            "line": candidate_line,
            "average": candidate_average,
            "distance": distance,
        })

    candidates.sort(key=lambda item: item["distance"])
    top_candidates = candidates[:6]
    if len(top_candidates) < 3:
        return {"available": False, "score": 0.0, "raw_score": 0.0, "details": {"candidates": top_candidates}}

    comp_gap = _average([
        candidate["average"] - candidate["line"]
        for candidate in top_candidates
    ])
    directional_gap = None if comp_gap is None else SIDE_MULTIPLIERS[side] * comp_gap
    score = _normalize_score_by_scale(directional_gap, scale)
    return {
        "available": True,
        "score": score,
        "raw_score": score,
        "details": {
            "sample_size": len(top_candidates),
            "average_gap_vs_line": _round(comp_gap),
            "candidates": [
                {
                    "id": candidate["id"],
                    "name": candidate["name"],
                    "team": candidate["team"],
                    "line": _round(candidate["line"]),
                    "average": _round(candidate["average"]),
                    "distance": round(candidate["distance"], 3),
                }
                for candidate in top_candidates
            ],
        },
    }


def _component_available_weight(components: Dict[str, Dict[str, Any]]) -> float:
    available_weight = 0.0
    for component_name, component in components.items():
        if component.get("available"):
            available_weight += COMPONENT_WEIGHTS.get(component_name, 0.0)
    return available_weight


def _build_reason_strings(
    side: str,
    line: float,
    stat_type: str,
    components: Dict[str, Dict[str, Any]],
) -> List[str]:
    reasons = []
    projection_details = components["projection"]["details"]
    projection_gap = projection_details.get("projection_gap")
    if projection_gap is not None:
        direction_word = "above" if projection_gap >= 0 else "below"
        reasons.append(
            f"Baseline projection sits {abs(projection_gap):.1f} {direction_word} the line."
        )

    recent_details = components["recent_form"]["details"]
    recent_10_avg = recent_details.get("averages", {}).get("last_10")
    recent_10_hit_rate = recent_details.get("hit_rates", {}).get("last_10")
    if recent_10_avg is not None and recent_10_hit_rate is not None:
        reasons.append(
            f"Recent form: last 10 average {recent_10_avg:.1f} with {recent_10_hit_rate:.0f}% {_side_name(side).lower()} rate."
        )

    if components["matchup"]["available"]:
        reasons.append("Opponent matchup grades favorable for this shot/profile mix.")

    market_details = components["market"]["details"]
    line_delta = market_details.get("line_delta_vs_consensus")
    chosen_book = market_details.get("chosen_book")
    if line_delta is not None and abs(line_delta) >= 0.25 and chosen_book:
        reasons.append(
            f"{BOOK_LABELS.get(chosen_book, chosen_book)} offers a {abs(line_delta):.1f}-point edge versus the market consensus."
        )

    movement_details = components["line_movement"]["details"]
    line_change = movement_details.get("favorable_line_change")
    if line_change is not None and abs(line_change) >= 0.25:
        reasons.append(f"Today’s movement improved this {_side_name(side).lower()} number by {abs(line_change):.1f}.")

    similar_details = components["similar_players"]["details"]
    average_gap = similar_details.get("average_gap_vs_line")
    if average_gap is not None:
        direction_word = "above" if average_gap >= 0 else "below"
        reasons.append(
            f"Comparable profiles average {abs(average_gap):.1f} {direction_word} their own lines."
        )

    b2b_details = components["back_to_back"]["details"]
    if b2b_details.get("current_is_b2b"):
        reasons.append("Back-to-back context is baked into the score.")

    return reasons[:5]


def _build_candidate(
    entry: Dict[str, Any],
    stat_type: str,
    side: str,
    market_selection: Dict[str, Any],
    active_entries: List[Dict[str, Any]],
    style_cache: Dict[int, Dict[str, float]],
    line_lookup: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    player = entry["player"]
    profile = STAT_PROFILES.get(stat_type)
    if not profile:
        return None

    chosen = market_selection.get("chosen", {})
    line = _safe_float(chosen.get("line"))
    if line is None:
        return None

    book = chosen.get("book")
    game_date = entry["game_date"] or chosen.get("game_date") or ""
    player_id = str(player.get("id"))
    opponent = entry.get("opponent")

    components = {
        "projection": _compute_projection_context(player, stat_type, line, side),
        "recent_form": _compute_recent_form_context(player, stat_type, line, side),
        "matchup": _compute_matchup_context(player, stat_type, side),
        "market": _compute_market_context(market_selection, side),
        "line_movement": _compute_line_movement_context(
            line_lookup,
            player_id=player_id,
            stat_type=stat_type,
            book=book,
            game_date=game_date,
            side=side,
            chosen_odds=chosen.get("odds"),
            chosen_line=line,
        ),
        "similar_players": _compute_similar_players_context(
            entry,
            stat_type,
            side,
            line,
            active_entries,
            style_cache,
        ),
        "head_to_head": _compute_head_to_head_context(player, opponent, stat_type, line, side),
        "back_to_back": _compute_back_to_back_context(player, stat_type, game_date, side),
    }

    weighted_score = 0.0
    for component_name, component in components.items():
        weighted_score += COMPONENT_WEIGHTS.get(component_name, 0.0) * component.get("score", 0.0)

    available_weight = _component_available_weight(components)
    confidence = round((available_weight / TOTAL_COMPONENT_WEIGHT) * 100.0, 1) if TOTAL_COMPONENT_WEIGHT else 0.0
    confidence_multiplier = 0.85 + (min(confidence, 100.0) / 100.0) * 0.15
    edge_score = round(_clamp(50.0 + (weighted_score * 45.0 * confidence_multiplier), 1.0, 99.0), 1)

    reasons = _build_reason_strings(side, line, stat_type, components)
    recommendation_key = f"{player_id}|{game_date}|{stat_type}|{side}"
    return {
        "recommendation_key": recommendation_key,
        "player_id": int(player.get("id")),
        "player_name": player.get("name"),
        "team": player.get("team"),
        "opponent": opponent,
        "position": player.get("position"),
        "game_id": entry.get("game_id") or chosen.get("game_id"),
        "game_date": game_date,
        "game_time_et": entry.get("game_time_et"),
        "sportsbook": book,
        "sportsbook_label": BOOK_LABELS.get(book, str(book).title()),
        "stat_type": stat_type,
        "stat_label": profile.get("display"),
        "pick": side,
        "pick_label": _side_name(side),
        "line": line,
        "odds": _safe_float(chosen.get("odds")),
        "odds_display": _format_odds(chosen.get("odds")),
        "opposite_odds": _safe_float(chosen.get("opposite_odds")),
        "edge_score": edge_score,
        "confidence": confidence,
        "signal_score": round(weighted_score, 3),
        "reasons": reasons,
        "inputs": {
            "projection": components["projection"]["details"],
            "recent_form": components["recent_form"]["details"],
            "matchup": components["matchup"]["details"],
            "market": components["market"]["details"],
            "line_movement": components["line_movement"]["details"],
            "similar_players": components["similar_players"]["details"],
            "head_to_head": components["head_to_head"]["details"],
            "back_to_back": components["back_to_back"]["details"],
        },
        "component_scores": {
            component_name: round(component.get("score", 0.0), 3)
            for component_name, component in components.items()
        },
        "available_component_weights": round(available_weight, 3),
    }


def _diversify_candidates(candidates: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    output = []
    per_player_counts: Dict[int, int] = {}
    per_game_counts: Dict[str, int] = {}
    seen_keys = set()

    for candidate in candidates:
        recommendation_key = candidate.get("recommendation_key")
        if recommendation_key in seen_keys:
            continue
        player_id = int(candidate.get("player_id"))
        game_id = str(candidate.get("game_id") or "")
        if per_player_counts.get(player_id, 0) >= 2:
            continue
        if game_id and per_game_counts.get(game_id, 0) >= 5:
            continue

        seen_keys.add(recommendation_key)
        per_player_counts[player_id] = per_player_counts.get(player_id, 0) + 1
        if game_id:
            per_game_counts[game_id] = per_game_counts.get(game_id, 0) + 1
        output.append(candidate)

        if len(output) >= limit:
            break

    return output


def _write_history_snapshot(payload: Dict[str, Any]) -> None:
    history = _load_json(EDGE_SCORE_HISTORY_PATH, {"snapshots": []})
    if not isinstance(history, dict):
        history = {"snapshots": []}
    snapshots = history.get("snapshots", [])
    if not isinstance(snapshots, list):
        snapshots = []

    snapshots.append({
        "generated_at": payload.get("generated_at"),
        "refresh_label": payload.get("refresh_label"),
        "game_dates": payload.get("game_dates", []),
        "top_15": [
            {
                "rank": recommendation.get("rank"),
                "recommendation_key": recommendation.get("recommendation_key"),
                "player_name": recommendation.get("player_name"),
                "team": recommendation.get("team"),
                "stat_type": recommendation.get("stat_type"),
                "pick": recommendation.get("pick"),
                "sportsbook": recommendation.get("sportsbook"),
                "line": recommendation.get("line"),
                "edge_score": recommendation.get("edge_score"),
            }
            for recommendation in payload.get("recommendations", [])
        ],
    })
    history["snapshots"] = snapshots[-120:]
    _write_json_atomic(EDGE_SCORE_HISTORY_PATH, history)


def _state_snapshot_for_recommendations(recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
    snapshot = {}
    for recommendation in recommendations:
        snapshot[recommendation["recommendation_key"]] = {
            "rank": recommendation.get("rank"),
            "player_name": recommendation.get("player_name"),
            "stat_type": recommendation.get("stat_type"),
            "pick": recommendation.get("pick"),
            "sportsbook": recommendation.get("sportsbook"),
            "line": recommendation.get("line"),
            "odds": recommendation.get("odds"),
            "edge_score": recommendation.get("edge_score"),
        }
    return snapshot


def _compute_notification_delta(
    recommendations: List[Dict[str, Any]],
    state: Dict[str, Any],
    refresh_label: str,
) -> Dict[str, Any]:
    current_snapshot = _state_snapshot_for_recommendations(recommendations)
    previous_snapshot = state.get("last_sent_snapshot", {})
    if not isinstance(previous_snapshot, dict):
        previous_snapshot = {}

    changes = []
    for recommendation in recommendations:
        key = recommendation["recommendation_key"]
        previous = previous_snapshot.get(key)
        reason_flags = []
        if previous is None:
            reason_flags.append("new entrant")
        else:
            if recommendation.get("sportsbook") != previous.get("sportsbook"):
                reason_flags.append("best book changed")
            if abs((_safe_float(recommendation.get("line"), 0.0) or 0.0) - (_safe_float(previous.get("line"), 0.0) or 0.0)) >= 0.5:
                reason_flags.append("line moved")
            if abs((_safe_float(recommendation.get("odds"), 0.0) or 0.0) - (_safe_float(previous.get("odds"), 0.0) or 0.0)) >= 15:
                reason_flags.append("odds moved")
            if abs((_safe_float(recommendation.get("edge_score"), 0.0) or 0.0) - (_safe_float(previous.get("edge_score"), 0.0) or 0.0)) >= 4:
                reason_flags.append("score moved")
            if abs(int(recommendation.get("rank") or 0) - int(previous.get("rank") or 0)) >= 3:
                reason_flags.append("rank changed")

        if reason_flags:
            changes.append({
                "recommendation_key": key,
                "rank": recommendation.get("rank"),
                "player_name": recommendation.get("player_name"),
                "stat_type": recommendation.get("stat_type"),
                "pick": recommendation.get("pick"),
                "sportsbook": recommendation.get("sportsbook"),
                "line": recommendation.get("line"),
                "edge_score": recommendation.get("edge_score"),
                "reasons": reason_flags,
            })

    removed = [
        {
            "recommendation_key": key,
            "player_name": payload.get("player_name"),
            "stat_type": payload.get("stat_type"),
            "pick": payload.get("pick"),
        }
        for key, payload in previous_snapshot.items()
        if key not in current_snapshot
    ]

    last_sent_at_raw = state.get("last_sent_at")
    last_sent_at = _parse_dt(last_sent_at_raw)
    now = get_et_now()
    cooldown_active = False
    if last_sent_at is not None and EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS > 0:
        cooldown_active = (now - last_sent_at).total_seconds() < EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS

    should_send = bool(changes)
    if refresh_label == "pre_game" and changes:
        cooldown_active = False
    if cooldown_active:
        should_send = False

    return {
        "changes": changes,
        "removed": removed,
        "cooldown_active": cooldown_active,
        "should_send": should_send,
        "current_snapshot": current_snapshot,
    }


def _send_discord_webhook(payload: Dict[str, Any], notification_delta: Dict[str, Any]) -> bool:
    if not EDGE_SCORE_DISCORD_WEBHOOK_URL:
        return False

    changes = notification_delta.get("changes", [])
    recommendations = payload.get("recommendations", [])
    lines = []
    for recommendation in recommendations[:EDGE_LIMIT]:
        lines.append(
            f"**#{recommendation['rank']}** {recommendation['player_name']} {recommendation['stat_label']} "
            f"{recommendation['pick_label']} {recommendation['line']:.1f} "
            f"({recommendation['sportsbook_label']} {recommendation['odds_display']}) "
            f"| Edge {recommendation['edge_score']:.1f}"
        )

    change_lines = []
    for change in changes[:6]:
        change_lines.append(
            f"#{change['rank']} {change['player_name']} {change['stat_type']} {change['pick']}: "
            f"{', '.join(change['reasons'])}"
        )

    discord_payload = {
        "username": "NBA Dashboard Edge Score",
        "embeds": [
            {
                "title": f"Edge Score Refresh • {payload.get('refresh_label', 'intraday')}",
                "description": "\n".join(lines[:15])[:3800],
                "color": 0x22C55E,
                "timestamp": payload.get("generated_at"),
                "fields": [
                    {
                        "name": "Top 15 Dates",
                        "value": ", ".join(payload.get("game_dates", [])) or "n/a",
                        "inline": True,
                    },
                    {
                        "name": "Meaningful Changes",
                        "value": "\n".join(change_lines)[:1000] if change_lines else "No material changes.",
                        "inline": False,
                    },
                ],
            }
        ],
    }

    response = requests.post(
        EDGE_SCORE_DISCORD_WEBHOOK_URL,
        json=discord_payload,
        timeout=10,
    )
    response.raise_for_status()
    return True


def _is_missing_relation_error(error: Exception, table_name: str) -> bool:
    message = str(error).lower()
    return (
        ("relation" in message and "does not exist" in message and table_name.lower() in message)
        or ("could not find the table" in message and table_name.lower() in message)
        or ("schema cache" in message and table_name.lower() in message)
    )


def _sync_payload_to_supabase(payload: Dict[str, Any]) -> bool:
    try:
        from utils.supabase_client import get_supabase_client
        client = get_supabase_client()
    except Exception as exc:
        logger.warning("Supabase unavailable for edge scores: %s", exc)
        return True

    row = {
        "ranking_key": "current",
        "game_dates": payload.get("game_dates", []),
        "generated_at": payload.get("generated_at"),
        "refresh_label": payload.get("refresh_label"),
        "summary": payload.get("summary", {}),
        "top_recommendations": payload.get("recommendations", []),
        "notification": payload.get("notification", {}),
    }

    try:
        client.table(EDGE_SCORE_TABLE).upsert(
            [row],
            on_conflict="ranking_key",
        ).execute()
        return True
    except Exception as exc:
        if _is_missing_relation_error(exc, EDGE_SCORE_TABLE):
            log_status(
                logger,
                "WARN",
                "Edge Score Supabase sync skipped; table missing",
                table=EDGE_SCORE_TABLE,
            )
            return True
        logger.warning("Edge Score Supabase sync failed: %s", exc)
        return False


def run_edge_score_refresh(
    refresh_label: str = "manual",
    current_players_data: Optional[Dict[str, Any]] = None,
    master_feed_path: str = MASTER_FEED_PATH,
    schedule_path: str = SCHEDULE_PATH,
    line_movements_path: str = LINE_MOVEMENTS_PATH,
    prizepicks_path: str = PRIZEPICKS_PATH,
) -> Dict[str, Any]:
    start_time = time.time()
    master_feed = _load_json(master_feed_path, [])
    schedule_payload = _load_json(schedule_path, {"games": []})
    line_movements_payload = _load_json(line_movements_path, {"snapshots": []})
    state = _load_json(EDGE_SCORE_STATE_PATH, {})

    if not isinstance(master_feed, list):
        master_feed = []

    schedule_context = _build_schedule_context(schedule_payload)
    overlay_props_index = _build_overlay_props_index(current_players_data)
    overlay_props_index = _merge_overlay_indexes(
        overlay_props_index,
        _load_prizepicks_overlay(master_feed, schedule_context, prizepicks_path),
    )
    line_lookup = _build_line_movement_lookup(line_movements_payload)

    active_entries = []
    for player in master_feed:
        if not isinstance(player, dict):
            continue
        team = player.get("team")
        if schedule_context["active_teams"] and team not in schedule_context["active_teams"]:
            continue

        player_id = str(player.get("id"))
        game = schedule_context["game_by_team"].get(team, {})
        game_date = _normalize_game_date(game.get("game_date")) or ""

        base_props = _merge_props_trees(
            _normalize_props_tree_for_game_date(
                player.get("props_by_date", {}),
                target_game_date=game_date,
                active_dates=schedule_context["active_dates"],
            ),
            _normalize_props_tree_for_game_date(
                player.get("props", {}),
                target_game_date=game_date,
                active_dates=schedule_context["active_dates"],
            ),
        )
        merged_props = _merge_props_trees(
            base_props,
            _normalize_props_tree_for_game_date(
                overlay_props_index.get(player_id, {}),
                target_game_date=game_date,
                active_dates=schedule_context["active_dates"],
            ),
        )
        if not merged_props:
            continue

        if not game_date:
            for book_map in merged_props.values():
                for prop in book_map.values():
                    if prop.get("game_date"):
                        game_date = _normalize_game_date(prop.get("game_date"))
                        break
                if game_date:
                    break

        active_entries.append({
            "player": player,
            "team": team,
            "props": merged_props,
            "game_id": game.get("game_id"),
            "game_date": game_date,
            "game_time_et": game.get("game_time_et"),
            "opponent": schedule_context["opponent_by_team"].get(team),
        })

    style_cache = {
        int(entry["player"].get("id")): _build_style_vector(entry["player"])
        for entry in active_entries
    }

    candidates = []
    for entry in active_entries:
        for stat_type, book_props in entry["props"].items():
            if stat_type not in STAT_PROFILES:
                continue

            side_candidates = []
            for side in ("over", "under"):
                market_selection = _select_market_for_side(stat_type, book_props, side)
                if not market_selection:
                    continue
                candidate = _build_candidate(
                    entry,
                    stat_type,
                    side,
                    market_selection,
                    active_entries,
                    style_cache,
                    line_lookup,
                )
                if candidate is not None:
                    side_candidates.append(candidate)

            if not side_candidates:
                continue

            best_candidate = max(
                side_candidates,
                key=lambda candidate: (
                    candidate.get("edge_score", 0.0),
                    candidate.get("confidence", 0.0),
                    candidate.get("signal_score", 0.0),
                ),
            )
            candidates.append(best_candidate)

    candidates.sort(
        key=lambda candidate: (
            candidate.get("edge_score", 0.0),
            candidate.get("confidence", 0.0),
            candidate.get("signal_score", 0.0),
        ),
        reverse=True,
    )
    top_recommendations = _diversify_candidates(candidates, EDGE_LIMIT)
    for rank, recommendation in enumerate(top_recommendations, start=1):
        recommendation["rank"] = rank

    notification_delta = _compute_notification_delta(top_recommendations, state, refresh_label)

    payload = {
        "generated_at": get_et_now().isoformat(),
        "refresh_label": refresh_label,
        "game_dates": sorted({entry["game_date"] for entry in active_entries if entry.get("game_date")}),
        "summary": {
            "active_players": len(active_entries),
            "candidate_count": len(candidates),
            "top_count": len(top_recommendations),
            "available_books": sorted(SUPPORTED_BOOKS),
            "duration_s": round(time.time() - start_time, 2),
            "scoring_model": "Edge Score",
        },
        "recommendations": top_recommendations,
        "notification": {
            "discord_configured": bool(EDGE_SCORE_DISCORD_WEBHOOK_URL),
            "should_send": bool(notification_delta.get("should_send")),
            "cooldown_active": bool(notification_delta.get("cooldown_active")),
            "change_count": len(notification_delta.get("changes", [])),
            "removed_count": len(notification_delta.get("removed", [])),
            "changes": notification_delta.get("changes", []),
            "removed": notification_delta.get("removed", []),
            "dedupe_rules": {
                "new_entrant": True,
                "line_move_points": 0.5,
                "odds_move_american": 15,
                "edge_score_delta": 4,
                "rank_delta": 3,
                "min_interval_seconds": EDGE_NOTIFICATION_MIN_INTERVAL_SECONDS,
            },
        },
    }

    _write_json_atomic(EDGE_SCORE_PATH, payload)
    _write_history_snapshot(payload)

    discord_sent = False
    if notification_delta.get("should_send") and EDGE_SCORE_DISCORD_WEBHOOK_URL:
        try:
            discord_sent = _send_discord_webhook(payload, notification_delta)
        except Exception as exc:
            logger.warning("Discord Edge Score webhook failed: %s", exc)

    if discord_sent:
        state["last_sent_at"] = payload["generated_at"]
        state["last_sent_snapshot"] = notification_delta.get("current_snapshot", {})
        _write_json_atomic(EDGE_SCORE_STATE_PATH, state)
    elif not os.path.exists(EDGE_SCORE_STATE_PATH):
        _write_json_atomic(EDGE_SCORE_STATE_PATH, state if isinstance(state, dict) else {})

    sync_ok = _sync_payload_to_supabase(payload)
    if discord_sent:
        log_status(
            logger,
            "OK",
            "Edge Score refresh complete",
            refresh_label=refresh_label,
            top=len(top_recommendations),
            discord_sent=True,
            supabase_sync_ok=sync_ok,
        )
    else:
        log_status(
            logger,
            "OK",
            "Edge Score refresh complete",
            refresh_label=refresh_label,
            top=len(top_recommendations),
            discord_sent=False,
            supabase_sync_ok=sync_ok,
        )

    return payload
