import argparse
import csv
import json
import os
import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

PRIZEPICKS_URL = "https://api.prizepicks.com/projections"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "current" / "prizepicks.csv"
DEFAULT_STATS_PATH = Path(__file__).resolve().parents[1] / "data" / "current" / "season_stats.csv"
DEFAULT_SCHEDULE_PATH = Path(__file__).resolve().parents[1] / "data" / "current" / "today_schedule.json"

DEFAULT_PARAMS = {
    "league_id": 7,
    "per_page": 250,
    "single_stat": "true",
    "in_game": "true",
    "state_code": "MO",
    "game_mode": "prizepools",
}

CSV_COLUMNS = [
    "player",
    "raw_player_name",
    "team",
    "prop_type",
    "prop_label",
    "line",
    "over_odds",
    "under_odds",
    "implied_prob",
    "game",
    "opponent",
    "game_date",
    "start_time",
    "updated_at",
    "game_id",
    "sportsbook",
]

SOURCE_ID_COLUMNS = [
    "projection_id",
    "source_game_id",
]

BASE_HEADERS = {
    "accept": "application/json",
    "accept-encoding": "gzip, deflate",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "no-cache",
    "content-type": "application/json",
    "dnt": "1",
    "origin": "https://app.prizepicks.com",
    "pragma": "no-cache",
    "priority": "u=1, i",
    "referer": "https://app.prizepicks.com/",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
}

PROP_TYPE_MAP = {
    "3PTM": "threes",
    "3-PT MADE": "threes",
    "ASSISTS": "assists",
    "BLKS+STLS": "stocks",
    "BLOCKED SHOTS": "blocks",
    "BLOCKS": "blocks",
    "FANTASY SCORE": "fantasy",
    "FG ATTEMPTED": "fga",
    "FIELD GOAL ATTEMPTS": "fga",
    "FTA": "fta",
    "FREE THROW ATTEMPTS": "fta",
    "PERSONAL FOULS": "fouls",
    "POINTS": "points",
    "PRA": "pra",
    "PTS+ASTS": "pa",
    "PTS+REBS": "pr",
    "PTS+REBS+ASTS": "pra",
    "RA": "ra",
    "REBOUNDS": "rebounds",
    "REBS+ASTS": "ra",
    "STEALS": "steals",
    "STEALS+BLOCKS": "stocks",
    "TURNOVERS": "turnovers",
}

# Keep this intentionally conservative for the first integration pass.
# These are the prop families already normalized through the current dashboard
# player_props flow in backend/utils/upsert_props.py.
DASHBOARD_PROP_TYPES = {
    "points",
    "rebounds",
    "assists",
    "threes",
    "blocks",
    "steals",
    "pra",
    "pr",
    "pa",
    "ra",
    "stocks",
}

VALID_FETCH_MODES = {"proxy_first", "proxy_only", "direct_only"}
FETCH_RETRY_DELAY_SECONDS = 1.0
FETCH_RETRY_ATTEMPTS_PER_ROUTE = 3
DEFAULT_CACHE_MAX_AGE_SECONDS = 6 * 60 * 60

