"""Train the per-stat quantile bundle, reproducibly and without selecting on test.

This is the single entry point for a retrain. It used to be a drifted sibling of
regression_notebook.py -- it fit `loss_function="RMSE"` and stamped
`model_type: "per_stat_rmse_legacy"` into the metadata, while the bundle that
actually ships is `per_stat_multiquantile` and was produced by the notebook. Two
trainers meant the reproducible one did not build the artifact and the one that
built the artifact was not reproducible, so a retrain was a manual notebook run
whose split geometry nobody could check afterwards.

What changed, and why each matters:

  * Splitting goes through `splits.chronological_split`, which carves a
    validation block out of train and refuses to hand it back as test. Optuna
    selects on val and CatBoost early-stops on val. Previously Optuna tuned on
    the full frame -- whose last 15 dates are a strict subset of the 20-date
    test block -- and the tuned retrain passed the test pool as `eval_set` with
    `use_best_model=True`, so every reported test metric described a model that
    had chosen both its hyperparameters and its tree count on those rows.
  * Metrics go through `quantile_metrics.quantile_report`, which scores all
    three heads with pinball loss and compares the median against an explicit
    baseline -- the player's own recent-10 average. The old headline was a mean
    MAE pooled across markets whose scale runs from 0.46 to 5.9.
  * The exported metadata records the split boundaries, the per-stat
    hyperparameters that were actually chosen, the dataset fingerprint and the
    git SHA, so a future reader does not have to trust a comment.

Tuning is opt-in (`--tune`). Without it the per-stat defaults are used, which on
the last measured run was worth 0.9% of PTS MAE against 40 trials of
(leaking) search -- so the default is off.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from feature_schema import GENERATED_DIR, PROP_MODELING_DIR, DEFAULT_MINUTES_DATASET_PATH
from quantile_metrics import quantile_report
from scrapers.season_type import REGULAR_SEASON, season_type_from_game_id
from splits import (
    DEFAULT_TEST_DATES,
    DEFAULT_VAL_DATES,
    DateSplit,
    assert_not_test,
    chronological_split,
    coverage as split_coverage,
    latest_season_key,
    season_dates,
    walk_forward_blocks,
)
from minutes_model_config import (
    MINUTES_MODEL_CATEGORICAL_FEATURES,
    MINUTES_MODEL_FEATURE_COLUMNS,
    MINUTES_MODEL_FILE_NAME,
    MINUTES_MODEL_METADATA_FILE_NAME,
    MINUTES_MODEL_QUANTILES_FILE_NAME,
    MINUTES_MODEL_TARGET_COLUMN,
    MODELED_MINUTES_FEATURE_COLUMNS,
    MINUTES_OOF_BLOCK_DATES,
    MINUTES_OOF_MIN_TRAIN_DATES,
    apply_modeled_minutes_features,
    build_fallback_modeled_minutes,
    minutes_metrics_summary,
)
from injury_feature_config import (
    default_promotion_guardrail_config,
    season_key_for_date,
)


DEFAULT_REGRESSION_DATASET_PATH = GENERATED_DIR / "regression_training_dataset.csv"
DEFAULT_REGRESSION_MODEL_DIR = GENERATED_DIR / "regression_models"
TARGET_COLUMN = "actual_value"
STAT_MODEL_QUANTILE_ALPHAS = [0.25, 0.5, 0.75]
ENGINEERED_FEATURE_COLUMNS = [
    "momentum_diff_5v20",
    "momentum_diff_3v10",
    "expected_possessions",
    "predicted_minutes",
    "modeled_minutes_x_recent10_target_per_min",
    "modeled_minutes_q50_x_missing_key_teammates_player_target_per_min_delta",
    "modeled_minutes_q50_x_returning_key_teammates_player_target_per_min_delta",
]
NON_FEATURE_COLUMNS = {"game_date", "game_id", TARGET_COLUMN}

# Rows per chunk when attaching the modelled-minutes features. 50k x ~125 keys
# is a few hundred MB of dicts at a time instead of all 461k rows at once.
ATTACH_CHUNK_ROWS = 50_000

# Out-of-fold predictions travel with the bundle. Without them the only way to
# compare against a closing line is to re-score the dataset with the shipped
# weights -- which were refitted on every row, making that comparison in-sample.
OOF_PREDICTIONS_FILE = "oof_predictions.csv"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train regression models for stat value prediction.")
    p.add_argument("--dataset-csv", default=str(DEFAULT_REGRESSION_DATASET_PATH))
    p.add_argument("--minutes-dataset-csv", default=str(DEFAULT_MINUTES_DATASET_PATH))
    p.add_argument("--model-dir", default=str(DEFAULT_REGRESSION_MODEL_DIR))
    p.add_argument("--iterations", type=int, default=1000)
    p.add_argument("--depth", type=int, default=6)
    p.add_argument("--learning-rate", type=float, default=0.03)
    p.add_argument("--l2-leaf-reg", type=float, default=5.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--test-date-count", type=int, default=DEFAULT_TEST_DATES,
                   help="Most recent dates held out for evaluation. Never selected on.")
    p.add_argument("--val-date-count", type=int, default=DEFAULT_VAL_DATES,
                   help="Dates immediately before the test block, carved out of train. "
                        "Optuna selects on these and CatBoost early-stops on them.")
    p.add_argument("--tune", action="store_true", default=False,
                   help="Run per-stat Optuna search against the validation block.")
    p.add_argument("--tune-trials", type=int, default=30,
                   help="Optuna trials per stat when --tune is set.")
    p.add_argument("--max-iterations", type=int, default=1200,
                   help="Upper bound on the tuned iteration count. Unbounded search "
                        "against a holdout is what produced an 18MB FG3M head.")
    p.add_argument("--stats", nargs="*", default=None,
                   help="Restrict training to these stat types (default: all in the dataset).")
    p.add_argument("--feature-contract",
                   default=str(PROP_MODELING_DIR / "exported_regression_model" / "model_metadata.json"),
                   help="Bundle whose feature_columns the new one must match. "
                        "Set empty to skip the check.")
    p.add_argument("--include-playoffs", action="store_true", default=False,
                   help="Train on postseason rows if the dataset still carries them. "
                        "Off by default; build_regression_dataset.py drops them.")
    p.add_argument("--no-current-season-eval", action="store_true", default=False,
                   help="Skip the walk-forward evaluation of the current season. "
                        "That evaluation is the only honest in-regime number early in "
                        "a season, so skipping it leaves only a prior-season metric.")
    p.add_argument("--block-dates", type=int, default=6,
                   help="Dates per walk-forward block. Smaller is less stale and more "
                        "fits; a ~16-date season at 6 is three fits per stat.")
    p.add_argument("--no-final-refit", action="store_true", default=False,
                   help="Ship the holdout-trained weights instead of refitting on all "
                        "data. The holdout fit excludes the newest 35 dates, which early "
                        "in a season is the entire current season.")
    p.add_argument("--reset-promotion-guardrail", action="store_true", default=False,
                   help="Write the code-default guardrail thresholds instead of carrying "
                        "the shipping bundle's calibrated ones forward. edge_score reads "
                        "these live, so the default is to preserve them.")
    p.add_argument("--allow-feature-change", action="store_true", default=False,
                   help="Permit a feature set that differs from --feature-contract. "
                        "Required because silent feature drift breaks the serve path.")
    p.add_argument("--walk-forward", action="store_true", default=False)
    p.add_argument("--walk-forward-min-train-dates", type=int, default=30)
    p.add_argument("--unified", action="store_true", default=False,
                   help="Train one model across all stat types.")
    p.add_argument("--backtest-lines-csv", default="",
                   help="Optional prop dataset CSV to backtest hit rate against actual lines.")
    return p.parse_args()


def _safe_metric(v: Any) -> Optional[float]:
    if v is None:
        return None
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 6)


def _load_dataset(path: Path) -> pd.DataFrame:
    # game_id as a string, explicitly. Left to pandas it infers int64, and a
    # single blank cell anywhere in the 487k-row CSV makes the whole column
    # float64 -- at which point str(22400104.0) == "22400104.0", .isdigit() is
    # False, every season-type read returns None, and the postseason guard
    # below reports "all regular season" and trains on playoff rows. The
    # builder writes this column as `... or None`, so a blank is reachable.
    df = pd.read_csv(path, dtype={"game_id": str})
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df[df["game_date"].notna()].copy()
    df[TARGET_COLUMN] = pd.to_numeric(df[TARGET_COLUMN], errors="coerce")
    df = df[df[TARGET_COLUMN].notna()].copy()
    return df


def _load_minutes_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df[df["game_date"].notna()].copy()
    df[MINUTES_MODEL_TARGET_COLUMN] = pd.to_numeric(df[MINUTES_MODEL_TARGET_COLUMN], errors="coerce")
    df = df[df[MINUTES_MODEL_TARGET_COLUMN].notna()].copy()
    return df


def _prepare_features(
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    cat_features: Sequence[str],
) -> pd.DataFrame:
    X = df[feature_cols].copy()
    for c in feature_cols:
        if c in cat_features or c == "stat_type":
            X[c] = X[c].fillna("UNKNOWN").astype(str)
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return X


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(text)
    temp_path.replace(path)


def _atomic_write_json(path: Path, payload: Any) -> None:
    _atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True),
    )


def _atomic_save_catboost_model(model: CatBoostRegressor, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    model.save_model(str(temp_path), format="cbm")
    temp_path.replace(path)


def _stat_model_filename(stat_type: str) -> str:
    """model_PTS.cbm, model_PTS_REB_AST.cbm -- the names the shipped bundle uses.

    ml_inference reads the mapping out of model_metadata.json rather than
    guessing, but keeping the convention means a retrained bundle drops into
    exported_regression_model/ over the old files instead of beside them.
    """
    return "model_%s.cbm" % stat_type.replace("+", "_")


def _git_state() -> Dict[str, Any]:
    """The commit the bundle was trained from, and whether the tree was dirty.

    The dirty flag is the point. `rev-parse HEAD` alone names a commit that
    may not contain the code that produced the bundle -- and for the one field
    whose stated purpose is that a future reader need not trust a comment,
    silently naming the wrong commit is the worst available failure. A retrain
    run mid-development is the normal case, not the exception.
    """
    def _run(args: List[str]) -> Optional[str]:
        try:
            out = subprocess.run(
                args, cwd=str(Path(__file__).resolve().parent),
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout

    head = _run(["git", "rev-parse", "HEAD"])
    if head is None:
        return {"sha": None, "dirty": None}
    status = _run(["git", "status", "--porcelain"])
    dirty = None if status is None else bool(status.strip())
    state: Dict[str, Any] = {"sha": head.strip() or None, "dirty": dirty}
    if dirty:
        # Named, not just flagged: "dirty" without the file list cannot be
        # acted on six months later.
        state["dirty_paths"] = sorted(
            line[3:].strip() for line in status.strip().splitlines()
        )[:40]
    return state


def _dataset_fingerprint(path: Path, rows: int, dates: int) -> Dict[str, Any]:
    """Enough to tell whether two bundles were trained on the same data.

    Not a content hash: the regression dataset is 295 MB and hashing it on
    every run costs more than it tells us. Size plus mtime plus row and date
    counts distinguishes a rebuild from a reuse, which is the question.
    """
    try:
        stat = path.stat()
        size, mtime = int(stat.st_size), int(stat.st_mtime)
    except OSError:
        size, mtime = None, None
    return {
        "path": str(path.resolve()),
        "bytes": size,
        "mtime": mtime,
        "rows": int(rows),
        "dates": int(dates),
    }


def _engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    if "recent5_stat_avg" in out.columns and "recent20_stat_avg" in out.columns:
        out["momentum_diff_5v20"] = out["recent5_stat_avg"] - out["recent20_stat_avg"]
    if "recent3_stat_avg" in out.columns and "recent10_stat_avg" in out.columns:
        out["momentum_diff_3v10"] = out["recent3_stat_avg"] - out["recent10_stat_avg"]

    if "team_pace" in out.columns and "season_usage_pct_avg" in out.columns:
        out["expected_possessions"] = (
            out["team_pace"] * (out["season_usage_pct_avg"].fillna(0.0) / 100.0)
        )

    min_cols = ["recent5_minutes_avg", "recent10_minutes_avg", "season_minutes_avg"]
    if all(c in out.columns for c in min_cols):
        r5 = out["recent5_minutes_avg"].fillna(out["season_minutes_avg"])
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
    if (
        "modeled_minutes_q50" in out.columns
        and "missing_key_teammates_player_target_per_min_delta" in out.columns
    ):
        out["modeled_minutes_q50_x_missing_key_teammates_player_target_per_min_delta"] = (
            pd.to_numeric(out["modeled_minutes_q50"], errors="coerce").fillna(0.0)
            * pd.to_numeric(
                out["missing_key_teammates_player_target_per_min_delta"],
                errors="coerce",
            ).fillna(0.0)
        )
    if (
        "modeled_minutes_q50" in out.columns
        and "returning_key_teammates_player_target_per_min_delta" in out.columns
    ):
        out["modeled_minutes_q50_x_returning_key_teammates_player_target_per_min_delta"] = (
            pd.to_numeric(out["modeled_minutes_q50"], errors="coerce").fillna(0.0)
            * pd.to_numeric(
                out["returning_key_teammates_player_target_per_min_delta"],
                errors="coerce",
            ).fillna(0.0)
        )

    return out


def make_sample_weights(input_df: pd.DataFrame, decay: float = 0.001) -> np.ndarray:
    today = input_df["game_date"].max()
    days_ago = (today - input_df["game_date"]).dt.days.clip(lower=0).to_numpy()
    weights = np.exp(-decay * days_ago)
    weights = weights / weights.mean()
    return weights.astype(np.float32)


def _derive_feature_columns(df: pd.DataFrame) -> List[str]:
    return [
        column
        for column in df.columns
        if column not in NON_FEATURE_COLUMNS and column != "stat_type"
    ]


def _minutes_cat_indices() -> List[int]:
    return [
        MINUTES_MODEL_FEATURE_COLUMNS.index(column)
        for column in MINUTES_MODEL_CATEGORICAL_FEATURES
        if column in MINUTES_MODEL_FEATURE_COLUMNS
    ]


def _train_minutes_quantile_model(
    train_df: pd.DataFrame,
    predict_df: pd.DataFrame,
    *,
    val_df: Optional[pd.DataFrame] = None,
) -> Tuple[CatBoostRegressor, np.ndarray]:
    """Fit the minutes head, early-stopping on `val_df` rather than on the target.

    `predict_df` is only scored, never fitted against. The split matters: this
    function used to take (train, test) and pass the test pool as `eval_set`
    with `use_best_model=True`, so the 4.52-minute MAE in
    minutes_model_metadata.json came from a model whose tree count was chosen on
    the rows it was then scored against. Callers inside the walk-forward loop
    pass no `val_df` because their fit block is already strictly prior to their
    prediction block; they get a fixed iteration count instead of early stopping.
    """
    cat_indices = _minutes_cat_indices()
    X_train = _prepare_features(train_df, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
    X_predict = _prepare_features(predict_df, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
    y_train = train_df[MINUTES_MODEL_TARGET_COLUMN].to_numpy()
    w_train = make_sample_weights(train_df)

    train_pool = Pool(X_train, label=y_train, cat_features=cat_indices, weight=w_train)
    predict_pool = Pool(X_predict, cat_features=cat_indices)

    model = CatBoostRegressor(
        iterations=700,
        depth=6,
        learning_rate=0.035,
        l2_leaf_reg=5.0,
        loss_function="MultiQuantile:alpha=0.25,0.5,0.75",
        random_seed=42,
        verbose=False,
        allow_writing_files=False,
        task_type="CPU",
    )
    if val_df is not None and not val_df.empty:
        X_val = _prepare_features(val_df, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
        val_pool = Pool(
            X_val,
            label=val_df[MINUTES_MODEL_TARGET_COLUMN].to_numpy(),
            cat_features=cat_indices,
        )
        model.fit(train_pool, eval_set=val_pool, use_best_model=True, early_stopping_rounds=50)
    else:
        model.fit(train_pool)
    return model, model.predict(predict_pool)


def _split_minutes_oof_train_eval(
    train_block: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    unique_dates = sorted(train_block["game_date"].dt.date.unique())
    eval_date_count = min(MINUTES_OOF_BLOCK_DATES, max(1, len(unique_dates) // 5))
    if len(unique_dates) <= eval_date_count:
        return train_block, train_block.iloc[0:0].copy()

    eval_dates = set(unique_dates[-eval_date_count:])
    fit_block = train_block[~train_block["game_date"].dt.date.isin(eval_dates)].copy()
    eval_block = train_block[train_block["game_date"].dt.date.isin(eval_dates)].copy()
    if fit_block.empty or eval_block.empty:
        return train_block, train_block.iloc[0:0].copy()
    return fit_block, eval_block


def _build_minutes_prediction_frame(
    minutes_df: pd.DataFrame,
    *,
    split: DateSplit,
) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float], Dict[str, Any], CatBoostRegressor]:
    """Out-of-fold modeled minutes for train+val, and a held-out score on test.

    The walk-forward loop below was already correct -- each block fits on
    strictly prior dates and `_split_minutes_oof_train_eval` carves its early
    stopping block out of that fit window. What was not correct was the final
    model: it fit on everything before the test boundary and then passed the
    test pool as `eval_set`, so the 4.52-minute MAE described a model that had
    picked its tree count on the rows it was scored against.

    Now the final model fits on train, early-stops on val, and is scored on
    test. That leaves the val rows out of the final fit, which costs a little
    data on a 51k-row dataset -- the alternative, refitting on train+val at the
    chosen iteration count, buys the rows back but makes the reported metric
    describe a model that is not quite the one evaluated. The honest score is
    worth more here than the rows.
    """
    oof_frames: List[pd.DataFrame] = []
    all_dates = sorted(minutes_df["game_date"].dt.date.unique())
    test_start = split.test_start
    cat_indices = _minutes_cat_indices()

    for block_start in range(MINUTES_OOF_MIN_TRAIN_DATES, len(all_dates), MINUTES_OOF_BLOCK_DATES):
        pred_dates = all_dates[block_start:block_start + MINUTES_OOF_BLOCK_DATES]
        if not pred_dates:
            continue
        train_dates = all_dates[:block_start]
        train_block = minutes_df[minutes_df["game_date"].dt.date.isin(train_dates)].copy()
        pred_block = minutes_df[
            minutes_df["game_date"].dt.date.isin(pred_dates)
            & (minutes_df["game_date"] < test_start)
        ].copy()
        if train_block.empty or pred_block.empty:
            continue

        fit_block, eval_block = _split_minutes_oof_train_eval(train_block)
        X_train = _prepare_features(fit_block, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
        X_pred = _prepare_features(pred_block, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
        y_train = fit_block[MINUTES_MODEL_TARGET_COLUMN].to_numpy()
        w_train = make_sample_weights(fit_block)

        train_pool = Pool(X_train, label=y_train, cat_features=cat_indices, weight=w_train)
        pred_pool = Pool(X_pred, cat_features=cat_indices)
        model = CatBoostRegressor(
            iterations=700,
            depth=6,
            learning_rate=0.035,
            l2_leaf_reg=5.0,
            loss_function="MultiQuantile:alpha=0.25,0.5,0.75",
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
            task_type="CPU",
        )
        if not eval_block.empty:
            X_eval = _prepare_features(eval_block, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
            eval_pool = Pool(
                X_eval,
                label=eval_block[MINUTES_MODEL_TARGET_COLUMN].to_numpy(),
                cat_features=cat_indices,
            )
            model.fit(
                train_pool,
                eval_set=eval_pool,
                use_best_model=True,
                early_stopping_rounds=50,
            )
        else:
            model.fit(train_pool, verbose=False)
        preds = model.predict(pred_pool)

        oof_frame = pred_block[["game_date", "player_id", "game_id"]].copy()
        oof_frame["modeled_minutes_q25"] = preds[:, 0]
        oof_frame["modeled_minutes_q50"] = preds[:, 1]
        oof_frame["modeled_minutes_q75"] = preds[:, 2]
        oof_frames.append(oof_frame)

    train_minutes = split.frame(minutes_df, "train")
    val_minutes = split.frame(minutes_df, "val")
    test_minutes = split.frame(minutes_df, "test")
    assert_not_test(split, val_minutes, purpose="minutes early stopping")
    final_model, final_preds = _train_minutes_quantile_model(
        train_minutes, test_minutes, val_df=val_minutes
    )
    final_frame = test_minutes[["game_date", "player_id", "game_id"]].copy()
    final_frame["modeled_minutes_q25"] = final_preds[:, 0]
    final_frame["modeled_minutes_q50"] = final_preds[:, 1]
    final_frame["modeled_minutes_q75"] = final_preds[:, 2]

    pred_frame = pd.concat([*oof_frames, final_frame], ignore_index=True)
    minutes_metrics = minutes_metrics_summary(
        actuals=test_minutes[MINUTES_MODEL_TARGET_COLUMN].to_numpy(),
        q25_preds=final_preds[:, 0],
        q50_preds=final_preds[:, 1],
        q75_preds=final_preds[:, 2],
    )
    fitted_minutes = pd.concat([train_minutes, val_minutes], ignore_index=True)
    train_key_count = len(fitted_minutes[["game_date", "player_id", "game_id"]].drop_duplicates())
    oof_rows = pred_frame[pred_frame["game_date"] < test_start]
    oof_key_count = len(oof_rows[["game_date", "player_id", "game_id"]].drop_duplicates())
    oof_metrics = {
        "train_rows": int(len(train_minutes)),
        "val_rows": int(len(val_minutes)),
        "test_rows": int(len(test_minutes)),
        "oof_rows": int(len(oof_rows)),
        "distinct_train_keys": int(train_key_count),
        "distinct_oof_keys": int(oof_key_count),
        # Named for what it is: the share of PRE-TEST rows (train + val) that
        # carry an out-of-fold modelled-minutes value. The final minutes model
        # fits on train alone, so this is not "the model's training coverage".
        "pre_test_key_coverage": _safe_metric(oof_key_count / train_key_count) if train_key_count else 0.0,
        "training_key_coverage": _safe_metric(oof_key_count / train_key_count) if train_key_count else 0.0,
    }

    heuristic_source = test_minutes.copy()
    recent5 = pd.to_numeric(heuristic_source.get("recent5_minutes_avg"), errors="coerce")
    recent10 = pd.to_numeric(heuristic_source.get("recent10_minutes_avg"), errors="coerce")
    season = pd.to_numeric(heuristic_source.get("season_minutes_avg"), errors="coerce")
    heuristic_pred = (
        recent5.fillna(season) * 0.50
        + recent10.fillna(season) * 0.30
        + season.fillna(recent5) * 0.20
    )
    if "is_b2b" in heuristic_source.columns:
        heuristic_pred *= (
            1.0 - 0.035 * pd.to_numeric(heuristic_source["is_b2b"], errors="coerce").fillna(0.0)
        )
    heuristic_pred = heuristic_pred.clip(lower=4.0, upper=42.0)
    heuristic_metrics = {
        "mae_q50": _safe_metric(mean_absolute_error(test_minutes[MINUTES_MODEL_TARGET_COLUMN], heuristic_pred)),
        "rmse_q50": _safe_metric(math.sqrt(mean_squared_error(test_minutes[MINUTES_MODEL_TARGET_COLUMN], heuristic_pred))),
        "r2_q50": _safe_metric(r2_score(test_minutes[MINUTES_MODEL_TARGET_COLUMN], heuristic_pred)),
    }
    return pred_frame, minutes_metrics, heuristic_metrics, oof_metrics, final_model


def _attach_modeled_minutes_features(
    stat_df: pd.DataFrame,
    minutes_pred_frame: pd.DataFrame,
) -> pd.DataFrame:
    merged = stat_df.copy()
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

    # Processed in chunks rather than all at once. `to_dict("records")` over
    # the whole frame materialises ~461k dicts of ~125 keys -- about 58M dict
    # entries live simultaneously, alongside the frame being rebuilt from them.
    # That peak lands immediately after the ~30-fit minutes OOF loop, so an OOM
    # there discards the most expensive part of the run, and it is the kind of
    # failure a shared Colab runtime produces and a laptop does not.
    #
    # Chunked and not vectorised on purpose: `apply_modeled_minutes_features`
    # is shared with the serve path, and reimplementing it over columns would
    # create a third definition of these features to drift from the other two
    # (`engineered_features.attach_modeled_minutes` already differs from this
    # one on the q50 fallback chain and on rounding). Chunking leaves the
    # semantics identical by construction.
    chunks: List[pd.DataFrame] = []
    for start in range(0, len(merged), ATTACH_CHUNK_ROWS):
        block = merged.iloc[start:start + ATTACH_CHUNK_ROWS]
        rows: List[Dict[str, Any]] = []
        for row in block.to_dict("records"):
            q25 = row.pop("modeled_minutes_q25", None)
            q50 = row.get("modeled_minutes_q50")
            q75 = row.pop("modeled_minutes_q75", None)
            modeled_iqr = None
            if q25 is not None and q75 is not None:
                modeled_iqr = max(0.0, float(q75) - float(q25))
            apply_modeled_minutes_features(
                row,
                modeled_minutes_q50=q50,
                modeled_minutes_iqr=modeled_iqr,
            )
            rows.append(row)
        chunks.append(pd.DataFrame(rows))
        del rows
    if not chunks:
        return merged.iloc[0:0].copy()
    out = pd.concat(chunks, ignore_index=True)
    del chunks
    return out


def _regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Any]:
    residuals = y_true - y_pred
    return {
        "mae": _safe_metric(mean_absolute_error(y_true, y_pred)),
        "rmse": _safe_metric(math.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": _safe_metric(r2_score(y_true, y_pred)),
        "median_ae": _safe_metric(float(np.median(np.abs(y_true - y_pred)))),
        "mean_actual": _safe_metric(float(y_true.mean())),
        "mean_predicted": _safe_metric(float(y_pred.mean())),
        "bias": _safe_metric(float((y_pred - y_true).mean())),
        "residual_std": _safe_metric(float(np.std(residuals))),
    }


def _prop_hit_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    lines: np.ndarray,
    sides: np.ndarray,
) -> Dict[str, Any]:
    """Evaluate how well the regression predictions classify prop hits.

    If predicted > line → model says "over", compare to actual.
    If predicted < line → model says "under", compare to actual.
    """
    n = len(y_true)
    if n == 0:
        return {}

    correct = 0
    total_with_opinion = 0
    strong_correct = 0
    strong_total = 0

    for i in range(n):
        pred = y_pred[i]
        actual = y_true[i]
        line = lines[i]
        side = sides[i]

        # Does the model agree with the side?
        if side == "over":
            model_agrees = pred > line
            hit = actual > line
        else:
            model_agrees = pred < line
            hit = actual < line

        if actual == line:
            continue  # push

        total_with_opinion += 1
        if hit:
            correct += 1

        # If model confidently agrees (>1 unit margin)
        if side == "over" and pred > line + 1.0:
            strong_total += 1
            if hit:
                strong_correct += 1
        elif side == "under" and pred < line - 1.0:
            strong_total += 1
            if hit:
                strong_correct += 1

    # Model-derived hit rate: model picks a side, does it hit?
    model_picks_correct = 0
    model_picks_total = 0
    for i in range(n):
        pred = y_pred[i]
        actual = y_true[i]
        line = lines[i]

        if abs(pred - line) < 0.5:
            continue  # no opinion
        if actual == line:
            continue  # push

        model_picks_total += 1
        if pred > line and actual > line:
            model_picks_correct += 1
        elif pred < line and actual < line:
            model_picks_correct += 1

    return {
        "overall_hit_rate": _safe_metric(correct / total_with_opinion) if total_with_opinion else None,
        "model_pick_accuracy": _safe_metric(model_picks_correct / model_picks_total) if model_picks_total else None,
        "model_pick_count": model_picks_total,
        "strong_pick_accuracy": _safe_metric(strong_correct / strong_total) if strong_total else None,
        "strong_pick_count": strong_total,
    }


# ---------------------------------------------------------------------------
# The per-stat quantile head: what actually ships.
#
# Three rules hold throughout, and each one is a defect that was measured:
#   1. `val_df` is the only frame model selection may touch. Optuna scores on
#      it and CatBoost early-stops on it. `assert_not_test` enforces that.
#   2. `iterations` is bounded. An unbounded search against a holdout produced
#      an 18.4 MB FG3M head (R-squared 0.287) beside a 438 KB BLK head.
#   3. Metrics come back from `quantile_report`, which scores all three heads
#      and compares the median to the player's own recent-10 average -- a
#      baseline the model has to beat before "accuracy" means anything.
# ---------------------------------------------------------------------------

# Same bar the dataset builder applies, imported in spirit rather than from
# build_regression_dataset so the trainer does not pull in that module's
# enrichment loading just to read one float.
_MAX_UNREADABLE_GAME_ID_FRACTION = 0.01

STAT_DEFAULT_PARAMS = {
    "iterations": 800,
    "depth": 6,
    "learning_rate": 0.03,
    "l2_leaf_reg": 5.0,
}

# The notebook's baseline pass used these and the 40-trial tuned retrain beat
# them by 0.9% of PTS MAE -- bought with a search that selected on the test
# set. Tuning is therefore opt-in and this is the default.
BASELINE_MAE_COLUMN = "recent10_stat_avg"


def _quantile_loss_spec(alphas: Sequence[float]) -> str:
    return "MultiQuantile:alpha=" + ",".join(str(a) for a in alphas)


def _fit_quantile_model(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    feature_cols: Sequence[str],
    cat_cols: Sequence[str],
    cat_indices: Sequence[int],
    params: Dict[str, Any],
    alphas: Sequence[float],
    *,
    seed: int,
    early_stopping_rounds: int = 60,
    verbose: Any = False,
) -> CatBoostRegressor:
    """Fit one head. `val_df` drives early stopping and nothing else does."""
    X_train = _prepare_features(train_df, feature_cols, cat_cols)
    y_train = train_df[TARGET_COLUMN].to_numpy()
    train_pool = Pool(
        X_train, label=y_train, cat_features=list(cat_indices),
        weight=make_sample_weights(train_df),
    )

    model = CatBoostRegressor(
        **params,
        loss_function=_quantile_loss_spec(alphas),
        random_seed=seed,
        verbose=verbose,
        allow_writing_files=False,
        # MultiQuantile is CPU-only in CatBoost.
        task_type="CPU",
    )

    if val_df is not None and not val_df.empty:
        X_val = _prepare_features(val_df, feature_cols, cat_cols)
        val_pool = Pool(
            X_val, label=val_df[TARGET_COLUMN].to_numpy(), cat_features=list(cat_indices)
        )
        model.fit(
            train_pool, eval_set=val_pool,
            use_best_model=True, early_stopping_rounds=early_stopping_rounds,
        )
    else:
        model.fit(train_pool)
    return model


def _best_iteration(model: CatBoostRegressor, fallback: int) -> int:
    """The chosen iteration, without `or` turning a genuine 0 into the cap."""
    best = model.get_best_iteration()
    return int(fallback if best is None else best)


def _predict_quantiles(
    model: CatBoostRegressor,
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    cat_cols: Sequence[str],
    cat_indices: Sequence[int],
) -> np.ndarray:
    """Predictions under the serve path's contract: ordered, then floored at zero.

    `ml_inference._sanitize_quantiles` sorts before clamping, in that order, and
    `backtest.score_dataset` replicates it. Applying the same thing here means
    the metrics describe the numbers that reach `p_over`, not the raw heads --
    BLK produced negative quantiles on 38% of rows before the clamp existed.
    """
    X = _prepare_features(df, feature_cols, cat_cols)
    preds = model.predict(Pool(X, cat_features=list(cat_indices)))
    preds = np.atleast_2d(np.asarray(preds, dtype=float))
    return np.clip(np.sort(preds, axis=1), 0.0, None)


def _stat_baselines(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    """Baselines the model is scored against, where the dataset carries them."""
    out: Dict[str, np.ndarray] = {}
    if BASELINE_MAE_COLUMN in df.columns:
        out["recent10"] = pd.to_numeric(df[BASELINE_MAE_COLUMN], errors="coerce").to_numpy()
    return out


def _tune_stat_params(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    feature_cols: Sequence[str],
    cat_cols: Sequence[str],
    cat_indices: Sequence[int],
    alphas: Sequence[float],
    *,
    n_trials: int,
    seed: int,
    max_iterations: int,
) -> Tuple[Dict[str, Any], Optional[float]]:
    """Search hyperparameters against the validation block.

    Returns the default params untouched if optuna is not installed, rather
    than failing a retrain over an optional dependency. The objective is
    pinball loss summed across alphas, not q50 MAE: the previous objective
    optimised the median alone, which lets a trial win by tightening the
    median while widening the band the probability layer depends on.
    """
    try:
        import optuna
    except ImportError:
        print("  optuna not installed; using default params")
        return dict(STAT_DEFAULT_PARAMS), None

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial) -> float:
        params = {
            "iterations": trial.suggest_int("iterations", 300, max_iterations),
            "depth": trial.suggest_int("depth", 4, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.12, log=True),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 25.0),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 5, 80),
            "random_strength": trial.suggest_float("random_strength", 0.3, 5.0),
            "bootstrap_type": "Bernoulli",
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
        }
        model = _fit_quantile_model(
            train_df, val_df, feature_cols, cat_cols, cat_indices, params, alphas,
            seed=seed, early_stopping_rounds=40,
        )
        preds = _predict_quantiles(model, val_df, feature_cols, cat_cols, cat_indices)
        report = quantile_report(
            val_df[TARGET_COLUMN].to_numpy(), preds, alphas=alphas
        )
        losses = [v for v in report["pinball"].values() if v is not None]
        if not losses:
            return float("inf")
        return float(sum(losses))

    study = optuna.create_study(direction="minimize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best = dict(study.best_params)
    best.setdefault("bootstrap_type", "Bernoulli")
    return best, float(study.best_value)


def _season_segments(df: pd.DataFrame) -> List[Tuple[str, pd.DataFrame]]:
    """Split a frame by NBA season, latest last.

    The test block is 20 *dates*, and the dataset's last 20 regular-season
    dates straddle the offseason: roughly 11 dates at the end of 2025-26
    (April 2026) and 9 at the start of 2026-27 (late October onward). Pooling
    them reports a 62/38 blend of two regimes as one number, and the half that
    decides whether to ship -- current-season performance on current rosters --
    is the smaller half and invisible.

    Seasons are keyed with `season_key_for_date`, the same rule the injury and
    presence features use, so a segment label here means the same thing it
    means everywhere else in the pipeline.
    """
    dates = pd.to_datetime(df["game_date"], errors="coerce")
    keys = dates.map(lambda d: season_key_for_date(d.date()) if pd.notna(d) else None)
    out: List[Tuple[str, pd.DataFrame]] = []
    for key in sorted({k for k in keys if k is not None}):
        label = "%d-%02d" % (key, (key + 1) % 100)
        out.append((label, df[keys == key]))
    return out


def _oof_rows(
    frame: pd.DataFrame,
    preds: np.ndarray,
    stat_type: str,
    source: str,
) -> pd.DataFrame:
    """Keyed out-of-fold predictions, in the shape backtest.py can join.

    Columns match `(player_id, game_date, stat_type)` because that is the key
    `score_dataset` uses, so the gate can read these instead of re-scoring --
    which is the whole point, since re-scoring uses the refitted weights.
    """
    out = pd.DataFrame({
        "player_id": frame["player_id"].astype(str).to_numpy(),
        "game_date": pd.to_datetime(frame["game_date"], errors="coerce")
                       .dt.strftime("%Y-%m-%d").to_numpy(),
        "stat_type": stat_type,
        "source": source,
        "q25": preds[:, 0],
        "q50": preds[:, 1],
        "q75": preds[:, 2],
        "actual": pd.to_numeric(frame[TARGET_COLUMN], errors="coerce").to_numpy(),
    })
    return out


def _walk_forward_current_season(
    stat_type: str,
    stat_df: pd.DataFrame,
    feature_cols: Sequence[str],
    cat_cols: Sequence[str],
    cat_indices: Sequence[int],
    alphas: Sequence[float],
    params: Dict[str, Any],
    args: argparse.Namespace,
) -> Optional[Dict[str, Any]]:
    """Out-of-fold predictions for every current-season date.

    This is the number that decides whether a bundle is worth shipping, and
    `chronological_split` cannot produce it early in a season: it holds out the
    last 35 dates and the season has about 16, so the whole season sits in the
    holdout and the fitted model stops in March. Here each block trains only on
    strictly prior dates, so every current-season row is scored by a model that
    never saw it, and no row has to be withheld from the final fit to make that
    true.

    Returns None when the season is too short to produce a single block, which
    is reported rather than silently counted as a coverage of zero.
    """
    season = latest_season_key(stat_df, season_key_fn=season_key_for_date)
    if season is None:
        return None
    targets = season_dates(stat_df, season, season_key_fn=season_key_for_date)
    blocks = walk_forward_blocks(
        stat_df, evaluate_dates=targets, block_dates=max(1, args.block_dates),
    )
    if not blocks:
        return None

    frames: List[pd.DataFrame] = []
    preds: List[np.ndarray] = []
    for block in blocks:
        train_df = block.frame(stat_df, "train")
        val_df = block.frame(stat_df, "val")
        predict_df = block.frame(stat_df, "predict")
        if train_df.empty or predict_df.empty:
            continue
        model = _fit_quantile_model(
            train_df, val_df, feature_cols, cat_cols, cat_indices, params, alphas,
            seed=args.seed,
        )
        preds.append(_predict_quantiles(
            model, predict_df, feature_cols, cat_cols, cat_indices
        ))
        frames.append(predict_df)
        del model

    if not frames:
        return None
    scored = pd.concat(frames, ignore_index=True)
    matrix = np.vstack(preds)
    report = quantile_report(
        scored[TARGET_COLUMN].to_numpy(), matrix,
        alphas=alphas, baselines=_stat_baselines(scored),
    )
    report["season"] = "%d-%02d" % (season, (season + 1) % 100)
    report["blocks"] = len(frames)
    report["block_dates"] = int(args.block_dates)
    report["dates_covered"] = len({d for b in blocks for d in b.predict_dates})
    report["dates_in_season"] = len(targets)
    report["_predictions"] = _oof_rows(scored, matrix, stat_type, "walk_forward")
    return report


def _train_stat_quantile_head(
    stat_type: str,
    frames: Dict[str, pd.DataFrame],
    feature_cols: Sequence[str],
    cat_cols: Sequence[str],
    cat_indices: Sequence[int],
    alphas: Sequence[float],
    args: argparse.Namespace,
    split: DateSplit,
) -> Optional[Dict[str, Any]]:
    """Tune (optionally), fit, and score one stat's head on the test block."""
    train_df, val_df, test_df = frames["train"], frames["val"], frames["test"]
    if "all" not in frames:
        raise KeyError(
            "frames must carry an 'all' entry: the final refit and the "
            "walk-forward evaluation both need every row for this stat, not "
            "just the three blocks."
        )
    if len(train_df) < 200:
        print("  SKIP %s: only %d train rows" % (stat_type, len(train_df)))
        return None
    if val_df.empty or test_df.empty:
        print("  SKIP %s: empty val (%d) or test (%d) block"
              % (stat_type, len(val_df), len(test_df)))
        return None

    # The guard that would have caught both notebook leaks.
    assert_not_test(split, train_df, purpose="%s training" % stat_type)
    assert_not_test(split, val_df, purpose="%s early stopping" % stat_type)

    tuned_value = None
    if args.tune:
        params, tuned_value = _tune_stat_params(
            train_df, val_df, feature_cols, cat_cols, cat_indices, alphas,
            n_trials=args.tune_trials, seed=args.seed,
            max_iterations=args.max_iterations,
        )
    else:
        params = dict(STAT_DEFAULT_PARAMS)
    params["iterations"] = min(int(params.get("iterations", 800)), args.max_iterations)

    model = _fit_quantile_model(
        train_df, val_df, feature_cols, cat_cols, cat_indices, params, alphas,
        seed=args.seed,
    )
    test_preds = _predict_quantiles(model, test_df, feature_cols, cat_cols, cat_indices)
    val_preds = _predict_quantiles(model, val_df, feature_cols, cat_cols, cat_indices)

    test_report = quantile_report(
        test_df[TARGET_COLUMN].to_numpy(), test_preds,
        alphas=alphas, baselines=_stat_baselines(test_df),
    )

    # Per season, because the pooled number above spans the offseason. Scored
    # from the same prediction array by positional mask, so the segments cannot
    # drift from the pooled figure they decompose.
    segments: Dict[str, Any] = {}
    positions = {idx: i for i, idx in enumerate(test_df.index)}
    for label, segment_df in _season_segments(test_df):
        if segment_df.empty:
            continue
        rows = [positions[idx] for idx in segment_df.index]
        segments[label] = quantile_report(
            segment_df[TARGET_COLUMN].to_numpy(), test_preds[rows, :],
            alphas=alphas, baselines=_stat_baselines(segment_df),
        )
    test_report["segments"] = segments

    # Out-of-fold predictions, kept rather than discarded. These are the only
    # predictions that can honestly be compared against a closing line: the
    # shipped weights are refitted on every row, so re-scoring the dataset with
    # them measures the model on its own training data. Attempting exactly that
    # produced a 0.105 MAE "win" over the closing line on PTS that was pure
    # in-sample leakage.
    oof_frames: List[pd.DataFrame] = [
        _oof_rows(test_df, test_preds, stat_type, "holdout_test")
    ]
    # Reported alongside so a gap between the two is visible. The val block
    # drove early stopping, so it is optimistic by construction.
    val_report = quantile_report(
        val_df[TARGET_COLUMN].to_numpy(), val_preds, alphas=alphas
    )

    holdout_iterations = _best_iteration(model, params["iterations"])

    # The current-season number, measured out-of-fold rather than by holding
    # the season out. See _walk_forward_current_season.
    current_season = None
    if not args.no_current_season_eval:
        wf_params = dict(params)
        wf_params["iterations"] = max(50, holdout_iterations)
        current_season = _walk_forward_current_season(
            stat_type, frames["all"], feature_cols, cat_cols, cat_indices,
            alphas, wf_params, args,
        )

    # What ships. Refitted on every row -- including the val and test blocks,
    # and therefore the whole current season -- at the iteration count the
    # holdout fit chose. Without this the shipped weights stop at the start of
    # the holdout window, which early in a season means they contain no
    # current-season games and no current rosters.
    #
    # Its own accuracy is not measurable by construction: there is nothing left
    # to score it on. That is why both reports above exist, and why
    # `shipped_fit` is recorded in the metadata -- the metrics describe the
    # holdout model, not quite the one being served.
    shipped_model = model
    shipped_fit = "holdout"
    if not args.no_final_refit:
        refit_params = dict(params)
        refit_params["iterations"] = max(50, holdout_iterations)
        shipped_model = _fit_quantile_model(
            frames["all"], frames["all"].iloc[0:0],
            feature_cols, cat_cols, cat_indices, refit_params, alphas,
            seed=args.seed,
        )
        shipped_fit = "all_rows_at_holdout_iterations"

    if current_season is not None and current_season.get("_predictions") is not None:
        oof_frames.append(current_season.pop("_predictions"))

    return {
        "oof_predictions": pd.concat(oof_frames, ignore_index=True),
        "model": shipped_model,
        "params": params,
        "tuned_objective": tuned_value,
        "best_iteration": holdout_iterations,
        "tree_count": int(shipped_model.tree_count_ or 0),
        "shipped_fit": shipped_fit,
        "test": test_report,
        "val": val_report,
        "current_season": current_season,
        "rows": {
            "train": int(len(train_df)),
            "val": int(len(val_df)),
            "test": int(len(test_df)),
            "shipped_fit_rows": int(len(frames["all"]) if shipped_fit != "holdout" else len(train_df)),
        },
    }


