from __future__ import annotations

from datetime import date
import math
import re
import unicodedata
from typing import Any, Dict, Optional, Sequence, Tuple


ACTIVE_ROSTER_LOOKBACK_DAYS = 30
TRAILING_ABSENT_PRIOR_GAMES = 10
MIN_LIVE_PRIOR_ACTIVE_GAMES = 3
KEY_TEAMMATE_MIN_USAGE_PCT = 18.0
KEY_TEAMMATE_MIN_MINUTES = 18.0
TEAMMATE_ONOFF_LOOKBACK_PLAYER_GAMES = 20
TEAMMATE_ONOFF_MIN_PRESENT_GAMES = 5
TEAMMATE_ONOFF_MIN_ABSENT_GAMES = 2
TEAMMATE_ONOFF_FULL_WEIGHT_GAMES = 5
RETURN_LOOKBACK_TEAM_GAMES = 5
RETURN_ABSENT_THRESHOLD = 3

HIGH_USAGE_THRESHOLD = 22.0
PLAYMAKER_AST_PCT_THRESHOLD = 18.0
PLAYMAKER_POTENTIAL_AST_THRESHOLD = 8.0
ONBALL_DRIVES_THRESHOLD = 8.0

INJURY_FRESHNESS_NORMAL_MAX_AGE_MINUTES = 60
INJURY_FRESHNESS_LOCK_SENSITIVE_MAX_AGE_MINUTES = 15
INJURY_FRESHNESS_LOCK_SENSITIVE_START_HOUR_ET = 6

OVERLAY_MIN_RECENT5_MINUTES = 12.0
SAME_POS_BENEFIT_MINUTES_THRESHOLD = 20.0
CREATION_BENEFIT_POTENTIAL_AST_THRESHOLD = 10.0
ONBALL_BENEFIT_DRIVES_THRESHOLD = 10.0
USAGE_BENEFIT_USAGE_THRESHOLD = 10.0
OVERLAY_SINGLE_BENEFIT_MULTIPLIER = 1.02
OVERLAY_MULTI_BENEFIT_MULTIPLIER = 1.04
OVERLAY_MAX_MULTIPLIER = 1.05

POSITION_GROUPS = ("G", "F", "C")
PROMOTION_GUARDRAIL_SUPPORTED_STAT_TYPES = (
    "PTS",
    "AST",
    "REB",
    "FG3M",
    "PTS+AST",
    "PTS+REB",
    "REB+AST",
    "PTS+REB+AST",
)

TEAM_VACANCY_FEATURE_COLUMNS = (
    "missing_team_usage_pct",
    "missing_team_minutes",
    "missing_guard_usage_pct",
    "missing_guard_minutes",
    "missing_high_usage_usage_pct",
    "missing_high_usage_minutes",
    "missing_playmaker_potential_ast_pg",
    "missing_playmaker_minutes",
    "missing_onball_drives_pg",
    "missing_onball_minutes",
)

SAME_POS_VACANCY_FEATURE_COLUMNS = (
    "missing_same_pos_usage_pct",
    "missing_same_pos_minutes",
)

INJURY_INTERACTION_COLUMNS = (
    "playmaker_vacuum_x_player_ast_rate",
    "onball_vacuum_x_player_drive_rate",
    "usage_vacuum_x_player_usage_pct",
    "missing_playmaker_potential_ast_pg_x_player_ast_rate",
    "missing_onball_drives_pg_x_player_drive_rate",
    "missing_high_usage_usage_pct_x_player_usage_rate",
    "missing_playmaker_potential_ast_pg_x_player_target_per_min",
    "missing_onball_drives_pg_x_player_target_per_min",
)

TEAMMATE_ONOFF_FEATURE_COLUMNS = (
    "missing_key_teammates_player_stat_delta",
    "missing_key_teammates_player_minutes_delta",
    "missing_key_teammates_player_usage_pct_delta",
    "missing_key_teammates_player_potential_ast_rate_delta",
    "missing_key_teammates_player_drive_rate_delta",
    "missing_key_teammates_player_target_per_min_delta",
    "missing_key_teammates_effective_support",
    "missing_key_teammate_count",
    "missing_same_pos_key_count",
    "missing_guard_key_count",
    "missing_playmaker_key_count",
    "returning_key_teammates_player_stat_delta",
    "returning_key_teammates_player_minutes_delta",
    "returning_key_teammates_player_usage_pct_delta",
    "returning_key_teammates_player_potential_ast_rate_delta",
    "returning_key_teammates_player_drive_rate_delta",
    "returning_key_teammates_player_target_per_min_delta",
    "returning_key_teammates_effective_support",
    "returning_key_teammate_count",
    "returning_same_pos_key_count",
    "returning_guard_key_count",
    "returning_playmaker_key_count",
)

