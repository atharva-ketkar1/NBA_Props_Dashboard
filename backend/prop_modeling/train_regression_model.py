"""Train regression models to predict actual stat values, then derive prop hit probabilities.

The key idea: instead of training a classifier on ~5k prop rows to predict
hit/miss directly, train a regressor on ~400k+ game-log rows to predict the
actual stat value.  At inference time:

  predicted_value > line  →  lean over
  predicted_value < line  →  lean under

The regression residuals give us a natural confidence/probability estimate.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from feature_schema import GENERATED_DIR, DEFAULT_MINUTES_DATASET_PATH
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


DEFAULT_REGRESSION_DATASET_PATH = GENERATED_DIR / "regression_training_dataset.csv"
DEFAULT_REGRESSION_MODEL_DIR = GENERATED_DIR / "regression_models"
TARGET_COLUMN = "actual_value"
ENGINEERED_FEATURE_COLUMNS = [
    "momentum_diff_5v20",
    "momentum_diff_3v10",
    "expected_possessions",
    "predicted_minutes",
]
NON_FEATURE_COLUMNS = {"game_date", "game_id", TARGET_COLUMN}


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
    p.add_argument("--test-date-count", type=int, default=20,
                   help="Number of most recent dates held out for evaluation.")
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
    df = pd.read_csv(path)
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


def _train_minutes_quantile_model(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> Tuple[CatBoostRegressor, np.ndarray]:
    cat_indices = [
        MINUTES_MODEL_FEATURE_COLUMNS.index(column)
        for column in MINUTES_MODEL_CATEGORICAL_FEATURES
        if column in MINUTES_MODEL_FEATURE_COLUMNS
    ]
    X_train = _prepare_features(train_df, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
    X_test = _prepare_features(test_df, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
    y_train = train_df[MINUTES_MODEL_TARGET_COLUMN].to_numpy()
    w_train = make_sample_weights(train_df)

    train_pool = Pool(X_train, label=y_train, cat_features=cat_indices, weight=w_train)
    test_pool = Pool(X_test, label=test_df[MINUTES_MODEL_TARGET_COLUMN].to_numpy(), cat_features=cat_indices)

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
    model.fit(train_pool, eval_set=test_pool, use_best_model=True, early_stopping_rounds=50)
    return model, model.predict(test_pool)


def _build_minutes_prediction_frame(
    minutes_df: pd.DataFrame,
    *,
    split_date: pd.Timestamp,
) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float], CatBoostRegressor]:
    oof_frames: List[pd.DataFrame] = []
    all_dates = sorted(minutes_df["game_date"].dt.date.unique())
    cat_indices = [
        MINUTES_MODEL_FEATURE_COLUMNS.index(column)
        for column in MINUTES_MODEL_CATEGORICAL_FEATURES
        if column in MINUTES_MODEL_FEATURE_COLUMNS
    ]

    for block_start in range(MINUTES_OOF_MIN_TRAIN_DATES, len(all_dates), MINUTES_OOF_BLOCK_DATES):
        pred_dates = all_dates[block_start:block_start + MINUTES_OOF_BLOCK_DATES]
        if not pred_dates:
            continue
        train_dates = all_dates[:block_start]
        train_block = minutes_df[minutes_df["game_date"].dt.date.isin(train_dates)].copy()
        pred_block = minutes_df[
            minutes_df["game_date"].dt.date.isin(pred_dates)
            & (minutes_df["game_date"] < split_date)
        ].copy()
        if train_block.empty or pred_block.empty:
            continue

        X_train = _prepare_features(train_block, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
        X_pred = _prepare_features(pred_block, MINUTES_MODEL_FEATURE_COLUMNS, MINUTES_MODEL_CATEGORICAL_FEATURES)
        y_train = train_block[MINUTES_MODEL_TARGET_COLUMN].to_numpy()
        w_train = make_sample_weights(train_block)

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
        model.fit(train_pool, verbose=False)
        preds = model.predict(pred_pool)

        oof_frame = pred_block[["game_date", "player_id", "game_id"]].copy()
        oof_frame["modeled_minutes_q25"] = preds[:, 0]
        oof_frame["modeled_minutes_q50"] = preds[:, 1]
        oof_frame["modeled_minutes_q75"] = preds[:, 2]
        oof_frames.append(oof_frame)

    train_minutes = minutes_df[minutes_df["game_date"] < split_date].copy()
    test_minutes = minutes_df[minutes_df["game_date"] >= split_date].copy()
    final_model, final_preds = _train_minutes_quantile_model(train_minutes, test_minutes)
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

    heuristic_source = test_minutes.copy()
    heuristic_pred = (
        pd.to_numeric(heuristic_source.get("recent5_minutes_avg"), errors="coerce").fillna(
            pd.to_numeric(heuristic_source.get("season_minutes_avg"), errors="coerce")
        ) * 0.50
        + pd.to_numeric(heuristic_source.get("recent10_minutes_avg"), errors="coerce").fillna(
            pd.to_numeric(heuristic_source.get("season_minutes_avg"), errors="coerce")
        ) * 0.30
        + pd.to_numeric(heuristic_source.get("season_minutes_avg"), errors="coerce").fillna(
            pd.to_numeric(heuristic_source.get("recent5_minutes_avg"), errors="coerce")
        ) * 0.20
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
    return pred_frame, minutes_metrics, heuristic_metrics, final_model


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

    output_rows: List[Dict[str, Any]] = []
    for row in merged.to_dict("records"):
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
        output_rows.append(row)
    return pd.DataFrame(output_rows)


def _regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, Any]:
    return {
        "mae": _safe_metric(mean_absolute_error(y_true, y_pred)),
        "rmse": _safe_metric(math.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": _safe_metric(r2_score(y_true, y_pred)),
        "median_ae": _safe_metric(float(np.median(np.abs(y_true - y_pred)))),
        "mean_actual": _safe_metric(float(y_true.mean())),
        "mean_predicted": _safe_metric(float(y_pred.mean())),
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

    train_pool = Pool(X_train, label=y_train, cat_features=cat_indices)
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
    model.fit(train_pool, eval_set=test_pool, use_best_model=True, early_stopping_rounds=50)

    y_pred = model.predict(test_pool)
    metrics = _regression_metrics(y_test, y_pred)
    metrics["stat_type"] = stat_type
    metrics["train_rows"] = len(train_df)
    metrics["test_rows"] = len(test_df)
    metrics["best_iteration"] = int(model.get_best_iteration() or args.iterations)

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

    unique_dates = sorted(df["game_date"].dt.date.unique())
    split_idx = max(0, len(unique_dates) - args.test_date_count)
    split_date = pd.Timestamp(unique_dates[split_idx])

    minutes_pred_frame, minutes_metrics, heuristic_minutes_metrics, minutes_model = _build_minutes_prediction_frame(
        minutes_df,
        split_date=split_date,
    )
    df = _attach_modeled_minutes_features(df, minutes_pred_frame)

    feature_cols = _derive_feature_columns(df)
    if args.unified and "stat_type" not in feature_cols:
        feature_cols = ["stat_type"] + feature_cols

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
        },
        "stat_models": {},
    }

    minutes_model.save_model(str(model_dir / MINUTES_MODEL_FILE_NAME))
    (model_dir / MINUTES_MODEL_METADATA_FILE_NAME).write_text(
        json.dumps(
            {
                "target": MINUTES_MODEL_TARGET_COLUMN,
                "feature_columns": MINUTES_MODEL_FEATURE_COLUMNS,
                "cat_features": MINUTES_MODEL_CATEGORICAL_FEATURES,
                "model_file": MINUTES_MODEL_FILE_NAME,
                "test_metrics": minutes_metrics,
                "heuristic_baseline_metrics": heuristic_minutes_metrics,
                "modeled_minutes_features": MODELED_MINUTES_FEATURE_COLUMNS,
            },
            indent=2,
            sort_keys=True,
        )
    )
    (model_dir / MINUTES_MODEL_QUANTILES_FILE_NAME).write_text(
        json.dumps(minutes_metrics, indent=2, sort_keys=True)
    )
    print(f"Minutes model test metrics: {minutes_metrics}")
    print(f"Heuristic minutes baseline: {heuristic_minutes_metrics}")

    # Optional: load prop lines for backtest hit-rate evaluation
    prop_df = None
    if args.backtest_lines_csv and Path(args.backtest_lines_csv).exists():
        prop_df = pd.read_csv(args.backtest_lines_csv)
        prop_df["game_date"] = pd.to_datetime(prop_df["game_date"], errors="coerce")
        print(f"Loaded {len(prop_df):,} prop rows for backtest hit-rate evaluation")

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
            result["model"].save_model(str(model_path))

            # Feature importance
            try:
                imps = result["model"].get_feature_importance()
                imp_pairs = sorted(zip(feature_cols, imps), key=lambda x: -x[1])
                print(f"  Top features: {[f'{n}={round(i,1)}' for n,i in imp_pairs[:10]]}")
                (model_dir / "unified_regression.feature_importance.json").write_text(
                    json.dumps([{"feature": n, "importance": round(float(i), 4)} for n, i in imp_pairs], indent=2)
                )
            except Exception:
                pass

            meta = {"stat_type": stat_type_label, "metrics": m, "feature_columns": feature_cols}
            (model_dir / "unified_regression.metadata.json").write_text(
                json.dumps(meta, indent=2, sort_keys=True)
            )
            manifest["stat_models"][stat_type_label] = {"metrics": m}
    else:
        for stat_type in sorted(df["stat_type"].dropna().unique()):
            stat_df = df[df["stat_type"] == stat_type]
            print(f"\n{stat_type}: {len(stat_df):,} rows")

            if args.walk_forward:
                result = _walk_forward_regression(stat_df, feature_cols, cat_cols_in_use, cat_indices, args, stat_type)
            else:
                stat_dates = sorted(stat_df["game_date"].dt.date.unique())
                if len(stat_dates) < 5:
                    print(f"  SKIP: only {len(stat_dates)} dates")
                    continue
                train_df = stat_df[stat_df["game_date"] < split_date]
                test_df = stat_df[stat_df["game_date"] >= split_date]
                if len(train_df) < 100:
                    print(f"  SKIP: only {len(train_df)} train rows")
                    continue
                r = _train_and_eval(train_df, test_df, feature_cols, cat_cols_in_use, cat_indices, args, stat_type)
                result = {"model": r["model"], "metrics": r["metrics"],
                          "all_preds": r["y_pred"], "all_actuals": r["y_test"]}

            if result is None:
                print(f"  SKIP: insufficient data")
                continue

            m = result["metrics"]
            print(f"  MAE={m['mae']}  RMSE={m['rmse']}  R²={m['r2']}  "
                  f"Median_AE={m['median_ae']}")

            slug = stat_type.lower().replace("+", "_plus_")
            model_path = model_dir / f"{slug}_regression.cbm"
            result["model"].save_model(str(model_path))

            meta = {"stat_type": stat_type, "metrics": m, "feature_columns": feature_cols}
            (model_dir / f"{slug}_regression.metadata.json").write_text(
                json.dumps(meta, indent=2, sort_keys=True)
            )
            manifest["stat_models"][stat_type] = {"metrics": m}

    # Save manifest
    manifest_path = model_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"\nmanifest={manifest_path}")
    print(f"trained_models={len(manifest['stat_models'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
