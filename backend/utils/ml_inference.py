import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import date, datetime
import sys

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

logger = logging.getLogger("MLInference")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR / "prop_modeling"))

# Import the EnrichmentData logic that builds the row initially
from build_regression_dataset import EnrichmentData, _build_regression_row

ML_MODEL_DIR = BACKEND_DIR / "prop_modeling" / "exported_regression_model"
CURRENT_DATA_DIR = BACKEND_DIR / "data" / "current"

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
        self.meta = {}
        self.q_stats = {}
        self.drift_b = {}
        self.enrichment = None
        self.ready = False

        meta_path = ML_MODEL_DIR / "model_metadata.json"
        q_stats_path = ML_MODEL_DIR / "quantile_stats.json"
        drift_b_path = ML_MODEL_DIR / "feature_drift_baseline.json"

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

            # Load per-stat models
            model_files = self.meta.get("model_files", {})
            for st, fname in model_files.items():
                cbm_path = ML_MODEL_DIR / fname
                if cbm_path.exists():
                    model = CatBoostRegressor().load_model(str(cbm_path))
                    self.models[st] = model
                else:
                    logger.warning(f"MLPredictor: Missing model file {fname} for {st}")

            self.feat_cols = self.meta.get("feature_columns", [])
            self.cat_cols = self.meta.get("cat_features", [])
            self.cat_idx = [self.feat_cols.index(c) for c in self.cat_cols if c in self.feat_cols]

            # Load the cache dictionary for team paces and opposing defensives (~0.5s)
            self.enrichment = EnrichmentData(CURRENT_DATA_DIR)
            
            # Load live injury report precisely for inference today
            self.live_injured_players = set()
            p = CURRENT_DATA_DIR / "nba_injury_report.json"
            if p.exists():
                try:
                    with open(p) as f:
                        raw = json.load(f)
                    games = raw.get("games", [])
                    for game in games:
                        teams = game.get("teams", {})
                        for team_data in teams.values():
                            players = team_data.get("players", [])
                            for report in players:
                                name_str = report.get("player_name")
                                status = report.get("current_status")
                                if name_str and status in ["Out", "Doubtful", "Probable"]:
                                    self.live_injured_players.add(name_str.upper())
                except Exception as e:
                    logger.error(f"MLPredictor: Failed to load live injuries: {e}")
            
            self.ready = True
            logger.info(f"MLPredictor: Successfully loaded {len(self.models)} per-stat models.")
        except Exception as e:
            logger.error(f"MLPredictor: Initialization failed: {e}")

    def _check_drift(self, features: Dict[str, Any], z_threshold: float = 3.0) -> List[str]:
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
                alerts.append(
                    f"{col}: {fval:.3f} is {z:.1f}sigma from training mean={mean_val:.3f}"
                )
        return alerts

    def _get_injured_star_flag(self, team_abbrev: str) -> int:
        if not self.enrichment:
            return 0
            
        stars = set()
        for pid, stats in self.enrichment.season_stats.items():
            t = str(stats.get("TEAM_ABBREVIATION") or "").strip()
            if t == team_abbrev:
                usg = stats.get("USG_PCT")
                try:
                    f_usg = float(usg)
                except (TypeError, ValueError):
                    f_usg = 0.0
                    
                if f_usg >= 23.0:
                    name = self.enrichment.player_id_to_name.get(pid)
                    if name:
                        stars.add(name.upper())

        for star_name in stars:
            if star_name in getattr(self, "live_injured_players", set()):
                logger.info(f"Team {team_abbrev} has star {star_name} injured! Setting flag.")
                return 1
        return 0

    def _build_feature_dict(self, player_info: Dict[str, Any], logs: List[Dict[str, Any]], stat_type: str) -> Optional[Dict[str, Any]]:
        # Same initial conversion as old setup
        last_log = logs[0]
        asc_logs = list(reversed(logs))
        
        dummy_row = {
            "PLAYER_ID": player_info.get("player_id", last_log.get("PLAYER_ID")),
            "PLAYER_NAME": player_info.get("player_name"),
            "TEAM_ABBREVIATION": player_info.get("team_abbrev", last_log.get("TEAM_ABBREVIATION")),
            "MATCHUP": f"vs. {player_info.get('opponent_abbrev', 'UNK')}",
            "POSITION": player_info.get("position", ""),
            "MIN": 30.0,
        }
        
        for st in ["PTS", "AST", "REB", "STL", "BLK", "FG3M", "TOV", "FGA", "FTA", "FGM", "FTM"]:
            dummy_row[st] = 0.0
            
        if "team_abbrev" not in player_info and "team" in player_info:
            dummy_row["TEAM_ABBREVIATION"] = player_info["team"]

        asc_logs.append(dummy_row)
        
        target_date = date.today()
        player_history = []
        for i, r in enumerate(asc_logs):
            d = r.get("GAME_DATE")
            parsed_d = None
            if d:
                try:
                    parsed_d = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
                except:
                    pass
            if i == len(asc_logs) - 1:
                parsed_d = target_date
            player_history.append((parsed_d, r))
            
        row_obj = _build_regression_row(
            game_idx=len(player_history)-1,
            player_history=player_history,
            stat_type=stat_type,
            min_prior=3,
            min_minutes=1.0,
            enrichment=self.enrichment,
            star_out_dict={}
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

        # Check for live injured star teammates!
        team_abbr = player_info.get("team_abbrev", dummy_row.get("TEAM_ABBREVIATION"))
        row_obj["star_teammate_out_flag"] = self._get_injured_star_flag(team_abbr)

        return row_obj

    def predict(self, player_info: Dict[str, Any], logs: List[Dict[str, Any]], stat_type: str) -> Optional[Dict[str, Any]]:
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

        for warning in self._check_drift(features):
            logger.warning(f"Feature drift: {warning}")

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
                return {
                    "q25": float(preds[0, 0]),
                    "q50": float(preds[0, 1]),
                    "q75": float(preds[0, 2])
                }
            elif preds.shape == (1,):
                # Legacy model handling
                return {
                    "prediction": float(preds[0]),
                    "std_dev": self.q_stats.get(stat_type, {}).get("std", 1.0) # Fallback to residuals
                }
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
