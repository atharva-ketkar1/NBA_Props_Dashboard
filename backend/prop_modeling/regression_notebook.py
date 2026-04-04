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
# # NBA Prop Model — Regression Approach (Predict Actual Stat Values)
#
# **Key insight**: Instead of training a classifier on ~5k prop rows to predict
# hit/miss, train a regressor on **455k+ game-log rows** to predict the
# actual stat value. Then compare predicted vs. sportsbook line to derive
# hit probability.
#
# **Upload files when prompted:**
# 1. `regression_training_dataset.csv` (455k rows, ~80MB)
# 2. Optionally `prop_training_dataset.csv` (for backtest against actual lines)

# %% [markdown]
# ## 1. Setup

# %%
import subprocess, sys
def pip_install(*pkgs):
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs])

pip_install("catboost", "optuna", "lightgbm", "xgboost", "shap")

# %%
import warnings
warnings.filterwarnings("ignore")

import json, math, os
from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

sns.set_theme(style="darkgrid", palette="muted")
plt.rcParams["figure.figsize"] = (14, 6)
plt.rcParams["figure.dpi"] = 100

print("✅ Imports loaded")

# %% [markdown]
# ## 2. Upload & Load Data

# %%
try:
    from google.colab import files
    print("Upload regression_training_dataset.csv:")
    uploaded = files.upload()
    REG_CSV = list(uploaded.keys())[0]
except ImportError:
    REG_CSV = "backend/prop_modeling/generated/regression_training_dataset.csv"
    print(f"Running locally: {REG_CSV}")

# %%
df = pd.read_csv(REG_CSV)
df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
df = df[df["game_date"].notna()].copy()
df["actual_value"] = pd.to_numeric(df["actual_value"], errors="coerce")
df = df[df["actual_value"].notna()].copy()

print(f"Loaded {len(df):,} rows")
print(f"Dates: {df['game_date'].dt.date.nunique()} ({df['game_date'].min().date()} → {df['game_date'].max().date()})")
print(f"Players: {df['player_id'].nunique()}")
print(f"Stat types: {sorted(df['stat_type'].unique())}")

# %%
# Optionally upload prop dataset for backtest
PROP_CSV = None
try:
    from google.colab import files as f2
    print("\n(Optional) Upload prop_training_dataset.csv for line backtest:")
    try:
        up2 = f2.upload()
        if up2:
            PROP_CSV = list(up2.keys())[0]
            print(f"✅ Prop data: {PROP_CSV}")
    except Exception:
        print("Skipped — will evaluate regression only")
except ImportError:
    prop_path = "backend/prop_modeling/generated/prop_training_dataset.csv"
    if os.path.exists(prop_path):
        PROP_CSV = prop_path

# %% [markdown]
# ## 3. EDA

# %%
fig, axes = plt.subplots(1, 2, figsize=(16, 5))
df["stat_type"].value_counts().sort_index().plot(kind="barh", ax=axes[0], color="steelblue")
axes[0].set_title("Rows per Stat Type")

# Distribution of target for PTS
pts = df[df["stat_type"] == "PTS"]["actual_value"]
pts.hist(bins=50, ax=axes[1], color="coral", alpha=0.7)
axes[1].set_title("PTS Distribution")
axes[1].set_xlabel("Points")
plt.tight_layout()
plt.show()

# %%
# Per-stat summary stats
summary = df.groupby("stat_type")["actual_value"].agg(["mean", "std", "median", "min", "max"])
display(summary.round(2))

# %%
# Missing values
feat_cols = [c for c in df.columns if c not in ["game_date", "player_id", "team", "opponent",
                                                   "game_id", "stat_type", "actual_value"]]
missing = df[feat_cols].isnull().mean().sort_values(ascending=False)
print("Features with >10% missing:")
print(missing[missing > 0.1].to_string())

# %% [markdown]
# ## 4. Feature & Model Setup