def _train_and_eval(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    feature_cols: List[str],
    cat_cols: List[str],
    cat_indices: List[int],
    args: argparse.Namespace,
    stat_type: str,
) -> Dict[str, Any]:
    """Train one regression model on train_df and evaluate on test_df."""
    X_train = _prepare_features(train_df, feature_cols, cat_cols)
    X_test = _prepare_features(test_df, feature_cols, cat_cols)
    y_train = train_df[TARGET_COLUMN].to_numpy()
    y_test = test_df[TARGET_COLUMN].to_numpy()

    train_pool = Pool(X_train, label=y_train, cat_features=cat_indices,
                      weight=make_sample_weights(train_df))
    test_pool = Pool(X_test, label=y_test, cat_features=cat_indices)

    model = CatBoostRegressor(
        iterations=args.iterations,
        depth=args.depth,
        learning_rate=args.learning_rate,
        l2_leaf_reg=args.l2_leaf_reg,
        loss_function="RMSE",
        eval_metric="MAE",
        random_seed=args.seed,
        verbose=False,
        allow_writing_files=False,
    )
    # No eval_set. This path is reachable only from --unified and from
    # _walk_forward_regression, both diagnostics that never write a bundle --
    # but it used to pass test_pool with use_best_model=True and then report
    # metrics on those same rows, per fold. That is the exact mechanism behind
    # the old cv_results mean_r2 0.7753 that this file's docstring disowns, and
    # leaving it here reproduced the defect inside the file that retracts it.
    # A fixed iteration count makes the printed number mean what it says.
    model.fit(train_pool)

    y_pred = model.predict(test_pool)
    metrics = _regression_metrics(y_test, y_pred)
    metrics["stat_type"] = stat_type
    metrics["train_rows"] = len(train_df)
    metrics["test_rows"] = len(test_df)
    best = model.get_best_iteration()
    # `or` would report the cap when the true best iteration is 0.
    metrics["best_iteration"] = int(args.iterations if best is None else best)

    return {"model": model, "metrics": metrics, "y_pred": y_pred, "y_test": y_test}


