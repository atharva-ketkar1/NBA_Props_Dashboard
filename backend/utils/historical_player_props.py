import math
from datetime import datetime


SOURCE_PRIORITY = {
    "closing_line": 3,
    "pre_game_snapshot_fallback": 2,
    "last_snapshot_fallback": 1,
    "line_movements_fallback": 1,
    None: 0,
}

SPORTSBOOK_ALIASES = {
    "draftkings": "dk",
    "dk": "dk",
    "fanduel": "fd",
    "fd": "fd",
    "prizepicks": "pp",
    "pp": "pp",
}

STAT_TYPE_ALIASES = {
    "PTS": "PTS",
    "POINTS": "PTS",
    "REB": "REB",
    "REBOUNDS": "REB",
    "AST": "AST",
    "ASSISTS": "AST",
    "FG3M": "FG3M",
    "3PTM": "FG3M",
    "3PM": "FG3M",
    "THREES": "FG3M",
    "PTS+REB": "PTS+REB",
    "PR": "PTS+REB",
    "PTS+AST": "PTS+AST",
    "PA": "PTS+AST",
    "REB+AST": "REB+AST",
    "RA": "REB+AST",
    "PTS+REB+AST": "PTS+REB+AST",
    "PRA": "PTS+REB+AST",
    "STL": "STL",
    "STEALS": "STL",
    "BLK": "BLK",
    "BLOCKS": "BLK",
    "STL+BLK": "STL+BLK",
    "STOCKS": "STL+BLK",
    "1Q_PTS": "1Q_PTS",
    "1Q_AST": "1Q_AST",
    "1Q_REB": "1Q_REB",
    "1H_PTS": "1H_PTS",
    "DOUBLE_DOUBLE": "DOUBLE_DOUBLE",
    "DOUBLE DOUBLE": "DOUBLE_DOUBLE",
    "TRIPLE_DOUBLE": "TRIPLE_DOUBLE",
    "TRIPLE DOUBLE": "TRIPLE_DOUBLE",
    "FAN": "FAN",
    "FANTASY": "FAN",
    "TOV": "TOV",
    "TURNOVERS": "TOV",
}


def source_priority(source):
    return SOURCE_PRIORITY.get(source, 0)


def canonicalize_sportsbook_key(raw_value):
    if raw_value is None:
        return None
    return SPORTSBOOK_ALIASES.get(str(raw_value).strip().lower())


def canonicalize_stat_type(raw_value):
    if raw_value is None:
        return None
    candidate = str(raw_value).strip()
    if not candidate:
        return None
    return STAT_TYPE_ALIASES.get(candidate.upper(), candidate)


def _coerce_number(raw_value):
    if raw_value in (None, ""):
        return None

    if isinstance(raw_value, bool):
        return None

    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(parsed):
        return None

    return parsed


def _normalize_odds(raw_value):
    numeric = _coerce_number(raw_value)
    if numeric is None:
        return None

    if abs(numeric - round(numeric)) < 1e-9:
        return int(round(numeric))

    return round(numeric, 4)


def _normalize_line_payload(payload):
    if not isinstance(payload, dict):
        return None

    line = _coerce_number(payload.get("line"))
    if line is None:
        return None

    normalized = {
        "line": round(line, 4),
        "over_odds": _normalize_odds(payload.get("over")),
        "under_odds": _normalize_odds(payload.get("under")),
        "implied": _coerce_number(payload.get("implied")),
    }

    if normalized["implied"] is not None:
        normalized["implied"] = round(normalized["implied"], 6)

    return normalized


def _extract_record_context(record):
    if not isinstance(record, dict):
        return {}

    nested = record.get("props") if isinstance(record.get("props"), dict) else {}
    return {
        "name": record.get("name") or nested.get("name"),
        "team": record.get("team") or nested.get("team"),
        "source": record.get("source") or nested.get("source"),
        "captured_at": record.get("captured_at") or nested.get("captured_at"),
    }


