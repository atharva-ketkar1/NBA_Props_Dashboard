# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # NBA Prop Model — v2 Regression (Per-Stat MultiQuantile + Walk-Forward CV)
#
# **Changes from v1:**
# - Per-stat CatBoost MultiQuantile models (q25 / q50 / q75) replace the single
#   unified RMSE model. The IQR spread is used directly by `edge_score.py` to
#   derive `p_over` without the Gaussian residual-std hack.
# - Walk-forward cross-validation (4 folds, 20 days each) replaces the static
#   20-date holdout for more reliable MAE estimates.
# - New engineered features: `momentum_diff_5v20`, `expected_possessions`,
#   `predicted_minutes` (infrastructure-safe weighted-blend approximation),
#   exponential time-decay sample weights.
# - Per-stat Optuna tuning with trial budgets scaled to sample size
#   (40 trials for PTS/AST/REB/FG3M, 20 for all others).
# - `backtest_vs_lines` now reports simulated ROI at -110 juice alongside
#   raw directional accuracy.
# - Feature drift monitoring block: exports `feature_drift_baseline.json`
#   consumed by `utils/ml_inference.py` to log inference-time warnings.
# - Exports one `.cbm` per stat type + `quantile_stats.json`
#   (replaces `residual_stats.json`).
#
# **Upload files when prompted:**
# 1. `regression_training_dataset.csv`
# 2. `minutes_training_dataset.csv`
# 3. `prop_training_dataset.csv`  (optional, for backtest against actual lines)

# %% [markdown]
# ## 1. Setup

# %%
nvidia-smi


# %%
from google.colab import drive
drive.mount('/content/drive')

# %%
import subprocess, sys
def pip_install(*pkgs):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])

pip_install("catboost", "optuna", "lightgbm", "shap", "scipy")

# %%
import warnings
warnings.filterwarnings("ignore")

import json, math, os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from catboost import CatBoostRegressor, Pool
from IPython.display import display
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sns.set_theme(style="darkgrid", palette="muted")
plt.rcParams["figure.figsize"] = (14, 6)
plt.rcParams["figure.dpi"] = 100

print("Imports loaded")

# CatBoost MultiQuantile is not supported on GPU in this workflow, so force CPU
# even in Colab GPU runtimes to avoid failed Optuna trials and retrains.
CATBOOST_MULTIQUANTILE_TASK_TYPE = "CPU"
print(f"CatBoost MultiQuantile task_type: {CATBOOST_MULTIQUANTILE_TASK_TYPE}")

# %% [markdown]
# ## 2. Upload & Load Data

# %%
import os

# The actual path based on your print output
COLAB_PATH = "/content/drive/MyDrive/regression_training_dataset.csv"
LOCAL_PATH = "backend/prop_modeling/generated/regression_training_dataset.csv"

if os.path.exists(COLAB_PATH):
    REG_CSV = COLAB_PATH
    print(f"✅ Success! Found in Google Drive: {REG_CSV}")
elif os.path.exists(LOCAL_PATH):
    REG_CSV = LOCAL_PATH
    print(f"✅ Running locally: {REG_CSV}")
else:
    print("❌ Still can't find it. Run !ls /content/drive/MyDrive to double-check spelling.")

# Now you can load it
# import pandas as pd
# df = pd.read_csv(REG_CSV)

# %%
COLAB_MINUTES_PATH = "/content/drive/MyDrive/minutes_training_dataset.csv"
LOCAL_MINUTES_PATH = "backend/prop_modeling/generated/minutes_training_dataset.csv"

if os.path.exists(COLAB_MINUTES_PATH):
    MINUTES_CSV = COLAB_MINUTES_PATH
    print(f"✅ Minutes data found in Drive: {MINUTES_CSV}")
elif os.path.exists(LOCAL_MINUTES_PATH):
    MINUTES_CSV = LOCAL_MINUTES_PATH
    print(f"✅ Minutes data found locally: {MINUTES_CSV}")
else:
    raise FileNotFoundError(
        "minutes_training_dataset.csv not found. Upload it to Google Drive or place it in "
        "backend/prop_modeling/generated/."
    )

# %%
df = pd.read_csv(REG_CSV)
df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
df = df[df["game_date"].notna()].copy()
df["actual_value"] = pd.to_numeric(df["actual_value"], errors="coerce")
df = df[df["actual_value"].notna()].copy()

minutes_df = pd.read_csv(MINUTES_CSV)
minutes_df["game_date"] = pd.to_datetime(minutes_df["game_date"], errors="coerce")
minutes_df = minutes_df[minutes_df["game_date"].notna()].copy()
minutes_df["actual_minutes"] = pd.to_numeric(minutes_df["actual_minutes"], errors="coerce")
minutes_df = minutes_df[minutes_df["actual_minutes"].notna()].copy()

print(f"Loaded {len(df):,} rows")
print(f"Dates: {df['game_date'].dt.date.nunique()} "
      f"({df['game_date'].min().date()} to {df['game_date'].max().date()})")
print(f"Players:    {df['player_id'].nunique()}")
print(f"Stat types: {sorted(df['stat_type'].unique())}")
print(f"Minutes rows: {len(minutes_df):,}")

# %%
import os

# 1. Define your potential paths
COLAB_PROP_PATH = "/content/drive/MyDrive/prop_training_dataset.csv"
LOCAL_PROP_PATH = "backend/prop_modeling/generated/prop_training_dataset.csv"

PROP_CSV = None

# 2. Logic to assign the correct path
if os.path.exists(COLAB_PROP_PATH):
    PROP_CSV = COLAB_PROP_PATH
    print(f"✅ Prop data found in Drive: {PROP_CSV}")
    
elif os.path.exists(LOCAL_PROP_PATH):
    PROP_CSV = LOCAL_PROP_PATH
    print(f"✅ Prop data found locally: {PROP_CSV}")
    
else:
    print("⚠️ (Optional) Prop dataset not found. Skipping line backtest.")
    print("   To include it, ensure 'prop_training_dataset.csv' is in your Google Drive base folder.")

# Your backtest logic continues here...
if PROP_CSV:
    print(f"Proceeding with backtest using: {PROP_CSV}")

# %% [markdown]
# ## 3. EDA

# %%
fig, axes = plt.subplots(1, 2, figsize=(16, 5))
df["stat_type"].value_counts().sort_index().plot(kind="barh", ax=axes[0], color="steelblue")
axes[0].set_title("Rows per Stat Type")

pts = df[df["stat_type"] == "PTS"]["actual_value"]
pts.hist(bins=50, ax=axes[1], color="coral", alpha=0.7)
axes[1].set_title("PTS Distribution")
axes[1].set_xlabel("Points")
plt.tight_layout()
plt.show()

# %%
summary = df.groupby("stat_type")["actual_value"].agg(["mean", "std", "median", "min", "max"])
display(summary.round(2))

# %%
feat_cols_check = [c for c in df.columns if c not in [
    "game_date", "player_id", "team", "opponent", "game_id", "stat_type", "actual_value"
]]
missing = df[feat_cols_check].isnull().mean().sort_values(ascending=False)
print("Features with >10% missing:")
print(missing[missing > 0.1].to_string())

# %% [markdown]
# ## 4. Feature Engineering & Column Setup