ALL_INJURY_FEATURE_COLUMNS = (
    *TEAM_VACANCY_FEATURE_COLUMNS,
    *SAME_POS_VACANCY_FEATURE_COLUMNS,
    *INJURY_INTERACTION_COLUMNS,
    *TEAMMATE_ONOFF_FEATURE_COLUMNS,
)

EMPTY_TEAM_VACANCY_STATS = {column: 0.0 for column in TEAM_VACANCY_FEATURE_COLUMNS}
EMPTY_SAME_POS_VACANCY_STATS = {column: 0.0 for column in SAME_POS_VACANCY_FEATURE_COLUMNS}
EMPTY_TEAMMATE_ONOFF_STATS = {column: 0.0 for column in TEAMMATE_ONOFF_FEATURE_COLUMNS}

_NAME_SUFFIX_RE = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def _coerce_float(raw_value: Any) -> Optional[float]:
    if raw_value is None:
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(value) or math.isinf(value)) else value


def _rounded_or_zero(value: Optional[float], digits: int = 4) -> float:
    if value is None:
        return 0.0
    return round(float(value), digits)


def normalize_player_name(raw_name: Any) -> str:
    name = str(raw_name or "").lower().strip()
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("utf-8")
    name = name.replace(".", "").replace("'", "")
    name = _NAME_SUFFIX_RE.sub("", name).strip()
    return " ".join(name.split())


def normalize_percent_metric(raw_value: Any) -> Optional[float]:
    value = _coerce_float(raw_value)
    if value is None:
        return None
    if value <= 1.5:
        value *= 100.0
    return value


def normalize_position_group(raw_position: Any) -> str:
    position = str(raw_position or "").upper().strip()
    if "G" in position:
        return "G"
    if "C" in position:
        return "C"
    return "F"


def season_key_for_date(game_date: date) -> int:
    return game_date.year if game_date.month >= 10 else game_date.year - 1


def trailing_active_values(
    history: Sequence[Tuple[date, float]],
    current_date: date,
    *,
    lookback_days: int = ACTIVE_ROSTER_LOOKBACK_DAYS,
    max_games: int = TRAILING_ABSENT_PRIOR_GAMES,
) -> list[float]:
    values = [
        value
        for hist_date, value in history
        if 0 < (current_date - hist_date).days <= lookback_days
    ]
    if max_games > 0:
        values = values[-max_games:]
    return values


def make_team_vacancy_stats() -> Dict[str, float]:
    return dict(EMPTY_TEAM_VACANCY_STATS)


def make_same_pos_vacancy_stats() -> Dict[str, float]:
    return dict(EMPTY_SAME_POS_VACANCY_STATS)


def make_teammate_onoff_stats() -> Dict[str, float]:
    return dict(EMPTY_TEAMMATE_ONOFF_STATS)


def default_promotion_guardrail_config() -> Dict[str, float]:
    return {
        "min_missing_team_usage_pct": 45.0,
        "min_missing_team_minutes": 60.0,
        "min_recent5_minutes_avg": 12.0,
        "min_modeled_minutes_delta_vs_recent5": 3.0,
        "min_missing_key_teammates_player_minutes_delta": 2.0,
        "min_missing_same_pos_minutes": 28.0,
        "min_missing_guard_minutes": 28.0,
        "min_cross_position_creator_metric": 10.0,
        "single_stat_gap_pct": 0.15,
        "combo_stat_gap_pct": 0.12,
        "display_edge_score_cap": 69.9,
        "edge_score_penalty_points": 12.0,
        "confidence_penalty_points": 15.0,
    }


def resolve_promotion_guardrail_config(raw_config: Optional[Dict[str, Any]]) -> Dict[str, float]:
    resolved = default_promotion_guardrail_config()
    if not isinstance(raw_config, dict):
        return resolved
    for key in list(resolved.keys()):
        value = _coerce_float(raw_config.get(key))
        if value is not None:
            resolved[key] = value
    return resolved


def is_high_usage(usage_pct: Optional[float]) -> bool:
    return usage_pct is not None and usage_pct >= HIGH_USAGE_THRESHOLD


def is_playmaker(ast_pct: Optional[float], potential_ast_pg: Optional[float]) -> bool:
    return (
        (ast_pct is not None and ast_pct >= PLAYMAKER_AST_PCT_THRESHOLD)
        or (potential_ast_pg is not None and potential_ast_pg >= PLAYMAKER_POTENTIAL_AST_THRESHOLD)
    )


