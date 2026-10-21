import json
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.pregame_props import is_pregame_schedule_game

ET_ZONE = ZoneInfo("America/New_York")

TEAM_NAME_ALIASES = {
    "76ers": {"76ers", "sixers", "philadelphia 76ers"},
    "Trail Blazers": {"trail blazers", "blazers", "portland trail blazers"},
    "Clippers": {"clippers", "la clippers", "los angeles clippers"},
    "Lakers": {"lakers", "la lakers", "los angeles lakers"},
}


def normalize_game_date(raw_value):
    if not raw_value:
        return ""
    if isinstance(raw_value, float) and math.isnan(raw_value):
        return ""
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped.lower() in {"nan", "nat", "none", "null"}:
            return ""
        if len(stripped) >= 10 and stripped[4] == "-" and stripped[7] == "-":
            if len(stripped) == 10:
                return stripped
            try:
                dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                return dt.astimezone(ET_ZONE).strftime("%Y-%m-%d")
            except ValueError:
                return stripped[:10]
    return str(raw_value)


def load_schedule_rows(base_dir=None):
    if base_dir is None:
        base_dir = os.path.join(os.path.dirname(__file__), "..", "data", "current")
    else:
        base_dir = os.path.abspath(base_dir)

    candidate_paths = [
        os.path.join(base_dir, "today_schedule.json"),
        os.path.join(base_dir, "nba_dashboard_games.json"),
    ]

    best_games = []
    best_score = ("", 0)

    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                games = data.get("games", [])
            elif isinstance(data, list):
                games = data
            else:
                games = []

            if not isinstance(games, list):
                continue

            max_date = ""
            for game in games:
                game_date = normalize_game_date(game.get("game_date"))
                if game_date and game_date > max_date:
                    max_date = game_date

            score = (max_date, len(games))
            if score > best_score:
                best_games = games
                best_score = score
        except Exception:
            continue

    return best_games


def _team_tokens(city, name, tricode):
    tokens = set()

    city = (city or "").strip().lower()
    name = (name or "").strip().lower()
    tricode = (tricode or "").strip().lower()

    if tricode:
        tokens.add(tricode)
    if name:
        tokens.add(name)
    if city:
        tokens.add(city)
    if city and name:
        tokens.add(f"{city} {name}")

    for alias in TEAM_NAME_ALIASES.get(name.title(), set()):
        tokens.add(alias.lower())

    return {token for token in tokens if token}


def _label_matches_game(game, canonical_team, game_label):
    label = (game_label or "").strip().lower()
    if not label:
        return False

    home_team = str(game.get("home_team_tricode", "")).strip()
    away_team = str(game.get("away_team_tricode", "")).strip()
    if canonical_team not in {home_team, away_team}:
        return False

    if canonical_team == home_team:
        own_tokens = _team_tokens(
            game.get("home_team_city", ""),
            game.get("home_team_name", ""),
            home_team,
        )
        opp_tokens = _team_tokens(
            game.get("away_team_city", ""),
            game.get("away_team_name", ""),
            away_team,
        )
    else:
        own_tokens = _team_tokens(
            game.get("away_team_city", ""),
            game.get("away_team_name", ""),
            away_team,
        )
        opp_tokens = _team_tokens(
            game.get("home_team_city", ""),
            game.get("home_team_name", ""),
            home_team,
        )

    own_hit = any(token in label for token in own_tokens)
    opp_hit = any(token in label for token in opp_tokens)
    return own_hit and opp_hit


def _parse_deadline(game):
    deadline = game.get("closing_scrape_deadline")
    if not deadline:
        return None
    try:
        return datetime.fromisoformat(deadline)
    except ValueError:
        return None


def _pick_best_game(games, now_et=None):
    if not games:
        return None

    if now_et is None:
        now_et = datetime.now(ET_ZONE)
    else:
        now_et = now_et.astimezone(ET_ZONE)

    games = [game for game in games if is_pregame_schedule_game(game, now_et=now_et)]
    if not games:
        return None

    def game_sort_key(game):
        deadline_dt = _parse_deadline(game)
        game_date = normalize_game_date(game.get("game_date"))
        is_relevant = deadline_dt is not None and deadline_dt > now_et
        is_future = deadline_dt is not None and deadline_dt >= now_et
        return (
            0 if is_relevant else 1,
            0 if is_future else 1,
            game_date or "9999-12-31",
            game.get("game_time_utc") or "",
        )

    return sorted(games, key=game_sort_key)[0]


def resolve_prop_game_date(raw_game_date, canonical_team=None, game_label=None, schedule_rows=None, now_et=None):
    explicit_date = normalize_game_date(raw_game_date)
    if explicit_date:
        return explicit_date, "raw"

    schedule_rows = schedule_rows or load_schedule_rows()
    if not schedule_rows or not canonical_team:
        return "", "missing"

    team_games = [g for g in schedule_rows if canonical_team in {g.get("home_team_tricode"), g.get("away_team_tricode")}]
    matching_games = [g for g in team_games if _label_matches_game(g, canonical_team, game_label)]

    chosen = _pick_best_game(matching_games, now_et=now_et)
    if chosen:
        return normalize_game_date(chosen.get("game_date")), "schedule_label"

    fallback = _pick_best_game(team_games, now_et=now_et)
    if fallback:
        return normalize_game_date(fallback.get("game_date")), "schedule_team"

    return "", "missing"