# %%
TARGET = "actual_value"
CAT_FEATURES = ["player_id", "team", "opponent"]
MINUTES_TARGET = "actual_minutes"
MINUTES_CAT_FEATURES = ["player_id", "team", "opponent"]
MODELED_MINUTES_COLS = [
    "modeled_minutes_q50",
    "modeled_minutes_iqr",
    "modeled_minutes_delta_vs_recent5",
]
MINUTES_FEATURE_COLS = [
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
MINUTES_OOF_BLOCK_DATES = 10
MINUTES_OOF_MIN_TRAIN_DATES = 30

BASE_FEATURE_COLS = [
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
    "recent10_target_per_min",
    "missing_same_pos_minutes_x_player_target_per_min",
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
    # Continuous teammate absence context from trailing inactive-player usage/minutes.
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
    "playmaker_vacuum_x_player_ast_rate",
    "onball_vacuum_x_player_drive_rate",
    "usage_vacuum_x_player_usage_pct",
    "missing_playmaker_potential_ast_pg_x_player_ast_rate",
    "missing_onball_drives_pg_x_player_drive_rate",
    "missing_high_usage_usage_pct_x_player_usage_rate",
    "missing_key_teammates_player_stat_delta",
    "missing_key_teammates_player_minutes_delta",
    "missing_key_teammates_player_usage_pct_delta",
    "missing_key_teammates_player_potential_ast_rate_delta",
    "missing_key_teammates_player_drive_rate_delta",
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
    "returning_key_teammates_effective_support",
    "returning_key_teammate_count",
    "returning_same_pos_key_count",
    "returning_guard_key_count",
    "returning_playmaker_key_count",
    "team_fg_attempts_share_last5",
]

# Filter to columns that actually exist in the loaded CSV
BASE_FEATURE_COLS = [c for c in BASE_FEATURE_COLS if c in df.columns]
MINUTES_FEATURE_COLS = [c for c in MINUTES_FEATURE_COLS if c in minutes_df.columns]
print(f"Base features available in CSV: {len(BASE_FEATURE_COLS)}")
print(f"Minutes features available in CSV: {len(MINUTES_FEATURE_COLS)}")

# %%
# ── Engineered features ──────────────────────────────────────────────────────
# These are computed at training time and must be replicated in ml_inference.py
# using the same logic so training and inference remain aligned.

def engineer_features(input_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add engineered features in-place on a copy of the input dataframe.

    All features here must also be computed in ml_inference.py before
    running model.predict() to avoid training/inference mismatch.
    """
    out = input_df.copy()

    # 1. Signed momentum difference (raw gap, not ratio)
    #    Gradient boosters benefit from having both the ratio (momentum_5v20)
    #    and the raw difference as separate features.
    if "recent5_stat_avg" in out.columns and "recent20_stat_avg" in out.columns:
        out["momentum_diff_5v20"] = out["recent5_stat_avg"] - out["recent20_stat_avg"]
    if "recent3_stat_avg" in out.columns and "recent10_stat_avg" in out.columns:
        out["momentum_diff_3v10"] = out["recent3_stat_avg"] - out["recent10_stat_avg"]

    # 2. Pace-adjusted expected possessions
    #    Captures player opportunity in context of game pace and usage.
    if "team_pace" in out.columns and "season_usage_pct_avg" in out.columns:
        out["expected_possessions"] = (
            out["team_pace"] * (out["season_usage_pct_avg"].fillna(0.0) / 100.0)
        )

    # 3. Infrastructure-safe predicted minutes
    #    A dedicated minutes sub-model would require loading a second .cbm on
    #    the e2-micro VM (1 GB RAM) and risk OOM during the cron cycle.
    #    This weighted blend captures the majority of the signal at zero
    #    memory overhead. Revisit if compute is ever upgraded.
    min_cols = ["recent5_minutes_avg", "recent10_minutes_avg", "season_minutes_avg"]
    if all(c in out.columns for c in min_cols):
        r5  = out["recent5_minutes_avg"].fillna(out["season_minutes_avg"])
        r10 = out["recent10_minutes_avg"].fillna(out["season_minutes_avg"])
        sea = out["season_minutes_avg"].fillna(out["recent5_minutes_avg"])
        pred_min = r5 * 0.50 + r10 * 0.30 + sea * 0.20
        if "is_b2b" in out.columns:
            b2b = pd.to_numeric(out["is_b2b"], errors="coerce").fillna(0.0)
            pred_min = pred_min * (1.0 - 0.035 * b2b)
        out["predicted_minutes"] = pred_min.clip(lower=4.0, upper=42.0)

    if "modeled_minutes_q50" in out.columns and "recent10_target_per_min" in out.columns:
        out["modeled_minutes_x_recent10_target_per_min"] = (
            pd.to_numeric(out["modeled_minutes_q50"], errors="coerce").fillna(0.0)
            * pd.to_numeric(out["recent10_target_per_min"], errors="coerce").fillna(0.0)
        )

    return out


df = engineer_features(df)

# Final feature list: base (from CSV) + engineered
ENGINEERED_COLS = [
    c for c in ["momentum_diff_5v20", "momentum_diff_3v10",
                 "expected_possessions", "predicted_minutes",
                 "modeled_minutes_x_recent10_target_per_min"]
    if c in df.columns
]
FEATURE_COLS = BASE_FEATURE_COLS + ENGINEERED_COLS

# Unified feature list (adds stat_type for LightGBM comparison)
UNIFIED_FEATURES = ["stat_type"] + FEATURE_COLS
UNIFIED_CAT      = ["stat_type"] + CAT_FEATURES

print(f"Engineered features added: {ENGINEERED_COLS}")
print(f"Total feature columns:     {len(FEATURE_COLS)}")


def prepare(
    input_df: pd.DataFrame,
    feat_cols: List[str],
    cat_cols: List[str] = CAT_FEATURES,
) -> pd.DataFrame:
    """Cast categoricals to str and numerics to float for model input."""
    X = input_df[feat_cols].copy()
    for c in feat_cols:
        if c in cat_cols or c == "stat_type":
            X[c] = X[c].fillna("UNKNOWN").astype(str)
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return X


def make_sample_weights(input_df: pd.DataFrame, decay: float = 0.001) -> np.ndarray:
    """
    Exponential time-decay sample weights keyed on game_date.

    decay=0.001 halves the weight at ~700 days (~2 seasons), so recent
    games matter roughly 2x more than games from two seasons ago.
    Weights are normalised so the mean equals 1.0, keeping the loss scale
    the same as unweighted training.
    """
    today    = input_df["game_date"].max()
    days_ago = (today - input_df["game_date"]).dt.days.clip(lower=0).to_numpy()
    weights  = np.exp(-decay * days_ago)
    weights  = weights / weights.mean()
    return weights.astype(np.float32)


def estimate_minutes_iqr_series(input_df: pd.DataFrame, center_col: str) -> pd.Series:
    center = pd.to_numeric(input_df[center_col], errors="coerce")
    recent5 = pd.to_numeric(input_df.get("recent5_minutes_avg"), errors="coerce")
    recent10 = pd.to_numeric(input_df.get("recent10_minutes_avg"), errors="coerce")
    season = pd.to_numeric(input_df.get("season_minutes_avg"), errors="coerce")
    cv = pd.to_numeric(input_df.get("minutes_cv_recent5"), errors="coerce")

    spread = pd.Series(1.0, index=input_df.index, dtype=float)
    if cv is not None:
        spread = np.maximum(spread, (center.fillna(0.0) * cv.fillna(0.0)).abs())
    if recent5 is not None and recent10 is not None:
        spread = np.maximum(spread, (recent5.fillna(0.0) - recent10.fillna(0.0)).abs())
    if recent5 is not None and season is not None:
        spread = np.maximum(spread, (recent5.fillna(0.0) - season.fillna(0.0)).abs())
    if recent10 is not None and season is not None:
        spread = np.maximum(spread, (recent10.fillna(0.0) - season.fillna(0.0)).abs())

    return pd.Series(spread, index=input_df.index).round(4)


def minutes_metrics(actual: np.ndarray, q25: np.ndarray, q50: np.ndarray, q75: np.ndarray) -> Dict[str, float]:
    coverage = float(((actual >= q25) & (actual <= q75)).mean()) if len(actual) else 0.0
    return {
        "mae_q50": round(float(mean_absolute_error(actual, q50)), 4),
        "rmse_q50": round(float(math.sqrt(mean_squared_error(actual, q50))), 4),
        "r2_q50": round(float(r2_score(actual, q50)), 4),
        "median_iqr": round(float(np.median(q75 - q25)), 4),
        "q50_bias": round(float((q50 - actual).mean()), 4),
        "iqr_coverage": round(coverage, 4),
    }


def train_minutes_model(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[CatBoostRegressor, np.ndarray]:
    train_params = {
        "iterations": 700,
        "depth": 6,
        "learning_rate": 0.035,
        "l2_leaf_reg": 5.0,
        "loss_function": "MultiQuantile:alpha=0.25,0.5,0.75",
        "random_seed": 42,
        "verbose": False,
        "allow_writing_files": False,
        "task_type": CATBOOST_MULTIQUANTILE_TASK_TYPE,
    }
    if params:
        train_params.update(params)

    X_tr = prepare(train_df, MINUTES_FEATURE_COLS, MINUTES_CAT_FEATURES)
    X_te = prepare(test_df, MINUTES_FEATURE_COLS, MINUTES_CAT_FEATURES)
    y_tr = train_df[MINUTES_TARGET].to_numpy()
    w_tr = make_sample_weights(train_df)

    cat_idx = [MINUTES_FEATURE_COLS.index(c) for c in MINUTES_CAT_FEATURES if c in MINUTES_FEATURE_COLS]
    tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_idx, weight=w_tr)
    te_pool = Pool(X_te, label=test_df[MINUTES_TARGET].to_numpy(), cat_features=cat_idx)

    model = CatBoostRegressor(**train_params)
    model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=50)
    preds = model.predict(te_pool)
    return model, preds


def build_minutes_prediction_frame(
    input_df: pd.DataFrame,
    *,
    train_cutoff: pd.Timestamp,
) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float], CatBoostRegressor]:
    oof_records: List[pd.DataFrame] = []
    all_dates = sorted(input_df["game_date"].dt.date.unique())
    cat_idx = [MINUTES_FEATURE_COLS.index(c) for c in MINUTES_CAT_FEATURES if c in MINUTES_FEATURE_COLS]

    for block_start in range(MINUTES_OOF_MIN_TRAIN_DATES, len(all_dates), MINUTES_OOF_BLOCK_DATES):
        block_dates = all_dates[block_start:block_start + MINUTES_OOF_BLOCK_DATES]
        if not block_dates:
            continue
        train_dates = all_dates[:block_start]
        train_block = input_df[input_df["game_date"].dt.date.isin(train_dates)].copy()
        pred_block = input_df[
            input_df["game_date"].dt.date.isin(block_dates)
            & (input_df["game_date"] < train_cutoff)
        ].copy()
        if train_block.empty or pred_block.empty:
            continue

        X_tr = prepare(train_block, MINUTES_FEATURE_COLS, MINUTES_CAT_FEATURES)
        X_pr = prepare(pred_block, MINUTES_FEATURE_COLS, MINUTES_CAT_FEATURES)
        y_tr = train_block[MINUTES_TARGET].to_numpy()
        w_tr = make_sample_weights(train_block)

        tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_idx, weight=w_tr)
        pr_pool = Pool(X_pr, cat_features=cat_idx)
        model = CatBoostRegressor(
            iterations=700,
            depth=6,
            learning_rate=0.035,
            l2_leaf_reg=5.0,
            loss_function="MultiQuantile:alpha=0.25,0.5,0.75",
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
            task_type=CATBOOST_MULTIQUANTILE_TASK_TYPE,
        )
        model.fit(tr_pool, verbose=False)
        preds = model.predict(pr_pool)

        block_frame = pred_block[["game_date", "player_id", "game_id"]].copy()
        block_frame["modeled_minutes_q25"] = preds[:, 0]
        block_frame["modeled_minutes_q50"] = preds[:, 1]
        block_frame["modeled_minutes_q75"] = preds[:, 2]
        oof_records.append(block_frame)

    oof_minutes = (
        pd.concat(oof_records, ignore_index=True)
        if oof_records
        else pd.DataFrame(columns=["game_date", "player_id", "game_id", "modeled_minutes_q25", "modeled_minutes_q50", "modeled_minutes_q75"])
    )

    train_minutes = input_df[input_df["game_date"] < train_cutoff].copy()
    test_minutes = input_df[input_df["game_date"] >= train_cutoff].copy()
    final_model, test_preds = train_minutes_model(train_minutes, test_minutes)
    final_test_preds = test_minutes[["game_date", "player_id", "game_id"]].copy()
    final_test_preds["modeled_minutes_q25"] = test_preds[:, 0]
    final_test_preds["modeled_minutes_q50"] = test_preds[:, 1]
    final_test_preds["modeled_minutes_q75"] = test_preds[:, 2]

    minutes_pred_frame = pd.concat([oof_minutes, final_test_preds], ignore_index=True)
    minutes_test_metrics = minutes_metrics(
        test_minutes[MINUTES_TARGET].to_numpy(),
        test_preds[:, 0],
        test_preds[:, 1],
        test_preds[:, 2],
    )

    baseline_pred = pd.to_numeric(test_minutes["recent5_minutes_avg"], errors="coerce").fillna(
        pd.to_numeric(test_minutes["season_minutes_avg"], errors="coerce")
    )
    heuristic_pred = pd.to_numeric(test_minutes.get("recent5_minutes_avg"), errors="coerce").fillna(
        pd.to_numeric(test_minutes.get("season_minutes_avg"), errors="coerce")
    ) * 0.50
    heuristic_pred += pd.to_numeric(test_minutes.get("recent10_minutes_avg"), errors="coerce").fillna(
        pd.to_numeric(test_minutes.get("season_minutes_avg"), errors="coerce")
    ) * 0.30
    heuristic_pred += pd.to_numeric(test_minutes.get("season_minutes_avg"), errors="coerce").fillna(
        pd.to_numeric(test_minutes.get("recent5_minutes_avg"), errors="coerce")
    ) * 0.20
    if "is_b2b" in test_minutes.columns:
        heuristic_pred *= 1.0 - 0.035 * pd.to_numeric(test_minutes["is_b2b"], errors="coerce").fillna(0.0)
    heuristic_pred = heuristic_pred.clip(lower=4.0, upper=42.0)
    heuristic_metrics = {
        "mae_q50": round(float(mean_absolute_error(test_minutes[MINUTES_TARGET], heuristic_pred)), 4),
        "rmse_q50": round(float(math.sqrt(mean_squared_error(test_minutes[MINUTES_TARGET], heuristic_pred))), 4),
        "r2_q50": round(float(r2_score(test_minutes[MINUTES_TARGET], heuristic_pred)), 4),
    }

    return minutes_pred_frame, minutes_test_metrics, heuristic_metrics, final_model


def attach_modeled_minutes(input_df: pd.DataFrame, minutes_pred_frame: pd.DataFrame) -> pd.DataFrame:
    merged = input_df.copy()
    merged["player_id"] = merged["player_id"].astype(str)
    merged["game_id"] = merged["game_id"].astype(str)

    pred_frame = minutes_pred_frame.copy()
    pred_frame["player_id"] = pred_frame["player_id"].astype(str)
    pred_frame["game_id"] = pred_frame["game_id"].astype(str)

    merged = merged.merge(
        pred_frame,
        on=["game_date", "player_id", "game_id"],
        how="left",
    )

    fallback_q50 = pd.to_numeric(merged["predicted_minutes"], errors="coerce")
    fallback_q50 = fallback_q50.fillna(
        (
            pd.to_numeric(merged.get("recent5_minutes_avg"), errors="coerce").fillna(
                pd.to_numeric(merged.get("season_minutes_avg"), errors="coerce")
            ) * 0.50
            + pd.to_numeric(merged.get("recent10_minutes_avg"), errors="coerce").fillna(
                pd.to_numeric(merged.get("season_minutes_avg"), errors="coerce")
            ) * 0.30
            + pd.to_numeric(merged.get("season_minutes_avg"), errors="coerce").fillna(
                pd.to_numeric(merged.get("recent5_minutes_avg"), errors="coerce")
            ) * 0.20
        ).clip(lower=4.0, upper=42.0)
    )
    fallback_iqr = estimate_minutes_iqr_series(
        merged.assign(modeled_minutes_q50=fallback_q50),
        "modeled_minutes_q50",
    )

    merged["modeled_minutes_q50"] = pd.to_numeric(merged["modeled_minutes_q50"], errors="coerce").fillna(fallback_q50)
    q25 = pd.to_numeric(merged.get("modeled_minutes_q25"), errors="coerce")
    q75 = pd.to_numeric(merged.get("modeled_minutes_q75"), errors="coerce")
    modeled_iqr = (q75 - q25).where(q25.notna() & q75.notna())
    merged["modeled_minutes_iqr"] = pd.to_numeric(modeled_iqr, errors="coerce").fillna(fallback_iqr).clip(lower=0.0)
    merged["modeled_minutes_delta_vs_recent5"] = (
        merged["modeled_minutes_q50"]
        - pd.to_numeric(merged.get("recent5_minutes_avg"), errors="coerce").fillna(0.0)
    )

    return merged


shared_dates = sorted(df["game_date"].dt.date.unique())
shared_test_dates = shared_dates[-20:]
shared_split_ts = pd.Timestamp(shared_test_dates[0])

minutes_pred_frame, minutes_test_metrics, heuristic_minutes_metrics, minutes_model = build_minutes_prediction_frame(
    minutes_df,
    train_cutoff=shared_split_ts,
)
df = attach_modeled_minutes(df, minutes_pred_frame)
df = engineer_features(df)

ENGINEERED_COLS = [
    c for c in ["momentum_diff_5v20", "momentum_diff_3v10",
                 "expected_possessions", "predicted_minutes",
                 "modeled_minutes_x_recent10_target_per_min"]
    if c in df.columns
]
FEATURE_COLS = BASE_FEATURE_COLS + ENGINEERED_COLS + [
    column for column in MODELED_MINUTES_COLS if column in df.columns
]
UNIFIED_FEATURES = ["stat_type"] + FEATURE_COLS
UNIFIED_CAT = ["stat_type"] + CAT_FEATURES

print("Minutes model test metrics:", minutes_test_metrics)
print("Heuristic minutes baseline:", heuristic_minutes_metrics)
print(f"Total feature columns after modeled minutes: {len(FEATURE_COLS)}")

# %% [markdown]
# ## 5. Train / Test Split

# %%
unique_dates = sorted(df["game_date"].dt.date.unique())
test_dates   = unique_dates[-20:]   # last 20 game dates = held-out test set
split_ts     = pd.Timestamp(test_dates[0])

train_all = df[df["game_date"] < split_ts].copy()
test_all  = df[df["game_date"] >= split_ts].copy()

print(f"Train: {len(train_all):,} rows  ({train_all['game_date'].dt.date.nunique()} dates)")
print(f"Test:  {len(test_all):,}  rows  ({test_all['game_date'].dt.date.nunique()} dates)")

# %% [markdown]
# ## 6. Walk-Forward Cross-Validation
#
# 4 folds, each using 20 game dates as the test window. Training always uses
# only dates prior to the fold's test window — no future data leaks into any
# fold. We run CV on the unified feature set (all stat types combined) for
# speed; per-stat CV would be too expensive on Colab free tier.

# %%
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)


def walk_forward_cv(
    input_df: pd.DataFrame,
    feature_cols: List[str],
    cat_cols: List[str],
    cat_indices: List[int],
    n_folds: int = 4,
    fold_days: int = 20,
    model_params: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Time-series walk-forward cross-validation.

    Folds are built from the tail of the dataset and walked backwards so
    that the most recent fold's test window aligns with dates just before
    the final held-out test set.
    """
    all_dates = sorted(input_df["game_date"].dt.date.unique())
    n_dates   = len(all_dates)

    folds: List[Tuple[set, set]] = []
    for i in range(n_folds):
        test_end_idx   = n_dates - i * fold_days
        test_start_idx = test_end_idx - fold_days
        if test_start_idx < fold_days * 2:
            break
        test_window  = set(all_dates[test_start_idx:test_end_idx])
        train_window = set(all_dates[:test_start_idx])
        folds.append((train_window, test_window))
    folds.reverse()  # report in chronological order

    if model_params is None:
        model_params = {
            "iterations": 600, "depth": 6, "learning_rate": 0.04,
            "l2_leaf_reg": 5.0, "loss_function": "RMSE", "eval_metric": "RMSE",
            "random_seed": 42, "verbose": False, "allow_writing_files": False,
            "task_type": "CPU",
        }

    fold_metrics: List[Dict] = []
    for fold_num, (train_dates, test_dates_set) in enumerate(folds, start=1):
        fold_train = input_df[input_df["game_date"].dt.date.isin(train_dates)]
        fold_test  = input_df[input_df["game_date"].dt.date.isin(test_dates_set)]
        if fold_train.empty or fold_test.empty:
            continue

        X_tr = prepare(fold_train, feature_cols, cat_cols)
        X_te = prepare(fold_test,  feature_cols, cat_cols)
        y_tr = fold_train[TARGET].to_numpy()
        y_te = fold_test[TARGET].to_numpy()
        w_tr = make_sample_weights(fold_train)

        tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_indices, weight=w_tr)
        te_pool = Pool(X_te, label=y_te, cat_features=cat_indices)

        model = CatBoostRegressor(**model_params)
        model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=50)

        preds = model.predict(te_pool)
        mae   = mean_absolute_error(y_te, preds)
        rmse  = math.sqrt(mean_squared_error(y_te, preds))
        r2    = r2_score(y_te, preds)
        fold_metrics.append({
            "fold": fold_num, "mae": mae, "rmse": rmse, "r2": r2,
            "train_rows": len(fold_train), "test_rows": len(fold_test),
        })
        print(f"  Fold {fold_num}  MAE={mae:.3f}  RMSE={rmse:.3f}  R²={r2:.3f}"
              f"  (train={len(fold_train):,}  test={len(fold_test):,})")

    if not fold_metrics:
        return {"fold_metrics": [], "mean_mae": None, "mean_rmse": None, "mean_r2": None}

    mean_mae  = float(np.mean([m["mae"]  for m in fold_metrics]))
    mean_rmse = float(np.mean([m["rmse"] for m in fold_metrics]))
    mean_r2   = float(np.mean([m["r2"]   for m in fold_metrics]))
    print(f"\n  CV Summary  MAE={mean_mae:.3f}  RMSE={mean_rmse:.3f}  R²={mean_r2:.3f}")
    return {
        "fold_metrics": fold_metrics,
        "mean_mae": mean_mae,
        "mean_rmse": mean_rmse,
        "mean_r2": mean_r2,
    }