def _walk_forward_regression(
    df: pd.DataFrame,
    feature_cols: List[str],
    cat_cols: List[str],
    cat_indices: List[int],
    args: argparse.Namespace,
    stat_type: str,
) -> Optional[Dict[str, Any]]:
    """Walk-forward expanding-window evaluation for regression."""
    unique_dates = sorted(df["game_date"].dt.date.unique())
    min_train = args.walk_forward_min_train_dates

    if len(unique_dates) < min_train + 1:
        return None

    all_preds = []
    all_actuals = []
    fold_metrics = []
    final_model = None

    for test_idx in range(min_train, len(unique_dates)):
        test_date = unique_dates[test_idx]
        split_ts = pd.Timestamp(test_date)
        train_df = df[df["game_date"] < split_ts]
        test_df = df[df["game_date"] == split_ts]

        if train_df.empty or test_df.empty or len(train_df) < 100:
            continue

        result = _train_and_eval(train_df, test_df, feature_cols, cat_cols, cat_indices, args, stat_type)
        final_model = result["model"]
        all_preds.extend(result["y_pred"].tolist())
        all_actuals.extend(result["y_test"].tolist())
        fold_metrics.append(result["metrics"])

    if not all_preds or final_model is None:
        return None

    agg = _regression_metrics(np.array(all_actuals), np.array(all_preds))
    agg["stat_type"] = stat_type
    agg["method"] = "walk_forward"
    agg["num_folds"] = len(fold_metrics)
    agg["total_test_rows"] = len(all_preds)

    return {
        "model": final_model,
        "metrics": agg,
        "fold_metrics": fold_metrics,
        "all_preds": np.array(all_preds),
        "all_actuals": np.array(all_actuals),
    }


