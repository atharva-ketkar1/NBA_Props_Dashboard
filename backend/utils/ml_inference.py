import json
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import date, datetime
import sys

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

logger = logging.getLogger("MLInference")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR / "prop_modeling"))

# Import the EnrichmentData logic that builds the row initially
from build_regression_dataset import (
    EnrichmentData,
    _build_history,
    _build_key_teammate_onoff_features,
    _build_minutes_row,
    _build_player_index,
    _build_team_presence_context,
    _parse_date,
    _build_regression_row,
    _mean,
    _safe_float,
)
from injury_feature_config import (
    ACTIVE_ROSTER_LOOKBACK_DAYS,
    MIN_LIVE_PRIOR_ACTIVE_GAMES,
    POSITION_GROUPS,
    SAME_POS_VACANCY_FEATURE_COLUMNS,
    TEAM_VACANCY_FEATURE_COLUMNS,
    TRAILING_ABSENT_PRIOR_GAMES,
    apply_injury_feature_values,
    is_high_usage,
    is_onball,
    is_playmaker,
    make_same_pos_vacancy_stats,
    make_team_vacancy_stats,
    normalize_percent_metric,
    normalize_player_name,
    normalize_position_group,
)
from minutes_model_config import (
    MINUTES_LIVE_MIN_SAME_TEAM_GAMES,
    MINUTES_MODEL_FILE_NAME,
    MINUTES_MODEL_METADATA_FILE_NAME,
    MINUTES_MODEL_QUANTILES_FILE_NAME,
    apply_modeled_minutes_features,
)

ML_MODEL_DIR = BACKEND_DIR / "prop_modeling" / "exported_regression_model"
CURRENT_DATA_DIR = BACKEND_DIR / "data" / "current"
UNAVAILABLE_INJURY_STATUSES = {"out", "doubtful"}