print("Running 4-fold walk-forward CV on unified feature set...")
unified_cat_indices = [UNIFIED_FEATURES.index(c) for c in UNIFIED_CAT if c in UNIFIED_FEATURES]
cv_results = walk_forward_cv(
    train_all,
    feature_cols=UNIFIED_FEATURES,
    cat_cols=UNIFIED_CAT,
    cat_indices=unified_cat_indices,
)

# %% [markdown]
# ## 7. Per-Stat MultiQuantile Models (Baseline)
#
# One CatBoostRegressor per stat type using `MultiQuantile:alpha=0.25,0.5,0.75`.
# Outputs shape (n, 3) = [q25, q50, q75].
#
# The IQR (q75 - q25) replaces the old residual-std Gaussian approach.
# `edge_score.py` uses IQR linear interpolation to derive `p_over`.

# %%
QUANTILE_ALPHAS    = "0.25,0.5,0.75"
cat_indices_per_st = [FEATURE_COLS.index(c) for c in CAT_FEATURES if c in FEATURE_COLS]

per_stat_models:  Dict[str, CatBoostRegressor] = {}
per_stat_results: Dict[str, Dict]              = {}

for stat_type in sorted(df["stat_type"].unique()):
    train_st = train_all[train_all["stat_type"] == stat_type]
    test_st  = test_all[test_all["stat_type"]  == stat_type]
    if len(train_st) < 200 or test_st.empty:
        print(f"  {stat_type:15s}  SKIP (train rows={len(train_st)})")
        continue

    X_tr = prepare(train_st, FEATURE_COLS)
    X_te = prepare(test_st,  FEATURE_COLS)
    y_tr = train_st[TARGET].to_numpy()
    y_te = test_st[TARGET].to_numpy()
    w_tr = make_sample_weights(train_st)

    tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_indices_per_st, weight=w_tr)
    te_pool = Pool(X_te, label=y_te, cat_features=cat_indices_per_st)

    model = CatBoostRegressor(
        iterations=800, depth=6, learning_rate=0.03, l2_leaf_reg=5.0,
        loss_function=f"MultiQuantile:alpha={QUANTILE_ALPHAS}",
        random_seed=42, verbose=False, allow_writing_files=False,
        task_type="CPU", # MultiQuantile loss is not supported on GPU in CatBoost
    )
    model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=50)

    preds_q  = model.predict(te_pool)        # shape (n, 3)
    q25, q50, q75 = preds_q[:, 0], preds_q[:, 1], preds_q[:, 2]

    mae    = mean_absolute_error(y_te, q50)
    rmse   = math.sqrt(mean_squared_error(y_te, q50))
    r2     = r2_score(y_te, q50)
    med_ae = float(np.median(np.abs(y_te - q50)))
    iqr    = float(np.median(q75 - q25))

    per_stat_models[stat_type]  = model
    per_stat_results[stat_type] = {
        "mae": mae, "rmse": rmse, "r2": r2, "median_ae": med_ae,
        "median_iqr": iqr, "test_rows": len(test_st),
        "q25": q25, "q50": q50, "q75": q75, "actuals": y_te,
    }
    print(f"  {stat_type:15s}  MAE={mae:.2f}  RMSE={rmse:.2f}  "
          f"R²={r2:.3f}  MedianIQR={iqr:.2f}  n_test={len(test_st)}")