def _build_feature_drift_baseline(
    df: pd.DataFrame,
    feature_cols: Sequence[str],
    cat_cols: Sequence[str],
) -> Dict[str, Dict[str, float]]:
    baseline: Dict[str, Dict[str, float]] = {}
    numeric_cols = [col for col in feature_cols if col not in cat_cols and col != "stat_type"]
    for col in numeric_cols:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if series.empty:
            continue
        baseline[col] = {
            "mean": round(float(series.mean()), 6),
            "std": round(float(series.std(ddof=0)), 6),
        }
    return baseline


# A run whose out-of-fold minutes coverage falls below this is not measuring
# the model it claims to. Below ~51 dates every OOF block lands at or after the
# test boundary, oof_frames comes back empty, and train/val rows take
# build_fallback_modeled_minutes (the heuristic) while test rows get real model
# output -- so the two sides of the split carry different feature semantics and
# the reported metrics describe neither configuration. The full 324-date
# dataset simulates to 0.90; the shortfall there is the first
# MINUTES_OOF_MIN_TRAIN_DATES dates, which is expected.
MIN_OOF_KEY_COVERAGE = 0.60

# Below this share of rows carrying a real modeled_minutes_q50, the merge has
# effectively missed and every row is silently on the heuristic fallback.
MIN_MODELED_MINUTES_RATE = 0.50