TEAM_NAME_TO_ABBREV = {
    "ATL": "ATL",
    "ATLANTA": "ATL",
    "ATLANTA HAWKS": "ATL",
    "HAWKS": "ATL",
    "BOS": "BOS",
    "BOSTON": "BOS",
    "BOSTON CELTICS": "BOS",
    "CELTICS": "BOS",
    "BKN": "BKN",
    "BROOKLYN": "BKN",
    "BROOKLYN NETS": "BKN",
    "NETS": "BKN",
    "CHA": "CHA",
    "CHARLOTTE": "CHA",
    "CHARLOTTE HORNETS": "CHA",
    "HORNETS": "CHA",
    "CHI": "CHI",
    "CHICAGO": "CHI",
    "CHICAGO BULLS": "CHI",
    "BULLS": "CHI",
    "CLE": "CLE",
    "CLEVELAND": "CLE",
    "CLEVELAND CAVALIERS": "CLE",
    "CAVALIERS": "CLE",
    "CAVS": "CLE",
    "DAL": "DAL",
    "DALLAS": "DAL",
    "DALLAS MAVERICKS": "DAL",
    "MAVERICKS": "DAL",
    "DEN": "DEN",
    "DENVER": "DEN",
    "DENVER NUGGETS": "DEN",
    "NUGGETS": "DEN",
    "DET": "DET",
    "DETROIT": "DET",
    "DETROIT PISTONS": "DET",
    "PISTONS": "DET",
    "GSW": "GSW",
    "GOLDEN STATE": "GSW",
    "GOLDEN STATE WARRIORS": "GSW",
    "WARRIORS": "GSW",
    "HOU": "HOU",
    "HOUSTON": "HOU",
    "HOUSTON ROCKETS": "HOU",
    "ROCKETS": "HOU",
    "IND": "IND",
    "INDIANA": "IND",
    "INDIANA PACERS": "IND",
    "PACERS": "IND",
    "LAC": "LAC",
    "LA CLIPPERS": "LAC",
    "LOS ANGELES CLIPPERS": "LAC",
    "CLIPPERS": "LAC",
    "LAL": "LAL",
    "LA LAKERS": "LAL",
    "LOS ANGELES LAKERS": "LAL",
    "LAKERS": "LAL",
    "MEM": "MEM",
    "MEMPHIS": "MEM",
    "MEMPHIS GRIZZLIES": "MEM",
    "GRIZZLIES": "MEM",
    "MIA": "MIA",
    "MIAMI": "MIA",
    "MIAMI HEAT": "MIA",
    "HEAT": "MIA",
    "MIL": "MIL",
    "MILWAUKEE": "MIL",
    "MILWAUKEE BUCKS": "MIL",
    "BUCKS": "MIL",
    "MIN": "MIN",
    "MINNESOTA": "MIN",
    "MINNESOTA TIMBERWOLVES": "MIN",
    "TIMBERWOLVES": "MIN",
    "WOLVES": "MIN",
    "NOP": "NOP",
    "NEW ORLEANS": "NOP",
    "NEW ORLEANS PELICANS": "NOP",
    "PELICANS": "NOP",
    "NYK": "NYK",
    "NEW YORK": "NYK",
    "NEW YORK KNICKS": "NYK",
    "KNICKS": "NYK",
    "OKC": "OKC",
    "OKLAHOMA CITY": "OKC",
    "OKLAHOMA CITY THUNDER": "OKC",
    "THUNDER": "OKC",
    "ORL": "ORL",
    "ORLANDO": "ORL",
    "ORLANDO MAGIC": "ORL",
    "MAGIC": "ORL",
    "PHI": "PHI",
    "PHILADELPHIA": "PHI",
    "PHILADELPHIA 76ERS": "PHI",
    "PHILADELPHIA SIXERS": "PHI",
    "76ERS": "PHI",
    "SIXERS": "PHI",
    "PHX": "PHX",
    "PHOENIX": "PHX",
    "PHOENIX SUNS": "PHX",
    "SUNS": "PHX",
    "POR": "POR",
    "PORTLAND": "POR",
    "PORTLAND TRAIL BLAZERS": "POR",
    "TRAIL BLAZERS": "POR",
    "BLAZERS": "POR",
    "SAC": "SAC",
    "SACRAMENTO": "SAC",
    "SACRAMENTO KINGS": "SAC",
    "KINGS": "SAC",
    "SAS": "SAS",
    "SAN ANTONIO": "SAS",
    "SAN ANTONIO SPURS": "SAS",
    "SPURS": "SAS",
    "TOR": "TOR",
    "TORONTO": "TOR",
    "TORONTO RAPTORS": "TOR",
    "RAPTORS": "TOR",
    "UTA": "UTA",
    "UTAH": "UTA",
    "UTAH JAZZ": "UTA",
    "JAZZ": "UTA",
    "WAS": "WAS",
    "WASHINGTON": "WAS",
    "WASHINGTON WIZARDS": "WAS",
    "WIZARDS": "WAS",
}


def build_headers() -> Dict[str, str]:
    headers = dict(BASE_HEADERS)

    cookie = os.getenv("PRIZEPICKS_COOKIE")
    if cookie:
        headers["cookie"] = cookie

    device_id = os.getenv("PRIZEPICKS_DEVICE_ID")
    if device_id:
        headers["x-device-id"] = device_id

    device_info = os.getenv("PRIZEPICKS_DEVICE_INFO")
    if device_info:
        headers["x-device-info"] = device_info

    return headers


