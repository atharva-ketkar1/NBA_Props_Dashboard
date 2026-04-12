import json
import logging
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool

FEATURE_COLS = [
    "player_id", "team", "opponent", "is_home",
    "prior_games", "days_rest", "is_b2b",
    "season_stat_avg", "recent3_stat_avg", "recent5_stat_avg",
    "recent10_stat_avg", "recent20_stat_avg",
    "recent5_stat_ema",
    "season_home_stat_avg", "season_away_stat_avg",
    "season_stat_std", "recent5_stat_std", "recent10_stat_std",
    "momentum_5v20", "momentum_3v10",
    "recent5_cv", "recent10_cv",
    "season_minutes_avg", "recent3_minutes_avg",
    "recent5_minutes_avg", "recent10_minutes_avg",
    "minutes_trend_5v20", "minutes_cv_recent5",
    "season_usage_pct_avg", "recent10_usage_pct_avg",
    "season_ast_pct_avg", "recent10_ast_pct_avg",
    "season_reb_pct_avg", "recent10_reb_pct_avg",
    "season_ts_pct_avg", "recent10_ts_pct_avg",
    "season_potential_ast_rate", "recent10_potential_ast_rate",
    "season_reb_chance_rate", "recent10_reb_chance_rate",
    "season_drive_rate", "recent10_drive_rate",
    "season_fg3a_rate", "recent10_fg3a_rate",
    "recent5_pts_per100",
    "recent5_1h_stat_share",
    # Opponent defensive features
    "opp_pts_defense_rank",
    "opp_catchAndShoot_rank",
    "opp_pullup_rank",
    "opp_lessThan10ft_rank",
    "opp_def_restricted_pct",
    "opp_def_paint_pct",
    "opp_def_3pt_pct",
    "opp_def_restricted_rank",
    "opp_def_3pt_rank",
    # Player style fingerprint
    "player_catchAndShoot_pg",
    "player_pullup_pg",
    "player_lessThan10ft_pg",
    "player_transition_pg",
    "player_isolation_pg",
    "player_pnr_pg",
    "player_spotup_pg",
    # Cross-feature matchup scores
    "matchup_score_fg3",
    "matchup_score_pts",
    "matchup_score_interior",
    # Context
    "team_pace",
    "opp_def_rating",
    "recent5_avg_game_margin",
    "recent5_blowout_flag",
]

TARGET = "actual_value"
UNIFIED_CAT = ["stat_type", "player_id", "team", "opponent"]
REG_CSV = "backend/prop_modeling/generated/regression_training_dataset.csv"

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

export_dir = Path("backend/prop_modeling/exported_regression_model")
export_dir.mkdir(parents=True, exist_ok=True)

logging.info("Reading CSV...")
df = pd.read_csv(REG_CSV)
df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")

# Filter
df = df.dropna(subset=[TARGET]).copy()
df = df[df["prior_games"] >= 5].copy()

# Add stat_type to unified features
FEATURE_COLS = ["stat_type"] + [c for c in FEATURE_COLS if c in df.columns]

# Prep features
X = df[FEATURE_COLS].copy()
for c in FEATURE_COLS:
    if c in UNIFIED_CAT or c == "stat_type":
        X[c] = X[c].fillna("UNKNOWN").astype(str)
    else:
        X[c] = pd.to_numeric(X[c], errors="coerce")
y = df[TARGET].to_numpy()

cat_indices = [FEATURE_COLS.index(c) for c in UNIFIED_CAT if c in FEATURE_COLS]

logging.info(f"Training Baseline Model on {len(X)} rows with {len(FEATURE_COLS)} features...")

train_pool = Pool(X, label=y, cat_features=cat_indices)

# From regression_notebook Cell 21 (Baseline params):
# bestIteration was 330.
model = CatBoostRegressor(
    iterations=331,
    depth=6,
    learning_rate=0.03,
    l2_leaf_reg=5.0,
    loss_function="RMSE",
    eval_metric="MAE",
    random_seed=42,
    verbose=100
)

model.fit(train_pool)

logging.info("Saving unified_regression.cbm...")
model.save_model(export_dir / "unified_regression.cbm")

# Generate residual distribution for probabilities
y_pred = model.predict(train_pool)
resids = y - y_pred

stats = {
    "count": float(len(resids)),
    "mean": float(np.mean(resids)),
    "std": float(np.std(resids)),
    "mae": float(np.mean(np.abs(resids))),
    "percentiles": {
        "25": float(np.percentile(resids, 25)),
        "50": float(np.percentile(resids, 50)),
        "75": float(np.percentile(resids, 75))
    }
}
with open(export_dir / "residual_stats.json", "w") as f:
    json.dump(stats, f, indent=2)

with open(export_dir / "model_metadata.json", "w") as f:
    json.dump({
        "desc": "CatBoost Baseline (v3)",
        "features": FEATURE_COLS,
        "cat_features": UNIFIED_CAT,
        "target": TARGET
    }, f, indent=2)

logging.info("Export completed.")
