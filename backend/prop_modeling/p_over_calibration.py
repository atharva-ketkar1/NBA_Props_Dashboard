"""One definition of how model quantiles become P(outcome > line).

This map existed twice -- inline in `edge_score._compute_ml_regression_context`
and again in `backtest._p_over_from_quantiles`, whose docstring claimed to
mirror it. Two copies of a number the harness is supposed to be measuring means
the harness can silently stop measuring what ships, which is the same failure
that made `engineered_features.py` necessary for the eight post-build features.

Pure stdlib on purpose: this runs in the serve path on a VM without scipy or
sklearn, the same reason `ml_inference.hit_probability` uses `math.erf`.
"""

import math
from typing import Any, Optional

# The shipped interpolation's constants, named so the fallback is traceable.
_LEGACY_TOP = 0.75
_LEGACY_SPAN = 0.50
_LEGACY_FLOOR = 0.10
_LEGACY_CEIL = 0.90
_DEGENERATE_IQR = 1e-6


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def legacy_p_over(q25: float, q50: float, q75: float, line: float) -> float:
    """The shipped linear interpolation across [q25, q75].

    line <= q25 gives ~0.75 and line >= q75 gives ~0.25, clamped to
    [0.10, 0.90] -- a clamp that never actually binds, since the output already
    spans only [0.25, 0.75].

    It does NOT give 0.50 at the median, which is the definitional error here:
    `frac` is measured from q25, so the output is 0.50 at the *midpoint* of
    [q25, q75]. Those coincide only for a symmetric distribution, so on
    skewed quantiles (18, 20, 30) a line sitting exactly on the median q50=20
    returns 0.667. 3.6% of rows fall in the affected band.

    Measured on 21,562 rows after the models' train cutoff it scores log-loss
    0.7103, worse than answering 0.5 every time (0.6931). It is not slightly
    off, it is confidently wrong: it spreads output across [0.25, 0.75] on a
    signal whose directional AUC is 0.53. It also saturates -- once `line`
    reaches q75 it pins at 0.25 and stops responding, so a line just past q75
    scores identically to one far past it.

    NOT what ships any more, and nothing falls back to it: `honest_p_over`
    replaced it in the serve path. It is retained so the backtest harness can
    report a "before" alongside the "after", and so these cases document
    exactly what was wrong.
    """
    iqr = q75 - q25
    if iqr > _DEGENERATE_IQR:
        # Argument order matches edge_score._clamp, which is max(lo, min(hi, v)).
        # min/max are order-sensitive with NaN -- max(nan, 0.0) is nan while
        # max(0.0, nan) is 0.0 -- so the obvious-looking min(max(v, lo), hi)
        # is a *different function* on a NaN line. The 200,006-case sweep that
        # checked this against both originals had no NaN in it.
        frac = max(0.0, min(1.0, (line - q25) / iqr))
        return max(_LEGACY_FLOOR, min(_LEGACY_CEIL, _LEGACY_TOP - frac * _LEGACY_SPAN))
    # Zero-width IQR (degenerate prediction) -- fall back to a median comparison.
    return 0.60 if q50 > line else 0.40


# ── The honest probability ───────────────────────────────────────────────────
# A pooled logistic on the raw gap (q50 - line), fitted on post-cutoff rows
# before the current season and evaluated on the current season alone
# (n=10,625). It scores log-loss 0.6912 against the shipped map's 0.7164 -- a
# 0.0252 improvement at 10.1 sigma -- and is statistically indistinguishable
# from a constant at the base rate (0.1 sigma).
#
# That last point is the finding, not a disappointment: no fitted probability
# beats a constant here. A monotone single-feature calibration was fitted per
# stat on three slices and 1 of 11 passed (PTS+REB+AST, 2.1 sigma), which needs
# 2.77 sigma under Bonferroni across nine tests and wanders between 1.3 and 2.3
# sigma as the fit boundary moves. So per-stat coefficients would be fitting
# noise, and these two pooled numbers live in source -- reviewable in git --
# rather than in an artifact that can be swapped silently.
#
# Pooled on raw gap on purpose: the same fit on gap/iqr scores 0.6941, which is
# worse than a constant. Ranking is the opposite way round -- see
# ranking_signal.
_HONEST_INTERCEPT = -0.0223
_HONEST_COEF_GAP = 0.0571

# The gap range the fit actually saw: |gap| is 7.6 at the 99th percentile and
# 11.9 at the 99.9th across both slices. A logistic extrapolates without bound,
# so an unclamped gap of 40 -- arithmetically reachable on a compound stat --
# would report 0.906 from a model whose directional AUC is 0.53. Clamping the
# input to the fit's support is the honest bound: inside it the coefficients
# mean something, outside it they are an extrapolation. This caps the output at
# [0.330, 0.660] -- asymmetric about 0.5 because the intercept carries the base
# rate, which favours unders.
_MAX_FIT_GAP = 12.0

