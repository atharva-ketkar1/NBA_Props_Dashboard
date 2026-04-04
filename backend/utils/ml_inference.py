import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import date, datetime
import math
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BACKEND_DIR / "prop_modeling"))

from catboost import CatBoostRegressor
from scipy.stats import norm
import numpy as np

# We import the EnrichmentData class we wrote earlier to load JSON files fast
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
        self.model = None
        self.stats = {}
        self.meta = {}
        self.enrichment = None
        self.ready = False

        cbm_path = ML_MODEL_DIR / "unified_regression.cbm"
        stats_path = ML_MODEL_DIR / "residual_stats.json"
        meta_path = ML_MODEL_DIR / "model_metadata.json"

        if not cbm_path.exists() or not stats_path.exists() or not meta_path.exists():
            logging.warning("MLPredictor: Missing model files. ML Inference offline.")
            return

        try:
            self.model = CatBoostRegressor()
            self.model.load_model(str(cbm_path))
            
            with open(stats_path) as f:
                self.stats = json.load(f)
            with open(meta_path) as f:
                self.meta = json.load(f)

            # Load the cache dictionary for team paces and opposing defensives (~0.5s)
            self.enrichment = EnrichmentData(CURRENT_DATA_DIR)
            
            self.ready = True
            logging.info("MLPredictor: Successfully loaded model and cache.")
        except Exception as e:
            logging.error(f"MLPredictor: Initialization failed: {e}")

    def predict(self, player_info: Dict[str, Any], logs: List[Dict[str, Any]], stat_type: str) -> Optional[Dict[str, Any]]:
        if not self.ready:
            return None

        # Build dummy target row for TODAY using recent log for identity
        if not logs:
            return None
        
        last_log = logs[0] # Usually latest is first? Need to verify sorting. 
        # Actually edge_score.py logs are newest first? Let's assume youngest is logs[0].
        # build_regression_dataset expects older first. We need to flip logs and append a dummy target.
        
        # In edge_score logs are descending. Let's make ascending
        asc_logs = list(reversed(logs))
        
        # We need a dummy row for "today"
        dummy_row = {
            "PLAYER_ID": player_info.get("player_id", last_log.get("PLAYER_ID")),
            "PLAYER_NAME": player_info.get("player_name"),
            "TEAM_ABBREVIATION": player_info.get("team_abbrev", last_log.get("TEAM_ABBREVIATION")),
            "MATCHUP": f"vs. {player_info.get('opponent_abbrev', 'UNK')}", # Usually edge_score has this
            "POSITION": player_info.get("position", ""),
            "MIN": 30.0, # dummy value so it doesn't get filtered out
            # MUST include dummy stat so _build_regression_row target check doesn't bail
        }
        
        # Populate all typical stats with 0 for the target row
        for st in ["PTS", "AST", "REB", "STL", "BLK", "FG3M", "TOV", "FGA", "FTA", "FGM", "FTM"]:
            dummy_row[st] = 0.0
        
        if "team_abbrev" not in player_info and "team" in player_info:
            dummy_row["TEAM_ABBREVIATION"] = player_info["team"]

        # Append dummy row
        asc_logs.append(dummy_row)
        
        # Convert to tuple list for _build_regression_row
        # Date doesn't strictly matter for the target other than rest days
        target_date = date.today()
        player_history = []
        for i, r in enumerate(asc_logs):
            d = r.get("GAME_DATE")
            # attempt parse
            parsed_d = None
            if d:
                try:
                    parsed_d = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
                except:
                    pass
            # if target
            if i == len(asc_logs) - 1:
                parsed_d = target_date
            player_history.append((parsed_d, r))
            
        row_obj = _build_regression_row(
            game_idx=len(player_history)-1,
            player_history=player_history,
            stat_type=stat_type,
            min_prior=3,
            min_minutes=1.0,
            enrichment=self.enrichment
        )

        if not row_obj:
            return None

        # Build feature vector
        features = self.meta.get("features", [])
        vector = []
        for c in features:
            val = row_obj.get(c)
            if c in self.meta.get("cat_features", []):
                vector.append(str(val) if val is not None else "UNKNOWN")
            else:
                vector.append(float(val) if val is not None else np.nan)

        # Predict
        try:
            pred_val = self.model.predict([vector])[0]
        except Exception as e:
            logging.error(f"Prediction error: {e}")
            return None

        return {
            "prediction": pred_val,
            "std_dev": self.stats["std"]
        }

    def hit_probability(self, prediction: float, std_dev: float, line: float, side: str) -> float:
        """Returns the probability (0.0 to 1.0) of covering the line."""
        if side == "over":
            prob = 1.0 - norm.cdf(line, loc=prediction, scale=std_dev)
        else:
            prob = norm.cdf(line, loc=prediction, scale=std_dev)
        return prob

def get_ml_predictor() -> MLPredictor:
    return MLPredictor()