def prizepicks_enabled() -> bool:
    return str(os.getenv("ENABLE_PRIZEPICKS", "false")).strip().lower() in {"1", "true", "yes", "on"}


def get_fetch_mode() -> str:
    raw_value = str(os.getenv("PRIZEPICKS_FETCH_MODE", "proxy_first")).strip().lower()
    return raw_value if raw_value in VALID_FETCH_MODES else "proxy_first"


def build_prepared_url() -> str:
    return requests.Request("GET", PRIZEPICKS_URL, params=DEFAULT_PARAMS).prepare().url


def run_request(url: str, headers: Dict[str, str], timeout: int, via_proxy: bool) -> requests.Response:
    if via_proxy:
        proxy_url = os.getenv("PBPSTATS_PROXY_URL")
        if not proxy_url:
            raise RuntimeError("PBPSTATS_PROXY_URL is not set.")
        return requests.get(proxy_url, params={"url": url}, headers=headers, timeout=timeout)
    return requests.get(url, headers=headers, timeout=timeout)


def fetch_payload(mode: str = "proxy_first", timeout: int = 20) -> Dict[str, Any]:
    url = build_prepared_url()
    headers = build_headers()

    routes: List[Tuple[str, bool]] = []
    if mode == "proxy_only":
        routes = [("proxy", True)]
    elif mode == "direct_only":
        routes = [("direct", False)]
    else:
        if os.getenv("PBPSTATS_PROXY_URL"):
            routes.append(("proxy", True))
        routes.append(("direct", False))

    last_error = None
    for label, via_proxy in routes:
        for attempt in range(1, FETCH_RETRY_ATTEMPTS_PER_ROUTE + 1):
            try:
                response = run_request(url, headers=headers, timeout=timeout, via_proxy=via_proxy)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                attempt_suffix = f" (attempt {attempt}/{FETCH_RETRY_ATTEMPTS_PER_ROUTE})"
                last_error = RuntimeError(f"{label} request failed{attempt_suffix}: {exc}")
                if attempt < FETCH_RETRY_ATTEMPTS_PER_ROUTE:
                    time.sleep(FETCH_RETRY_DELAY_SECONDS * attempt)

    if last_error:
        raise last_error
    raise RuntimeError("No request routes were attempted.")


def normalize_player_name(name: Any) -> str:
    if not isinstance(name, str):
        return "unknown_player"
    value = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("utf-8")
    value = value.lower().strip().replace(".", "").replace("'", "")
    value = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", value).strip()
    return " ".join(value.split()) or "unknown_player"