# %%
baseline_df = pd.DataFrame({
    st: {
        "MAE": r["mae"], "RMSE": r["rmse"], "R²": r["r2"],
        "Median AE": r["median_ae"], "Median IQR": r["median_iqr"],
    }
    for st, r in per_stat_results.items()
}).T.round(3)
display(baseline_df.sort_values("MAE"))

# %% [markdown]
# ## 8. Prop Line Backtest — Hit Rate + Simulated ROI
#
# For each matched prop row: q50 vs. line determines the model's pick.
# Simulated ROI assumes -110 juice on every bet (break-even = 52.4%).
# We also break down accuracy by confidence tier (margin and p_over).

# %%
def simulated_roi(correct_series: pd.Series) -> float:
    """Per-bet ROI at -110 juice. Wins return +100/110; losses cost -1 unit."""
    n = len(correct_series)
    if n == 0:
        return float("nan")
    wins   = correct_series.sum()
    losses = n - wins
    profit = wins * (100.0 / 110.0) - losses * 1.0
    return profit / n


def backtest_vs_lines(
    q50_preds: np.ndarray,
    test_df: pd.DataFrame,
    prop_df: Optional[pd.DataFrame] = None,
    q25_preds: Optional[np.ndarray] = None,
    q75_preds: Optional[np.ndarray] = None,
    label: str = "",
) -> Optional[pd.DataFrame]:
    """
    Backtest quantile regression predictions against actual prop lines.

    Reports directional accuracy, simulated ROI at -110, confidence-tier
    breakdowns by margin and p_over, and per-stat accuracy.
    """
    if prop_df is None:
        print("No prop data available - skipping line backtest")
        return None

    prop_df = prop_df.copy()
    prop_df["game_date"] = pd.to_datetime(prop_df["game_date"], errors="coerce")

    test_with_preds = test_df.copy()
    test_with_preds["q50_pred"] = q50_preds
    if q25_preds is not None:
        test_with_preds["q25_pred"] = q25_preds
    if q75_preds is not None:
        test_with_preds["q75_pred"] = q75_preds

    test_with_preds["player_id"] = test_with_preds["player_id"].astype(str)
    prop_df["player_id"] = prop_df["player_id"].astype(str)

    merged = test_with_preds.merge(
        prop_df[[
            "game_date", "player_id", "stat_type", "sportsbook",
            "side", "line", "final_stat_value", "hit_label",
        ]].drop_duplicates(),
        on=["game_date", "player_id", "stat_type"],
        how="inner",
    )

    if merged.empty:
        print("No overlapping games between test set and prop data")
        return None

    print(f"\n{'=' * 55}")
    if label:
        print(f"  Backtest: {label}")
    print(f"  Matched {len(merged):,} prop rows")

    merged["line"]      = pd.to_numeric(merged["line"],      errors="coerce")
    merged["hit_label"] = pd.to_numeric(merged["hit_label"], errors="coerce")
    merged = merged[merged["line"].notna() & merged["hit_label"].isin([0, 1])].copy()

    merged["model_side"]   = np.where(merged["q50_pred"] > merged["line"], "over", "under")
    merged["model_margin"] = np.abs(merged["q50_pred"] - merged["line"])

    # IQR-derived p_over (mirrors edge_score.py _compute_ml_regression_context logic)
    if "q25_pred" in merged.columns and "q75_pred" in merged.columns:
        iqr  = (merged["q75_pred"] - merged["q25_pred"]).clip(lower=1e-6)
        frac = ((merged["line"] - merged["q25_pred"]) / iqr).clip(0.0, 1.0)
        merged["p_over"] = (0.75 - frac * 0.50).clip(0.10, 0.90)
    else:
        merged["p_over"] = np.where(merged["model_side"] == "over", 0.60, 0.40)

    model_correct = (
        ((merged["model_side"] == "over")  & (merged["actual_value"] > merged["line"])) |
        ((merged["model_side"] == "under") & (merged["actual_value"] < merged["line"]))
    )
    merged["model_correct"] = model_correct.astype(int)

    overall_acc = model_correct.mean()
    overall_roi = simulated_roi(merged["model_correct"])
    print(f"\n  Overall  acc={overall_acc:.3f}  ROI={overall_roi:+.4f}  "
          f"(break-even: 0.524)")

    # Margin tiers
    print("\n  --- Confidence tiers by q50 margin ---")
    for thr in [0.5, 1.0, 2.0, 3.0, 5.0]:
        tier = merged[merged["model_margin"] >= thr]
        if len(tier) < 10:
            continue
        acc = tier["model_correct"].mean()
        roi = simulated_roi(tier["model_correct"])
        print(f"    Margin >= {thr}: n={len(tier):,}  acc={acc:.3f}  ROI={roi:+.4f}")

    # p_over tiers
    print("\n  --- Confidence tiers by p_over ---")
    for thr in [0.55, 0.60, 0.65, 0.70]:
        over_mask  = (merged["model_side"] == "over")  & (merged["p_over"] >= thr)
        under_mask = (merged["model_side"] == "under") & (merged["p_over"] <= 1.0 - thr)
        tier = merged[over_mask | under_mask]
        if len(tier) < 10:
            continue
        acc = tier["model_correct"].mean()
        roi = simulated_roi(tier["model_correct"])
        print(f"    p_over >= {thr} (or <= {1-thr:.2f}): "
              f"n={len(tier):,}  acc={acc:.3f}  ROI={roi:+.4f}")

    # Per-stat breakdown
    print("\n  --- Per-stat accuracy ---")
    for st in sorted(merged["stat_type"].unique()):
        mask = merged["stat_type"] == st
        sub  = merged[mask]
        if len(sub) < 10:
            continue
        acc = sub["model_correct"].mean()
        roi = simulated_roi(sub["model_correct"])
        print(f"    {st:15s}  n={len(sub):4d}  acc={acc:.3f}  ROI={roi:+.4f}")

    return merged


