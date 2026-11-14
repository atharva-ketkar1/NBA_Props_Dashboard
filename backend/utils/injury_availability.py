"""How likely is a player on the injury report to actually play?

Measured 2026-11-13 against gamelog ground truth -- did the player appear in the
box score -- rather than against the report's own later status. That matters: 47%
of Questionables never resolve in the editions we capture, because they resolve at
the inactive list after the last poll of the night, so grading on report
transitions alone throws away half the sample and biases what is left.

Restricted to players with a prop quoted on the board (261 player ids), which is
the population the model actually projects. One observation per (player-game,
time bucket), bands are block bootstraps over player-game:

    status          P(plays)   2sd     n       what the serve path assumed
    available        98.5%    +-2.0    412     100%  -- fine
    probable         96.4%    +-4.6    194     100%  -- fine
    questionable     58.1%    +-9.5    458     100%  -- WRONG
    doubtful          1.5%    +-3.1     66       0%  -- fine
    out               0.0%    +-0.0   1111       0%  -- fine

Two things follow, and the second is the reason this module exists rather than a
constant being edited in place.

**The defect is precisely and only Questionable.** `out` and `doubtful` round to
unavailable, `available` and `probable` round to available. So the applied rule
below stays binary everywhere except one status. Keeping the deviation that narrow
matters because the vacancy features were *trained* on actual DNPs, which are
binary -- a fractional weight is already a train/serve shape change, and there is
no reason to spend it on a 1.5% correction for healthy-listed players.

**Do not use this as a multiplier on a projection.** A Questionable player is a
mixture: ~42% chance of roughly zero, ~58% chance of their normal distribution.
The mixture's median is the 13.8th percentile of the playing distribution, not
0.58x its median, and the mixture's q25 is exactly 0. Scaling q25/q50/q75 by
P(plays) is wrong in a way that then compounds through the sorted-quantile
contract and through `honest_p_over`'s fitted gap term.

Where it *is* exact is probability, because a DNP is under any positive line:

    P(over) = P(plays) * P(over | plays)

That is the only form in which this should touch a published number.

Without the props restriction P(plays | questionable) reads 49.4%, but that
population includes two-way contracts, G League assignments and deep bench. The
tell is that `available` reads an implausible 79.6% there -- a player the league
lists as Available plays far more often than that. Use the restricted figures.

Caveats worth carrying: n=458 / 127 clusters puts Questionable at [48.6%, 67.6%],
so 0.581 is a point estimate and not a constant to defend. Coverage is the 21
gamelog dates 2026-10-20 -> 2026-11-10. P(plays) does rise toward tip (55.1% at
>6h to 71.1% at 1-2h) but every bucket is +-10pp and that rise is not significant
on its own, so no time term is applied yet.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

OUT = "out"
DOUBTFUL = "doubtful"
QUESTIONABLE = "questionable"
PROBABLE = "probable"
AVAILABLE = "available"

# The measurement, kept whole for future reference even where the applied rule
# below rounds it. Editing these means re-running the measurement, not guessing.
MEASURED_P_PLAYS: Dict[str, float] = {
    AVAILABLE: 0.985,
    PROBABLE: 0.964,
    QUESTIONABLE: 0.581,
    DOUBTFUL: 0.015,
    OUT: 0.0,
}

# Only this status gets a fractional treatment; see the module docstring.
PARTIAL_STATUSES = frozenset({QUESTIONABLE})

_ENV_OVERRIDE = "EDGE_QUESTIONABLE_AVAILABILITY"


def normalize_status(value: object) -> str:
    return str(value or "").strip().lower()


def questionable_availability() -> float:
    """P(plays | Questionable), overridable so it can be reverted without a deploy.

    Setting EDGE_QUESTIONABLE_AVAILABILITY=1.0 restores the previous behaviour
    exactly: a Questionable player projects at full freight and contributes no
    vacancy. A push to main deploys both Vercel and the pipeline, so this needs a
    switch that does not require one.
    """
    raw = os.environ.get(_ENV_OVERRIDE)
    if raw is None or not str(raw).strip():
        return MEASURED_P_PLAYS[QUESTIONABLE]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return MEASURED_P_PLAYS[QUESTIONABLE]
    if value != value or value < 0.0 or value > 1.0:
        return MEASURED_P_PLAYS[QUESTIONABLE]
    return value


def probability_plays(status: object) -> Optional[float]:
    """Applied P(plays), or None when the status is not one we recognise.

    None rather than a default, for the same reason `season_type_from_game_id`
    returns None on an unreadable id: defaulting an unknown status to "plays"
    silently projects a missing player at full freight, and defaulting it to
    "out" silently deletes them. The caller decides.
    """
    clean = normalize_status(status)
    if not clean:
        return None
    if clean == QUESTIONABLE:
        return questionable_availability()
    if clean in (OUT, DOUBTFUL):
        return 0.0
    if clean in (AVAILABLE, PROBABLE):
        return 1.0
    return None


def vacancy_weight(status: object) -> float:
    """How much of a vacancy this player's status creates for teammates.

    1.0 for out/doubtful, 1 - P(plays) for Questionable, 0.0 for
    available/probable, and 0.0 for anything unrecognised -- an unknown status
    must not manufacture a vacancy.
    """
    plays = probability_plays(status)
    if plays is None:
        return 0.0
    return 1.0 - plays


def is_fully_unavailable(status: object) -> bool:
    """The old `status in {"out", "doubtful"}` test, by name."""
    return normalize_status(status) in (OUT, DOUBTFUL)
