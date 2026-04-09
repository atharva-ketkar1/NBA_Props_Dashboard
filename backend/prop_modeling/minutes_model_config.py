from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence


MINUTES_MODEL_TARGET_COLUMN = "actual_minutes"
MINUTES_MODEL_FILE_NAME = "minutes_model.cbm"
MINUTES_MODEL_METADATA_FILE_NAME = "minutes_model_metadata.json"
MINUTES_MODEL_QUANTILES_FILE_NAME = "minutes_quantile_stats.json"

MINUTES_MODEL_CATEGORICAL_FEATURES = ["player_id", "team", "opponent"]
MINUTES_MODEL_FEATURE_COLUMNS = [
    "player_id",
    "team",
    "opponent",
    "is_home",
    "prior_games",
    "same_team_current_season_games",
    "days_rest",
    "is_b2b",
    "season_minutes_avg",
    "recent3_minutes_avg",
    "recent5_minutes_avg",
    "recent10_minutes_avg",
    "minutes_trend_5v20",
    "minutes_cv_recent5",
    "recent3_1q_minutes_avg",
    "recent5_1h_minutes_avg",
    "recent5_1h_minutes_share",
    "missing_team_usage_pct",
    "missing_team_minutes",
    "missing_same_pos_usage_pct",
    "missing_same_pos_minutes",
    "missing_guard_usage_pct",
    "missing_guard_minutes",
    "missing_high_usage_usage_pct",
    "missing_high_usage_minutes",
    "missing_playmaker_potential_ast_pg",
    "missing_playmaker_minutes",
    "missing_onball_drives_pg",
    "missing_onball_minutes",
    "missing_key_teammates_player_minutes_delta",
    "missing_key_teammate_count",
    "missing_same_pos_key_count",
    "missing_guard_key_count",
    "missing_playmaker_key_count",
    "returning_key_teammates_player_minutes_delta",
    "returning_key_teammate_count",
    "returning_same_pos_key_count",
    "returning_guard_key_count",
    "returning_playmaker_key_count",
    "recent_team_games_missed_10",
    "inactive_streak_team_games",
    "games_since_return",
    "previous_absence_streak_team_games",
    "minutes_last_game",
    "minutes_delta_last1_vs_recent5",
    "recent5_minutes_max",
    "recent5_minutes_min",
    "recent10_blowout_rate",
]

MODELED_MINUTES_FEATURE_COLUMNS = [
    "modeled_minutes_q50",
    "modeled_minutes_iqr",
    "modeled_minutes_delta_vs_recent5",
]

MINUTES_OOF_BLOCK_DATES = 10
MINUTES_OOF_MIN_TRAIN_DATES = 30
MINUTES_LIVE_MIN_SAME_TEAM_GAMES = 6


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def estimate_minutes_iqr(
    *,
    modeled_minutes_q50: Optional[float],
    recent5_minutes_avg: Optional[float],
    recent10_minutes_avg: Optional[float],
    season_minutes_avg: Optional[float],
    minutes_cv_recent5: Optional[float],
) -> float:
    center = _safe_float(modeled_minutes_q50)
    r5 = _safe_float(recent5_minutes_avg)
    r10 = _safe_float(recent10_minutes_avg)
    season = _safe_float(season_minutes_avg)
    cv = _safe_float(minutes_cv_recent5)

    spread_candidates = [1.0]
    if center is not None and cv is not None:
        spread_candidates.append(abs(center * cv))
    if r5 is not None and r10 is not None:
        spread_candidates.append(abs(r5 - r10))
    if r5 is not None and season is not None:
        spread_candidates.append(abs(r5 - season))
    if r10 is not None and season is not None:
        spread_candidates.append(abs(r10 - season))

    return round(max(spread_candidates), 4)


