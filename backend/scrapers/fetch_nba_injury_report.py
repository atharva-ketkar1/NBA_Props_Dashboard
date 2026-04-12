import argparse
import hashlib
import io
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

logger = logging.getLogger(__name__)

ET_ZONE = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).resolve().parent.parent
CURRENT_OUTPUT_PATH = BASE_DIR / "data" / "current" / "nba_injury_report.json"
ARCHIVE_DIR = BASE_DIR / "data" / "archive" / "nba_injury_report"
DEFAULT_SCHEDULE_PATH = BASE_DIR / "data" / "current" / "today_schedule.json"
DEFAULT_MIN_REFRESH_INTERVAL_SECONDS = 3600

PLAYER_STATUSES = {"Available", "Probable", "Questionable", "Doubtful", "Out"}
NOT_SUBMITTED_TOKENS = ("NOT", "YET", "SUBMITTED")
TABLE_HEADER_TOKENS = (
    "Game",
    "Date",
    "Game",
    "Time",
    "Matchup",
    "Team",
    "Player",
    "Name",
    "Current",
    "Status",
    "Reason",
)

PAGE_DATA_MIN_Y = 80.0
PAGE_DATA_MAX_Y = 530.0
ROW_Y_TOLERANCE = 1.5
REASON_ATTACH_Y_TOLERANCE = 14.5

DATE_TOKEN_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
TIME_TOKEN_RE = re.compile(r"^\d{1,2}:\d{2}$")
MATCHUP_TOKEN_RE = re.compile(r"^[A-Z]{2,4}@[A-Z]{2,4}$")
PDF_URL_RE = re.compile(
    r"Injury-Report_(?P<date>\d{4}-\d{2}-\d{2})_(?P<hour>\d{1,2})_(?P<minute>\d{2})(?P<ampm>AM|PM)\.pdf",
    re.IGNORECASE,
)

STATIC_TEAM_NAME_TO_TRICODE = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and parse the official NBA injury report PDF.",
    )
    parser.add_argument(
        "--date",
        help="Target report date in ET, YYYYMMDD or YYYY-MM-DD. Defaults to today + --days-ahead.",
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=0,
        help="Offset from today's ET date when --date is omitted.",
    )
    parser.add_argument(
        "--schedule-path",
        default=str(DEFAULT_SCHEDULE_PATH),
        help="Schedule artifact used to attach NBA game_id and canonical team metadata.",
    )
    parser.add_argument(
        "--output-path",
        default=str(CURRENT_OUTPUT_PATH),
        help="Current output JSON path.",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(ARCHIVE_DIR),
        help="Daily archive directory for deduped report snapshots.",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip writing the archive snapshot file.",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the normalized payload to stdout.",
    )
    return parser.parse_args()


def get_et_now() -> datetime:
    return datetime.now(ET_ZONE)


def resolve_query_date(raw_date: Optional[str], days_ahead: int = 0) -> str:
    if raw_date:
        value = raw_date.strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).strftime("%Y%m%d")
            except ValueError:
                continue
        raise ValueError(f"Invalid --date value: {raw_date}. Expected YYYYMMDD or YYYY-MM-DD.")

    target_date = (get_et_now() + timedelta(days=days_ahead)).date()
    return target_date.strftime("%Y%m%d")


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_token(value: Any) -> str:
    text = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9&.-]", "", text)


def _normalize_team_code(value: Any) -> Optional[str]:
    text = _normalize_token(value)
    return text or None