SUPPORTED_PROMOTION_GUARDRAIL_STATS = {
    "PTS", "AST", "REB", "FG3M",
    "PTS+AST", "PTS+REB", "REB+AST", "PTS+REB+AST",
}


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


def _merge_backtest_predictions(
    q50_preds: np.ndarray,
    test_df: pd.DataFrame,
    prop_df: Optional[pd.DataFrame],
    *,
    q25_preds: Optional[np.ndarray] = None,
    q75_preds: Optional[np.ndarray] = None,
) -> Optional[pd.DataFrame]:
    if prop_df is None:
        return None
    prop_local = prop_df.copy()
    prop_local["game_date"] = pd.to_datetime(prop_local["game_date"], errors="coerce")

    test_with_preds = test_df.copy()
    test_with_preds["q50_pred"] = q50_preds
    if q25_preds is not None:
        test_with_preds["q25_pred"] = q25_preds
    if q75_preds is not None:
        test_with_preds["q75_pred"] = q75_preds
    test_with_preds["player_id"] = test_with_preds["player_id"].astype(str)
    prop_local["player_id"] = prop_local["player_id"].astype(str)

    merged = test_with_preds.merge(
        prop_local[[
            "game_date", "player_id", "stat_type", "sportsbook",
            "side", "line", "final_stat_value", "hit_label",
        ]].drop_duplicates(),
        on=["game_date", "player_id", "stat_type"],
        how="inner",
    )
    if merged.empty:
        return None
    merged["line"] = pd.to_numeric(merged["line"], errors="coerce")
    merged = merged[merged["line"].notna()].copy()
    return merged


def _promotion_role_alignment_mask(merged: pd.DataFrame, cfg: Dict[str, float]) -> pd.Series:
    same_pos_ok = pd.to_numeric(merged.get("missing_same_pos_minutes"), errors="coerce").fillna(0.0) >= cfg["min_missing_same_pos_minutes"]
    guard_ok = pd.to_numeric(merged.get("missing_guard_minutes"), errors="coerce").fillna(0.0) >= cfg["min_missing_guard_minutes"]
    creator_ok = (
        (
            pd.to_numeric(merged.get("missing_playmaker_potential_ast_pg"), errors="coerce").fillna(0.0)
            >= cfg["min_cross_position_creator_metric"]
        )
        & (
            pd.to_numeric(
                merged.get("missing_playmaker_potential_ast_pg_x_player_ast_rate"),
                errors="coerce",
            ).fillna(
                pd.to_numeric(merged.get("playmaker_vacuum_x_player_ast_rate"), errors="coerce").fillna(0.0)
            ) > 0.0
        )
    ) | (
        (
            pd.to_numeric(merged.get("missing_onball_drives_pg"), errors="coerce").fillna(0.0)
            >= cfg["min_cross_position_creator_metric"]
        )
        & (
            pd.to_numeric(
                merged.get("missing_onball_drives_pg_x_player_drive_rate"),
                errors="coerce",
            ).fillna(
                pd.to_numeric(merged.get("onball_vacuum_x_player_drive_rate"), errors="coerce").fillna(0.0)
            ) > 0.0
        )
    )
    return same_pos_ok | guard_ok | creator_ok


def _best_gap_threshold(eligible: pd.DataFrame, *, default_threshold: float) -> Tuple[float, Dict[str, Any]]:
    if eligible.empty:
        return default_threshold, {"status": "no_eligible_rows", "rows": 0}

    false_under = eligible["actual_value"] > eligible["line"]
    if false_under.sum() == 0:
        return default_threshold, {"status": "no_false_unders", "rows": int(len(eligible))}

    best = {
        "threshold": default_threshold,
        "f1": -1.0,
        "precision": 0.0,
        "recall": 0.0,
        "suppressed": 0,
        "rows": int(len(eligible)),
    }
    for threshold in np.arange(0.05, 0.251, 0.01):
        suppressed = eligible["gap_pct"] < threshold
        tp = int((suppressed & false_under).sum())
        fp = int((suppressed & ~false_under).sum())
        fn = int((~suppressed & false_under).sum())
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        if (
            f1 > best["f1"]
            or (math.isclose(f1, best["f1"]) and precision > best["precision"])
            or (
                math.isclose(f1, best["f1"])
                and math.isclose(precision, best["precision"])
                and threshold < best["threshold"]
            )
        ):
            best = {
                "threshold": round(float(threshold), 4),
                "f1": round(float(f1), 4),
                "precision": round(float(precision), 4),
                "recall": round(float(recall), 4),
                "suppressed": int(suppressed.sum()),
                "rows": int(len(eligible)),
            }
    return float(best["threshold"]), best