def _assert_split_applies(df: pd.DataFrame, split: DateSplit, label: str) -> None:
    """Check a frame against the split, distinguishing two different problems.

    The split is built from the stat dataset and also applied to the minutes
    dataset, and the two legitimately differ: the minutes dataset is built with
    `--minutes-min-prior-games 3` against the stat dataset's
    `--min-prior-games 5`, so it both starts earlier and admits current-season
    dates sooner. So a date mismatch is not automatically a defect, and an
    earlier version of this check failed the run over 401 rows that were being
    excluded entirely correctly.

    Two cases, and only one is fatal:

      * MISSING -- the frame has no rows on dates the split expects, which is
        the stale-file case. It empties a block, and an empty test block
        crashes on `final_preds[:, 0]` after the ~30-fit OOF loop has run.
        Fatal.
      * EXTRA -- the frame has dates inside the holdout window that the split
        does not enumerate, like those four late-October minutes dates. Those
        rows cannot join train without leaking and are not part of what is
        being scored, so dropping them is the correct outcome. Reported, not
        fatal.
    """
    counts = split_coverage(df, split)
    empty = [name for name in ("train", "val", "test") if not counts[name]]
    if empty:
        raise SystemExit(
            "%s has no rows in block(s) %s, so nothing could be %s. The split comes "
            "from the stat dataset; this frame is most likely stale. Rebuild both with "
            "build_regression_dataset.py."
            % (label, ", ".join(empty),
               "fitted" if "train" in empty else "selected on or scored")
        )

    frame_dates = set(
        pd.to_datetime(df["game_date"], errors="coerce").dt.date.dropna().unique()
    )
    expected = set(split.val_dates) | set(split.test_dates)
    missing = sorted(expected - frame_dates)
    if missing:
        raise SystemExit(
            "%s is missing %d of the %d holdout date(s) the split expects, e.g. %s. "
            "The two datasets disagree about which dates exist -- rebuild both with "
            "build_regression_dataset.py."
            % (label, len(missing), len(expected), [str(d) for d in missing[:3]])
        )

    print("  %s rows: %s" % (label, counts))
    if counts["unassigned"]:
        extra = sorted(d for d in frame_dates
                       if d >= split.val_start.date() and d not in expected)
        print("    %d row(s) on %d date(s) inside the holdout window are not part of "
              "the evaluated set and were dropped: %s"
              % (counts["unassigned"], len(extra), [str(d) for d in extra[:4]]))
        print("    (expected when this frame's min-prior-games gate is looser than the "
              "stat dataset's; they cannot join training without leaking)")


def _assert_oof_coverage(oof_metrics: Dict[str, Any]) -> None:
    coverage = oof_metrics.get("training_key_coverage")
    if coverage is None:
        return
    if float(coverage) < MIN_OOF_KEY_COVERAGE:
        raise SystemExit(
            "Out-of-fold modeled-minutes coverage is %.3f, below the %.2f floor. "
            "The pre-test rows that missed out take the heuristic fallback while the "
            "test rows get real minutes-model output, so the stat heads would be "
            "trained and scored on differently-derived features. This is what a "
            "too-short date range looks like: the OOF loop needs more than "
            "MINUTES_OOF_MIN_TRAIN_DATES + test + val dates to produce anything."
            % (float(coverage), MIN_OOF_KEY_COVERAGE)
        )