# %%
TARGET = "actual_value"
CAT_FEATURES = ["player_id", "team", "opponent"]

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
    # ── v2: Opponent defensive features ────────────────────────────────────
    "opp_pts_defense_rank",
    "opp_catchAndShoot_rank",
    "opp_pullup_rank",
    "opp_lessThan10ft_rank",
    "opp_def_restricted_pct",
    "opp_def_paint_pct",
    "opp_def_3pt_pct",
    "opp_def_restricted_rank",
    "opp_def_3pt_rank",
    # ── v2: Player style fingerprint ────────────────────────────────────────
    "player_catchAndShoot_pg",
    "player_pullup_pg",
    "player_lessThan10ft_pg",
    "player_transition_pg",
    "player_isolation_pg",
    "player_pnr_pg",
    "player_spotup_pg",
    # ── v2: Cross-feature matchup scores ────────────────────────────────────
    "matchup_score_fg3",
    "matchup_score_pts",
    "matchup_score_interior",
    # ── v2: Context ──────────────────────────────────────────────────────────
    "team_pace",
    "opp_def_rating",
    "recent5_avg_game_margin",
    "recent5_blowout_flag",
]

# Filter to columns that actually exist
FEATURE_COLS = [c for c in FEATURE_COLS if c in df.columns]
print(f"Using {len(FEATURE_COLS)} features")

# For unified model, add stat_type
UNIFIED_FEATURES = ["stat_type"] + FEATURE_COLS
UNIFIED_CAT = ["stat_type"] + CAT_FEATURES

def prepare(df, feat_cols, cat_cols=CAT_FEATURES):
    X = df[feat_cols].copy()
    for c in feat_cols:
        if c in cat_cols or c == "stat_type":
            X[c] = X[c].fillna("UNKNOWN").astype(str)
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return X

# %% [markdown]
# ## 5. Holdout Evaluation — Per-Stat Regression

# %%
from catboost import CatBoostRegressor, Pool

unique_dates = sorted(df["game_date"].dt.date.unique())
test_dates = unique_dates[-20:]  # Last 20 dates as test
split_ts = pd.Timestamp(test_dates[0])

train_all = df[df["game_date"] < split_ts]
test_all = df[df["game_date"] >= split_ts]
print(f"Train: {len(train_all):,} rows ({train_all['game_date'].dt.date.nunique()} dates)")
print(f"Test:  {len(test_all):,} rows ({test_all['game_date'].dt.date.nunique()} dates)")

# %%
# Per-stat holdout results
cat_indices = [FEATURE_COLS.index(c) for c in CAT_FEATURES if c in FEATURE_COLS]
per_stat_results = {}

for stat_type in sorted(df["stat_type"].unique()):
    train_st = train_all[train_all["stat_type"] == stat_type]
    test_st = test_all[test_all["stat_type"] == stat_type]
    if len(train_st) < 100 or test_st.empty:
        continue

    X_tr = prepare(train_st, FEATURE_COLS)
    X_te = prepare(test_st, FEATURE_COLS)
    y_tr = train_st[TARGET].to_numpy()
    y_te = test_st[TARGET].to_numpy()

    model = CatBoostRegressor(
        iterations=800, depth=6, learning_rate=0.03, l2_leaf_reg=5.0,
        loss_function="RMSE", eval_metric="RMSE",
        random_seed=42, verbose=False, allow_writing_files=False,
        task_type="GPU" if os.environ.get("COLAB_GPU") else "CPU",
    )
    tr_pool = Pool(X_tr, label=y_tr, cat_features=cat_indices)
    te_pool = Pool(X_te, label=y_te, cat_features=cat_indices)
    model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=50)

    preds = model.predict(te_pool)
    mae = mean_absolute_error(y_te, preds)
    rmse = math.sqrt(mean_squared_error(y_te, preds))
    r2 = r2_score(y_te, preds)
    median_ae = float(np.median(np.abs(y_te - preds)))

    per_stat_results[stat_type] = {
        "mae": mae, "rmse": rmse, "r2": r2,
        "median_ae": median_ae, "test_rows": len(test_st),
        "model": model, "preds": preds, "actuals": y_te,
    }
    print(f"  {stat_type:15s}  MAE={mae:.2f}  RMSE={rmse:.2f}  R²={r2:.3f}  Median_AE={median_ae:.2f}")

# %%
results_df = pd.DataFrame({
    st: {"MAE": r["mae"], "RMSE": r["rmse"], "R²": r["r2"], "Median AE": r["median_ae"]}
    for st, r in per_stat_results.items()
}).T.round(3)
display(results_df.sort_values("MAE"))