class MLPredictor:
    """Singleton ML Predictor for blazingly fast in-memory inference."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MLPredictor, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.models: Dict[str, CatBoostRegressor] = {}
        self.minutes_model: Optional[CatBoostRegressor] = None
        self.meta = {}
        self.minutes_meta: Dict[str, Any] = {}
        self.q_stats = {}
        self.minutes_q_stats: Dict[str, Any] = {}
        self.drift_b = {}
        self.enrichment = None
        self.ready = False
        self._drift_feature_high_water: Dict[str, float] = {}

        meta_path = ML_MODEL_DIR / "model_metadata.json"
        q_stats_path = ML_MODEL_DIR / "quantile_stats.json"
        drift_b_path = ML_MODEL_DIR / "feature_drift_baseline.json"
        minutes_meta_path = ML_MODEL_DIR / MINUTES_MODEL_METADATA_FILE_NAME
        minutes_q_stats_path = ML_MODEL_DIR / MINUTES_MODEL_QUANTILES_FILE_NAME
        minutes_model_path = ML_MODEL_DIR / MINUTES_MODEL_FILE_NAME

        if not meta_path.exists():
            logger.warning("MLPredictor: Missing model_metadata.json. ML Inference offline.")
            return

        try:
            with open(meta_path) as f:
                self.meta = json.load(f)
            
            if q_stats_path.exists():
                with open(q_stats_path) as f:
                    self.q_stats = json.load(f)
            
            if drift_b_path.exists():
                with open(drift_b_path) as f:
                    self.drift_b = json.load(f)
            if minutes_meta_path.exists():
                with open(minutes_meta_path) as f:
                    self.minutes_meta = json.load(f)
            if minutes_q_stats_path.exists():
                with open(minutes_q_stats_path) as f:
                    self.minutes_q_stats = json.load(f)

            # Load per-stat models
            model_files = self.meta.get("model_files", {})
            for st, fname in model_files.items():
                cbm_path = ML_MODEL_DIR / fname
                if cbm_path.exists():
                    model = CatBoostRegressor().load_model(str(cbm_path))
                    self.models[st] = model
                else:
                    logger.warning(f"MLPredictor: Missing model file {fname} for {st}")

            if minutes_model_path.exists():
                self.minutes_model = CatBoostRegressor().load_model(str(minutes_model_path))

            self.feat_cols = self.meta.get("feature_columns", [])
            self.cat_cols = self.meta.get("cat_features", [])
            self.cat_idx = [self.feat_cols.index(c) for c in self.cat_cols if c in self.feat_cols]
            self.minutes_feat_cols = self.minutes_meta.get("feature_columns", [])
            self.minutes_cat_cols = self.minutes_meta.get("cat_features", [])
            self.minutes_cat_idx = [
                self.minutes_feat_cols.index(c)
                for c in self.minutes_cat_cols
                if c in self.minutes_feat_cols
            ]

            # Load the cache dictionary for team paces and opposing defensives (~0.5s)
            self.enrichment = EnrichmentData(CURRENT_DATA_DIR)

            self.live_player_histories: Dict[str, List[Tuple[date, Dict[str, Any]]]] = {}
            self.live_player_name_lookup: Dict[Tuple[str, str], str] = {}
            self.live_team_player_ids: Dict[str, set[str]] = {}
            self.live_player_prior_cache: Dict[Tuple[str, date], Dict[str, Any]] = {}
            self.live_team_vacancy_stats: Dict[Tuple[str, date], Dict[str, float]] = {}
            self.live_same_pos_vacancy_stats: Dict[Tuple[str, date, str], Dict[str, float]] = {}
            self.live_missing_player_priors: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]] = {}
            self.live_active_player_priors: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]] = {}
            self.live_team_presence_index: Dict[Tuple[str, date], set[str]] = {}
            self.live_team_game_dates_by_season: Dict[Tuple[str, int], List[date]] = {}
            self.live_team_game_dates: Dict[str, date] = {}
            self.live_team_matchup_dates: Dict[Tuple[str, str], date] = {}
            self._initialize_live_injury_feature_cache()
            
            self.ready = True
            logger.info(f"MLPredictor: Successfully loaded {len(self.models)} per-stat models.")
        except Exception as e:
            logger.error(f"MLPredictor: Initialization failed: {e}")

    @staticmethod
    def _normalize_player_name(raw_name: Any) -> str:
        return normalize_player_name(raw_name)

    @staticmethod
    def _normalize_percent_metric(raw_value: Any) -> Optional[float]:
        return normalize_percent_metric(raw_value)

    def _initialize_live_injury_feature_cache(self) -> None:
        self._load_live_player_histories()
        self._build_live_player_name_lookup()
        self._load_live_injury_report_context()

    def _load_live_player_histories(self) -> None:
        current_gamelog_paths = sorted(CURRENT_DATA_DIR.glob("gamelogs_*.csv"))
        if not current_gamelog_paths:
            return
        live_rows = _build_history(current_gamelog_paths)
        self.live_player_histories = _build_player_index(live_rows)
        self.live_team_presence_index, self.live_team_game_dates_by_season = _build_team_presence_context(
            live_rows
        )

    def _build_live_player_name_lookup(self) -> None:
        lookup: Dict[Tuple[str, str], str] = {}
        team_player_ids: Dict[str, set[str]] = defaultdict(set)
        if not self.enrichment:
            self.live_player_name_lookup = lookup
            self.live_team_player_ids = {}
            return

        for pid, stats in self.enrichment.season_stats.items():
            team = str(stats.get("TEAM_ABBREVIATION") or "").strip().upper()
            normalized_name = normalize_player_name(
                self.enrichment.player_id_to_name.get(pid) or stats.get("PLAYER_NAME")
            )
            if team and normalized_name and (team, normalized_name) not in lookup:
                lookup[(team, normalized_name)] = pid
            if team:
                team_player_ids[team].add(pid)

        self.live_player_name_lookup = lookup
        self.live_team_player_ids = {team: set(player_ids) for team, player_ids in team_player_ids.items()}

    def _fallback_live_player_priors(self, player_id: str) -> Dict[str, Any]:
        if not self.enrichment:
            return {
                "active_games": 0,
                "within_active_roster_window": False,
                "usage_pct": None,
                "ast_pct": None,
                "potential_ast_pg": None,
                "drives_pg": None,
                "minutes": None,
                "pos_group": normalize_position_group(None),
            }

        stats = self.enrichment.season_stats.get(str(player_id).strip(), {})
        return {
            "active_games": 0,
            "within_active_roster_window": False,
            "usage_pct": self._normalize_percent_metric(stats.get("USG_PCT")),
            "ast_pct": self._normalize_percent_metric(stats.get("AST_PCT")),
            "potential_ast_pg": _safe_float(stats.get("POTENTIAL_AST")),
            "drives_pg": _safe_float(stats.get("DRIVES")),
            "minutes": _safe_float(stats.get("MIN")),
            "pos_group": self.enrichment.get_player_position_group(player_id),
        }

    def _get_live_player_priors(self, player_id: str, slate_date: date) -> Dict[str, Any]:
        cache_key = (str(player_id).strip(), slate_date)
        if cache_key in self.live_player_prior_cache:
            return self.live_player_prior_cache[cache_key]

        history = self.live_player_histories.get(cache_key[0], [])
        prior_rows = [
            row
            for game_date, row in history
            if 0 < (slate_date - game_date).days <= ACTIVE_ROSTER_LOOKBACK_DAYS
        ][-TRAILING_ABSENT_PRIOR_GAMES:]

        priors = {
            "active_games": len(prior_rows),
            "within_active_roster_window": bool(prior_rows),
            "usage_pct": _mean([_safe_float(row.get("USG_PCT")) for row in prior_rows]),
            "ast_pct": _mean([_safe_float(row.get("AST_PCT")) for row in prior_rows]),
            "potential_ast_pg": _mean([_safe_float(row.get("POTENTIAL_AST")) for row in prior_rows]),
            "drives_pg": _mean([_safe_float(row.get("DRIVES")) for row in prior_rows]),
            "minutes": _mean([_safe_float(row.get("MIN")) for row in prior_rows]),
            "pos_group": self.enrichment.get_player_position_group(player_id) if self.enrichment else normalize_position_group(None),
        }

        if priors["active_games"] < MIN_LIVE_PRIOR_ACTIVE_GAMES and priors["within_active_roster_window"]:
            fallback = self._fallback_live_player_priors(cache_key[0])
            priors = {
                **fallback,
                "active_games": priors["active_games"],
                "within_active_roster_window": True,
            }
        else:
            fallback = self._fallback_live_player_priors(cache_key[0])
            for metric_key in ("usage_pct", "ast_pct", "potential_ast_pg", "drives_pg", "minutes"):
                if priors.get(metric_key) is None:
                    priors[metric_key] = fallback.get(metric_key)

        self.live_player_prior_cache[cache_key] = priors
        return priors

    def _load_live_injury_report_context(self) -> None:
        if not self.enrichment:
            return

        injury_report_path = CURRENT_DATA_DIR / "nba_injury_report.json"
        if not injury_report_path.exists():
            return

        try:
            with injury_report_path.open() as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.error(f"MLPredictor: Failed to load live injuries: {exc}")
            return

        for game in payload.get("games", []):
            if not isinstance(game, dict):
                continue

            game_date = _parse_date(game.get("game_date")) or date.today()
            home_team = str(game.get("home_team_tricode") or "").strip().upper()
            away_team = str(game.get("away_team_tricode") or "").strip().upper()
            if home_team:
                self.live_team_game_dates[home_team] = game_date
            if away_team:
                self.live_team_game_dates[away_team] = game_date
            if home_team and away_team:
                self.live_team_matchup_dates[(home_team, away_team)] = game_date
                self.live_team_matchup_dates[(away_team, home_team)] = game_date

            teams = game.get("teams", {})
            if not isinstance(teams, dict):
                continue

            for team_abbrev, team_data in teams.items():
                team_key = str(team_abbrev or "").strip().upper()
                if not team_key:
                    continue
                if not isinstance(team_data, dict):
                    continue
                team_stats = make_team_vacancy_stats()
                same_pos_stats = {
                    group: make_same_pos_vacancy_stats()
                    for group in POSITION_GROUPS
                }
                current_missing_player_priors: Dict[str, Dict[str, Any]] = {}
                current_active_player_priors: Dict[str, Dict[str, Any]] = {}
                missing_player_ids: set[str] = set()

                for report in team_data.get("players", []):
                    if not isinstance(report, dict):
                        continue
                    status = str(report.get("current_status") or "").strip().lower()
                    if status not in UNAVAILABLE_INJURY_STATUSES:
                        continue

                    normalized_name = normalize_player_name(
                        report.get("player_name") or report.get("report_player_name")
                    )
                    if not normalized_name:
                        continue

                    player_id = self.live_player_name_lookup.get((team_key, normalized_name))
                    if not player_id:
                        continue
                    missing_player_ids.add(player_id)

                for player_id in sorted(self.live_team_player_ids.get(team_key, set())):
                    priors = self._get_live_player_priors(player_id, game_date)
                    if not priors.get("within_active_roster_window"):
                        continue

                    if player_id in missing_player_ids:
                        current_missing_player_priors[player_id] = dict(priors)
                        usage_pct = priors.get("usage_pct")
                        minutes = priors.get("minutes")
                        pos_group = priors.get("pos_group") or normalize_position_group(None)
                        if usage_pct is None and minutes is None:
                            continue

                        team_stats["missing_team_usage_pct"] += usage_pct or 0.0
                        team_stats["missing_team_minutes"] += minutes or 0.0
                        same_pos_stats[pos_group]["missing_same_pos_usage_pct"] += usage_pct or 0.0
                        same_pos_stats[pos_group]["missing_same_pos_minutes"] += minutes or 0.0

                        if pos_group == "G":
                            team_stats["missing_guard_usage_pct"] += usage_pct or 0.0
                            team_stats["missing_guard_minutes"] += minutes or 0.0

                        if is_high_usage(usage_pct):
                            team_stats["missing_high_usage_usage_pct"] += usage_pct or 0.0
                            team_stats["missing_high_usage_minutes"] += minutes or 0.0

                        if is_playmaker(priors.get("ast_pct"), priors.get("potential_ast_pg")):
                            team_stats["missing_playmaker_potential_ast_pg"] += priors.get("potential_ast_pg") or 0.0
                            team_stats["missing_playmaker_minutes"] += minutes or 0.0

                        if is_onball(priors.get("drives_pg")):
                            team_stats["missing_onball_drives_pg"] += priors.get("drives_pg") or 0.0
                            team_stats["missing_onball_minutes"] += minutes or 0.0
                    else:
                        current_active_player_priors[player_id] = dict(priors)

                self.live_team_vacancy_stats[(team_key, game_date)] = {
                    key: round(float(team_stats.get(key) or 0.0), 4)
                    for key in TEAM_VACANCY_FEATURE_COLUMNS
                }
                self.live_missing_player_priors[(team_key, game_date)] = current_missing_player_priors
                self.live_active_player_priors[(team_key, game_date)] = current_active_player_priors
                for pos_group, pos_stats in same_pos_stats.items():
                    self.live_same_pos_vacancy_stats[(team_key, game_date, pos_group)] = {
                        key: round(float(pos_stats.get(key) or 0.0), 4)
                        for key in SAME_POS_VACANCY_FEATURE_COLUMNS
                    }

    def _resolve_live_target_date(self, team_abbrev: str, opponent_abbrev: Optional[str]) -> date:
        team_key = str(team_abbrev or "").strip().upper()
        opp_key = str(opponent_abbrev or "").strip().upper()
        if team_key and opp_key:
            matchup_date = self.live_team_matchup_dates.get((team_key, opp_key))
            if matchup_date is not None:
                return matchup_date
        if team_key and team_key in self.live_team_game_dates:
            return self.live_team_game_dates[team_key]
        return date.today()

    def _check_drift(self, features: Dict[str, Any], z_threshold: float = 3.0) -> List[Dict[str, float]]:
        alerts = []
        for col, stats in self.drift_b.items():
            val = features.get(col)
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            std = stats.get("std", 0.0)
            if std < 1e-9:
                continue
            mean_val = stats.get("mean", 0.0)
            z = abs(fval - mean_val) / std
            if z > z_threshold:
                alerts.append({
                    "column": col,
                    "value": fval,
                    "z_score": z,
                    "mean": mean_val,
                })
        return alerts

    def _log_drift_summary(
        self,
        player_info: Dict[str, Any],
        stat_type: str,
        alerts: List[Dict[str, float]],
    ) -> None:
        if not alerts:
            return

        significant_alerts = []
        for alert in alerts:
            column = str(alert.get("column") or "").strip()
            z_score = float(alert.get("z_score") or 0.0)
            if not column:
                continue
            high_water = self._drift_feature_high_water.get(column, 0.0)
            if z_score >= high_water + 0.5:
                self._drift_feature_high_water[column] = z_score
                significant_alerts.append(alert)

        if not significant_alerts:
            return

        top_alerts = sorted(
            significant_alerts,
            key=lambda alert: float(alert.get("z_score") or 0.0),
            reverse=True,
        )[:5]
        top_summary = "; ".join(
            (
                f"{alert['column']}={alert['value']:.3f} "
                f"({alert['z_score']:.1f}sigma vs {alert['mean']:.3f})"
            )
            for alert in top_alerts
        )
        player_name = str(
            player_info.get("player_name")
            or player_info.get("name")
            or player_info.get("player")
            or "unknown"
        ).strip()
        player_id = str(
            player_info.get("player_id")
            or player_info.get("PLAYER_ID")
            or ""
        ).strip()
        logger.warning(
            "Feature drift summary | stat_type=%s player=%s player_id=%s "
            "new_features=%s total_features=%s top=%s",
            stat_type,
            player_name or "unknown",
            player_id or "n/a",
            len(significant_alerts),
            len(alerts),
            top_summary,
        )

    def _build_live_player_history_context(
        self,
        player_info: Dict[str, Any],
        logs: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not logs:
            return None

        last_log = logs[0]
        asc_logs = list(reversed(logs))
        team_abbr = str(
            player_info.get("team_abbrev")
            or player_info.get("team")
            or last_log.get("TEAM_ABBREVIATION")
            or ""
        ).strip().upper()
        opponent_abbr = str(player_info.get("opponent_abbrev") or "UNK").strip().upper()
        player_id = str(player_info.get("player_id", last_log.get("PLAYER_ID")) or "").strip()
        target_date = self._resolve_live_target_date(team_abbr, opponent_abbr)
        dummy_matchup = (
            f"{team_abbr} vs. {opponent_abbr}"
            if team_abbr and opponent_abbr and opponent_abbr != "UNK"
            else f"vs. {opponent_abbr or 'UNK'}"
        )

        dummy_row = {
            "PLAYER_ID": player_id,
            "PLAYER_NAME": player_info.get("player_name"),
            "TEAM_ABBREVIATION": team_abbr or player_info.get("team_abbrev", last_log.get("TEAM_ABBREVIATION")),
            "MATCHUP": dummy_matchup,
            "POSITION": player_info.get("position", ""),
            "MIN": 30.0,
        }
        for st in ["PTS", "AST", "REB", "STL", "BLK", "FG3M", "TOV", "FGA", "FTA", "FGM", "FTM"]:
            dummy_row[st] = 0.0

        if "team_abbrev" not in player_info and "team" in player_info:
            dummy_row["TEAM_ABBREVIATION"] = player_info["team"]

        asc_logs.append(dummy_row)

        player_history: List[Tuple[date, Dict[str, Any]]] = []
        for idx, row in enumerate(asc_logs):
            parsed_d = None
            game_date = row.get("GAME_DATE")
            if game_date:
                try:
                    parsed_d = datetime.strptime(str(game_date)[:10], "%Y-%m-%d").date()
                except Exception:
                    parsed_d = None
            if idx == len(asc_logs) - 1:
                parsed_d = target_date
            player_history.append((parsed_d, row))

        return {
            "player_history": player_history,
            "team_abbr": team_abbr,
            "opponent_abbr": opponent_abbr,
            "player_id": player_id,
            "target_date": target_date,
        }

    @staticmethod
    def _prepare_model_frame(
        feature_row: Dict[str, Any],
        feature_cols: List[str],
        cat_cols: List[str],
    ) -> pd.DataFrame:
        frame = pd.DataFrame([{column: feature_row.get(column) for column in feature_cols}])
        for column in feature_cols:
            if column in cat_cols:
                frame[column] = frame[column].fillna("UNKNOWN").astype(str)
            else:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame

    def _predict_modeled_minutes(
        self,
        row_obj: Dict[str, Any],
        minutes_row: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, float]]:
        if (
            self.minutes_model is None
            or not self.minutes_feat_cols
            or not minutes_row
            or float(minutes_row.get("same_team_current_season_games") or 0.0)
            < MINUTES_LIVE_MIN_SAME_TEAM_GAMES
        ):
            apply_modeled_minutes_features(
                row_obj,
                modeled_minutes_q50=None,
                modeled_minutes_iqr=None,
            )
            return None

        minutes_frame = self._prepare_model_frame(
            minutes_row,
            self.minutes_feat_cols,
            self.minutes_cat_cols,
        )
        minutes_pool = Pool(minutes_frame, cat_features=self.minutes_cat_idx)
        preds = self.minutes_model.predict(minutes_pool)
        if getattr(preds, "shape", None) == (1, 3):
            q25 = float(preds[0, 0])
            q50 = float(preds[0, 1])
            q75 = float(preds[0, 2])
            apply_modeled_minutes_features(
                row_obj,
                modeled_minutes_q50=q50,
                modeled_minutes_iqr=max(0.0, q75 - q25),
            )
            return {"q25": q25, "q50": q50, "q75": q75}

        apply_modeled_minutes_features(
            row_obj,
            modeled_minutes_q50=None,
            modeled_minutes_iqr=None,
        )
        return None

    def _build_feature_dict(self, player_info: Dict[str, Any], logs: List[Dict[str, Any]], stat_type: str) -> Optional[Dict[str, Any]]:
        history_context = self._build_live_player_history_context(player_info, logs)
        if not history_context:
            return None

        player_history = history_context["player_history"]
        team_abbr = history_context["team_abbr"]
        opponent_abbr = history_context["opponent_abbr"]
        player_id = history_context["player_id"]
        target_date = history_context["target_date"]

        row_obj = _build_regression_row(
            game_idx=len(player_history)-1,
            player_history=player_history,
            stat_type=stat_type,
            min_prior=3,
            min_minutes=1.0,
            enrichment=self.enrichment,
            team_missing_stats_dict={},
            same_pos_missing_stats_dict={},
            team_missing_player_priors_dict={},
            team_active_player_priors_dict={},
            team_presence_index={},
            team_game_dates_by_season={},
        )

        if not row_obj:
            return None

        # Engineer the fast, real-time features before prediction
        r5_stat = row_obj.get("recent5_stat_avg")
        r20_stat = row_obj.get("recent20_stat_avg")
        if r5_stat is not None and r20_stat is not None:
            row_obj["momentum_diff_5v20"] = r5_stat - r20_stat

        r3_stat = row_obj.get("recent3_stat_avg")
        r10_stat = row_obj.get("recent10_stat_avg")
        if r3_stat is not None and r10_stat is not None:
            row_obj["momentum_diff_3v10"] = r3_stat - r10_stat

        team_pace = row_obj.get("team_pace")
        season_usg = row_obj.get("season_usage_pct_avg")
        if team_pace is not None and season_usg is not None:
            row_obj["expected_possessions"] = team_pace * (season_usg / 100.0)

        r5_min = row_obj.get("recent5_minutes_avg")
        r10_min = row_obj.get("recent10_minutes_avg")
        sea_min = row_obj.get("season_minutes_avg")
        
        if r5_min is not None or r10_min is not None or sea_min is not None:
            r5 = r5_min if r5_min is not None else sea_min
            r10 = r10_min if r10_min is not None else sea_min
            sea = sea_min if sea_min is not None else r5_min
            
            if r5 is not None and r10 is not None and sea is not None:
                pred_min = r5 * 0.50 + r10 * 0.30 + sea * 0.20
                is_b2b = row_obj.get("is_b2b", 0)
                if is_b2b == 1:
                    pred_min *= (1.0 - 0.035)
                row_obj["predicted_minutes"] = max(4.0, min(42.0, pred_min))

        target_pos_group = (
            self.enrichment.get_player_position_group(player_id)
            if self.enrichment
            else normalize_position_group(player_info.get("position"))
        )
        team_vacancy_stats = self.live_team_vacancy_stats.get((team_abbr, target_date))
        same_pos_vacancy_stats = self.live_same_pos_vacancy_stats.get((team_abbr, target_date, target_pos_group))
        teammate_onoff_stats = _build_key_teammate_onoff_features(
            player_history,
            len(player_history) - 1,
            stat_type=stat_type,
            team_presence_index=self.live_team_presence_index,
            team_game_dates_by_season=self.live_team_game_dates_by_season,
            current_missing_player_priors=self.live_missing_player_priors.get((team_abbr, target_date)),
            current_active_player_priors=self.live_active_player_priors.get((team_abbr, target_date)),
        )
        apply_injury_feature_values(
            row_obj,
            team_vacancy_stats=team_vacancy_stats,
            same_pos_vacancy_stats=same_pos_vacancy_stats,
            teammate_onoff_stats=teammate_onoff_stats,
        )

        minutes_row = _build_minutes_row(
            player_history,
            len(player_history) - 1,
            min_prior=3,
            enrichment=self.enrichment,
            team_missing_player_priors_dict=self.live_missing_player_priors,
            team_active_player_priors_dict=self.live_active_player_priors,
            team_presence_index=self.live_team_presence_index,
            team_game_dates_by_season=self.live_team_game_dates_by_season,
        )
        minutes_preds = self._predict_modeled_minutes(row_obj, minutes_row)
        if minutes_row:
            row_obj["same_team_current_season_games"] = minutes_row.get("same_team_current_season_games")
            row_obj["recent_team_games_missed_10"] = minutes_row.get("recent_team_games_missed_10")
            row_obj["inactive_streak_team_games"] = minutes_row.get("inactive_streak_team_games")
            row_obj["games_since_return"] = minutes_row.get("games_since_return")
            row_obj["previous_absence_streak_team_games"] = minutes_row.get("previous_absence_streak_team_games")
        if minutes_preds:
            row_obj["minutes_model_q25"] = round(minutes_preds["q25"], 4)
            row_obj["minutes_model_q50"] = round(minutes_preds["q50"], 4)
            row_obj["minutes_model_q75"] = round(minutes_preds["q75"], 4)

        # Keep legacy exported models working until retraining replaces the old flag.
        if "star_teammate_out_flag" in getattr(self, "feat_cols", []):
            row_obj["star_teammate_out_flag"] = 1 if (
                (row_obj.get("missing_team_usage_pct") or 0.0) > 0.0
                or (row_obj.get("missing_team_minutes") or 0.0) > 0.0
            ) else 0

        return row_obj

    def predict(
        self,
        player_info: Dict[str, Any],
        logs: List[Dict[str, Any]],
        stat_type: str,
        *,
        include_features: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if not self.ready:
            return None

        model = self.models.get(stat_type)
        if model is None:
            # Fallback for unified model backwards compatibility if it exists
            model = self.models.get("unified")
            if model is None:
                return None

        features = self._build_feature_dict(player_info, logs, stat_type)
        if not features:
            return None

        drift_alerts = self._check_drift(features)
        self._log_drift_summary(player_info, stat_type, drift_alerts)

        vector = []
        for c in self.feat_cols:
            if c == "stat_type" and "stat_type" not in features:
                features["stat_type"] = stat_type
                
            val = features.get(c)
            if c in self.cat_cols:
                vector.append(str(val) if val is not None else "UNKNOWN")
            else:
                vector.append(float(val) if val is not None else np.nan)

        try:
            preds = model.predict([vector]) # shape (1, 3) for quantiles if length 3
            if preds.shape == (1, 3):
                result = {
                    "q25": float(preds[0, 0]),
                    "q50": float(preds[0, 1]),
                    "q75": float(preds[0, 2])
                }
                if include_features:
                    result["feature_snapshot"] = dict(features)
                return result
            elif preds.shape == (1,):
                # Legacy model handling
                result = {
                    "prediction": float(preds[0]),
                    "std_dev": self.q_stats.get(stat_type, {}).get("std", 1.0) # Fallback to residuals
                }
                if include_features:
                    result["feature_snapshot"] = dict(features)
                return result
            else:
                return None
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return None

    def hit_probability(self, prediction: float, std_dev: float, line: float, side: str) -> float:
        """Legacy interface — Kept for backward compatibility."""
        from scipy.stats import norm
        if std_dev is None or std_dev <= 0:
            return 0.6 if (side == "over") == (prediction > line) else 0.4
        p_over = 1.0 - norm.cdf(line, loc=prediction, scale=std_dev)
        return p_over if side == "over" else 1.0 - p_over

def get_ml_predictor() -> MLPredictor:
    return MLPredictor()