# Unreachable while _MAX_FIT_GAP is 12.0, which holds |z| under 0.69. Kept so
# that widening or removing the gap clamp cannot produce a 0.0 or a 1.0, not
# because either bound shapes an answer today.
PROB_FLOOR, PROB_CEIL = 0.02, 0.98

# ── The ranking signal ───────────────────────────────────────────────────────
# Solved so 0.5 * tanh(gap_over_iqr / k) reproduces the legacy component's
# magnitude, and solved on fit+select only. Solving it on the evaluation slice
# gives 0.445, but then reporting "the composite does not move" on that same
# slice is circular -- the parity is true there by construction. On 0.485 the
# parity is an out-of-sample result: mean |score| 0.2378 against the legacy
# 0.2511 on rows the scale never saw, within 5%, correlation 0.974.
_RANKING_SCALE = 0.485
_RANKING_CAP = 0.5


def has_usable_spread(q25: float, q75: float) -> bool:
    """Whether the quantiles carry enough spread to rank with.

    A zero-width IQR has no scale to standardize a gap by, so `ranking_signal`
    returns a neutral 0.0 -- and a 0.0 score on an *available* component is the
    dilution trap, keeping ml_regression's full 0.25 in the renormalization
    denominator while contributing nothing. That is ~4.3 composite points worse
    than being dark, which is exactly why the single-output path is dark. The
    degenerate quantile case has the same problem and gets the same answer.

    Not currently reachable: 0 of 2,624 real predictions had a degenerate IQR.
    But `ml_inference._sanitize_quantiles` clamps negative quantiles to 0, and
    its own docstring records BLK producing negative quantiles on 38% of rows
    before that fix, so a retrain can reach it.
    """
    lo, hi = _finite(q25), _finite(q75)
    if lo is None or hi is None:
        return False
    return (hi - lo) > _DEGENERATE_IQR


def honest_p_over(q25: float, q50: float, q75: float, line: float) -> float:
    """P(outcome > line), calibrated rather than confident.

    Anchored at `q50`: the probability is 0.5 exactly when the line sits on the
    model's median. The shipped map instead anchored on the *midpoint* of
    [q25, q75], which for a right-skewed count distribution is above the
    median -- so it returned p_over > 0.5 for a line sitting exactly on the
    median, on 4.35% of rows. That was a definitional error, not a tuning one.

    The gap is clamped to the range the fit was built on, so the output stays
    within [0.330, 0.660]. Beyond that support the logistic would keep climbing
    and manufacture conviction the model has not earned -- its directional AUC
    is 0.53.

    `q25`/`q75` are accepted so the signature matches `legacy_p_over` and the
    caller does not have to care which inputs the fit used.
    """
    mid, ln = _finite(q50), _finite(line)
    if mid is None or ln is None:
        return 0.5
    gap = min(max(mid - ln, -_MAX_FIT_GAP), _MAX_FIT_GAP)
    z = _HONEST_INTERCEPT + _HONEST_COEF_GAP * gap
    if z > 40.0:
        return PROB_CEIL
    if z < -40.0:
        return PROB_FLOOR
    return min(max(1.0 / (1.0 + math.exp(-z)), PROB_FLOOR), PROB_CEIL)


def ranking_signal(q25: float, q50: float, q75: float, line: float) -> float:
    """A [-cap, cap] ordering signal, which is a different job to the probability.

    Conflating the two was the original defect. The probability has to be
    honest, and an honest one here is nearly constant -- feeding it through
    `(p - 0.5) * 2` would hand the composite a ~0 score while
    `_component_available_weight` kept ml_regression's full 0.25 in the
    renormalization denominator, diluting every other component by ~4.3 points.

    So the ordering is taken from the IQR-standardized gap directly, which is
    where the signal actually is: AUC 0.5299 on the current season, z=5.3,
    p<0.0001 on a 2,000-draw permutation test. (Raw gap ranks worse, 0.5234 --
    the mirror image of the probability, where raw gap wins.)

    The signal is weak and that is not hidden here: AUC 0.53 is a real but thin
    ordering, significant only because it is pooled over 10,625 rows. Per stat,
    FG3M, AST, REB, BLK and STL clear 2 sigma while PTS and the three PTS
    combos -- the largest markets -- do not.
    """
    lo, mid, hi = _finite(q25), _finite(q50), _finite(q75)
    ln = _finite(line)
    if None in (lo, mid, hi, ln):
        return 0.0
    iqr = hi - lo
    if iqr <= _DEGENERATE_IQR:
        # No spread to standardize by, so there is no honest magnitude. Neutral
        # rather than a guess from the median, which is what the old map did.
        return 0.0
    return _RANKING_CAP * math.tanh(((mid - ln) / iqr) / _RANKING_SCALE)