# %% [markdown]
# ## 6. Unified Regression Model

# %%
unified_cat_indices = [UNIFIED_FEATURES.index(c) for c in UNIFIED_CAT if c in UNIFIED_FEATURES]

X_tr_u = prepare(train_all, UNIFIED_FEATURES, UNIFIED_CAT)
X_te_u = prepare(test_all, UNIFIED_FEATURES, UNIFIED_CAT)
y_tr_u = train_all[TARGET].to_numpy()
y_te_u = test_all[TARGET].to_numpy()

unified_model = CatBoostRegressor(
    iterations=1000, depth=7, learning_rate=0.03, l2_leaf_reg=5.0,
    loss_function="RMSE", eval_metric="RMSE",
    random_seed=42, verbose=100, allow_writing_files=False,
    task_type="GPU" if os.environ.get("COLAB_GPU") else "CPU",
)
tr_pool_u = Pool(X_tr_u, label=y_tr_u, cat_features=unified_cat_indices)
te_pool_u = Pool(X_te_u, label=y_te_u, cat_features=unified_cat_indices)
unified_model.fit(tr_pool_u, eval_set=te_pool_u, use_best_model=True, early_stopping_rounds=75)

preds_u = unified_model.predict(te_pool_u)
mae_u = mean_absolute_error(y_te_u, preds_u)
rmse_u = math.sqrt(mean_squared_error(y_te_u, preds_u))
r2_u = r2_score(y_te_u, preds_u)
print(f"\nUnified:  MAE={mae_u:.3f}  RMSE={rmse_u:.3f}  R²={r2_u:.3f}")

# %% [markdown]
# ## 7. Prop Line Backtest — Derived Hit Rate
#
# Use the regression predictions to classify prop hits:
# - If model predicts value > line → model says "take over"
# - Compare to actual outcome

# %%
def backtest_vs_lines(preds, test_df, prop_df=None):
    """Backtest regression predictions against actual prop lines."""
    if prop_df is None:
        print("No prop data — skipping line backtest")
        return None

    prop_df = prop_df.copy()
    prop_df["game_date"] = pd.to_datetime(prop_df["game_date"], errors="coerce")

    # Merge predictions into test data
    test_with_preds = test_df.copy()
    test_with_preds["predicted_value"] = preds

    # Join with prop lines on (player_id, game_date, stat_type)
    test_with_preds["player_id"] = test_with_preds["player_id"].astype(str)
    prop_df["player_id"] = prop_df["player_id"].astype(str)

    merged = test_with_preds.merge(
        prop_df[["game_date", "player_id", "stat_type", "sportsbook", "side",
                 "line", "final_stat_value", "hit_label"]].drop_duplicates(),
        on=["game_date", "player_id", "stat_type"],
        how="inner",
    )

    if merged.empty:
        print("No overlapping games between regression test set and prop data")
        return None

    print(f"Matched {len(merged):,} prop rows against regression predictions")

    # Model pick accuracy: does pred vs line agree with actual outcome?
    merged["line"] = pd.to_numeric(merged["line"], errors="coerce")
    merged["hit_label"] = pd.to_numeric(merged["hit_label"], errors="coerce")
    merged = merged[merged["line"].notna() & merged["hit_label"].isin([0, 1])].copy()

    # Model side: if predicted > line → over, else under
    merged["model_side"] = np.where(merged["predicted_value"] > merged["line"], "over", "under")
    merged["model_agrees_with_side"] = merged["model_side"] == merged["side"]

    # Model confidence: abs(predicted - line)
    merged["model_margin"] = np.abs(merged["predicted_value"] - merged["line"])

    # When model agrees with the bet side, does it hit?
    agrees = merged[merged["model_agrees_with_side"]]
    disagrees = merged[~merged["model_agrees_with_side"]]
    print(f"\nModel agrees with bet side: {len(agrees):,} rows → "
          f"hit rate = {agrees['hit_label'].mean():.3f}")
    print(f"Model disagrees with bet side: {len(disagrees):,} rows → "
          f"hit rate = {disagrees['hit_label'].mean():.3f}")

    # Confidence tiers
    for margin_threshold in [0.5, 1.0, 2.0, 3.0, 5.0]:
        confident = merged[merged["model_margin"] >= margin_threshold]
        if len(confident) < 10:
            continue
        model_correct = (
            ((confident["model_side"] == "over") & (confident["actual_value"] > confident["line"])) |
            ((confident["model_side"] == "under") & (confident["actual_value"] < confident["line"]))
        )
        acc = model_correct.mean()
        print(f"  Margin ≥ {margin_threshold}: {len(confident):,} picks → "
              f"accuracy = {acc:.3f}")

    return merged