def normalize_team_code(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = unicodedata.normalize("NFKD", value).encode("ASCII", "ignore").decode("utf-8")
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", cleaned).upper().strip()
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return ""
    return TEAM_NAME_TO_ABBREV.get(cleaned, TEAM_NAME_TO_ABBREV.get(cleaned.replace(" ", ""), ""))


def normalize_prop_type(prop_label: Any) -> str:
    if not isinstance(prop_label, str):
        return "unknown_prop"
    cleaned = unicodedata.normalize("NFKD", prop_label).encode("ASCII", "ignore").decode("utf-8")
    cleaned = re.sub(r"\s+", " ", cleaned).upper().strip()
    mapped = PROP_TYPE_MAP.get(cleaned)
    if mapped:
        return mapped
    return re.sub(r"[^a-z0-9]+", "_", prop_label.lower()).strip("_") or "unknown_prop"


def parse_iso_datetime(raw_value: Any) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        return datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None


def safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_team_lookup(stats_path: Path) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    if not stats_path.exists():
        return lookup

    with stats_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            player_name = row.get("PLAYER_NAME") or row.get("player_name") or ""
            team = normalize_team_code(row.get("TEAM_ABBREVIATION") or row.get("team") or "")
            if player_name and team:
                lookup.setdefault(normalize_player_name(player_name), team)
    return lookup


def load_schedule_rows(schedule_path: Path) -> List[Dict[str, Any]]:
    if not schedule_path.exists():
        return []

    try:
        with schedule_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return []

    if isinstance(payload, dict):
        games = payload.get("games", [])
        return games if isinstance(games, list) else []

    if isinstance(payload, list):
        return payload

    return []


def build_schedule_index(schedule_rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for game in schedule_rows:
        game_id = str(game.get("game_id") or "").strip()
        game_date = str(game.get("game_date") or "").strip()[:10]
        if not game_id or not game_date:
            continue

        home = normalize_team_code(game.get("home_team_tricode") or game.get("home_team_name") or game.get("home_team"))
        away = normalize_team_code(game.get("away_team_tricode") or game.get("away_team_name") or game.get("away_team"))

        for team in (home, away):
            if not team:
                continue
            index.setdefault((team, game_date), []).append(game)
    return index


def resolve_schedule_game_id(
    team: str,
    opponent: str,
    game_date: str,
    matchup: str,
    schedule_index: Dict[Tuple[str, str], List[Dict[str, Any]]],
) -> str:
    if not team or not game_date:
        return ""

    candidates = schedule_index.get((team, game_date), [])
    if not candidates:
        return ""

    if len(candidates) == 1:
        return str(candidates[0].get("game_id") or "")

    normalized_matchup = str(matchup or "").strip().upper()
    for game in candidates:
        home = normalize_team_code(game.get("home_team_tricode") or game.get("home_team_name") or game.get("home_team"))
        away = normalize_team_code(game.get("away_team_tricode") or game.get("away_team_name") or game.get("away_team"))
        candidate_matchup = f"{away} @ {home}" if away and home else ""
        if normalized_matchup and normalized_matchup == candidate_matchup:
            return str(game.get("game_id") or "")
        if opponent and opponent in {home, away}:
            return str(game.get("game_id") or "")

    return str(candidates[0].get("game_id") or "")


def build_resource_index(payload: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for item in payload.get("included", []) or []:
        resource_type = item.get("type")
        resource_id = item.get("id")
        if resource_type is not None and resource_id is not None:
            index[(str(resource_type), str(resource_id))] = item
    return index


def get_relationship_resource(
    resource: Dict[str, Any],
    relationship_name: str,
    resource_index: Dict[Tuple[str, str], Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    data = ((resource.get("relationships") or {}).get(relationship_name) or {}).get("data")
    if not isinstance(data, dict):
        return None
    resource_type = data.get("type")
    resource_id = data.get("id")
    if resource_type is None or resource_id is None:
        return None
    return resource_index.get((str(resource_type), str(resource_id)))


def extract_player_name(player_resource: Dict[str, Any]) -> str:
    attrs = player_resource.get("attributes") or {}
    for key in ("display_name", "name", "full_name", "player_name"):
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_team_from_attrs(attrs: Dict[str, Any]) -> str:
    for key in (
        "team",
        "team_name",
        "team_abbreviation",
        "team_tricode",
        "abbreviation",
        "tricode",
        "short_name",
        "name",
    ):
        code = normalize_team_code(attrs.get(key))
        if code:
            return code
    return ""


def extract_player_team(
    player_resource: Dict[str, Any],
    resource_index: Dict[Tuple[str, str], Dict[str, Any]],
    roster_lookup: Dict[str, str],
) -> str:
    attrs = player_resource.get("attributes") or {}
    team = extract_team_from_attrs(attrs)
    if team:
        return team

    for relationship_name in ("team", "league_team", "pro_team"):
        related = get_relationship_resource(player_resource, relationship_name, resource_index)
        if related:
            team = extract_team_from_attrs(related.get("attributes") or {})
            if team:
                return team

    player_name = extract_player_name(player_resource)
    return roster_lookup.get(normalize_player_name(player_name), "")


def parse_matchup_text(raw_value: Any) -> Tuple[str, List[str]]:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return "", []

    text = raw_value.strip()
    for separator in (" @ ", "/", " vs ", " v "):
        if separator not in text:
            continue
        parts = [part.strip() for part in text.split(separator) if part.strip()]
        if len(parts) != 2:
            continue
        teams = [normalize_team_code(part) or part.upper() for part in parts]
        return f"{teams[0]} @ {teams[1]}", teams

    return text, []


def extract_matchup(
    projection_attrs: Dict[str, Any],
    game_resource: Optional[Dict[str, Any]],
    resource_index: Dict[Tuple[str, str], Dict[str, Any]],
) -> Tuple[str, List[str]]:
    fallback_matchup, fallback_teams = parse_matchup_text(projection_attrs.get("description"))
    if not game_resource:
        return fallback_matchup, fallback_teams

    game_attrs = game_resource.get("attributes") or {}
    away = ""
    home = ""

    for key in ("away_team", "away_team_abbreviation", "away_team_tricode", "away"):
        away = normalize_team_code(game_attrs.get(key))
        if away:
            break

    for key in ("home_team", "home_team_abbreviation", "home_team_tricode", "home"):
        home = normalize_team_code(game_attrs.get(key))
        if home:
            break

    if not away:
        related = get_relationship_resource(game_resource, "away_team", resource_index)
        if related:
            away = extract_team_from_attrs(related.get("attributes") or {})

    if not home:
        related = get_relationship_resource(game_resource, "home_team", resource_index)
        if related:
            home = extract_team_from_attrs(related.get("attributes") or {})

    if away and home:
        return f"{away} @ {home}", [away, home]

    metadata = game_attrs.get("metadata") or {}
    game_info = metadata.get("game_info") or {}
    teams = game_info.get("teams") or {}
    away_meta = teams.get("away") or {}
    home_meta = teams.get("home") or {}
    away = normalize_team_code(away_meta.get("abbreviation") or away_meta.get("name"))
    home = normalize_team_code(home_meta.get("abbreviation") or home_meta.get("name"))
    if away and home:
        return f"{away} @ {home}", [away, home]

    for key in ("description", "matchup", "name", "title"):
        matchup, teams = parse_matchup_text(game_attrs.get(key))
        if matchup:
            return matchup, teams

    return fallback_matchup, fallback_teams


def derive_opponent(team: str, matchup_teams: List[str]) -> str:
    if team and len(matchup_teams) == 2 and team in matchup_teams:
        return matchup_teams[1] if matchup_teams[0] == team else matchup_teams[0]
    return ""


def is_combo_projection(projection_attrs: Dict[str, Any], player_resource: Dict[str, Any], prop_label: str) -> bool:
    if bool((player_resource.get("attributes") or {}).get("combo")):
        return True

    event_type = str(projection_attrs.get("event_type") or "").lower()
    stat_type = str(projection_attrs.get("stat_type") or "").lower()
    label = str(prop_label or "").lower()
    return event_type == "combo" or "combo" in stat_type or "combo" in label


def is_player_projection(projection_attrs: Dict[str, Any], player_name: str) -> bool:
    return bool(player_name)


def is_full_game(duration_resource: Optional[Dict[str, Any]], duration_id: str) -> bool:
    attrs = (duration_resource or {}).get("attributes") or {}
    label = " ".join(
        str(attrs.get(key) or "")
        for key in ("name", "display_name", "short_name", "abbreviation", "description")
    ).lower()

    if label:
        if any(token in label for token in ("quarter", "q1", "q2", "q3", "q4", "1h", "2h", "half")):
            return False
        if any(token in label for token in ("full", "game")):
            return True

    return duration_id in {"", "11"}


def projection_priority(attrs: Dict[str, Any]) -> Tuple[int, int, int, int, int, float]:
    try:
        rank = int(attrs.get("rank"))
    except (TypeError, ValueError):
        rank = 9999

    updated_at = parse_iso_datetime(attrs.get("updated_at") or attrs.get("board_time"))
    updated_score = -updated_at.timestamp() if updated_at else 0.0

    return (
        0 if str(attrs.get("odds_type") or "").lower() == "standard" else 1,
        0 if not attrs.get("adjusted_odds") else 1,
        0 if not attrs.get("is_promo") else 1,
        0 if attrs.get("flash_sale_line_score") in (None, "") else 1,
        rank,
        updated_score,
    )


def is_live_projection(attrs: Dict[str, Any]) -> bool:
    if attrs.get("is_live") or attrs.get("in_game"):
        return True

    status = str(attrs.get("status") or "").strip().lower()
    return status in {
        "in_progress",
        "live",
        "final",
        "closed",
        "settled",
        "graded",
    }


def parse_player_projections(
    payload: Dict[str, Any],
    stats_path: Path = DEFAULT_STATS_PATH,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    dashboard_only: bool = True,
    include_source_ids: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    resource_index = build_resource_index(payload)
    roster_lookup = load_team_lookup(stats_path)
    schedule_index = build_schedule_index(load_schedule_rows(schedule_path))
    diagnostics = {
        "raw_projection_count": 0,
        "candidate_count": 0,
        "parsed_count": 0,
        "skipped_missing_player": 0,
        "skipped_team_projection": 0,
        "skipped_combo_projection": 0,
        "skipped_non_single_stat": 0,
        "skipped_non_full_game": 0,
        "skipped_live_projection": 0,
        "skipped_missing_line": 0,
        "skipped_unsupported_prop": 0,
        "deduped_out": 0,
        "rows_with_internal_game_id": 0,
    }

    best_rows: Dict[str, Tuple[Tuple[int, int, int, int, int, float], Dict[str, Any]]] = {}

    for projection in payload.get("data", []) or []:
        if projection.get("type") != "projection":
            continue

        diagnostics["raw_projection_count"] += 1
        attrs = projection.get("attributes") or {}
        relationships = projection.get("relationships") or {}

        player_ref = (relationships.get("new_player") or {}).get("data")
        if not isinstance(player_ref, dict):
            diagnostics["skipped_missing_player"] += 1
            continue

        player_resource = resource_index.get((str(player_ref.get("type")), str(player_ref.get("id"))))
        if not player_resource:
            diagnostics["skipped_missing_player"] += 1
            continue

        if str(attrs.get("projection_type") or "").lower() not in {"", "single stat", "fantasy score"}:
            diagnostics["skipped_non_single_stat"] += 1
            continue

        player_name = extract_player_name(player_resource)
        if not is_player_projection(attrs, player_name):
            diagnostics["skipped_team_projection"] += 1
            continue

        stat_resource = get_relationship_resource(projection, "stat_type", resource_index)
        stat_name = str(((stat_resource or {}).get("attributes") or {}).get("name") or "").strip()
        prop_label = str(attrs.get("stat_display_name") or stat_name or attrs.get("stat_type") or "").strip()

        if is_combo_projection(attrs, player_resource, prop_label):
            diagnostics["skipped_combo_projection"] += 1
            continue

        duration_ref = (relationships.get("duration") or {}).get("data")
        duration_id = str(duration_ref.get("id")) if isinstance(duration_ref, dict) and duration_ref.get("id") is not None else ""
        duration_resource = resource_index.get(("duration", duration_id)) if duration_id else None
        if not is_full_game(duration_resource, duration_id):
            diagnostics["skipped_non_full_game"] += 1
            continue

        if is_live_projection(attrs):
            diagnostics["skipped_live_projection"] += 1
            continue

        line = safe_float(attrs.get("line_score"))
        if line is None:
            diagnostics["skipped_missing_line"] += 1
            continue

        game_resource = get_relationship_resource(projection, "game", resource_index)
        matchup, matchup_teams = extract_matchup(attrs, game_resource, resource_index)
        team = extract_player_team(player_resource, resource_index, roster_lookup)
        opponent = derive_opponent(team, matchup_teams)
        start_time = str(attrs.get("start_time") or ((game_resource or {}).get("attributes") or {}).get("start_time") or "")
        game_date = ""
        parsed_start = parse_iso_datetime(start_time or attrs.get("board_time"))
        if parsed_start:
            game_date = parsed_start.date().isoformat()

        row = {
            "player": normalize_player_name(player_name),
            "raw_player_name": player_name,
            "team": team,
            "prop_type": normalize_prop_type(prop_label),
            "prop_label": prop_label,
            "line": line,
            "over_odds": None,
            "under_odds": None,
            "implied_prob": None,
            "game": matchup,
            "opponent": opponent,
            "game_date": game_date,
            "start_time": start_time,
            "updated_at": str(attrs.get("updated_at") or attrs.get("board_time") or ""),
            "sportsbook": "pp",
        }

        if dashboard_only and row["prop_type"] not in DASHBOARD_PROP_TYPES:
            diagnostics["skipped_unsupported_prop"] += 1
            continue

        row["game_id"] = resolve_schedule_game_id(
            team=row["team"],
            opponent=row["opponent"],
            game_date=row["game_date"],
            matchup=row["game"],
            schedule_index=schedule_index,
        )

        if include_source_ids:
            row["projection_id"] = str(projection.get("id") or "")
            row["source_game_id"] = str(attrs.get("game_id") or "")

        dedupe_key = str(attrs.get("group_key") or "") or "|".join(
            [
                row["player"],
                row["prop_type"],
                row["game_id"],
                duration_id,
            ]
        )

        diagnostics["candidate_count"] += 1
        priority = projection_priority(attrs)
        existing = best_rows.get(dedupe_key)
        if existing is None or priority < existing[0]:
            best_rows[dedupe_key] = (priority, row)

    rows = [entry[1] for entry in best_rows.values()]
    rows.sort(key=lambda row: (row["game_date"], row["game"], row["raw_player_name"], row["prop_type"]))

    diagnostics["parsed_count"] = len(rows)
    diagnostics["deduped_out"] = diagnostics["candidate_count"] - len(rows)
    diagnostics["rows_with_internal_game_id"] = sum(1 for row in rows if row.get("game_id"))
    return rows, diagnostics


def write_rows(rows: List[Dict[str, Any]], output_path: Path, include_source_ids: bool = False) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(CSV_COLUMNS)
    if include_source_ids:
        fieldnames.extend(SOURCE_ID_COLUMNS)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in fieldnames})


def write_json(payload: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def cached_output_path_if_recent(
    output_path: Path = DEFAULT_OUTPUT_PATH,
    max_age_seconds: int = DEFAULT_CACHE_MAX_AGE_SECONDS,
) -> Optional[Path]:
    try:
        if not output_path.exists() or output_path.stat().st_size == 0:
            return None
        age_seconds = time.time() - output_path.stat().st_mtime
        if age_seconds > max_age_seconds:
            return None
        return output_path
    except OSError:
        return None


def fetch_and_write_rows(
    *,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    raw_output_path: Optional[Path] = None,
    mode: Optional[str] = None,
    timeout: int = 20,
    stats_path: Path = DEFAULT_STATS_PATH,
    schedule_path: Path = DEFAULT_SCHEDULE_PATH,
    dashboard_only: bool = True,
    include_source_ids: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    resolved_mode = mode or get_fetch_mode()
    payload = fetch_payload(mode=resolved_mode, timeout=timeout)
    if raw_output_path:
        write_json(payload, raw_output_path)

    rows, diagnostics = parse_player_projections(
        payload,
        stats_path=stats_path,
        schedule_path=schedule_path,
        dashboard_only=dashboard_only,
        include_source_ids=include_source_ids,
    )
    write_rows(rows, output_path, include_source_ids=include_source_ids)
    return rows, diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch PrizePicks player projections and normalize them to CSV.")
    parser.add_argument("--mode", choices=["proxy_first", "proxy_only", "direct_only"], default=None)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--input-json", help="Parse a saved raw payload instead of making a network request.")
    parser.add_argument("--raw-output", help="Save the raw JSON payload to this path when fetching.")
    parser.add_argument("--output-csv", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--stats-path", default=str(DEFAULT_STATS_PATH))
    parser.add_argument("--schedule-path", default=str(DEFAULT_SCHEDULE_PATH))
    parser.add_argument("--limit", type=int, default=0, help="Trim parsed rows for inspection.")
    parser.add_argument("--json", action="store_true", help="Print parsed rows as JSON instead of writing CSV.")
    parser.add_argument("--diag", action="store_true", help="Include parser diagnostics in stdout.")
    parser.add_argument("--all-props", action="store_true", help="Keep unsupported PrizePicks prop families for debugging.")
    parser.add_argument("--include-source-ids", action="store_true", help="Include PrizePicks-specific projection ids in output.")
    return parser


def load_payload(args: argparse.Namespace) -> Dict[str, Any]:
    if args.input_json:
        with Path(args.input_json).open("r", encoding="utf-8") as handle:
            return json.load(handle)

    payload = fetch_payload(mode=args.mode or get_fetch_mode(), timeout=args.timeout)
    if args.raw_output:
        write_json(payload, Path(args.raw_output))
    return payload


def main() -> int:
    args = build_parser().parse_args()
    payload = load_payload(args)
    rows, diagnostics = parse_player_projections(
        payload,
        stats_path=Path(args.stats_path),
        schedule_path=Path(args.schedule_path),
        dashboard_only=not args.all_props,
        include_source_ids=args.include_source_ids,
    )

    if args.limit > 0:
        rows = rows[: args.limit]

    if args.json:
        result: Any = rows
        if args.diag:
            result = {"diagnostics": diagnostics, "rows": rows}
        print(json.dumps(result, indent=2))
        return 0

    output_path = Path(args.output_csv)
    write_rows(rows, output_path, include_source_ids=args.include_source_ids)
    print(f"Wrote {len(rows)} player projections to {output_path}")

    if args.diag:
        print(json.dumps(diagnostics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
