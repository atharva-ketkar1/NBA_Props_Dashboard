"""Scoring a quantile model, per stat, against a baseline it has to beat.

Why this exists: the shipped bundle's headline numbers were `cv_results`
{mean_mae 2.7589, mean_r2 0.7753}, which average across eleven markets whose
scale runs from 0.46 (BLK) to 5.9 (PTS+REB+AST). A mean MAE over those is not a
quantity -- it mostly reports how many low-count stats are in the mix -- and it
came from a different loss function on a different feature set than the models
it was written beside. So "is this model good" had no answer.

This module answers it three ways, per stat:

  * pinball loss, which is the loss the model actually minimises and the only
    metric that scores all three quantiles rather than just the median,
  * MAE against an explicit baseline -- the player's own recent-10 average, and
    the closing line where one exists. A model that cannot beat recent-10 is
    not a model, and one that cannot beat the line has no betting edge. Measured
    on 2026-27 rows the shipped bundle loses to the line on every PTS market,
  * interval coverage both ways, because neither way is right on its own.

On that last point: `quantile_stats.json` reports BLK coverage of 0.132 and
`backtest.interval_coverage` documents why that figure is an artifact -- a band
of median width 1.03 judged against a variable taking the values 0, 1, 2. Both
conventions are reported here precisely so the artifact is visible instead of
being picked. Measured OOS, continuous coverage is 0.44-0.47 on the big markets
(bands genuinely too narrow) while discrete coverage is 0.53-0.89 (meaningless
on the low-count stats). A single number would hide one or the other.

Pure numpy, and `interval_coverage` is imported rather than re-implemented --
two copies of a calibration metric is how the harness stops measuring what
ships.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Sequence

import numpy as np

from backtest import interval_coverage

# What the shipped bundle trains. Phase 2 widens this; everything here is
# written against an arbitrary ascending list so that change is a parameter.
DEFAULT_ALPHAS = (0.25, 0.5, 0.75)


def _finite_mask(*arrays: np.ndarray) -> np.ndarray:
    mask = np.ones(len(arrays[0]), dtype=bool)
    for arr in arrays:
        mask &= np.isfinite(arr)
    return mask


def _safe(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return round(out, 6) if math.isfinite(out) else None


def pinball_loss(y_true: Sequence[float], pred: Sequence[float], alpha: float) -> Optional[float]:
    """Mean quantile (pinball) loss at one alpha. Lower is better.

    This is what `MultiQuantile:alpha=...` minimises, so it is the metric that
    answers "did the fit improve" for all three heads at once. MAE only scores
    the median, which is why a retrain can tighten the median while quietly
    widening the band and still look better.
    """
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(pred, dtype=float)
    mask = _finite_mask(y, p)
    if not mask.any():
        return None
    diff = y[mask] - p[mask]
    return _safe(np.mean(np.maximum(alpha * diff, (alpha - 1.0) * diff)))


def calibration_table(
    y_true: Sequence[float],
    preds: np.ndarray,
    alphas: Sequence[float] = DEFAULT_ALPHAS,
) -> list:
    """Per-alpha nominal vs empirical P(y <= q_alpha).

    A calibrated head has empirical == nominal at every alpha. This is the
    table the Phase 2 isotonic step is fitted against, and reporting it per
    alpha rather than as one interval shows *where* a band is wrong -- a median
    that is biased low and a band that is too narrow look identical in a single
    coverage number.
    """
    y = np.asarray(y_true, dtype=float)
    preds = np.asarray(preds, dtype=float)
    rows = []
    for idx, alpha in enumerate(alphas):
        column = preds[:, idx]
        mask = _finite_mask(y, column)
        if not mask.any():
            rows.append({"alpha": alpha, "n": 0, "empirical": None, "gap": None})
            continue
        empirical = float(np.mean(y[mask] <= column[mask]))
        rows.append({
            "alpha": alpha,
            "n": int(mask.sum()),
            "empirical": _safe(empirical),
            "gap": _safe(empirical - alpha),
        })
    return rows


def baseline_comparison(
    y_true: Sequence[float],
    q50: Sequence[float],
    baselines: Dict[str, Sequence[float]],
) -> Dict[str, Dict[str, Optional[float]]]:
    """Model MAE and bias against each named baseline, on rows both can score.

    Restricted to the rows where the baseline is finite, so the comparison is
    like-for-like: scoring the model on all rows and the closing-line baseline
    on the ~12% that have a line would flatter whichever had the easier subset.
    """
    y = np.asarray(y_true, dtype=float)
    model = np.asarray(q50, dtype=float)
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for name, values in baselines.items():
        base = np.asarray(values, dtype=float)
        mask = _finite_mask(y, model, base)
        if not mask.any():
            out[name] = {"n": 0, "model_mae": None, "baseline_mae": None,
                         "delta": None, "model_bias": None, "baseline_bias": None}
            continue
        ym, mm, bm = y[mask], model[mask], base[mask]
        model_mae = float(np.mean(np.abs(mm - ym)))
        base_mae = float(np.mean(np.abs(bm - ym)))
        out[name] = {
            "n": int(mask.sum()),
            "model_mae": _safe(model_mae),
            "baseline_mae": _safe(base_mae),
            # Negative delta means the model wins. The shipped bundle is
            # positive against the closing line on every PTS market.
            "delta": _safe(model_mae - base_mae),
            "model_bias": _safe(np.mean(mm - ym)),
            "baseline_bias": _safe(np.mean(bm - ym)),
        }
    return out


def quantile_report(
    y_true: Sequence[float],
    preds: np.ndarray,
    *,
    alphas: Sequence[float] = DEFAULT_ALPHAS,
    baselines: Optional[Dict[str, Sequence[float]]] = None,
) -> Dict[str, Any]:
    """Everything worth recording about one stat's quantile head.

    `preds` is (n, len(alphas)) as CatBoost MultiQuantile returns it, columns
    ascending in alpha -- the same contract `backtest.score_dataset` checks
    before it will score a bundle.
    """
    y = np.asarray(y_true, dtype=float)
    preds = np.asarray(preds, dtype=float)
    if preds.ndim != 2 or preds.shape[1] != len(alphas):
        raise ValueError(
            "preds has shape %s but %d alphas were given" % (preds.shape, len(alphas))
        )
    if list(alphas) != sorted(alphas):
        raise ValueError("alphas %s must be ascending or the columns are mislabelled" % (alphas,))

    median_idx = _median_index(alphas)
    q50 = preds[:, median_idx]
    mask = _finite_mask(y, q50)
    ym, qm = y[mask], q50[mask]

    report: Dict[str, Any] = {
        "n": int(mask.sum()),
        "mae_q50": _safe(np.mean(np.abs(qm - ym))) if mask.any() else None,
        "rmse_q50": _safe(math.sqrt(float(np.mean((qm - ym) ** 2)))) if mask.any() else None,
        "r2_q50": _r2(ym, qm) if mask.any() else None,
        # Signed, and the number to watch: every stat in the shipped bundle is
        # negative (the median under-predicts), against a closing line whose
        # own bias is -0.08 to +0.02.
        "q50_bias": _safe(np.mean(qm - ym)) if mask.any() else None,
        "pinball": {str(a): pinball_loss(y, preds[:, i], a) for i, a in enumerate(alphas)},
        "calibration": calibration_table(y, preds, alphas),
    }

    lo_idx, hi_idx = _band_indices(alphas)
    if lo_idx is not None and hi_idx is not None:
        lo, hi = preds[:, lo_idx], preds[:, hi_idx]
        band_mask = _finite_mask(y, lo, hi)
        report["band"] = {
            "lo_alpha": alphas[lo_idx],
            "hi_alpha": alphas[hi_idx],
            "nominal": _safe(alphas[hi_idx] - alphas[lo_idx]),
            "median_width": _safe(np.median((hi - lo)[band_mask])) if band_mask.any() else None,
            "coverage_continuous": _safe(
                interval_coverage(y[band_mask], lo[band_mask], hi[band_mask], discrete=False)
            ) if band_mask.any() else None,
            "coverage_discrete": _safe(
                interval_coverage(y[band_mask], lo[band_mask], hi[band_mask], discrete=True)
            ) if band_mask.any() else None,
            "crossed_rows": int(np.sum(lo[band_mask] > hi[band_mask])) if band_mask.any() else 0,
        }

    if baselines:
        report["baselines"] = baseline_comparison(y, q50, baselines)
    return report


def _median_index(alphas: Sequence[float]) -> int:
    """Where the median lives, or the nearest alpha to it.

    Not assumed to be index 1: Phase 2 moves to nine alphas and a hardcoded 1
    would silently start reporting the 0.15 quantile as the median.
    """
    for idx, alpha in enumerate(alphas):
        if abs(alpha - 0.5) < 1e-9:
            return idx
    return int(np.argmin([abs(a - 0.5) for a in alphas]))


def _band_indices(alphas: Sequence[float]):
    """The 0.25/0.75 band when present, else the outermost pair.

    The quartile preference keeps the reported coverage comparable to the
    shipped `quantile_stats.json` as the alpha list widens -- a nine-alpha set
    containing 0.25 and 0.75 still reports the same 0.50 band.

    The fallback is the OUTERMOST pair, not the innermost. That matters and is
    stated because the two readings differ: with an alpha set that omits the
    quartiles, say 0.1 to 0.9, `band.nominal` becomes 0.80 and
    `coverage_continuous` stops being comparable to anything measured on the
    0.50 band. Callers widening the alphas should keep 0.25 and 0.75 in the
    set, or read `band.nominal` rather than assuming it.
    """
    lookup = {round(a, 6): i for i, a in enumerate(alphas)}
    if 0.25 in lookup and 0.75 in lookup:
        return lookup[0.25], lookup[0.75]
    if len(alphas) >= 2:
        return 0, len(alphas) - 1
    return None, None


def _r2(y: np.ndarray, pred: np.ndarray) -> Optional[float]:
    denom = float(np.sum((y - y.mean()) ** 2))
    if denom <= 0:
        return None
    return _safe(1.0 - float(np.sum((y - pred) ** 2)) / denom)