# %%
prop_df_loaded = None
if PROP_CSV:
    prop_df_loaded = pd.read_csv(PROP_CSV)
    prop_df_loaded["game_date"] = pd.to_datetime(prop_df_loaded["game_date"], errors="coerce")

backtest_result = backtest_vs_lines(preds_u, test_all, prop_df_loaded)

# %% [markdown]
# ## 8. Optuna Hyperparameter Tuning

# %%
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

# Use a smaller sample for faster tuning
tune_stat_types = ["PTS", "AST", "REB", "FG3M"]
tune_df = df[df["stat_type"].isin(tune_stat_types)].copy()
tune_dates = sorted(tune_df["game_date"].dt.date.unique())
tune_split = pd.Timestamp(tune_dates[-15])
tune_train = tune_df[tune_df["game_date"] < tune_split]
tune_test = tune_df[tune_df["game_date"] >= tune_split]

def objective(trial):
    params = {
        "iterations": trial.suggest_int("iterations", 300, 2000),
        "depth": trial.suggest_int("depth", 4, 9),
        "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.12, log=True),
        "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 25.0),
        "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 5, 100),
        "random_strength": trial.suggest_float("random_strength", 0.3, 5.0),
        # subsample requires bootstrap_type='Bernoulli' — bagging_temperature
        # is for the default 'Bayesian' bootstrap and cannot coexist with subsample.
        "bootstrap_type": "Bernoulli",
        "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        "loss_function": "RMSE",
        "eval_metric": "RMSE",  # MAE is not GPU-native; RMSE avoids the warning
        "random_seed": 42,
        "verbose": False,
        "allow_writing_files": False,
        "task_type": "GPU" if os.environ.get("COLAB_GPU") else "CPU",
    }

    X_tr = prepare(tune_train, UNIFIED_FEATURES, UNIFIED_CAT)
    X_te = prepare(tune_test, UNIFIED_FEATURES, UNIFIED_CAT)
    y_tr = tune_train[TARGET].to_numpy()
    y_te = tune_test[TARGET].to_numpy()

    model = CatBoostRegressor(**params)
    tr_pool = Pool(X_tr, label=y_tr, cat_features=unified_cat_indices)
    te_pool = Pool(X_te, label=y_te, cat_features=unified_cat_indices)
    model.fit(tr_pool, eval_set=te_pool, use_best_model=True, early_stopping_rounds=50)

    preds = model.predict(te_pool)
    return mean_absolute_error(y_te, preds)

study = optuna.create_study(direction="minimize", study_name="regression_hpo")
study.optimize(objective, n_trials=40, show_progress_bar=True)

print(f"\n🏆 Best MAE: {study.best_value:.4f}")
print(f"Best params:\n{json.dumps(study.best_params, indent=2)}")

# %% [markdown]
# ## 9. Retrain with Best Params

# %%
best_params = {
    **study.best_params,
    # Ensure bootstrap_type is always set — the study params include 'subsample'
    # which requires Bernoulli; if it's not in best_params for some reason, default it.
    "bootstrap_type": study.best_params.get("bootstrap_type", "Bernoulli"),
    "loss_function": "RMSE",
    "eval_metric": "RMSE",
    "random_seed": 42,
    "verbose": 100,
    "allow_writing_files": False,
    "task_type": "GPU" if os.environ.get("COLAB_GPU") else "CPU",
}

# Train on full train set with best params
tuned_model = CatBoostRegressor(**best_params)
tuned_model.fit(tr_pool_u, eval_set=te_pool_u, use_best_model=True, early_stopping_rounds=75)

