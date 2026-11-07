"""One definition of sportsbook price arithmetic: implied, de-vigged, payout.

These formulas existed twice before this module -- `_american_to_implied` in
both `utils/edge_score.py` and `prop_modeling/build_prop_dataset.py`, with
`_no_vig_probability` and `_american_to_payout_decimal` only in the latter. That
is the same latent duplication commit 301a815 removed for the quantile-to-p_over
map, and for the same reason: two copies of a number that a harness is supposed
to be measuring means the harness can silently stop measuring what ships.

The two `_american_to_implied` copies were also not behaviourally identical. The
edge_score one coerced through `_safe_float` first; the build_prop_dataset one
was typed `Optional[float]` and would raise TypeError on a string. The functions
here take the permissive form, which is a strict superset -- every
build_prop_dataset call site already pre-coerced, so adopting it changes nothing
there.

Importable from the serve path, which is why it imports only `math` and
`typing`. numpy and pandas are both installed on the VM, so the constraint is
not availability: it is that `build_prop_dataset` cannot be imported at serve
time at all, because its own import line is a bare `from feature_schema import`
and `feature_schema` globs the filesystem at import. `p_over_calibration.py` is
importable for exactly this reason and is the template. Keep it that way --
`tests/test_market_prices.py` asserts the import list with `ast`.
"""

import math
from typing import Any, Optional


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float, or None.

    Matches `edge_score._safe_float(value)` with no default, including its
    treatment of the empty string as missing and its rejection of NaN and
    infinities. That equivalence matters: this function now feeds the `market`
    and `line_movement` component scores, which feed `edge_score`, which is
    what every Discord threshold is denominated in. A coercion difference here
    moves alert volume.
    """
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def american_to_implied(odds: Any) -> Optional[float]:
    """Implied win probability of one side, vig included.

    This is the posted price's probability, not a fair one -- the two sides of a
    market sum to more than 1. Use `no_vig_from_american` for a fair estimate.
    """
    value = _finite(odds)
    if value is None or value == 0:
        return None
    if value > 0:
        return 100.0 / (value + 100.0)
    return abs(value) / (abs(value) + 100.0)


def no_vig_probability(side_implied: Optional[float], opp_implied: Optional[float]) -> Optional[float]:
    """Proportional (multiplicative) de-vig of a two-way market.

    Returns None when either side is missing, so a one-sided quote yields no
    number rather than a silently vigged one. Proportional is the simplest of
    the de-vig families and is biased on heavy favourites relative to Shin or
    power methods; it is what the training dataset's `no_vig_side_prob` feature
    was built with, so changing method here would silently desynchronise the
    model's inputs from its serve-time comparisons.
    """
    side = _finite(side_implied)
    opp = _finite(opp_implied)
    if side is None or opp is None:
        return None
    denominator = side + opp
    if denominator <= 0:
        return None
    return side / denominator


def no_vig_from_american(side_odds: Any, opposite_odds: Any) -> Optional[float]:
    """De-vigged probability of `side_odds` winning, from both posted prices."""
    return no_vig_probability(american_to_implied(side_odds), american_to_implied(opposite_odds))


def hold(side_odds: Any, opposite_odds: Any) -> Optional[float]:
    """The book's margin on a two-way market: implied probabilities minus 1.

    Worth recording alongside any edge estimate, because an edge is only
    meaningful against the hold it has to clear. A -110/-110 market holds
    ~4.5%, and a model edge of +2% over fair value loses money into it.
    """
    side = american_to_implied(side_odds)
    opp = american_to_implied(opposite_odds)
    if side is None or opp is None:
        return None
    return side + opp - 1.0


def american_to_payout_decimal(odds: Any) -> Optional[float]:
    """Net profit per unit staked. Returns None when there is no price.

    Net, not total return: -110 gives 0.909, so a winning unit stake returns
    0.909 and a losing one -1.0. Getting that wrong turns every win into a
    small loss and produces impossible ROI figures.

    Prefer this over `even_money_payout_decimal` anywhere a number is being
    graded, so an unpriced row is dropped rather than valued.
    """
    value = _finite(odds)
    if value is None or value == 0:
        return None
    if value > 0:
        return value / 100.0
    return 100.0 / abs(value)


def even_money_payout_decimal(odds: Any) -> float:
    """Net profit per unit, substituting even money when there is no price.

    Deliberately a separate function rather than a `default=` argument on
    `american_to_payout_decimal`, because the substitution is right in exactly
    one place and a trap everywhere else. PrizePicks rows carry no single-leg
    American odds, so the dataset builder needs a neutral placeholder to sort
    and train on. A *grading* path that reuses it fabricates a +100 price for
    any row with missing odds, and a selection that maximises payout will then
    choose the fabricated one and manufacture a positive return out of nothing.
    """
    payout = american_to_payout_decimal(odds)
    return 1.0 if payout is None else payout