def is_onball(drives_pg: Optional[float]) -> bool:
    return drives_pg is not None and drives_pg >= ONBALL_DRIVES_THRESHOLD


def is_key_teammate(
    usage_pct: Optional[float],
    minutes: Optional[float],
    ast_pct: Optional[float],
    potential_ast_pg: Optional[float],
    drives_pg: Optional[float],
) -> bool:
    playmaker_or_onball = is_playmaker(ast_pct, potential_ast_pg) or is_onball(drives_pg)
    has_meaningful_rotation_minutes = (
        minutes is not None and minutes >= (KEY_TEAMMATE_MIN_MINUTES / 2.0)
    )
    return (
        (usage_pct is not None and usage_pct >= KEY_TEAMMATE_MIN_USAGE_PCT)
        or (minutes is not None and minutes >= KEY_TEAMMATE_MIN_MINUTES)
        or (playmaker_or_onball and has_meaningful_rotation_minutes)
    )


def choose_recent_over_season(recent_value: Optional[float], season_value: Optional[float]) -> Optional[float]:
    return recent_value if recent_value is not None else season_value


def apply_injury_feature_values(
    row_obj: Dict[str, Any],
    *,
    team_vacancy_stats: Optional[Dict[str, float]] = None,
    same_pos_vacancy_stats: Optional[Dict[str, float]] = None,
    teammate_onoff_stats: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    resolved_team = make_team_vacancy_stats()
    if isinstance(team_vacancy_stats, dict):
        for key in TEAM_VACANCY_FEATURE_COLUMNS:
            if key in team_vacancy_stats:
                resolved_team[key] = float(team_vacancy_stats[key] or 0.0)

    resolved_same_pos = make_same_pos_vacancy_stats()
    if isinstance(same_pos_vacancy_stats, dict):
        for key in SAME_POS_VACANCY_FEATURE_COLUMNS:
            if key in same_pos_vacancy_stats:
                resolved_same_pos[key] = float(same_pos_vacancy_stats[key] or 0.0)

    resolved_teammate_onoff = make_teammate_onoff_stats()
    if isinstance(teammate_onoff_stats, dict):
        for key in TEAMMATE_ONOFF_FEATURE_COLUMNS:
            if key in teammate_onoff_stats:
                resolved_teammate_onoff[key] = float(teammate_onoff_stats[key] or 0.0)

    for key, value in resolved_team.items():
        row_obj[key] = _rounded_or_zero(value)
    for key, value in resolved_same_pos.items():
        row_obj[key] = _rounded_or_zero(value)
    for key, value in resolved_teammate_onoff.items():
        row_obj[key] = _rounded_or_zero(value)

    chosen_ast_rate = choose_recent_over_season(
        _coerce_float(row_obj.get("recent10_potential_ast_rate")),
        _coerce_float(row_obj.get("season_potential_ast_rate")),
    )
    chosen_drive_rate = choose_recent_over_season(
        _coerce_float(row_obj.get("recent10_drive_rate")),
        _coerce_float(row_obj.get("season_drive_rate")),
    )
    chosen_usage_pct = choose_recent_over_season(
        _coerce_float(row_obj.get("recent10_usage_pct_avg")),
        _coerce_float(row_obj.get("season_usage_pct_avg")),
    )

    row_obj["playmaker_vacuum_x_player_ast_rate"] = _rounded_or_zero(
        (resolved_team["missing_playmaker_potential_ast_pg"] or 0.0) * (chosen_ast_rate or 0.0)
    )
    row_obj["onball_vacuum_x_player_drive_rate"] = _rounded_or_zero(
        (resolved_team["missing_onball_drives_pg"] or 0.0) * (chosen_drive_rate or 0.0)
    )
    row_obj["usage_vacuum_x_player_usage_pct"] = _rounded_or_zero(
        (resolved_team["missing_high_usage_usage_pct"] or 0.0) * (chosen_usage_pct or 0.0)
    )
    row_obj["missing_playmaker_potential_ast_pg_x_player_ast_rate"] = row_obj[
        "playmaker_vacuum_x_player_ast_rate"
    ]
    row_obj["missing_onball_drives_pg_x_player_drive_rate"] = row_obj[
        "onball_vacuum_x_player_drive_rate"
    ]
    row_obj["missing_high_usage_usage_pct_x_player_usage_rate"] = row_obj[
        "usage_vacuum_x_player_usage_pct"
    ]
    return row_obj