tuned_preds = tuned_model.predict(te_pool_u)
tuned_mae = mean_absolute_error(y_te_u, tuned_preds)
tuned_rmse = math.sqrt(mean_squared_error(y_te_u, tuned_preds))
tuned_r2 = r2_score(y_te_u, tuned_preds)
print(f"\nTuned Unified:  MAE={tuned_mae:.3f}  RMSE={tuned_rmse:.3f}  R²={tuned_r2:.3f}")

# Compare
print(f"\nImprovement vs baseline:")
print(f"  MAE:  {mae_u:.3f} → {tuned_mae:.3f} ({(tuned_mae-mae_u)/mae_u*100:+.1f}%)")
print(f"  RMSE: {rmse_u:.3f} → {tuned_rmse:.3f} ({(tuned_rmse-rmse_u)/rmse_u*100:+.1f}%)")
print(f"  R²:   {r2_u:.3f} → {tuned_r2:.3f}")

# %%
# Backtest tuned model
if prop_df_loaded is not None:
    print("\n--- Tuned Model Backtest ---")
    backtest_tuned = backtest_vs_lines(tuned_preds, test_all, prop_df_loaded)

# %% [markdown]
# ## 10. LightGBM Comparison

# %%
import lightgbm as lgb

X_tr_lgb = prepare(train_all, UNIFIED_FEATURES, UNIFIED_CAT)
X_te_lgb = prepare(test_all, UNIFIED_FEATURES, UNIFIED_CAT)

for c in UNIFIED_CAT:
    if c in X_tr_lgb.columns:
        X_tr_lgb[c] = X_tr_lgb[c].astype("category")
        X_te_lgb[c] = X_te_lgb[c].astype("category")

lgb_model = lgb.LGBMRegressor(
    n_estimators=1000, max_depth=7, learning_rate=0.03, reg_lambda=5.0,
    random_state=42, verbose=-1, force_col_wise=True,
)
lgb_model.fit(X_tr_lgb, y_tr_u,
              eval_set=[(X_te_lgb, y_te_u)],
              callbacks=[lgb.early_stopping(50, verbose=False)])

lgb_preds = lgb_model.predict(X_te_lgb)
lgb_mae = mean_absolute_error(y_te_u, lgb_preds)
lgb_rmse = math.sqrt(mean_squared_error(y_te_u, lgb_preds))
lgb_r2 = r2_score(y_te_u, lgb_preds)
print(f"LightGBM:  MAE={lgb_mae:.3f}  RMSE={lgb_rmse:.3f}  R²={lgb_r2:.3f}")

# %% [markdown]
# ## 11. Feature Importance

# %%
# CatBoost feature importance
imps = tuned_model.get_feature_importance()
imp_df = pd.DataFrame({"feature": UNIFIED_FEATURES, "importance": imps})
imp_df = imp_df.sort_values("importance", ascending=False)

fig, ax = plt.subplots(figsize=(10, max(6, len(imp_df) * 0.25)))
top_n = min(25, len(imp_df))
sns.barplot(data=imp_df.head(top_n), y="feature", x="importance", ax=ax, palette="viridis")
ax.set_title(f"Top {top_n} Feature Importances — Tuned Regression")
plt.tight_layout()
plt.show()

# %% [markdown]
# ## 12. Prediction Error Analysis

# %%
# Residual plots
fig, axes = plt.subplots(2, 2, figsize=(14, 10))

# Predicted vs actual
axes[0, 0].scatter(y_te_u, tuned_preds, alpha=0.05, s=5, c="steelblue")
lims = [min(y_te_u.min(), tuned_preds.min()), max(y_te_u.max(), tuned_preds.max())]
axes[0, 0].plot(lims, lims, "--", c="red", alpha=0.5)
axes[0, 0].set_xlabel("Actual")
axes[0, 0].set_ylabel("Predicted")
axes[0, 0].set_title("Predicted vs Actual")

# Residual distribution
residuals = y_te_u - tuned_preds
axes[0, 1].hist(residuals, bins=80, alpha=0.7, color="coral")
axes[0, 1].set_title(f"Residual Distribution (μ={residuals.mean():.2f}, σ={residuals.std():.2f})")
axes[0, 1].set_xlabel("Residual (Actual - Predicted)")

