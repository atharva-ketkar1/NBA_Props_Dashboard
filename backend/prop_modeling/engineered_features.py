"""Trained features that `build_regression_dataset.py` does not emit.

Eight of the 117 columns the per-stat models consume are added *after* the
dataset builder runs, and they were added in two different places:

  * `momentum_diff_5v20`, `momentum_diff_3v10`, `expected_possessions` and
    `predicted_minutes` are computed inline in `regression_notebook.py` for
    training and again, by hand, in `utils/ml_inference.py` for serving.
  * the four `modeled_minutes_*` columns come from a separate minutes model,
    attached by the notebook at training time and predicted live at serve time.

Nothing else knew about them, which is why scoring the raw dataset offline
produced a model that looked signal-free: `reindex` filled all eight with NaN,
and `modeled_minutes_x_recent10_target_per_min` alone has a baseline mean of
7.301 against a target mean of 7.298 -- it is very nearly an estimator of the
answer. Predictions collapsed to sd 3.04 against an actual sd of 8.46.

This module is the single definition. The formulas mirror
`regression_notebook.py:484-520` and `:794-832`, because the notebook is what
the exported models were actually fit with, so it -- not the serve path -- is
the authority on what a feature means.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

PROP_MODELING_DIR = Path(__file__).resolve().parent
MODEL_DIR = PROP_MODELING_DIR / "exported_regression_model"

# Added after the dataset builder; see the module docstring.
POST_BUILD_FEATURES = (
    "momentum_diff_5v20",
    "momentum_diff_3v10",
    "expected_possessions",
    "predicted_minutes",
    "modeled_minutes_q50",
    "modeled_minutes_iqr",
    "modeled_minutes_delta_vs_recent5",
    "modeled_minutes_x_recent10_target_per_min",
)

B2B_MINUTES_PENALTY = 0.035
MINUTES_FLOOR, MINUTES_CEILING = 4.0, 42.0


def add_engineered_features(df):
    """The four non-minutes engineered columns, in place of two copies.

    Mirrors regression_notebook.py:484-515. Note `expected_possessions` fills a
    missing usage with 0 there, where ml_inference requires both inputs to be
    present -- the notebook's behaviour wins, since that is what was trained.
    """
    import pandas as pd

    out = df
    if {"recent5_stat_avg", "recent20_stat_avg"} <= set(out.columns):
        out["momentum_diff_5v20"] = (
            pd.to_numeric(out["recent5_stat_avg"], errors="coerce")
            - pd.to_numeric(out["recent20_stat_avg"], errors="coerce")
        )
    if {"recent3_stat_avg", "recent10_stat_avg"} <= set(out.columns):
        out["momentum_diff_3v10"] = (
            pd.to_numeric(out["recent3_stat_avg"], errors="coerce")
            - pd.to_numeric(out["recent10_stat_avg"], errors="coerce")
        )
    if {"team_pace", "season_usage_pct_avg"} <= set(out.columns):
        out["expected_possessions"] = pd.to_numeric(out["team_pace"], errors="coerce") * (
            pd.to_numeric(out["season_usage_pct_avg"], errors="coerce").fillna(0.0) / 100.0
        )
    min_cols = ["recent5_minutes_avg", "recent10_minutes_avg", "season_minutes_avg"]
    if all(c in out.columns for c in min_cols):
        r5 = pd.to_numeric(out["recent5_minutes_avg"], errors="coerce")
        r10 = pd.to_numeric(out["recent10_minutes_avg"], errors="coerce")
        sea = pd.to_numeric(out["season_minutes_avg"], errors="coerce")
        pred = r5.fillna(sea) * 0.50 + r10.fillna(sea) * 0.30 + sea.fillna(r5) * 0.20
        if "is_b2b" in out.columns:
            b2b = pd.to_numeric(out["is_b2b"], errors="coerce").fillna(0.0)
            pred = pred * (1.0 - B2B_MINUTES_PENALTY * b2b)
        out["predicted_minutes"] = pred.clip(lower=MINUTES_FLOOR, upper=MINUTES_CEILING)
    return out


def _fallback_iqr(df):
    """Vectorised twin of minutes_model_config.estimate_minutes_iqr."""
    import numpy as np
    import pandas as pd

    def num(col):
        return pd.to_numeric(df.get(col), errors="coerce") if col in df.columns else None

    center = num("modeled_minutes_q50")
    r5, r10, sea = num("recent5_minutes_avg"), num("recent10_minutes_avg"), num("season_minutes_avg")
    cv = num("minutes_cv_recent5")
    spread = pd.Series(1.0, index=df.index)
    for candidate in (
        (center * cv).abs() if center is not None and cv is not None else None,
        (r5 - r10).abs() if r5 is not None and r10 is not None else None,
        (r5 - sea).abs() if r5 is not None and sea is not None else None,
        (r10 - sea).abs() if r10 is not None and sea is not None else None,
    ):
        if candidate is not None:
            spread = np.fmax(spread, candidate)
    return pd.Series(spread, index=df.index).round(4)


def predict_minutes_quantiles(minutes_df, *, batch: int = 8192):
    """Score the exported minutes model. Returns the key columns plus q25/50/75."""
    import numpy as np
    import pandas as pd
    from catboost import CatBoostRegressor, Pool

    meta = json.loads((MODEL_DIR / "minutes_model_metadata.json").read_text())
    cbm = MODEL_DIR / str(meta.get("model_file") or "minutes_model.cbm")
    if not cbm.exists():
        raise FileNotFoundError("Minutes model missing: %s" % cbm)
    feat_cols: List[str] = list(meta["feature_columns"])
    cat_cols: List[str] = list(meta["cat_features"])
    missing = [c for c in feat_cols if c not in minutes_df.columns]
    if missing:
        raise RuntimeError(
            "Minutes dataset is missing %d trained features: %s"
            % (len(missing), ", ".join(sorted(missing)))
        )
    cat_idx = [feat_cols.index(c) for c in cat_cols if c in feat_cols]

    X = minutes_df.reindex(columns=feat_cols).copy()
    for c in feat_cols:
        if c in cat_cols:
            X[c] = X[c].fillna("UNKNOWN").astype(str)
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")

    model = CatBoostRegressor()
    model.load_model(str(cbm))
    chunks = [
        model.predict(Pool(X.iloc[i:i + batch], cat_features=cat_idx))
        for i in range(0, len(X), batch)
    ]
    preds = np.vstack(chunks)
    if preds.shape[1] != 3:
        raise RuntimeError("Minutes model returned %d outputs, expected 3" % preds.shape[1])
    # Same contract as the serve path: order first, then floor at zero.
    preds = np.clip(np.sort(preds, axis=1), 0.0, None)

    out = minutes_df[["game_date", "player_id", "game_id"]].copy()
    out["modeled_minutes_q25"] = preds[:, 0]
    out["modeled_minutes_q50"] = preds[:, 1]
    out["modeled_minutes_q75"] = preds[:, 2]
    return out


def attach_modeled_minutes(df, minutes_preds):
    """Merge minutes predictions and derive the dependent columns.

    Mirrors regression_notebook.py:794-832, including the fallback to the
    `predicted_minutes` blend on a merge miss -- which is what the training rows
    got whenever the minutes model had no prediction for them.
    """
    import pandas as pd

    merged = df.copy()
    for frame in (merged, minutes_preds):
        for key in ("player_id", "game_id", "game_date"):
            if key in frame.columns:
                frame[key] = frame[key].astype(str)
    merged = merged.merge(minutes_preds, on=["game_date", "player_id", "game_id"], how="left")

    fallback_q50 = pd.to_numeric(merged.get("predicted_minutes"), errors="coerce")
    merged["modeled_minutes_q50"] = pd.to_numeric(
        merged.get("modeled_minutes_q50"), errors="coerce"
    ).fillna(fallback_q50)

    q25 = pd.to_numeric(merged.get("modeled_minutes_q25"), errors="coerce")
    q75 = pd.to_numeric(merged.get("modeled_minutes_q75"), errors="coerce")
    modeled_iqr = (q75 - q25).where(q25.notna() & q75.notna())
    merged["modeled_minutes_iqr"] = modeled_iqr.fillna(_fallback_iqr(merged)).clip(lower=0.0)

    merged["modeled_minutes_delta_vs_recent5"] = merged["modeled_minutes_q50"] - pd.to_numeric(
        merged.get("recent5_minutes_avg"), errors="coerce"
    ).fillna(0.0)

    q50 = pd.to_numeric(merged["modeled_minutes_q50"], errors="coerce").fillna(0.0)
    for src, dest in (
        ("recent10_target_per_min", "modeled_minutes_x_recent10_target_per_min"),
        ("missing_key_teammates_player_target_per_min_delta",
         "modeled_minutes_q50_x_missing_key_teammates_player_target_per_min_delta"),
        ("returning_key_teammates_player_target_per_min_delta",
         "modeled_minutes_q50_x_returning_key_teammates_player_target_per_min_delta"),
    ):
        if src in merged.columns:
            merged[dest] = q50 * pd.to_numeric(merged[src], errors="coerce").fillna(0.0)
    return merged.drop(columns=["modeled_minutes_q25", "modeled_minutes_q75"], errors="ignore")


def complete_trained_features(df, minutes_df, *, required: Optional[Sequence[str]] = None):
    """Bring a builder-produced frame up to the full trained feature set."""
    out = add_engineered_features(df.copy())
    out = attach_modeled_minutes(out, predict_minutes_quantiles(minutes_df))
    if required:
        still_missing = [c for c in required if c not in out.columns]
        if still_missing:
            raise RuntimeError(
                "Features still missing after completion: %s" % ", ".join(sorted(still_missing))
            )
    return out
