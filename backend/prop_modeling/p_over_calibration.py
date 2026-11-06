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

    line <= q25 gives ~0.75, line == q50 gives 0.50, line >= q75 gives ~0.25,
    clamped to [0.10, 0.90] so extrapolation cannot run away.

    Measured against 21,562 out-of-sample outcomes this scores log-loss 0.7116,
    worse than answering 0.5 every time (0.6931). It is not slightly off, it is
    confidently wrong: it spreads output across [0.25, 0.75] on a signal whose
    directional AUC is only 0.51 to 0.62. It also saturates -- once `line`
    reaches q75 it pins at 0.25 and stops responding, so a line just past q75
    scores identically to one far past it.

    Kept because it is what ships, so a missing or rejected calibration
    degrades to today's behaviour rather than to nothing.
    """
    iqr = q75 - q25
    if iqr > _DEGENERATE_IQR:
        frac = min(max((line - q25) / iqr, 0.0), 1.0)
        return min(max(_LEGACY_TOP - frac * _LEGACY_SPAN, _LEGACY_FLOOR), _LEGACY_CEIL)
    # Zero-width IQR (degenerate prediction) -- fall back to a median comparison.
    return 0.60 if q50 > line else 0.40