def build_fallback_modeled_minutes(
    row_obj: Dict[str, Any],
    *,
    predicted_minutes_key: str = "predicted_minutes",
) -> Dict[str, float]:
    modeled_q50 = _safe_float(row_obj.get(predicted_minutes_key))
    if modeled_q50 is None:
        recent5 = _safe_float(row_obj.get("recent5_minutes_avg"))
        recent10 = _safe_float(row_obj.get("recent10_minutes_avg"))
        season = _safe_float(row_obj.get("season_minutes_avg"))
        r5 = recent5 if recent5 is not None else season
        r10 = recent10 if recent10 is not None else season
        sea = season if season is not None else recent5
        if r5 is None and r10 is None and sea is None:
            modeled_q50 = 0.0
        else:
            r5 = r5 if r5 is not None else 0.0
            r10 = r10 if r10 is not None else r5
            sea = sea if sea is not None else r5
            modeled_q50 = max(0.0, min(42.0, r5 * 0.50 + r10 * 0.30 + sea * 0.20))

    modeled_iqr = estimate_minutes_iqr(
        modeled_minutes_q50=modeled_q50,
        recent5_minutes_avg=row_obj.get("recent5_minutes_avg"),
        recent10_minutes_avg=row_obj.get("recent10_minutes_avg"),
        season_minutes_avg=row_obj.get("season_minutes_avg"),
        minutes_cv_recent5=row_obj.get("minutes_cv_recent5"),
    )
    recent5 = _safe_float(row_obj.get("recent5_minutes_avg")) or 0.0
    return {
        "modeled_minutes_q50": round(float(modeled_q50 or 0.0), 4),
        "modeled_minutes_iqr": modeled_iqr,
        "modeled_minutes_delta_vs_recent5": round(float((modeled_q50 or 0.0) - recent5), 4),
    }


def apply_modeled_minutes_features(
    row_obj: Dict[str, Any],
    *,
    modeled_minutes_q50: Optional[float],
    modeled_minutes_iqr: Optional[float],
) -> Dict[str, Any]:
    q50 = _safe_float(modeled_minutes_q50)
    iqr = _safe_float(modeled_minutes_iqr)
    if q50 is None or iqr is None:
        fallback = build_fallback_modeled_minutes(row_obj)
        q50 = fallback["modeled_minutes_q50"]
        iqr = fallback["modeled_minutes_iqr"]
        delta_vs_recent5 = fallback["modeled_minutes_delta_vs_recent5"]
    else:
        recent5 = _safe_float(row_obj.get("recent5_minutes_avg")) or 0.0
        delta_vs_recent5 = q50 - recent5

    row_obj["modeled_minutes_q50"] = round(float(q50), 4)
    row_obj["modeled_minutes_iqr"] = round(float(max(iqr, 0.0)), 4)
    row_obj["modeled_minutes_delta_vs_recent5"] = round(float(delta_vs_recent5), 4)
    return row_obj


def minutes_metrics_summary(
    *,
    actuals: Sequence[float],
    q25_preds: Sequence[float],
    q50_preds: Sequence[float],
    q75_preds: Sequence[float],
) -> Dict[str, float]:
    actual_arr = [_safe_float(value) or 0.0 for value in actuals]
    q25_arr = [_safe_float(value) or 0.0 for value in q25_preds]
    q50_arr = [_safe_float(value) or 0.0 for value in q50_preds]
    q75_arr = [_safe_float(value) or 0.0 for value in q75_preds]

    abs_errors = [abs(a - p) for a, p in zip(actual_arr, q50_arr)]
    sq_errors = [(a - p) ** 2 for a, p in zip(actual_arr, q50_arr)]
    mean_actual = sum(actual_arr) / len(actual_arr) if actual_arr else 0.0
    total_var = sum((a - mean_actual) ** 2 for a in actual_arr)
    explained = 1.0 - (sum(sq_errors) / total_var) if total_var > 1e-9 else 0.0
    cover = [
        1.0 if (a >= q25 and a <= q75) else 0.0
        for a, q25, q75 in zip(actual_arr, q25_arr, q75_arr)
    ]
    iqr_values = [max(0.0, q75 - q25) for q25, q75 in zip(q25_arr, q75_arr)]

    return {
        "mae_q50": round(sum(abs_errors) / len(abs_errors), 4) if abs_errors else 0.0,
        "rmse_q50": round(math.sqrt(sum(sq_errors) / len(sq_errors)), 4) if sq_errors else 0.0,
        "r2_q50": round(explained, 4),
        "median_iqr": round(sorted(iqr_values)[len(iqr_values) // 2], 4) if iqr_values else 0.0,
        "q50_bias": round(sum((p - a) for a, p in zip(actual_arr, q50_arr)) / len(actual_arr), 4) if actual_arr else 0.0,
        "iqr_coverage": round(sum(cover) / len(cover), 4) if cover else 0.0,
    }