def _parse_date_token(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not DATE_TOKEN_RE.fullmatch(text):
        return None
    try:
        return datetime.strptime(text, "%m/%d/%Y").date()
    except ValueError:
        return None


def _parse_iso_date(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _normalize_report_time(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not TIME_TOKEN_RE.fullmatch(text):
        return None
    return f"{text} ET"


def _season_slug_for_date(target_date: date) -> str:
    start_year = target_date.year if target_date.month >= 8 else target_date.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def _report_page_url_for_date(query_date: str) -> str:
    target_date = datetime.strptime(query_date, "%Y%m%d").date()
    season_slug = _season_slug_for_date(target_date)
    return f"https://official.nba.com/nba-injury-report-{season_slug}-season/"


def _parse_report_timestamp_from_url(report_url: str) -> Optional[datetime]:
    match = PDF_URL_RE.search(report_url or "")
    if not match:
        return None
    dt = datetime.strptime(
        f"{match.group('date')} {match.group('hour')}:{match.group('minute')} {match.group('ampm').upper()}",
        "%Y-%m-%d %I:%M %p",
    )
    return dt.replace(tzinfo=ET_ZONE)


def load_schedule_rows(schedule_path: Path) -> List[Dict[str, Any]]:
    if not schedule_path.exists():
        logger.warning("Schedule file not found for injury report merge: %s", schedule_path)
        return []
    try:
        with schedule_path.open("r") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("Unable to load schedule file %s: %s", schedule_path, exc)
        return []

    rows = payload.get("games", []) if isinstance(payload, dict) else payload
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _build_team_aliases(
    schedule_rows: Sequence[Dict[str, Any]],
) -> List[Tuple[Tuple[str, ...], str, str]]:
    team_name_to_tricode = dict(STATIC_TEAM_NAME_TO_TRICODE)

    for row in schedule_rows:
        for side in ("home", "away"):
            team_name = " ".join(
                part
                for part in [
                    str(row.get(f"{side}_team_city") or "").strip(),
                    str(row.get(f"{side}_team_name") or "").strip(),
                ]
                if part
            ).strip()
            tricode = _normalize_team_code(row.get(f"{side}_team_tricode"))
            if team_name and tricode:
                team_name_to_tricode[team_name] = tricode

    aliases = []
    for team_name, tricode in team_name_to_tricode.items():
        tokens = tuple(
            token
            for token in (_normalize_token(part) for part in team_name.split())
            if token
        )
        if tokens and tricode:
            aliases.append((tokens, team_name, tricode))
    aliases.sort(key=lambda item: len(item[0]), reverse=True)
    return aliases


def _build_schedule_index(
    schedule_rows: Sequence[Dict[str, Any]],
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    index = {}
    for row in schedule_rows:
        game_date = str(row.get("game_date") or "").strip()
        away_tricode = _normalize_team_code(row.get("away_team_tricode"))
        home_tricode = _normalize_team_code(row.get("home_team_tricode"))
        if game_date and away_tricode and home_tricode:
            index[(game_date, away_tricode, home_tricode)] = row
    return index


def _match_team_alias(
    tokens: Sequence[str],
    start_index: int,
    team_aliases: Sequence[Tuple[Tuple[str, ...], str, str]],
) -> Optional[Tuple[str, str, int]]:
    for alias_tokens, team_name, tricode in team_aliases:
        width = len(alias_tokens)
        if start_index + width > len(tokens):
            continue
        candidate = tuple(
            _normalize_token(token)
            for token in tokens[start_index:start_index + width]
        )
        if candidate == alias_tokens:
            return team_name, tricode, width
    return None


def _is_matchup_token(value: Any) -> bool:
    return bool(MATCHUP_TOKEN_RE.fullmatch(str(value or "").strip()))


def _is_time_token_pair(tokens: Sequence[str], start_index: int) -> bool:
    return (
        start_index + 1 < len(tokens)
        and TIME_TOKEN_RE.fullmatch(str(tokens[start_index] or "").strip())
        and str(tokens[start_index + 1] or "").strip() == "(ET)"
    )


def _normalize_player_name(report_name: str) -> str:
    report_name = " ".join(str(report_name or "").split()).strip()
    if "," not in report_name:
        return report_name
    last_name, first_name = report_name.split(",", 1)
    return " ".join(f"{first_name.strip()} {last_name.strip()}".split()).strip()


def _normalize_reason_tokens(tokens: Sequence[str]) -> Optional[str]:
    cleaned = " ".join(str(token or "").strip() for token in tokens if str(token or "").strip())
    if not cleaned:
        return None
    cleaned = re.sub(r"(\w)-\s+(\w)", r"\1-\2", cleaned)
    cleaned = re.sub(r"\s+-", " -", cleaned)
    cleaned = re.sub(r"\s+([,;:/])", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip() or None


def _row_columns_from_cells(cells: Sequence[Tuple[float, str]]) -> Dict[str, Any]:
    columns = {
        "date": [],
        "time": [],
        "matchup": [],
        "team": [],
        "player": [],
        "status": [],
        "reason": [],
    }
    for x_value, text in sorted(cells, key=lambda item: item[0]):
        if x_value < 100:
            columns["date"].append(text)
        elif x_value < 190:
            columns["time"].append(text)
        elif x_value < 255:
            columns["matchup"].append(text)
        elif x_value < 420:
            columns["team"].append(text)
        elif x_value < 585:
            columns["player"].append(text)
        elif x_value < 665:
            columns["status"].append(text)
        else:
            columns["reason"].append(text)

    return {
        key: " ".join(values).strip()
        for key, values in columns.items()
    }


def _is_table_header_row(row: Dict[str, Any]) -> bool:
    return (
        row.get("date") == "Game Date"
        and row.get("time") == "Game Time"
        and row.get("matchup") == "Matchup"
        and row.get("team") == "Team"
        and row.get("player") == "Player Name"
        and row.get("status") == "Current Status"
        and row.get("reason") == "Reason"
    )


def _extract_pdf_page_rows(pdf_bytes: bytes) -> List[List[Dict[str, Any]]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []

    for page in reader.pages:
        fragments: List[Tuple[float, float, str]] = []

        def visit_text(text, _cm, tm, _font_dict, _font_size):
            clean_text = " ".join((text or "").split()).strip()
            if not clean_text:
                return
            x_value = float(tm[4])
            y_value = float(tm[5])
            if y_value < PAGE_DATA_MIN_Y or y_value > PAGE_DATA_MAX_Y:
                return
            fragments.append((y_value, x_value, clean_text))

        page.extract_text(visitor_text=visit_text)

        row_groups: List[Dict[str, Any]] = []
        for y_value, x_value, text in sorted(fragments, key=lambda item: (item[0], item[1])):
            if not row_groups or abs(y_value - row_groups[-1]["y"]) > ROW_Y_TOLERANCE:
                row_groups.append({"y": y_value, "cells": []})
            row_groups[-1]["cells"].append((x_value, text))

        page_rows = []
        for row_group in row_groups:
            row = _row_columns_from_cells(row_group["cells"])
            row["y"] = row_group["y"]
            if any(row.get(key) for key in ("date", "time", "matchup", "team", "player", "status", "reason")):
                if not _is_table_header_row(row):
                    page_rows.append(row)
        pages.append(page_rows)

    return pages


def _team_match_from_text(
    team_text: str,
    team_aliases: Sequence[Tuple[Tuple[str, ...], str, str]],
) -> Optional[Tuple[str, str]]:
    tokens = [token for token in (_normalize_token(part) for part in team_text.split()) if token]
    if not tokens:
        return None
    team_match = _match_team_alias(tokens, 0, team_aliases)
    if team_match and team_match[2] == len(tokens):
        return team_match[0], team_match[1]
    return None


def _reason_text_from_row(row: Dict[str, Any]) -> Optional[str]:
    parts = [
        reason_text
        for _, reason_text in sorted(row.get("reason_parts", []), key=lambda item: item[0])
        if reason_text
    ]
    if not parts and row.get("reason"):
        parts.append(row["reason"])
    return _normalize_reason_tokens(parts)


def _assign_reason_rows_to_anchors(
    page_rows: Sequence[Dict[str, Any]],
    carryover_entry: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    anchor_rows = []
    reason_only_rows = []

    for row in page_rows:
        has_anchor_fields = any(
            row.get(key)
            for key in ("date", "time", "matchup", "team", "player", "status")
        )
        row["reason_parts"] = []
        if has_anchor_fields:
            anchor_rows.append(row)
        elif row.get("reason"):
            reason_only_rows.append(row)

    for reason_row in reason_only_rows:
        nearest_anchor = None
        nearest_distance = None
        for anchor_row in anchor_rows:
            distance = abs(float(reason_row["y"]) - float(anchor_row["y"]))
            if distance > REASON_ATTACH_Y_TOLERANCE:
                continue
            if nearest_distance is None or distance < nearest_distance:
                nearest_anchor = anchor_row
                nearest_distance = distance

        if nearest_anchor is not None:
            nearest_anchor["reason_parts"].append((reason_row["y"], reason_row["reason"]))
        elif carryover_entry is not None and float(reason_row["y"]) < 120:
            carryover_reasons = carryover_entry.setdefault("_reason_parts", [])
            carryover_reasons.append(reason_row["reason"])

    return anchor_rows, carryover_entry


def _parse_report_page_rows(
    page_rows_by_page: Sequence[Sequence[Dict[str, Any]]],
    team_aliases: Sequence[Tuple[Tuple[str, ...], str, str]],
) -> List[Dict[str, Any]]:
    entries = []
    current_game_date = None
    current_game_time_et = None
    current_matchup = None
    current_team_name = None
    current_team_tricode = None
    carryover_entry = None

    for page_rows in page_rows_by_page:
        anchor_rows, carryover_entry = _assign_reason_rows_to_anchors(page_rows, carryover_entry)

        for row in anchor_rows:
            parsed_date = _parse_date_token(row.get("date"))
            if parsed_date is not None:
                current_game_date = parsed_date.isoformat()

            time_tokens = str(row.get("time") or "").split()
            if time_tokens and TIME_TOKEN_RE.fullmatch(time_tokens[0]):
                current_game_time_et = _normalize_report_time(time_tokens[0])

            matchup_text = str(row.get("matchup") or "").strip().upper()
            if _is_matchup_token(matchup_text):
                current_matchup = matchup_text

            team_text = str(row.get("team") or "").strip()
            team_match = _team_match_from_text(team_text, team_aliases)
            if team_match:
                current_team_name, current_team_tricode = team_match

            reason_text = _reason_text_from_row(row)
            if (
                team_text
                and _normalize_token(reason_text) == "NOTYETSUBMITTED"
                and current_game_date
                and current_matchup
                and current_team_name
                and current_team_tricode
            ):
                entries.append({
                    "game_date": current_game_date,
                    "report_game_time_et": current_game_time_et,
                    "report_matchup": current_matchup,
                    "team_name": current_team_name,
                    "team_tricode": current_team_tricode,
                    "report_player_name": None,
                    "player_name": None,
                    "current_status": "NOT YET SUBMITTED",
                    "reason": None,
                    "submitted": False,
                })
                carryover_entry = None
                continue

            player_text = str(row.get("player") or "").strip()
            status_text = str(row.get("status") or "").strip()
            if (
                player_text
                and status_text in PLAYER_STATUSES
                and current_game_date
                and current_matchup
                and current_team_name
                and current_team_tricode
            ):
                if row.get("reason"):
                    reason_parts = row.setdefault("reason_parts", [])
                    reason_parts.append((row["y"], row["reason"]))

                entry = {
                    "game_date": current_game_date,
                    "report_game_time_et": current_game_time_et,
                    "report_matchup": current_matchup,
                    "team_name": current_team_name,
                    "team_tricode": current_team_tricode,
                    "report_player_name": player_text,
                    "player_name": _normalize_player_name(player_text),
                    "current_status": status_text,
                    "reason": _reason_text_from_row(row),
                    "submitted": True,
                }
                entries.append(entry)
                carryover_entry = entry

        if carryover_entry and carryover_entry.get("_reason_parts"):
            carryover_entry["reason"] = _normalize_reason_tokens([
                carryover_entry.get("reason"),
                *carryover_entry.pop("_reason_parts", []),
            ])

    if carryover_entry and carryover_entry.get("_reason_parts"):
        carryover_entry["reason"] = _normalize_reason_tokens([
            carryover_entry.get("reason"),
            *carryover_entry.pop("_reason_parts", []),
        ])

    return entries


def _looks_like_player_start(tokens: Sequence[str], start_index: int) -> bool:
    if start_index >= len(tokens):
        return False

    comma_index = None
    for idx in range(start_index, min(len(tokens), start_index + 3)):
        token = str(tokens[idx] or "").strip()
        if (
            _parse_date_token(token)
            or _is_matchup_token(token)
            or _is_time_token_pair(tokens, idx)
        ):
            return False
        if "," in token:
            comma_index = idx
            break

    if comma_index is None:
        return False

    for idx in range(comma_index + 1, min(len(tokens), start_index + 8)):
        token = str(tokens[idx] or "").strip()
        if token in PLAYER_STATUSES:
            return True
    return False


def _parse_player_entry(
    tokens: Sequence[str],
    start_index: int,
) -> Optional[Tuple[str, str, str, int]]:
    if not _looks_like_player_start(tokens, start_index):
        return None

    comma_index = None
    for idx in range(start_index, min(len(tokens), start_index + 3)):
        if "," in str(tokens[idx] or "").strip():
            comma_index = idx
            break
    if comma_index is None:
        return None

    for status_index in range(comma_index + 1, min(len(tokens), start_index + 8)):
        status = str(tokens[status_index] or "").strip()
        if status not in PLAYER_STATUSES:
            continue
        name_tokens = [
            str(token or "").strip()
            for token in tokens[start_index:status_index]
            if str(token or "").strip()
        ]
        if not name_tokens or not any("," in token for token in name_tokens):
            continue
        report_name = " ".join(name_tokens).strip()
        return report_name, _normalize_player_name(report_name), status, status_index + 1
    return None


def _extract_pdf_tokens(pdf_bytes: bytes) -> List[str]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    output_tokens = []

    for page in reader.pages:
        page_text = page.extract_text() or ""
        page_tokens = [token.strip() for token in page_text.splitlines() if token.strip()]

        index = 0
        while index < len(page_tokens):
            if (
                page_tokens[index:index + 2] == ["Injury", "Report:"]
                and index + 4 < len(page_tokens)
            ):
                index += 5
                continue
            if (
                page_tokens[index] == "Page"
                and index + 3 < len(page_tokens)
                and str(page_tokens[index + 1]).isdigit()
                and page_tokens[index + 2] == "of"
                and str(page_tokens[index + 3]).isdigit()
            ):
                index += 4
                continue
            if tuple(page_tokens[index:index + len(TABLE_HEADER_TOKENS)]) == TABLE_HEADER_TOKENS:
                index += len(TABLE_HEADER_TOKENS)
                continue

            output_tokens.append(page_tokens[index])
            index += 1

    return output_tokens


def _parse_report_tokens(
    tokens: Sequence[str],
    team_aliases: Sequence[Tuple[Tuple[str, ...], str, str]],
) -> List[Dict[str, Any]]:
    entries = []
    current_game_date = None
    current_game_time_et = None
    current_matchup = None
    current_team_name = None
    current_team_tricode = None

    index = 0
    while index < len(tokens):
        token = str(tokens[index] or "").strip()

        parsed_date = _parse_date_token(token)
        if parsed_date is not None:
            current_game_date = parsed_date.isoformat()
            index += 1
            continue

        if _is_time_token_pair(tokens, index):
            current_game_time_et = _normalize_report_time(tokens[index])
            index += 2
            continue

        if _is_matchup_token(token):
            current_matchup = token
            index += 1
            continue

        team_match = _match_team_alias(tokens, index, team_aliases)
        if team_match:
            current_team_name, current_team_tricode, width = team_match
            index += width
            if tuple(str(tok or "").strip() for tok in tokens[index:index + 3]) == NOT_SUBMITTED_TOKENS:
                entries.append({
                    "game_date": current_game_date,
                    "report_game_time_et": current_game_time_et,
                    "report_matchup": current_matchup,
                    "team_name": current_team_name,
                    "team_tricode": current_team_tricode,
                    "report_player_name": None,
                    "player_name": None,
                    "current_status": "NOT YET SUBMITTED",
                    "reason": None,
                    "submitted": False,
                })
                index += 3
            continue

        player_entry = _parse_player_entry(tokens, index)
        if player_entry and current_team_name and current_team_tricode and current_matchup and current_game_date:
            report_player_name, player_name, current_status, index = player_entry
            reason_tokens = []
            while index < len(tokens):
                if (
                    _parse_date_token(tokens[index]) is not None
                    or _is_time_token_pair(tokens, index)
                    or _is_matchup_token(tokens[index])
                    or _match_team_alias(tokens, index, team_aliases)
                    or _looks_like_player_start(tokens, index)
                ):
                    break
                reason_tokens.append(tokens[index])
                index += 1

            entries.append({
                "game_date": current_game_date,
                "report_game_time_et": current_game_time_et,
                "report_matchup": current_matchup,
                "team_name": current_team_name,
                "team_tricode": current_team_tricode,
                "report_player_name": report_player_name,
                "player_name": player_name,
                "current_status": current_status,
                "reason": _normalize_reason_tokens(reason_tokens),
                "submitted": True,
            })
            continue

        index += 1

    return entries


def _group_entries_by_game(
    entries: Sequence[Dict[str, Any]],
    schedule_index: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    games_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for entry in entries:
        report_matchup = str(entry.get("report_matchup") or "").strip().upper()
        game_date = str(entry.get("game_date") or "").strip()
        if not report_matchup or "@" not in report_matchup or not game_date:
            continue

        away_tricode, home_tricode = (
            _normalize_team_code(value)
            for value in report_matchup.split("@", 1)
        )
        if not away_tricode or not home_tricode:
            continue

        game_key = (game_date, away_tricode, home_tricode)
        schedule_game = schedule_index.get(game_key, {})
        game_record = games_by_key.setdefault(
            game_key,
            {
                "game_id": schedule_game.get("game_id"),
                "game_date": schedule_game.get("game_date") or game_date,
                "report_matchup": report_matchup,
                "matchup": schedule_game.get("matchup") or f"{away_tricode} @ {home_tricode}",
                "away_team_tricode": schedule_game.get("away_team_tricode") or away_tricode,
                "home_team_tricode": schedule_game.get("home_team_tricode") or home_tricode,
                "away_team_name": schedule_game.get("away_team_name"),
                "home_team_name": schedule_game.get("home_team_name"),
                "game_time_utc": schedule_game.get("game_time_utc"),
                "game_time_et": schedule_game.get("game_time_et") or entry.get("report_game_time_et"),
                "closing_scrape_deadline": schedule_game.get("closing_scrape_deadline"),
                "teams": {},
            },
        )

        team_tricode = _normalize_team_code(entry.get("team_tricode"))
        team_name = str(entry.get("team_name") or "").strip() or team_tricode
        if not team_tricode:
            continue

        team_record = game_record["teams"].setdefault(
            team_tricode,
            {
                "team_tricode": team_tricode,
                "team_name": team_name,
                "report_status": "submitted",
                "players": [],
            },
        )

        if not entry.get("submitted", True):
            team_record["report_status"] = "not_submitted"
            continue

        team_record["report_status"] = "submitted"
        team_record["players"].append({
            "player_name": entry.get("player_name"),
            "report_player_name": entry.get("report_player_name"),
            "current_status": entry.get("current_status"),
            "reason": entry.get("reason"),
        })

    return sorted(
        games_by_key.values(),
        key=lambda game: (
            str(game.get("game_date") or ""),
            str(game.get("game_time_utc") or game.get("game_time_et") or ""),
            str(game.get("matchup") or ""),
        ),
    )


def fetch_latest_report_pdf_url(
    *,
    query_date: str,
    timeout_seconds: int = 20,
) -> Tuple[str, str, str]:
    report_page_url = _report_page_url_for_date(query_date)
    response = requests.get(report_page_url, headers=HEADERS, timeout=timeout_seconds)
    response.raise_for_status()

    target_date = datetime.strptime(query_date, "%Y%m%d").date()
    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    for anchor in soup.find_all("a", href=True):
        report_url = urljoin(report_page_url, str(anchor.get("href") or "").strip())
        report_dt = _parse_report_timestamp_from_url(report_url)
        if report_dt and report_dt.date() == target_date:
            candidates.append((report_dt, report_url))

    if not candidates:
        raise ValueError(f"No injury report PDF links found for {target_date.isoformat()} at {report_page_url}")

    report_dt, report_pdf_url = max(candidates, key=lambda item: item[0])
    return report_page_url, report_pdf_url, report_dt.isoformat()


def fetch_official_injury_report_payload(
    *,
    query_date: Optional[str] = None,
    days_ahead: int = 0,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
) -> Dict[str, Any]:
    resolved_query_date = resolve_query_date(query_date, days_ahead=days_ahead)
    report_page_url, report_pdf_url, report_timestamp_et = fetch_latest_report_pdf_url(
        query_date=resolved_query_date,
    )

    logger.info(
        "[RUN] Official NBA injury report fetch | report_date=%s pdf=%s",
        datetime.strptime(resolved_query_date, "%Y%m%d").date().isoformat(),
        report_pdf_url,
    )
    response = requests.get(report_pdf_url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    schedule_rows = load_schedule_rows(schedule_path)
    team_aliases = _build_team_aliases(schedule_rows)
    schedule_index = _build_schedule_index(schedule_rows)

    page_rows_by_page = _extract_pdf_page_rows(response.content)
    entries = _parse_report_page_rows(page_rows_by_page, team_aliases)
    games = _group_entries_by_game(entries, schedule_index)

    player_rows = sum(
        len((team.get("players") or []))
        for game in games
        for team in (game.get("teams") or {}).values()
        if isinstance(team, dict)
    )
    not_submitted_teams = sum(
        1
        for game in games
        for team in (game.get("teams") or {}).values()
        if isinstance(team, dict) and team.get("report_status") == "not_submitted"
    )

    payload = {
        "source": "nba_official_injury_report",
        "query_date": datetime.strptime(resolved_query_date, "%Y%m%d").date().isoformat(),
        "generated_at": get_et_now().isoformat(),
        "report_page_url": report_page_url,
        "report_pdf_url": report_pdf_url,
        "report_timestamp_et": report_timestamp_et,
        "game_count": len(games),
        "player_row_count": player_rows,
        "not_submitted_team_count": not_submitted_teams,
        "games": games,
    }

    logger.info(
        "[OK] Official NBA injury report parsed | games=%d players=%d not_submitted_teams=%d",
        len(games),
        player_rows,
        not_submitted_teams,
    )
    return payload


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w") as handle:
        json.dump(payload, handle, indent=2)
    temp_path.replace(path)


def _load_existing_payload(output_path: Path) -> Optional[Dict[str, Any]]:
    if not output_path.exists():
        return None
    try:
        with output_path.open("r") as handle:
            payload = json.load(handle)
    except Exception as exc:
        logger.warning("Unable to load existing NBA injury report artifact %s: %s", output_path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _is_existing_payload_fresh(
    payload: Dict[str, Any],
    *,
    query_date: str,
    min_refresh_interval_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    if min_refresh_interval_seconds <= 0:
        return False

    expected_query_date = datetime.strptime(query_date, "%Y%m%d").date().isoformat()
    if str(payload.get("query_date") or "").strip() != expected_query_date:
        return False

    generated_at = str(payload.get("generated_at") or "").strip()
    if not generated_at:
        return False

    try:
        generated_dt = datetime.fromisoformat(generated_at)
    except ValueError:
        return False

    if generated_dt.tzinfo is None:
        generated_dt = generated_dt.replace(tzinfo=ET_ZONE)

    now = now or get_et_now()
    age_seconds = (now - generated_dt.astimezone(ET_ZONE)).total_seconds()
    return age_seconds < min_refresh_interval_seconds


def _games_fingerprint(games: Sequence[Dict[str, Any]]) -> str:
    serialized = json.dumps(games or [], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def write_current_and_archive(
    payload: Dict[str, Any],
    *,
    output_path: Path = CURRENT_OUTPUT_PATH,
    archive_dir: Path = ARCHIVE_DIR,
    archive_enabled: bool = True,
) -> bool:
    _write_json(output_path, payload)
    if not archive_enabled:
        return True

    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{payload['query_date']}.json"
    existing = {}
    if archive_path.exists():
        try:
            with archive_path.open("r") as handle:
                existing = json.load(handle)
        except Exception:
            existing = {}

    if not isinstance(existing, dict):
        existing = {}

    snapshots = existing.get("snapshots") if isinstance(existing.get("snapshots"), list) else []
    fingerprint = _games_fingerprint(payload.get("games", []))
    last_fingerprint = (
        snapshots[-1].get("fingerprint")
        if snapshots and isinstance(snapshots[-1], dict)
        else None
    )
    if fingerprint == last_fingerprint:
        return False

    snapshots.append({
        "captured_at": payload.get("generated_at"),
        "fingerprint": fingerprint,
        "report_pdf_url": payload.get("report_pdf_url"),
        "report_timestamp_et": payload.get("report_timestamp_et"),
        "game_count": payload.get("game_count"),
        "player_row_count": payload.get("player_row_count"),
        "not_submitted_team_count": payload.get("not_submitted_team_count"),
        "games": payload.get("games", []),
    })
    _write_json(
        archive_path,
        {
            "source": payload.get("source"),
            "query_date": payload.get("query_date"),
            "report_page_url": payload.get("report_page_url"),
            "snapshots": snapshots,
        },
    )
    return True


def refresh_nba_injury_report_if_needed(
    *,
    query_date: Optional[str] = None,
    days_ahead: int = 0,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    output_path: Path = CURRENT_OUTPUT_PATH,
    archive_dir: Path = ARCHIVE_DIR,
    min_refresh_interval_seconds: int = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
) -> Dict[str, Any]:
    resolved_query_date = resolve_query_date(query_date, days_ahead=days_ahead)
    existing_payload = _load_existing_payload(output_path)

    if existing_payload and _is_existing_payload_fresh(
        existing_payload,
        query_date=resolved_query_date,
        min_refresh_interval_seconds=min_refresh_interval_seconds,
    ):
        logger.debug(
            "[SKIP] NBA injury report fetch skipped; recent artifact is still fresh | "
            "date=%s output=%s",
            existing_payload.get("query_date"),
            output_path,
        )
        return {
            "payload": existing_payload,
            "refreshed": False,
            "output_path": str(output_path),
            "archive_updated": False,
        }

    payload = fetch_official_injury_report_payload(
        query_date=resolved_query_date,
        schedule_path=schedule_path,
    )
    archive_updated = write_current_and_archive(
        payload,
        output_path=output_path,
        archive_dir=archive_dir,
        archive_enabled=True,
    )
    return {
        "payload": payload,
        "refreshed": True,
        "output_path": str(output_path),
        "archive_updated": archive_updated,
    }


def main() -> int:
    args = _parse_args()
    payload = fetch_official_injury_report_payload(
        query_date=args.date,
        days_ahead=args.days_ahead,
        schedule_path=Path(args.schedule_path),
    )
    archive_updated = write_current_and_archive(
        payload,
        output_path=Path(args.output_path),
        archive_dir=Path(args.archive_dir),
        archive_enabled=not args.no_archive,
    )
    if args.stdout:
        print(json.dumps(payload, indent=2))
    logger.info(
        "Official NBA injury report artifact written | output=%s archive_updated=%s",
        args.output_path,
        archive_updated,
    )
    return 0


if __name__ == "__main__":
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    raise SystemExit(main())