def calibrate_promotion_guardrail_config(
    tuned_results: Dict[str, Dict[str, Any]],
    test_df: pd.DataFrame,
    prop_df: Optional[pd.DataFrame],
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    cfg = default_promotion_guardrail_config()
    if prop_df is None:
        return cfg, {"status": "skipped_missing_prop_dataset"}

    merged_frames = []
    for stat_type, result in tuned_results.items():
        if stat_type not in SUPPORTED_PROMOTION_GUARDRAIL_STATS:
            continue
        test_st = test_df[test_df["stat_type"] == stat_type].copy()
        if test_st.empty:
            continue
        merged = _merge_backtest_predictions(
            result["q50"],
            test_st,
            prop_df,
            q25_preds=result.get("q25"),
            q75_preds=result.get("q75"),
        )
        if merged is not None and not merged.empty:
            merged_frames.append(merged)

    if not merged_frames:
        return cfg, {"status": "no_overlapping_backtest_rows"}

    merged_all = pd.concat(merged_frames, ignore_index=True)
    merged_all["gap_pct"] = (
        (pd.to_numeric(merged_all["line"], errors="coerce") - pd.to_numeric(merged_all["q50_pred"], errors="coerce"))
        / pd.to_numeric(merged_all["line"], errors="coerce").abs().clip(lower=1.0)
    ).clip(lower=0.0)
    base_trigger = (
        (pd.to_numeric(merged_all.get("missing_team_usage_pct"), errors="coerce").fillna(0.0) >= cfg["min_missing_team_usage_pct"])
        & (pd.to_numeric(merged_all.get("missing_team_minutes"), errors="coerce").fillna(0.0) >= cfg["min_missing_team_minutes"])
        & (pd.to_numeric(merged_all.get("recent5_minutes_avg"), errors="coerce").fillna(0.0) >= cfg["min_recent5_minutes_avg"])
        & (
            (
                pd.to_numeric(merged_all.get("modeled_minutes_delta_vs_recent5"), errors="coerce").fillna(0.0)
                >= cfg["min_modeled_minutes_delta_vs_recent5"]
            )
            | (
                pd.to_numeric(
                    merged_all.get("missing_key_teammates_player_minutes_delta"),
                    errors="coerce",
                ).fillna(0.0) >= cfg["min_missing_key_teammates_player_minutes_delta"]
            )
        )
        & _promotion_role_alignment_mask(merged_all, cfg)
        & (pd.to_numeric(merged_all["q50_pred"], errors="coerce") < pd.to_numeric(merged_all["line"], errors="coerce"))
    )
    eligible = merged_all[base_trigger].copy()
    single_mask = ~eligible["stat_type"].astype(str).str.contains(r"\+")
    combo_mask = ~single_mask

    single_threshold, single_metrics = _best_gap_threshold(
        eligible[single_mask].copy(),
        default_threshold=cfg["single_stat_gap_pct"],
    )
    combo_threshold, combo_metrics = _best_gap_threshold(
        eligible[combo_mask].copy(),
        default_threshold=cfg["combo_stat_gap_pct"],
    )
    cfg["single_stat_gap_pct"] = single_threshold
    cfg["combo_stat_gap_pct"] = combo_threshold
    return cfg, {
        "status": "ok",
        "eligible_rows": int(len(eligible)),
        "single_stat": single_metrics,
        "combo_stat": combo_metrics,
    }


# %%
prop_df_loaded = None
if PROP_CSV:
    prop_df_loaded = pd.read_csv(PROP_CSV)
    prop_df_loaded["game_date"] = pd.to_datetime(
        prop_df_loaded["game_date"], errors="coerce"
    )

# Run backtest on PTS as representative stat
if "PTS" in per_stat_results and prop_df_loaded is not None:
    pts_test = test_all[test_all["stat_type"] == "PTS"]
    backtest_vs_lines(
        per_stat_results["PTS"]["q50"],
        pts_test,
        prop_df_loaded,
        q25_preds=per_stat_results["PTS"]["q25"],
        q75_preds=per_stat_results["PTS"]["q75"],
        label="Baseline PTS model",
    )

# %% [markdown]
# ## 9. Per-Stat Optuna Hyperparameter Tuning
#
# Runs independently per stat type. Trial budget is scaled by sample size:
# 40 trials for PTS / AST / REB / FG3M, 20 for all others.
# Tuning uses a rolling split within the training set — the final held-out
# test set is never touched during tuning.

# %%
HIGH_VOLUME_STATS = {"PTS", "AST", "REB", "FG3M"}
ALL_STAT_TYPES    = sorted(df["stat_type"].unique())
TRIAL_BUDGET      = {st: (40 if st in HIGH_VOLUME_STATS else 20) for st in ALL_STAT_TYPES}

best_params_per_stat: Dict[str, Dict] = {}

for stat_type in ALL_STAT_TYPES:
    stat_df = df[df["stat_type"] == stat_type].copy()
    n_trials = TRIAL_BUDGET.get(stat_type, 20)
    if len(stat_df) < 500:
        print(f"  {stat_type:15s}  SKIP (n={len(stat_df)})")
        continue

    tune_dates  = sorted(stat_df["game_date"].dt.date.unique())
    tune_split  = pd.Timestamp(tune_dates[-15])
    tune_train  = stat_df[stat_df["game_date"] < tune_split]
    tune_test   = stat_df[stat_df["game_date"] >= tune_split]
    if len(tune_train) < 200 or tune_test.empty:
        continue

    def objective(
        trial,
        _train=tune_train,
        _test=tune_test,
    ):
        params = {
            "iterations":          trial.suggest_int("iterations", 300, 1500),
            "depth":               trial.suggest_int("depth", 4, 9),
            "learning_rate":       trial.suggest_float("learning_rate", 0.005, 0.12, log=True),
            "l2_leaf_reg":         trial.suggest_float("l2_leaf_reg", 1.0, 25.0),
            "min_data_in_leaf":    trial.suggest_int("min_data_in_leaf", 5, 80),
            "random_strength":     trial.suggest_float("random_strength", 0.3, 5.0),
            "bootstrap_type":      "Bernoulli",
            "subsample":           trial.suggest_float("subsample", 0.6, 1.0),
            "loss_function":       f"MultiQuantile:alpha={QUANTILE_ALPHAS}",
            "random_seed":         42,
            "verbose":             False,
            "allow_writing_files": False,
            "task_type":           CATBOOST_MULTIQUANTILE_TASK_TYPE,
        }

        X_tr = prepare(_train, FEATURE_COLS)
        X_te = prepare(_test,  FEATURE_COLS)
        y_tr = _train[TARGET].to_numpy()
        y_te = _test[TARGET].to_numpy()
        w_tr = make_sample_weights(_train)

        model   = CatBoostRegressor(**params)
        tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_indices_per_st, weight=w_tr)
        te_pool = Pool(X_te, label=y_te, cat_features=cat_indices_per_st)
        model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=40)

        preds_q = model.predict(te_pool)
        return mean_absolute_error(y_te, preds_q[:, 1])  # optimise q50 MAE

    study = optuna.create_study(direction="minimize", study_name=f"tune_{stat_type}")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best_params_per_stat[stat_type] = study.best_params
    print(f"  {stat_type:15s}  best MAE={study.best_value:.4f}  trials={n_trials}")

print("\nOptuna tuning complete")

# %% [markdown]
# ## 10. Retrain Per-Stat Models with Tuned Params

# %%
tuned_per_stat_models:  Dict[str, CatBoostRegressor] = {}
tuned_per_stat_results: Dict[str, Dict]              = {}

for stat_type in ALL_STAT_TYPES:
    train_st = train_all[train_all["stat_type"] == stat_type]
    test_st  = test_all[test_all["stat_type"]  == stat_type]
    if len(train_st) < 200 or test_st.empty:
        continue

    base = best_params_per_stat.get(stat_type, {})
    params = {
        **base,
        "bootstrap_type":      base.get("bootstrap_type", "Bernoulli"),
        "loss_function":       f"MultiQuantile:alpha={QUANTILE_ALPHAS}",
        "random_seed":         42,
        "verbose":             100,
        "allow_writing_files": False,
        "task_type":           CATBOOST_MULTIQUANTILE_TASK_TYPE,
    }

    X_tr = prepare(train_st, FEATURE_COLS)
    X_te = prepare(test_st,  FEATURE_COLS)
    y_tr = train_st[TARGET].to_numpy()
    y_te = test_st[TARGET].to_numpy()
    w_tr = make_sample_weights(train_st)

    tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_indices_per_st, weight=w_tr)
    te_pool = Pool(X_te, label=y_te, cat_features=cat_indices_per_st)

    model = CatBoostRegressor(**params)
    model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=60)

    preds_q         = model.predict(te_pool)
    q25, q50, q75   = preds_q[:, 0], preds_q[:, 1], preds_q[:, 2]
    mae  = mean_absolute_error(y_te, q50)
    rmse = math.sqrt(mean_squared_error(y_te, q50))
    r2   = r2_score(y_te, q50)
    iqr  = float(np.median(q75 - q25))

    tuned_per_stat_models[stat_type]  = model
    tuned_per_stat_results[stat_type] = {
        "mae": mae, "rmse": rmse, "r2": r2, "median_iqr": iqr,
        "test_rows": len(test_st),
        "q25": q25, "q50": q50, "q75": q75, "actuals": y_te,
    }
    print(f"  {stat_type:15s}  MAE={mae:.3f}  RMSE={rmse:.3f}  "
          f"R²={r2:.3f}  IQR={iqr:.2f}")

# %%
# Backtest tuned models
if prop_df_loaded is not None and "PTS" in tuned_per_stat_results:
    pts_test = test_all[test_all["stat_type"] == "PTS"]
    backtest_vs_lines(
        tuned_per_stat_results["PTS"]["q50"],
        pts_test,
        prop_df_loaded,
        q25_preds=tuned_per_stat_results["PTS"]["q25"],
        q75_preds=tuned_per_stat_results["PTS"]["q75"],
        label="Tuned PTS model",
    )

promotion_guardrail_config, promotion_guardrail_calibration = calibrate_promotion_guardrail_config(
    tuned_per_stat_results,
    test_all,
    prop_df_loaded,
)
print("Promotion guardrail config:", promotion_guardrail_config)
print("Promotion guardrail calibration:", promotion_guardrail_calibration)


def build_position_contract_summary() -> Dict[str, Any]:
    current_dir_candidates = [
        Path("backend/data/current"),
        Path("../data/current"),
        Path("/content/drive/MyDrive/NBA_Dashboard/backend/data/current"),
    ]
    current_dir = next((path for path in current_dir_candidates if path.exists()), None)
    if current_dir is None:
        return {"status": "unavailable"}

    season_stats_path = current_dir / "season_stats.csv"
    master_feed_path = current_dir / "master_feed.json"
    gamelog_paths = sorted(current_dir.glob("gamelogs_*.csv"))
    if not season_stats_path.exists():
        return {"status": "missing_season_stats"}

    season_stats_df = pd.read_csv(season_stats_path)
    if "POSITION" not in season_stats_df.columns:
        season_stats_df["POSITION"] = np.nan
    season_positions = season_stats_df["POSITION"].replace("", np.nan)
    fill_rate = round(float(season_positions.notna().mean() * 100.0), 2) if len(season_stats_df) else 0.0

    live_players = []
    if master_feed_path.exists():
        try:
            live_players = json.loads(master_feed_path.read_text())
        except Exception:
            live_players = []

    log_modal: Dict[Tuple[str, str], str] = {}
    for path in gamelog_paths:
        logs_df = pd.read_csv(path, usecols=lambda c: c in {"PLAYER_ID", "TEAM_ABBREVIATION", "START_POSITION"})
        if logs_df.empty or "START_POSITION" not in logs_df.columns:
            continue
        logs_df["PLAYER_ID"] = logs_df["PLAYER_ID"].astype(str)
        logs_df["TEAM_ABBREVIATION"] = logs_df["TEAM_ABBREVIATION"].astype(str).str.upper()
        logs_df["START_POSITION"] = logs_df["START_POSITION"].fillna("").astype(str).str.upper()
        logs_df = logs_df[logs_df["START_POSITION"] != ""].copy()
        if logs_df.empty:
            continue
        counts = (
            logs_df.groupby(["PLAYER_ID", "TEAM_ABBREVIATION", "START_POSITION"])
            .size()
            .reset_index(name="count")
            .sort_values(["PLAYER_ID", "TEAM_ABBREVIATION", "count"], ascending=[True, True, False])
            .drop_duplicates(["PLAYER_ID", "TEAM_ABBREVIATION"])
        )
        for _, row in counts.iterrows():
            log_modal[(row["PLAYER_ID"], row["TEAM_ABBREVIATION"])] = row["START_POSITION"]

    season_position_map = {}
    if {"PLAYER_ID", "POSITION"}.issubset(season_stats_df.columns):
        season_position_map = {
            str(row["PLAYER_ID"]): str(row["POSITION"]).strip().upper()
            for _, row in season_stats_df.iterrows()
            if str(row.get("POSITION") or "").strip()
        }

    tier_counts: Dict[str, int] = {"season_stats": 0, "log_modal": 0, "master_feed": 0, "fallback": 0}
    for player in live_players if isinstance(live_players, list) else []:
        if not isinstance(player, dict):
            continue
        pid = str(player.get("id") or "").strip()
        team = str(player.get("team") or "").strip().upper()
        master_position = str(player.get("position") or "").strip().upper()
        if pid in season_position_map:
            tier_counts["season_stats"] += 1
        elif (pid, team) in log_modal:
            tier_counts["log_modal"] += 1
        elif master_position:
            tier_counts["master_feed"] += 1
        else:
            tier_counts["fallback"] += 1

    total_live = sum(tier_counts.values())
    return {
        "status": "ok",
        "season_stats_position_fill_rate_pct": fill_rate,
        "live_resolution_counts": tier_counts,
        "live_resolution_percentages": (
            {
                key: round((value / float(total_live)) * 100.0, 2)
                for key, value in tier_counts.items()
            }
            if total_live > 0
            else {}
        ),
        "live_resolution_total": total_live,
    }