# Per-stat MAE
stat_maes = {}
for st in sorted(test_all["stat_type"].unique()):
    mask = test_all["stat_type"].values == st
    if mask.sum() > 0:
        stat_maes[st] = mean_absolute_error(y_te_u[mask], tuned_preds[mask])
pd.Series(stat_maes).sort_values().plot(kind="barh", ax=axes[1, 0], color="teal")
axes[1, 0].set_title("MAE per Stat Type")

# Residual vs predicted
axes[1, 1].scatter(tuned_preds, residuals, alpha=0.05, s=5, c="purple")
axes[1, 1].axhline(y=0, color="red", linestyle="--", alpha=0.5)
axes[1, 1].set_xlabel("Predicted")
axes[1, 1].set_ylabel("Residual")
axes[1, 1].set_title("Residuals vs Predicted")

plt.tight_layout()
plt.show()

# %% [markdown]
# ## 13. Final Comparison

# %%
comparison = pd.DataFrame({
    "CatBoost Baseline": {"MAE": mae_u, "RMSE": rmse_u, "R²": r2_u},
    "CatBoost Tuned": {"MAE": tuned_mae, "RMSE": tuned_rmse, "R²": tuned_r2},
    "LightGBM": {"MAE": lgb_mae, "RMSE": lgb_rmse, "R²": lgb_r2},
}).T.round(4)
display(comparison)

best_name = comparison["MAE"].idxmin()
print(f"\n🏆 Best model: {best_name} (MAE={comparison.loc[best_name, 'MAE']:.4f})")

# %% [markdown]
# ## 14. Export Best Model

# %%
export_dir = Path("exported_regression_model")
export_dir.mkdir(exist_ok=True)

tuned_model.save_model(str(export_dir / "unified_regression.cbm"))

export_meta = {
    "model_file": "unified_regression.cbm",
    "model_type": "regression",
    "target": "actual_stat_value",
    "feature_columns": UNIFIED_FEATURES,
    "cat_features": UNIFIED_CAT,
    "best_params": study.best_params,
    "test_metrics": {
        "mae": round(tuned_mae, 4),
        "rmse": round(tuned_rmse, 4),
        "r2": round(tuned_r2, 4),
    },
    "usage": (
        "Load model, prepare features for a player-game, predict stat value. "
        "Compare predicted value to sportsbook line to derive over/under lean. "
        "Margin = abs(predicted - line) gives confidence. "
        "Residual std by stat type gives probability calibration."
    ),
}
(export_dir / "model_metadata.json").write_text(json.dumps(export_meta, indent=2, default=str))

# Per-stat residual stats for probability calibration
residual_stats = {}
for st in sorted(test_all["stat_type"].unique()):
    mask = test_all["stat_type"].values == st
    if mask.sum() > 10:
        res = y_te_u[mask] - tuned_preds[mask]
        residual_stats[st] = {"mean": round(float(res.mean()), 3), "std": round(float(res.std()), 3)}
(export_dir / "residual_stats.json").write_text(json.dumps(residual_stats, indent=2))

print(f"✅ Model exported to {export_dir}/")
print(f"   Files: {[f.name for f in export_dir.iterdir()]}")

# Download from Colab
try:
    from google.colab import files as dl
    import shutil
    shutil.make_archive("exported_regression_model", "zip", ".", "exported_regression_model")
    dl.download("exported_regression_model.zip")
except ImportError:
    print("   (Not on Colab — saved locally)")

# %% [markdown]
# ## 15. How to Use at Inference
#
# ```python
# from catboost import CatBoostRegressor, Pool
# import json
#
# model = CatBoostRegressor()
# model.load_model("unified_regression.cbm")
# meta = json.loads(open("model_metadata.json").read())
# residuals = json.loads(open("residual_stats.json").read())
#
# # For a player/game/stat, prepare features and predict:
# predicted_value = model.predict(feature_pool)[0]
# margin = predicted_value - line
#
# # Derive hit probability using the residual distribution:
# from scipy.stats import norm
# stat_std = residuals[stat_type]["std"]
# over_prob = 1 - norm.cdf(line, loc=predicted_value, scale=stat_std)
# under_prob = norm.cdf(line, loc=predicted_value, scale=stat_std)
# ```