def extract_props_tree(record):
    if not isinstance(record, dict):
        return {}

    outer_props = record.get("props") if isinstance(record.get("props"), dict) else None
    if outer_props and isinstance(outer_props.get("props"), dict):
        candidate_tree = outer_props.get("props")
    elif outer_props:
        candidate_tree = outer_props
    else:
        candidate_tree = record

    normalized_tree = {}
    if not isinstance(candidate_tree, dict):
        return normalized_tree

    for raw_stat_type, raw_books in candidate_tree.items():
        stat_type = canonicalize_stat_type(raw_stat_type)
        if not stat_type or not isinstance(raw_books, dict):
            continue

        for raw_book, raw_payload in raw_books.items():
            sportsbook = canonicalize_sportsbook_key(raw_book)
            if not sportsbook:
                continue

            line_payload = _normalize_line_payload(raw_payload)
            if not line_payload:
                continue

            normalized_tree.setdefault(stat_type, {})[sportsbook] = line_payload

    return normalized_tree


def flatten_historical_record(player_id, game_date, record, game_id=None, include_pp=False):
    if not game_date or not isinstance(record, dict):
        return []

    try:
        normalized_player_id = int(player_id)
    except (TypeError, ValueError):
        return []

    context = _extract_record_context(record)
    props_tree = extract_props_tree(record)
    if not props_tree:
        return []

    rows = []
    for stat_type, sportsbooks in props_tree.items():
        for sportsbook, payload in sportsbooks.items():
            if sportsbook == "pp" and not include_pp:
                continue

            rows.append(
                {
                    "player_id": normalized_player_id,
                    "game_date": game_date,
                    "game_id": str(game_id) if game_id else None,
                    "sportsbook": sportsbook,
                    "stat_type": stat_type,
                    "line": payload["line"],
                    "over_odds": payload.get("over_odds"),
                    "under_odds": payload.get("under_odds"),
                    "implied": payload.get("implied"),
                    "source": context.get("source"),
                    "captured_at": context.get("captured_at"),
                    "is_closing_line": context.get("source") == "closing_line",
                }
            )

    return rows


def flatten_historical_date_blob(game_date, date_blob, include_pp=False):
    if not isinstance(date_blob, dict):
        return []

    rows = []
    for player_id, record in date_blob.items():
        rows.extend(
            flatten_historical_record(
                player_id=player_id,
                game_date=game_date,
                record=record,
                include_pp=include_pp,
            )
        )
    return rows


def merge_rows_by_precedence(rows):
    merged = {}

    for row in rows or []:
        if not isinstance(row, dict):
            continue

        key = (
            row.get("player_id"),
            row.get("game_date"),
            row.get("sportsbook"),
            row.get("stat_type"),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
            continue

        existing_priority = source_priority(existing.get("source"))
        incoming_priority = source_priority(row.get("source"))
        if incoming_priority > existing_priority:
            merged[key] = row
            continue
        if incoming_priority < existing_priority:
            continue

        existing_captured = str(existing.get("captured_at") or "")
        incoming_captured = str(row.get("captured_at") or "")
        if incoming_captured >= existing_captured:
            merged[key] = row

    return list(merged.values())


def build_legacy_historical_row(game_date, player_id, rows):
    props = {}
    best_source = None
    best_captured_at = None
    legacy_book_map = {
        "dk": "draftkings",
        "fd": "fanduel",
        "pp": "pp",
    }

    for row in rows or []:
        stat_type = canonicalize_stat_type(row.get("stat_type"))
        sportsbook = canonicalize_sportsbook_key(row.get("sportsbook"))
        if not stat_type or not sportsbook:
            continue

        props.setdefault(stat_type, {})[legacy_book_map.get(sportsbook, sportsbook)] = {
            "line": row.get("line"),
            "over": row.get("over_odds"),
            "under": row.get("under_odds"),
            "implied": row.get("implied"),
        }

        current_source = row.get("source")
        if source_priority(current_source) > source_priority(best_source):
            best_source = current_source

        captured_at = row.get("captured_at")
        if captured_at and (best_captured_at is None or str(captured_at) > str(best_captured_at)):
            best_captured_at = captured_at

    return {
        "game_date": game_date,
        "props": props,
        "source": best_source,
        "captured_at": best_captured_at,
        "player_id": int(player_id),
    }


def parse_iso_datetime(raw_value):
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