def _assert_modeled_minutes_attached(df: pd.DataFrame, split: DateSplit) -> None:
    """The merge actually landed.

    `_attach_modeled_minutes_features` left-merges on (game_date, player_id,
    game_id). A dtype disagreement on any one of those keys -- one file's
    game_id inferred as int64, the other as object -- makes every row miss, and
    every row then silently takes build_fallback_modeled_minutes, which is just
    `predicted_minutes`. Nothing else observes it: training_key_coverage is
    computed off the prediction frame, not off the merge, so it stays near 0.90
    and reports nothing wrong.
    """
    column = "modeled_minutes_q50"
    if column not in df.columns:
        raise SystemExit("%s missing after the modeled-minutes merge" % column)
    rate = float(pd.to_numeric(df[column], errors="coerce").notna().mean())
    print("  modeled_minutes_q50 present on %.1f%% of rows" % (100.0 * rate))
    if rate < MIN_MODELED_MINUTES_RATE:
        raise SystemExit(
            "Only %.1f%% of rows carry a modeled_minutes_q50, below the %.0f%% floor. "
            "The merge on (game_date, player_id, game_id) has missed -- check that "
            "both datasets agree on those dtypes -- and every row would fall back to "
            "the minutes heuristic while the metadata claims a modelled feature."
            % (100.0 * rate, 100.0 * MIN_MODELED_MINUTES_RATE)
        )


def _resolve_promotion_guardrail(
    contract_path: Optional[Path],
    *,
    reset: bool,
) -> Dict[str, float]:
    """The guardrail the new bundle should carry.

    `default_promotion_guardrail_config()` is NOT the right answer on its own.
    edge_score reads `promotion_guardrail` straight out of model_metadata.json
    and lets every key override the code default
    (`edge_score._get_promotion_guardrail_config`), and the shipped bundle's
    values are not the defaults -- `single_stat_gap_pct` is 0.25 against a
    default of 0.15, and `combo_stat_gap_pct` 0.19 against 0.12, because they
    were fitted by the notebook's `calibrate_promotion_guardrail_config`
    (the result is recorded beside them as `promotion_guardrail_calibration`:
    492 eligible rows, single-stat F1 0.684, combo F1 0.747).

    The guardrail fires when `gap_pct < threshold`, so writing the lower
    defaults would stop penalising under picks whose gap sits between 15% and
    25% -- a live increase in under-pick alert volume caused by nothing in the
    model. So the calibrated values are carried forward unless `reset` is
    explicitly asked for, and any key the new default adds is filled in.
    """
    resolved = dict(default_promotion_guardrail_config())
    if reset or not contract_path or not contract_path.exists():
        if reset:
            print("Promotion guardrail: reset to code defaults on request")
        return resolved
    try:
        previous = json.loads(contract_path.read_text()).get("promotion_guardrail") or {}
    except (OSError, ValueError) as exc:
        print("Promotion guardrail: could not read %s (%s); using defaults"
              % (contract_path, exc))
        return resolved
    if not previous:
        return resolved

    carried = {k: v for k, v in previous.items() if k in resolved and resolved[k] != v}
    resolved.update({k: v for k, v in previous.items() if k in resolved})
    if carried:
        print("Promotion guardrail: carried %d calibrated value(s) forward from %s"
              % (len(carried), contract_path.name))
        for key in sorted(carried):
            print("  %s = %s (code default %s)"
                  % (key, carried[key], default_promotion_guardrail_config()[key]))
        print("  pass --reset-promotion-guardrail to use the code defaults instead")
    return resolved


def _assert_regular_season_dataset(df: pd.DataFrame, *, allow_playoffs: bool) -> Dict[str, int]:
    """Refuse a dataset that still carries postseason rows.

    The season-type filter lives in build_regression_dataset.py, so a dataset
    built before it existed is still full of playoff rows -- and the cost is
    not proportional to their share. In the 2026-11-04 dataset the 10,142
    playoff rows sit on the 20 dates immediately before the 2026-27 opener, so
    a 20-date test block lands about half on them. The evaluation would then be
    mostly postseason while the board prices regular-season games.

    Counted off the dataset's own `game_id` column rather than trusted from the
    builder, because the builder and the trainer are separate runs and the CSV
    on disk is what is actually being trained on.
    """
    counts: Dict[str, int] = {}
    if "game_id" not in df.columns:
        print("Season type: dataset has no game_id column; cannot verify")
        return counts

    types = df["game_id"].map(season_type_from_game_id)
    counts = {str(k): int(v) for k, v in types.value_counts(dropna=False).items()}
    offending = {k: v for k, v in counts.items()
                 if k not in (REGULAR_SEASON, "nan", "None")}
    unreadable = int(types.isna().sum())

    # Before trusting the verdict, check it was computed on something. A guard
    # that reports "all regular season" because it could not read any id is
    # worse than no guard: it is a check that cannot fail. The builder applies
    # the same bar in _build_history.
    if len(df) and unreadable / len(df) > _MAX_UNREADABLE_GAME_ID_FRACTION:
        raise SystemExit(
            "Refusing to train: %d of %d rows (%.1f%%) have a game_id whose season "
            "type cannot be read, so the postseason check proves nothing. The column "
            "is most likely float64 from a blank cell -- check how %s was written."
            % (unreadable, len(df), 100.0 * unreadable / len(df), "the dataset CSV")
        )

    if not offending:
        print("Season type: %d rows, all regular season%s"
              % (len(df), " (%d unreadable game_id)" % unreadable if unreadable else ""))
        return counts

    detail = ", ".join("%s=%d" % kv for kv in sorted(offending.items()))
    if allow_playoffs:
        print("Season type: %s present and --include-playoffs is set; proceeding" % detail)
        return counts

    dates = sorted(df.loc[types.isin(list(offending)), "game_date"]
                   .dt.date.astype(str).unique())
    raise SystemExit(
        "Refusing to train: the dataset contains non-regular-season rows (%s) on "
        "%d dates, %s to %s. Postseason rotations and usage do not describe the "
        "games the board prices, and these dates sit at the end of the dataset "
        "where the test block falls. Rebuild with:\n"
        "    python3 prop_modeling/build_regression_dataset.py\n"
        "which now drops them, or pass --include-playoffs to train on them anyway."
        % (detail, len(dates), dates[0], dates[-1])
    )


def _predict_feature_columns(df: pd.DataFrame) -> List[str]:
    """The feature set a run will train on, derived before anything is fitted.

    Mirrors `_derive_feature_columns` applied after the post-build columns are
    attached, by adding the names the constants declare instead of computing
    their values. main() asserts the two agree once the real frame exists, so
    this cannot silently drift from the pipeline it predicts.
    """
    columns = list(df.columns)
    for name in list(ENGINEERED_FEATURE_COLUMNS) + list(MODELED_MINUTES_FEATURE_COLUMNS):
        if name not in columns:
            columns.append(name)
    placeholder = pd.DataFrame(columns=columns)
    return _derive_feature_columns(placeholder)


def _check_feature_contract(
    feature_cols: Sequence[str],
    contract_path: Optional[Path],
    *,
    allow_change: bool,
) -> Dict[str, Any]:
    """Compare the derived feature set against the bundle currently shipping.

    `_derive_feature_columns` takes whatever columns the dataset CSV happens to
    carry, so rebuilding the dataset can change the trained feature set without
    anyone deciding to. That is not a theoretical risk: this file's first run
    picked up six columns the shipped bundle never trained on, four of them
    added to build_regression_dataset.py after the April export.

    Adding a feature is fine when it is intended -- but every trained feature
    has to be one `ml_inference._build_feature_dict` can produce at serve time,
    or `reindex` fills it with NaN and the model collapses toward a flat prior
    (measured once at sd(q50)=3.04 against sd(actual)=8.46, which reads exactly
    like a model with no signal). So the change has to be explicit.
    """
    report: Dict[str, Any] = {"checked": False, "added": [], "removed": []}
    if not contract_path or not contract_path.exists():
        print("Feature contract: %s not found; skipping the check"
              % (contract_path or "<unset>"))
        return report

    try:
        contract = json.loads(contract_path.read_text())
        previous = list(contract.get("feature_columns") or [])
    except (OSError, ValueError) as exc:
        print("Feature contract: could not read %s (%s); skipping" % (contract_path, exc))
        return report
    if not previous:
        print("Feature contract: %s lists no feature_columns; skipping" % contract_path)
        return report

    current, prior = set(feature_cols), set(previous)
    added, removed = sorted(current - prior), sorted(prior - current)
    report.update({"checked": True, "contract": str(contract_path),
                   "added": added, "removed": removed,
                   "contract_count": len(previous), "current_count": len(feature_cols)})
    if not added and not removed:
        print("Feature contract: %d features, unchanged from the shipped bundle"
              % len(feature_cols))
        return report

    print("Feature contract: %d features against the shipped bundle's %d"
          % (len(feature_cols), len(previous)))
    for name in added:
        print("  + %s" % name)
    for name in removed:
        print("  - %s" % name)
    if not allow_change:
        raise SystemExit(
            "Refusing to train: the feature set differs from %s by %d added and %d "
            "removed column(s). Every trained feature must be one the serve path can "
            "build, so confirm each one above reaches ml_inference._build_feature_dict "
            "(directly, via injury_feature_config.apply_injury_feature_values, or via "
            "engineered_features), then re-run with --allow-feature-change."
            % (contract_path, len(added), len(removed))
        )
    print("  --allow-feature-change given; proceeding")
    return report


def _print_bundle_summary(per_stat_test_metrics: Dict[str, Dict[str, Any]]) -> None:
    """The retrain's scoreboard, per stat, against the recent-10 baseline.

    Printed instead of a pooled mean because a mean MAE across these markets
    is not a quantity -- PTS+REB+AST sits near 5.9 and BLK near 0.46, so the
    average mostly reports the market mix.
    """
    if not per_stat_test_metrics:
        return
    print("\n%-14s %7s %9s %9s %8s %9s %9s" % (
        "stat", "n", "MAE q50", "bias", "R2", "vs rec10", "cov cont"))
    losers = []
    for stat_type in sorted(per_stat_test_metrics):
        m = per_stat_test_metrics[stat_type]
        baseline = (m.get("baselines") or {}).get("recent10") or {}
        delta = baseline.get("delta")
        band = m.get("band") or {}
        print("%-14s %7d %9.4f %+9.4f %8.4f %9s %9s" % (
            stat_type, m.get("n") or 0, m.get("mae_q50") or float("nan"),
            m.get("q50_bias") or float("nan"), m.get("r2_q50") or float("nan"),
            ("%+.4f" % delta) if delta is not None else "n/a",
            ("%.3f" % band["coverage_continuous"]) if band.get("coverage_continuous") is not None else "n/a",
        ))
        if delta is not None and delta > 0:
            losers.append(stat_type)
    if losers:
        print("\nLoses to the recent-10 baseline on: %s" % ", ".join(losers))
        print("A head that cannot beat the player's own trailing average is not "
              "adding information. Compare against the closing line with "
              "`python3 prop_modeling/backtest.py --model` before shipping it.")