position_contract_summary = build_position_contract_summary()
print("Position contract summary:", position_contract_summary)

# %% [markdown]
# ## 11. LightGBM Unified Comparison
#
# Three separate LightGBM passes (alpha=0.25, 0.5, 0.75) on the full unified
# feature set (all stat types combined with stat_type as a categorical).
# LightGBM does not support MultiQuantile natively.

# %%
import lightgbm as lgb

X_tr_lgb = prepare(train_all, UNIFIED_FEATURES, UNIFIED_CAT)
X_te_lgb = prepare(test_all,  UNIFIED_FEATURES, UNIFIED_CAT)
y_tr_u   = train_all[TARGET].to_numpy()
y_te_u   = test_all[TARGET].to_numpy()
w_tr_u   = make_sample_weights(train_all)

for c in UNIFIED_CAT:
    if c in X_tr_lgb.columns:
        X_tr_lgb[c] = X_tr_lgb[c].astype("category")
        X_te_lgb[c] = X_te_lgb[c].astype("category")

lgb_preds_by_alpha: Dict[float, np.ndarray] = {}
for alpha in [0.25, 0.5, 0.75]:
    lgb_model = lgb.LGBMRegressor(
        n_estimators=800, max_depth=7, learning_rate=0.03,
        reg_lambda=5.0, objective="quantile", alpha=alpha,
        random_state=42, verbose=-1, force_col_wise=True,
    )
    lgb_model.fit(
        X_tr_lgb, y_tr_u,
        sample_weight=w_tr_u,
        eval_set=[(X_te_lgb, y_te_u)],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    lgb_preds_by_alpha[alpha] = lgb_model.predict(X_te_lgb)
    print(f"  LightGBM alpha={alpha} done")

lgb_q50  = lgb_preds_by_alpha[0.5]
lgb_mae  = mean_absolute_error(y_te_u, lgb_q50)
lgb_rmse = math.sqrt(mean_squared_error(y_te_u, lgb_q50))
lgb_r2   = r2_score(y_te_u, lgb_q50)
lgb_iqr  = float(np.median(lgb_preds_by_alpha[0.75] - lgb_preds_by_alpha[0.25]))
print(f"\nLightGBM unified:  MAE={lgb_mae:.3f}  RMSE={lgb_rmse:.3f}  "
      f"R²={lgb_r2:.3f}  IQR={lgb_iqr:.2f}")

# %% [markdown]
# ## 12. Feature Importance

# %%
ref_stat  = "PTS" if "PTS" in tuned_per_stat_models else list(tuned_per_stat_models.keys())[0]
ref_model = tuned_per_stat_models[ref_stat]
imps      = ref_model.get_feature_importance()
imp_df    = pd.DataFrame({"feature": FEATURE_COLS, "importance": imps})
imp_df    = imp_df.sort_values("importance", ascending=False)

top_n = min(30, len(imp_df))
fig, ax = plt.subplots(figsize=(10, max(6, top_n * 0.28)))
sns.barplot(data=imp_df.head(top_n), y="feature", x="importance", ax=ax, palette="viridis")
ax.set_title(f"Top {top_n} Feature Importances — {ref_stat} Tuned MultiQuantile")
plt.tight_layout()
plt.show()

# %% [markdown]
# ## 13. Prediction Error Analysis

# %%
if "PTS" in tuned_per_stat_results:
    r_pts    = tuned_per_stat_results["PTS"]
    y_pts    = r_pts["actuals"]
    q25_pts  = r_pts["q25"]
    q50_pts  = r_pts["q50"]
    q75_pts  = r_pts["q75"]

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    axes[0, 0].scatter(y_pts, q50_pts, alpha=0.05, s=5, c="steelblue")
    lims = [min(y_pts.min(), q50_pts.min()), max(y_pts.max(), q50_pts.max())]
    axes[0, 0].plot(lims, lims, "--", c="red", alpha=0.5)
    axes[0, 0].set_xlabel("Actual"); axes[0, 0].set_ylabel("q50 Predicted")
    axes[0, 0].set_title("PTS: Predicted (q50) vs Actual")

    residuals = y_pts - q50_pts
    axes[0, 1].hist(residuals, bins=80, alpha=0.7, color="coral")
    axes[0, 1].set_title(f"PTS Residuals  mu={residuals.mean():.2f}  sigma={residuals.std():.2f}")
    axes[0, 1].set_xlabel("Residual (Actual - q50)")

    # IQR calibration check — well-calibrated quantiles should enclose ~50% of actuals
    in_iqr = ((y_pts >= q25_pts) & (y_pts <= q75_pts)).mean()
    axes[1, 0].bar(
        ["In IQR [q25,q75]", "Outside IQR"],
        [in_iqr, 1.0 - in_iqr],
        color=["teal", "salmon"],
    )
    axes[1, 0].set_title(f"PTS IQR Coverage: {in_iqr:.1%} (target ~50%)")
    axes[1, 0].set_ylim(0, 1)

    axes[1, 1].scatter(q50_pts, residuals, alpha=0.05, s=5, c="purple")
    axes[1, 1].axhline(y=0, color="red", linestyle="--", alpha=0.5)
    axes[1, 1].set_xlabel("q50 Predicted"); axes[1, 1].set_ylabel("Residual")
    axes[1, 1].set_title("PTS: Residuals vs Predicted")

    plt.tight_layout()
    plt.show()

# %%
# Per-stat IQR coverage summary
print("Per-stat IQR calibration ([q25,q75] coverage — target ~50%):")
for st, r in tuned_per_stat_results.items():
    y, q25, q75 = r["actuals"], r["q25"], r["q75"]
    cov = ((y >= q25) & (y <= q75)).mean()
    print(f"  {st:15s}  {cov:.1%}")

# %% [markdown]
# ## 14. Feature Drift Monitoring
#
# Computes per-feature mean and std from the training set and exports them as
# `feature_drift_baseline.json`. The `check_feature_drift` function below
# should be imported and called in `utils/ml_inference.py` before running
# `model.predict()` to catch stale data or ETL regressions early.

# %%
numeric_drift_cols = [c for c in FEATURE_COLS if c not in CAT_FEATURES]

drift_baseline: Dict[str, Dict[str, float]] = {}
for col in numeric_drift_cols:
    col_data = pd.to_numeric(train_all[col], errors="coerce").dropna()
    if len(col_data) < 10:
        continue
    drift_baseline[col] = {
        "mean": round(float(col_data.mean()), 4),
        "std":  round(float(col_data.std()),  4),
    }

print(f"Drift baseline built for {len(drift_baseline)} numeric features")


def check_feature_drift(
    feature_row: Dict[str, Any],
    baseline: Dict[str, Dict[str, float]],
    z_threshold: float = 3.0,
) -> List[str]:
    """
    Check a single inference-time feature dict against the training baseline.

    Returns a list of warning strings for any feature whose value sits more
    than z_threshold standard deviations from its training mean.

    Usage in ml_inference.py:
        warnings = check_feature_drift(feature_dict, drift_baseline)
        for w in warnings:
            logger.warning("Feature drift detected: %s", w)
    """
    alerts: List[str] = []
    for col, stats in baseline.items():
        val = feature_row.get(col)
        if val is None:
            continue
        try:
            fval = float(val)
        except (TypeError, ValueError):
            continue
        std = stats["std"]
        if std < 1e-9:
            continue
        z = abs(fval - stats["mean"]) / std
        if z > z_threshold:
            alerts.append(
                f"{col}: {fval:.3f} is {z:.1f}sigma from training "
                f"mean={stats['mean']:.3f} std={std:.3f}"
            )
    return alerts


# Sanity check: a training-set row should produce zero warnings
sample_row = train_all[numeric_drift_cols].iloc[0].to_dict()
sample_warnings = check_feature_drift(sample_row, drift_baseline)
print(f"Drift check on training row: {len(sample_warnings)} warnings (expect 0)")

# %% [markdown]
# ## 15. Final Comparison Table

# %%
comparison: Dict[str, Dict] = {}
for st, r in tuned_per_stat_results.items():
    comparison[f"CatBoost {st} (tuned)"] = {
        "MAE": r["mae"], "RMSE": r["rmse"], "R²": r["r2"],
        "Median IQR": r["median_iqr"],
    }
comparison["LightGBM unified"] = {
    "MAE": lgb_mae, "RMSE": lgb_rmse, "R²": lgb_r2, "Median IQR": lgb_iqr,
}
comparison_df = pd.DataFrame(comparison).T.round(4)
display(comparison_df.sort_values("MAE"))

best_name = comparison_df["MAE"].idxmin()
print(f"\nBest model by MAE: {best_name}  (MAE={comparison_df.loc[best_name,'MAE']:.4f})")

# %% [markdown]
# ## 16. Export Models

# %%
export_dir = Path("exported_regression_model")
export_dir.mkdir(exist_ok=True)

# Minutes model artifact + metadata
minutes_model_file = "minutes_model.cbm"
minutes_model.save_model(str(export_dir / minutes_model_file))
(export_dir / "minutes_quantile_stats.json").write_text(
    json.dumps(minutes_test_metrics, indent=2)
)
(export_dir / "minutes_model_metadata.json").write_text(
    json.dumps(
        {
            "model_type": "minutes_multiquantile",
            "quantile_alphas": [0.25, 0.5, 0.75],
            "target": "actual_minutes",
            "feature_columns": MINUTES_FEATURE_COLS,
            "cat_features": MINUTES_CAT_FEATURES,
            "model_file": minutes_model_file,
            "test_metrics": minutes_test_metrics,
            "heuristic_baseline_metrics": heuristic_minutes_metrics,
            "oof_block_dates": MINUTES_OOF_BLOCK_DATES,
            "oof_min_train_dates": MINUTES_OOF_MIN_TRAIN_DATES,
            "promotion_guardrail": promotion_guardrail_config,
        },
        indent=2,
    )
)

# One .cbm per stat type
model_files: Dict[str, str] = {}
for stat_type, model in tuned_per_stat_models.items():
    filename = f"model_{stat_type.replace('+', '_')}.cbm"
    model.save_model(str(export_dir / filename))
    model_files[stat_type] = filename
    print(f"  Saved {filename}")

# Quantile calibration stats consumed by ml_inference.py
quantile_stats: Dict[str, Dict] = {}
for st, r in tuned_per_stat_results.items():
    y, q25, q50, q75 = r["actuals"], r["q25"], r["q50"], r["q75"]
    quantile_stats[st] = {
        "median_iqr":   round(float(np.median(q75 - q25)), 3),
        "mae_q50":      round(float(mean_absolute_error(y, q50)), 3),
        "q50_bias":     round(float((q50 - y).mean()), 3),
        "iqr_coverage": round(float(((y >= q25) & (y <= q75)).mean()), 3),
    }
(export_dir / "quantile_stats.json").write_text(json.dumps(quantile_stats, indent=2))

# Feature drift baseline
(export_dir / "feature_drift_baseline.json").write_text(
    json.dumps(drift_baseline, indent=2)
)

# Metadata
export_meta = {
    "model_type":            "per_stat_multiquantile",
    "quantile_alphas":       [0.25, 0.5, 0.75],
    "target":                "actual_stat_value",
    "feature_columns":       FEATURE_COLS,
    "cat_features":          CAT_FEATURES,
    "engineered_features":   ENGINEERED_COLS,
    "modeled_minutes_features": MODELED_MINUTES_COLS,
    "model_files":           model_files,
    "minutes_model_file":    minutes_model_file,
    "minutes_model_metadata_file": "minutes_model_metadata.json",
    "minutes_quantile_stats_file": "minutes_quantile_stats.json",
    "cv_results": {
        "mean_mae":  round(cv_results["mean_mae"],  4) if cv_results.get("mean_mae")  else None,
        "mean_rmse": round(cv_results["mean_rmse"], 4) if cv_results.get("mean_rmse") else None,
        "mean_r2":   round(cv_results["mean_r2"],   4) if cv_results.get("mean_r2")   else None,
        "n_folds":   len(cv_results.get("fold_metrics", [])),
    },
    "minutes_test_metrics": minutes_test_metrics,
    "promotion_guardrail": promotion_guardrail_config,
    "promotion_guardrail_calibration": promotion_guardrail_calibration,
    "position_contract_summary": position_contract_summary,
    "per_stat_test_metrics": {
        st: {"mae": round(r["mae"], 4), "rmse": round(r["rmse"], 4), "r2": round(r["r2"], 4)}
        for st, r in tuned_per_stat_results.items()
    },
    "usage": (
        "Load the per-stat .cbm for the relevant stat_type. "
        "model.predict(pool) returns shape (n, 3): [q25, q50, q75]. "
        "Pass q25/q50/q75 to edge_score.py which derives p_over via IQR interpolation. "
        "Call check_feature_drift() with feature_drift_baseline.json before prediction "
        "to detect stale or out-of-distribution feature values."
    ),
}
(export_dir / "model_metadata.json").write_text(
    json.dumps(export_meta, indent=2, default=str)
)

print(f"\nAll files in export_dir:")
for f in sorted(export_dir.iterdir()):
    print(f"  {f.name}  ({f.stat().st_size // 1024} KB)")

# %%
# Download from Colab
from google.colab import drive
drive.mount('/content/drive')

import shutil
from pathlib import Path

export_dir = Path("exported_regression_model")
drive_zip_base = "/content/drive/MyDrive/exported_regression_model"

shutil.make_archive(drive_zip_base, "zip", root_dir=".", base_dir="exported_regression_model")
print("Saved to:", drive_zip_base + ".zip")


# %% [markdown]
# ## 17. How to Use at Inference  (`utils/ml_inference.py`)
#
# ```python
# import json, logging
# import numpy as np
# import pandas as pd
# from catboost import CatBoostRegressor, Pool
# from typing import Any, Dict, List, Optional
#
# logger = logging.getLogger("MLInference")
#
# # ── Load once at module import (not per prediction) ──────────────────────
# _MODEL_DIR  = "path/to/exported_regression_model"
# _meta       = json.loads(open(f"{_MODEL_DIR}/model_metadata.json").read())
# _q_stats    = json.loads(open(f"{_MODEL_DIR}/quantile_stats.json").read())
# _drift_b    = json.loads(open(f"{_MODEL_DIR}/feature_drift_baseline.json").read())
# _FEAT_COLS  = _meta["feature_columns"]
# _CAT_COLS   = _meta["cat_features"]
# _CAT_IDX    = [_FEAT_COLS.index(c) for c in _CAT_COLS if c in _FEAT_COLS]
# _MODELS: Dict[str, CatBoostRegressor] = {
#     st: CatBoostRegressor().load_model(f"{_MODEL_DIR}/{fname}")
#     for st, fname in _meta["model_files"].items()
# }
#
# def get_ml_predictor():
#     return _MLPredictor()
#
# class _MLPredictor:
#     def predict(
#         self,
#         player_info: Dict[str, Any],
#         logs: List[Dict[str, Any]],
#         stat_type: str,
#     ) -> Optional[Dict[str, float]]:
#         model = _MODELS.get(stat_type)
#         if model is None:
#             return None
#
#         features = _build_feature_dict(player_info, logs, stat_type)
#
#         # Drift check — log warnings but never block inference
#         for warning in _check_drift(features):
#             logger.warning("Feature drift: %s", warning)
#
#         X = pd.DataFrame([features])[_FEAT_COLS]
#         for c in _FEAT_COLS:
#             if c in _CAT_COLS:
#                 X[c] = X[c].fillna("UNKNOWN").astype(str)
#             else:
#                 X[c] = pd.to_numeric(X[c], errors="coerce")
#
#         pool  = Pool(X, cat_features=_CAT_IDX)
#         preds = model.predict(pool)  # shape (1, 3)
#
#         return {
#             "q25": float(preds[0, 0]),
#             "q50": float(preds[0, 1]),
#             "q75": float(preds[0, 2]),
#         }
#
#     def hit_probability(self, prediction, std_dev, line, side):
#         # Legacy interface — kept for backward compatibility.
#         # New code should use q25/q50/q75 directly via edge_score.py IQR path.
#         from scipy.stats import norm
#         if std_dev is None or std_dev <= 0:
#             return 0.6 if (side == "over") == (prediction > line) else 0.4
#         p_over = 1.0 - norm.cdf(line, loc=prediction, scale=std_dev)
#         return p_over if side == "over" else 1.0 - p_over
#
# def _check_drift(features: Dict[str, Any], z_threshold: float = 3.0) -> List[str]:
#     alerts = []
#     for col, stats in _drift_b.items():
#         val = features.get(col)
#         if val is None:
#             continue
#         try:
#             fval = float(val)
#         except (TypeError, ValueError):
#             continue
#         std = stats["std"]
#         if std < 1e-9:
#             continue
#         z = abs(fval - stats["mean"]) / std
#         if z > z_threshold:
#             alerts.append(
#                 f"{col}: {fval:.3f} is {z:.1f}sigma "
#                 f"from training mean={stats['mean']:.3f}"
#             )
#     return alerts
#
# def _build_feature_dict(
#     player_info: Dict[str, Any],
#     logs: List[Dict[str, Any]],
#     stat_type: str,
# ) -> Dict[str, Any]:
#     # Populate _FEAT_COLS from player_info and logs.
#     # Engineering must mirror regression_notebook.py: engineer_features().
#     # Key engineered features to replicate:
#     #   momentum_diff_5v20  = recent5_stat_avg  - recent20_stat_avg
#     #   momentum_diff_3v10  = recent3_stat_avg  - recent10_stat_avg
#     #   expected_possessions = team_pace * (season_usage_pct_avg / 100)
#     #   predicted_minutes    = (r5*0.5 + r10*0.3 + sea*0.2) * (1 - 0.035*is_b2b)
#     raise NotImplementedError("Implement feature extraction from player_info + logs")
# ```