def main() -> int:
    args = _parse_args()
    dataset_csv = Path(args.dataset_csv)
    minutes_dataset_csv = Path(args.minutes_dataset_csv)
    if not dataset_csv.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_csv}. Run build_regression_dataset.py first.")
    if not minutes_dataset_csv.exists():
        raise FileNotFoundError(
            f"Minutes dataset not found: {minutes_dataset_csv}. Run build_regression_dataset.py first."
        )

    df = _load_dataset(dataset_csv)
    df = _engineer_features(df)
    minutes_df = _load_minutes_dataset(minutes_dataset_csv)
    print(f"Loaded {len(df):,} rows, {df['game_date'].dt.date.nunique()} dates, "
          f"{df['stat_type'].nunique()} stat types")
    print(f"Loaded {len(minutes_df):,} minutes rows")

    model_dir = Path(args.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    # The feature set is knowable before any fitting: it is the dataset's own
    # columns plus the post-build columns the constants declare. Checking the
    # contract here means a feature change is rejected in seconds rather than
    # after the walk-forward minutes build.
    season_type_counts = _assert_regular_season_dataset(
        df, allow_playoffs=args.include_playoffs
    )

    promotion_guardrail = _resolve_promotion_guardrail(
        Path(args.feature_contract) if args.feature_contract else None,
        reset=args.reset_promotion_guardrail,
    )

    predicted_features = _predict_feature_columns(df)
    feature_contract = _check_feature_contract(
        predicted_features,
        Path(args.feature_contract) if args.feature_contract else None,
        allow_change=args.allow_feature_change,
    )

    # One split, built once, shared by the minutes head and every stat head.
    # Both used to re-derive their own boundary inline, which is how the two
    # leaks got in.
    split = chronological_split(
        df,
        test_date_count=args.test_date_count,
        val_date_count=args.val_date_count,
    )
    split_date = split.test_start
    print("Split: train %d dates / val %d / test %d  (test from %s)"
          % (len(split.train_dates), len(split.val_dates), len(split.test_dates),
             split.provenance()["test_first"]))
    print("Rows:  %s" % split_coverage(df, split))
    _assert_split_applies(df, split, "dataset")
    # The split is built from the stat dataset and then applied to the minutes
    # dataset, which is a separate output file a user can rebuild on its own.
    # Checked before the ~30-fit OOF loop rather than after: a stale minutes
    # file otherwise raises IndexError on final_preds[:, 0] at the very end, or
    # worse, pushes training_key_coverage above 1.0 without raising at all
    # (its numerator uses a boundary compare, its denominator membership).
    _assert_split_applies(minutes_df, split, "minutes dataset")

    minutes_pred_frame, minutes_metrics, heuristic_minutes_metrics, minutes_oof_metrics, minutes_model = _build_minutes_prediction_frame(
        minutes_df,
        split=split,
    )
    _assert_oof_coverage(minutes_oof_metrics)
    df = _attach_modeled_minutes_features(df, minutes_pred_frame)
    _assert_modeled_minutes_attached(df, split)
    df = _engineer_features(df)

    feature_cols = _derive_feature_columns(df)
    if args.unified and "stat_type" not in feature_cols:
        feature_cols = ["stat_type"] + feature_cols

    # The prediction above drove the contract check, so a mismatch here would
    # mean the check passed on a different feature set than the one trained.
    if set(feature_cols) != set(predicted_features):
        missing = sorted(set(predicted_features) - set(feature_cols))
        extra = sorted(set(feature_cols) - set(predicted_features))
        raise SystemExit(
            "Feature prediction disagreed with the built frame (missing %s, extra %s). "
            "_predict_feature_columns has drifted from _attach_modeled_minutes_features "
            "or _engineer_features; fix it rather than skipping the contract check."
            % (missing, extra)
        )

    base_cat_features = ["player_id", "team", "opponent"]
    cat_cols_in_use = [c for c in (base_cat_features + (["stat_type"] if args.unified else []))
                       if c in feature_cols]
    cat_indices = [feature_cols.index(c) for c in cat_cols_in_use]

    manifest = {
        "dataset": str(dataset_csv.resolve()),
        "minutes_dataset": str(minutes_dataset_csv.resolve()),
        "model_dir": str(model_dir.resolve()),
        "feature_columns": feature_cols,
        "cat_features": cat_cols_in_use,
        "params": {
            "iterations": args.iterations, "depth": args.depth,
            "learning_rate": args.learning_rate, "l2_leaf_reg": args.l2_leaf_reg,
            "seed": args.seed, "unified": args.unified, "walk_forward": args.walk_forward,
        },
        "minutes_model": {
            "model_file": MINUTES_MODEL_FILE_NAME,
            "metadata_file": MINUTES_MODEL_METADATA_FILE_NAME,
            "quantiles_file": MINUTES_MODEL_QUANTILES_FILE_NAME,
            "feature_columns": MINUTES_MODEL_FEATURE_COLUMNS,
            "cat_features": MINUTES_MODEL_CATEGORICAL_FEATURES,
            "test_metrics": minutes_metrics,
            "heuristic_baseline_metrics": heuristic_minutes_metrics,
            "oof_metrics": minutes_oof_metrics,
        },
        "promotion_guardrail": promotion_guardrail,
        "stat_models": {},
    }
    model_files: Dict[str, str] = {}
    quantile_stats: Dict[str, Dict[str, Any]] = {}
    per_stat_test_metrics: Dict[str, Dict[str, Any]] = {}
    per_stat_params: Dict[str, Dict[str, Any]] = {}
    per_stat_current_season: Dict[str, Dict[str, Any]] = {}
    oof_prediction_frames: List[pd.DataFrame] = []
    shipped_fits: set = set()

    _atomic_save_catboost_model(minutes_model, model_dir / MINUTES_MODEL_FILE_NAME)
    _atomic_write_json(model_dir / MINUTES_MODEL_QUANTILES_FILE_NAME, minutes_metrics)
    minutes_metadata_payload = {
        "model_type": "minutes_multiquantile",
        "quantile_alphas": STAT_MODEL_QUANTILE_ALPHAS,
        "target": MINUTES_MODEL_TARGET_COLUMN,
        "feature_columns": MINUTES_MODEL_FEATURE_COLUMNS,
        "cat_features": MINUTES_MODEL_CATEGORICAL_FEATURES,
        "model_file": MINUTES_MODEL_FILE_NAME,
        "test_metrics": minutes_metrics,
        "heuristic_baseline_metrics": heuristic_minutes_metrics,
        "oof_metrics": minutes_oof_metrics,
        "modeled_minutes_features": MODELED_MINUTES_FEATURE_COLUMNS,
        "promotion_guardrail": promotion_guardrail,
    }
    print(f"Minutes model test metrics: {minutes_metrics}")
    print(f"Heuristic minutes baseline: {heuristic_minutes_metrics}")
    print(f"Minutes model OOF coverage: {minutes_oof_metrics}")

    # Optional: load prop lines for backtest hit-rate evaluation
    prop_df = None
    if args.backtest_lines_csv and Path(args.backtest_lines_csv).exists():
        prop_df = pd.read_csv(args.backtest_lines_csv)
        prop_df["game_date"] = pd.to_datetime(prop_df["game_date"], errors="coerce")
        print(f"Loaded {len(prop_df):,} prop rows for backtest hit-rate evaluation")

    if args.walk_forward and not args.unified:
        raise SystemExit(
            "--walk-forward only applies to --unified. The per-stat path ignored it "
            "silently while recording walk_forward: true in the manifest, so the flag "
            "now has to be an error rather than a no-op."
        )

    if args.unified:
        print(f"\nUNIFIED REGRESSION: {len(df):,} rows")
        stat_type_label = "unified"

        if args.walk_forward:
            result = _walk_forward_regression(df, feature_cols, cat_cols_in_use, cat_indices, args, stat_type_label)
        else:
            train_df = df[df["game_date"] < split_date]
            test_df = df[df["game_date"] >= split_date]
            r = _train_and_eval(train_df, test_df, feature_cols, cat_cols_in_use, cat_indices, args, stat_type_label)
            result = {"model": r["model"], "metrics": r["metrics"],
                      "all_preds": r["y_pred"], "all_actuals": r["y_test"]}

        if result:
            m = result["metrics"]
            print(f"  MAE={m['mae']}  RMSE={m['rmse']}  R²={m['r2']}  "
                  f"Median_AE={m['median_ae']}")

            model_path = model_dir / "unified_regression.cbm"
            _atomic_save_catboost_model(result["model"], model_path)

            # Feature importance
            try:
                imps = result["model"].get_feature_importance()
                imp_pairs = sorted(zip(feature_cols, imps), key=lambda x: -x[1])
                print(f"  Top features: {[f'{n}={round(i,1)}' for n,i in imp_pairs[:10]]}")
                _atomic_write_text(
                    model_dir / "unified_regression.feature_importance.json",
                    json.dumps([{"feature": n, "importance": round(float(i), 4)} for n, i in imp_pairs], indent=2),
                )
            except Exception:
                pass

            meta = {"stat_type": stat_type_label, "metrics": m, "feature_columns": feature_cols}
            _atomic_write_json(model_dir / "unified_regression.metadata.json", meta)
            manifest["stat_models"][stat_type_label] = {"metrics": m}
            model_files[stat_type_label] = model_path.name
            per_stat_test_metrics[stat_type_label] = m
            quantile_stats[stat_type_label] = {
                "std": m.get("residual_std"),
                "mae": m.get("mae"),
                "rmse": m.get("rmse"),
                "bias": m.get("bias"),
                "mean_actual": m.get("mean_actual"),
                "mean_predicted": m.get("mean_predicted"),
            }
    else:
        available_stats = sorted(df["stat_type"].dropna().unique())
        wanted_stats = list(args.stats) if args.stats else available_stats
        unknown = [s for s in wanted_stats if s not in available_stats]
        if unknown:
            raise SystemExit(
                "--stats named %s, which the dataset does not contain. Available: %s"
                % (", ".join(unknown), ", ".join(available_stats))
            )

        for stat_type in wanted_stats:
            stat_df = df[df["stat_type"] == stat_type]
            print(f"\n{stat_type}: {len(stat_df):,} rows")

            # Built from the shared split, so a stat missing a date cannot
            # absorb it from the neighbouring block.
            frames = {name: split.frame(stat_df, name) for name in ("train", "val", "test")}
            frames["all"] = stat_df
            result = _train_stat_quantile_head(
                stat_type, frames, feature_cols, cat_cols_in_use, cat_indices,
                STAT_MODEL_QUANTILE_ALPHAS, args, split,
            )
            if result is None:
                continue

            test_m = result["test"]
            baseline = (test_m.get("baselines") or {}).get("recent10") or {}
            delta = baseline.get("delta")
            print("  MAE(q50)=%.4f  bias=%+.4f  R2=%.4f  pinball50=%.4f  trees=%d"
                  % (test_m["mae_q50"], test_m["q50_bias"], test_m["r2_q50"] or float("nan"),
                     test_m["pinball"][str(STAT_MODEL_QUANTILE_ALPHAS[1])], result["tree_count"]))
            if delta is not None:
                verdict = "beats" if delta < 0 else "LOSES TO"
                print("  vs recent10 baseline: %s it by %+.4f MAE (n=%d)"
                      % (verdict, delta, baseline.get("n") or 0))
            band = test_m.get("band") or {}
            print("  band %.2f nominal: coverage %.3f continuous / %.3f discrete, width %.2f"
                  % (band.get("nominal") or 0.0,
                     band.get("coverage_continuous") or float("nan"),
                     band.get("coverage_discrete") or float("nan"),
                     band.get("median_width") or float("nan")))
            cs = result.get("current_season")
            if cs:
                cs_base = (cs.get("baselines") or {}).get("recent10") or {}
                cs_delta = cs_base.get("delta")
                print("  CURRENT SEASON %s (out-of-fold, %d block(s), %d/%d dates): "
                      "n=%d  MAE=%.4f  bias=%+.4f  vs rec10 %s"
                      % (cs.get("season"), cs.get("blocks") or 0,
                         cs.get("dates_covered") or 0, cs.get("dates_in_season") or 0,
                         cs.get("n") or 0, cs.get("mae_q50") or float("nan"),
                         cs.get("q50_bias") or float("nan"),
                         ("%+.4f" % cs_delta) if cs_delta is not None else "n/a"))
            elif not args.no_current_season_eval:
                print("  CURRENT SEASON: no walk-forward block fitted -- the season is "
                      "too short for a block of %d dates with enough prior history"
                      % args.block_dates)
            print("  shipped weights: %s (%d rows)"
                  % (result["shipped_fit"], result["rows"]["shipped_fit_rows"]))

            segments = test_m.get("segments") or {}
            if len(segments) > 1:
                for label in sorted(segments):
                    seg_m = segments[label]
                    seg_base = (seg_m.get("baselines") or {}).get("recent10") or {}
                    seg_delta = seg_base.get("delta")
                    print("    season %-8s n=%5d  MAE=%.4f  bias=%+.4f  vs rec10 %s"
                          % (label, seg_m.get("n") or 0,
                             seg_m.get("mae_q50") or float("nan"),
                             seg_m.get("q50_bias") or float("nan"),
                             ("%+.4f" % seg_delta) if seg_delta is not None else "n/a"))

            model_path = model_dir / _stat_model_filename(stat_type)
            _atomic_save_catboost_model(result["model"], model_path)

            manifest["stat_models"][stat_type] = {
                "params": result["params"],
                "best_iteration": result["best_iteration"],
                "tree_count": result["tree_count"],
                "shipped_fit": result["shipped_fit"],
                "rows": result["rows"],
                "test": test_m,
                "val": result["val"],
                "current_season": result.get("current_season"),
            }
            if result.get("current_season"):
                per_stat_current_season[stat_type] = result["current_season"]
            if result.get("oof_predictions") is not None:
                oof_prediction_frames.append(result["oof_predictions"])
            shipped_fits.add(result["shipped_fit"])
            model_files[stat_type] = model_path.name
            per_stat_test_metrics[stat_type] = test_m
            per_stat_params[stat_type] = result["params"]
            # The shape ml_inference reads, kept to the keys it looks up plus
            # the two the old bundle was missing. Note there is still no "std"
            # key: ml_inference._single_output_probability looks one up and
            # falls back to 1.0, which is why that Gaussian branch must stay
            # unreachable for a multiquantile bundle.
            quantile_stats[stat_type] = {
                "median_iqr": (test_m.get("band") or {}).get("median_width"),
                "mae_q50": test_m.get("mae_q50"),
                "q50_bias": test_m.get("q50_bias"),
                "iqr_coverage": (test_m.get("band") or {}).get("coverage_continuous"),
                "iqr_coverage_discrete": (test_m.get("band") or {}).get("coverage_discrete"),
            }

    manifest["model_files"] = model_files

    drift_baseline_source = df[df["game_date"] < split_date].copy()
    if drift_baseline_source.empty:
        drift_baseline_source = df.copy()
    feature_drift_baseline = _build_feature_drift_baseline(
        drift_baseline_source,
        feature_cols=feature_cols,
        cat_cols=cat_cols_in_use,
    )

    export_meta = {
        "model_type": "per_stat_multiquantile",
        "quantile_alphas": STAT_MODEL_QUANTILE_ALPHAS,
        # The dataset's own column name. The old bundle recorded
        # "actual_stat_value", which exists in no dataset the builder emits.
        "target": TARGET_COLUMN,
        "feature_columns": feature_cols,
        "cat_features": cat_cols_in_use,
        "engineered_features": [col for col in ENGINEERED_FEATURE_COLUMNS if col in feature_cols],
        "modeled_minutes_features": MODELED_MINUTES_FEATURE_COLUMNS,
        "model_files": model_files,
        "minutes_model_file": MINUTES_MODEL_FILE_NAME,
        "minutes_model_metadata_file": MINUTES_MODEL_METADATA_FILE_NAME,
        "minutes_quantile_stats_file": MINUTES_MODEL_QUANTILES_FILE_NAME,
        "oof_predictions_file": OOF_PREDICTIONS_FILE,
        "minutes_test_metrics": minutes_metrics,
        "promotion_guardrail": promotion_guardrail,
        "per_stat_test_metrics": per_stat_test_metrics,
        # The in-regime number. per_stat_test_metrics' test block spans the
        # offseason (roughly 11 dates of last April plus 9 of the current
        # season), so it is mostly last season; this one is current-season
        # only, scored out-of-fold by walk-forward blocks.
        "per_stat_current_season_metrics": per_stat_current_season,
        "metrics_describe": (
            "per_stat_test_metrics and per_stat_current_season_metrics both describe "
            "models fitted on strictly prior data. The weights in this bundle were "
            "refitted on every row at the same iteration count, so they are not the "
            "exact models these metrics were measured on -- see provenance.shipped_fit."
            if shipped_fits and shipped_fits != {"holdout"} else
            "The weights in this bundle are the holdout-fitted models these metrics "
            "were measured on. They exclude the newest 35 dates."
        ),
        # Provenance. Previously absent, which is how a bundle came to carry
        # metrics from a different model than its own weights: the .cbm files
        # were committed 2026-04-11 while model_metadata.json was rewritten
        # 2026-11-05, and nothing recorded that they had diverged.
        "provenance": {
            "trained_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
            "git": _git_state(),
            "trainer": "prop_modeling/train_regression_model.py",
            "dataset": _dataset_fingerprint(
                dataset_csv, len(df), df["game_date"].dt.date.nunique()
            ),
            "minutes_dataset": _dataset_fingerprint(
                minutes_dataset_csv, len(minutes_df),
                minutes_df["game_date"].dt.date.nunique(),
            ),
            "split": split.provenance(),
            "tuning": {
                "enabled": bool(args.tune),
                "trials_per_stat": int(args.tune_trials) if args.tune else 0,
                "objective": "sum of pinball loss across alphas on the validation block",
                "max_iterations": int(args.max_iterations),
            },
            "per_stat_params": per_stat_params,
            "shipped_fit": sorted(shipped_fits),
            "current_season_eval": {
                "enabled": not args.no_current_season_eval,
                "block_dates": int(args.block_dates),
            },
            "feature_contract": feature_contract,
            "season_type_rows": season_type_counts,
            "include_playoffs": bool(args.include_playoffs),
            "seed": int(args.seed),
        },
        # Deliberately no cv_results. The old bundle carried
        # {mean_mae 2.7589, mean_r2 0.7753} from walk_forward_cv, which fits
        # loss_function="RMSE" on the unified feature set -- a different model
        # and a different loss to the eleven heads it was written beside -- and
        # averages MAE across markets whose scale runs 0.46 to 5.9. Per-stat
        # metrics with a named baseline live in per_stat_test_metrics instead.
        "usage": (
            "Load the per-stat .cbm for the relevant stat_type. "
            "model.predict(pool) returns shape (n, %d): %s. Sort then clamp at "
            "zero before use (ml_inference._sanitize_quantiles), then pass to "
            "p_over_calibration.honest_p_over for the probability and "
            "ranking_signal for the ordering. per_stat_test_metrics carries "
            "baselines.recent10.delta -- positive means the head loses to the "
            "player's own recent-10 average on the held-out block."
            % (len(STAT_MODEL_QUANTILE_ALPHAS),
               "[" + ", ".join("q%d" % int(a * 100) for a in STAT_MODEL_QUANTILE_ALPHAS) + "]")
        ),
    }

    manifest_path = model_dir / "manifest.json"
    _atomic_write_json(manifest_path, manifest)
    _atomic_write_json(model_dir / "feature_drift_baseline.json", feature_drift_baseline)

    if args.unified:
        # --unified fits one RMSE model across every stat type. It is a
        # diagnostic -- it is where the old bundle's mean_r2 0.7753 came from --
        # and it must not write the serve-path artifacts, because a
        # model_metadata.json stamped "per_stat_multiquantile" pointing at a
        # single-output RMSE model is exactly the mismatch this rewrite exists
        # to make impossible. ml_inference would load it and every prediction
        # would take the dead Gaussian branch, whose sigma resolves to 1.0.
        print("\n--unified is a diagnostic: skipping model_metadata.json, "
              "quantile_stats.json and the minutes metadata.")
        print(f"manifest={manifest_path}")
        return 0

    _atomic_write_json(model_dir / MINUTES_MODEL_METADATA_FILE_NAME, minutes_metadata_payload)
    _atomic_write_json(model_dir / "quantile_stats.json", quantile_stats)
    if oof_prediction_frames:
        oof_path = model_dir / OOF_PREDICTIONS_FILE
        oof = pd.concat(oof_prediction_frames, ignore_index=True)
        tmp = oof_path.with_suffix(".csv.tmp")
        oof.to_csv(tmp, index=False)
        tmp.replace(oof_path)
        print("out-of-fold predictions: %d rows -> %s (%s)"
              % (len(oof), oof_path.name,
                 ", ".join("%s=%d" % kv for kv in
                           sorted(oof["source"].value_counts().items()))))
        print("  gate these with: python3 prop_modeling/backtest.py --model "
              "--predictions-csv %s" % oof_path)
    _atomic_write_json(model_dir / "model_metadata.json", export_meta)
    print(f"\nmanifest={manifest_path}")
    _print_bundle_summary(per_stat_test_metrics)
    print(f"trained_models={len(manifest['stat_models'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
