"""One definition of the train / validation / test split, with the leak refused.

Why this exists: the bundle in exported_regression_model/ reports
per_stat_test_metrics from models that selected both their hyperparameters and
their iteration count against those same test rows. Two separate paths in
regression_notebook.py did it:

  * Optuna tuned on `df` -- the *full* frame -- with an inner split at the last
    15 dates, which is a strict subset of the 20-date test set (`:1432-1442`).
    The section comment above it asserted the opposite.
  * The tuned retrain then passed the test pool as `eval_set` with
    `use_best_model=True` (`:1522`), so early stopping chose the tree count on
    the reported holdout. model_FG3M.cbm is 18.4 MB against model_BLK.cbm's
    438 KB, which is what an unconstrained `iterations` search selected against
    a holdout.

Neither was caught because the split was re-derived inline at each call site
from a bare `unique_dates[-20:]`, so there was nothing to check a claim
against. This module makes the split a value that carries its own invariants:
three date ranges, strictly ordered, provably disjoint, each handed out by
name. A caller that wants an `eval_set` asks for "val" and cannot reach "test"
by accident, and `provenance()` writes the actual boundaries into the model
metadata so a future reader does not have to trust a comment.

Pure pandas on purpose -- no catboost, no sklearn -- so the invariants are
testable without fitting anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

DATE_COLUMN = "game_date"

# The final holdout, in game dates. 20 is what the notebook used and what the
# shipped metrics were (nominally) computed on, so keeping it makes the new
# numbers comparable to the old ones.
DEFAULT_TEST_DATES = 20

# Carved out of train, immediately before the test block. Optuna selects on
# this and CatBoost early-stops on it. 15 matches the width the notebook's
# inner split used, so the tuning budget sees a similarly sized signal -- the
# defect was never the width, it was which rows it came from.
DEFAULT_VAL_DATES = 15

SPLIT_NAMES = ("train", "val", "test")


class LeakageError(RuntimeError):
    """Raised when a split would let evaluation rows reach model selection."""


@dataclass(frozen=True)
class DateSplit:
    """Three disjoint, chronologically ordered blocks of game dates.

    `train` is everything before `val_start`, `val` is [val_start, test_start),
    and `test` is everything from `test_start` on. Held as explicit date tuples
    rather than just the two boundaries so that `assert_disjoint` has something
    to check and `provenance` has something to record.
    """

    train_dates: Tuple[date, ...]
    val_dates: Tuple[date, ...]
    test_dates: Tuple[date, ...]

    @property
    def val_start(self) -> pd.Timestamp:
        return pd.Timestamp(self.val_dates[0])

    @property
    def test_start(self) -> pd.Timestamp:
        return pd.Timestamp(self.test_dates[0])

    def dates(self, name: str) -> Tuple[date, ...]:
        if name not in SPLIT_NAMES:
            raise KeyError("unknown split %r; expected one of %s" % (name, SPLIT_NAMES))
        return {"train": self.train_dates, "val": self.val_dates, "test": self.test_dates}[name]

    def frame(self, df: pd.DataFrame, name: str, *, date_column: str = DATE_COLUMN) -> pd.DataFrame:
        """The rows of `df` belonging to one named split.

        `val` and `test` select by date membership rather than by a boundary
        comparison, so a frame whose dates differ from the ones the split was
        built on cannot pick up rows from a neighbouring block, and a date
        later than `test_last` is dropped rather than silently scored.

        `train` is the exception: it is every date strictly before `val_start`.
        Membership there was wrong, and the minutes dataset is why. It is built
        with `--minutes-min-prior-games 3` against the stat dataset's
        `--min-prior-games 5`, so it legitimately begins three days earlier --
        and since the split is built from the stat dataset, membership silently
        dropped 1,169 rows of real early-season training data for belonging to
        no block. Anything earlier than validation belongs in training; there
        is no gap between the blocks for a boundary test to fall into, because
        they are consecutive slices of one sorted date list.
        """
        dates = pd.to_datetime(df[date_column], errors="coerce").dt.date
        if name == "train":
            if not self.val_dates:
                raise LeakageError("split has no validation block to bound training by")
            return df[dates < self.val_start.date()].copy()
        wanted = set(self.dates(name))
        return df[dates.isin(wanted)].copy()

    def provenance(self) -> Dict[str, object]:
        """What to write into model metadata so the split is auditable later."""
        return {
            "train_dates": len(self.train_dates),
            "val_dates": len(self.val_dates),
            "test_dates": len(self.test_dates),
            "train_first": _iso(self.train_dates[0]) if self.train_dates else None,
            "train_last": _iso(self.train_dates[-1]) if self.train_dates else None,
            "val_first": _iso(self.val_dates[0]) if self.val_dates else None,
            "val_last": _iso(self.val_dates[-1]) if self.val_dates else None,
            "test_first": _iso(self.test_dates[0]) if self.test_dates else None,
            "test_last": _iso(self.test_dates[-1]) if self.test_dates else None,
        }


def _iso(value: date) -> str:
    return pd.Timestamp(value).date().isoformat()


def chronological_split(
    df: pd.DataFrame,
    *,
    test_date_count: int = DEFAULT_TEST_DATES,
    val_date_count: int = DEFAULT_VAL_DATES,
    date_column: str = DATE_COLUMN,
) -> DateSplit:
    """Split by game date into train / val / test, latest dates held out.

    Raises LeakageError rather than returning a degenerate split when the
    frame cannot support the requested widths. Returning an empty val block
    would be worse than failing: every caller treats val as the thing it is
    allowed to select on, and an empty one sends them back to the test set.
    """
    if test_date_count < 1 or val_date_count < 1:
        raise ValueError("test_date_count and val_date_count must both be >= 1")

    dates = pd.to_datetime(df[date_column], errors="coerce").dt.date
    unique_dates: List[date] = sorted({d for d in dates.dropna().unique()})
    needed = test_date_count + val_date_count + 1
    if len(unique_dates) < needed:
        raise LeakageError(
            "dataset has %d distinct dates in %r but a %d-date test block plus a "
            "%d-date validation block needs at least %d, leaving nothing to train on"
            % (len(unique_dates), date_column, test_date_count, val_date_count, needed)
        )

    test_dates = tuple(unique_dates[-test_date_count:])
    val_dates = tuple(unique_dates[-(test_date_count + val_date_count):-test_date_count])
    train_dates = tuple(unique_dates[: -(test_date_count + val_date_count)])

    split = DateSplit(train_dates=train_dates, val_dates=val_dates, test_dates=test_dates)
    assert_disjoint(split)
    return split


def assert_disjoint(split: DateSplit) -> None:
    """Verify the invariant every caller relies on, or raise LeakageError.

    Checks both that the blocks share no date and that they are ordered, since
    an overlap and a shuffle are the two ways a split stops meaning what its
    name says.
    """
    blocks = {name: set(split.dates(name)) for name in SPLIT_NAMES}
    for left, right in (("train", "val"), ("train", "test"), ("val", "test")):
        shared = blocks[left] & blocks[right]
        if shared:
            raise LeakageError(
                "%s and %s share %d date(s), e.g. %s"
                % (left, right, len(shared), _iso(sorted(shared)[0]))
            )
    for earlier, later in (("train", "val"), ("val", "test")):
        a, b = split.dates(earlier), split.dates(later)
        if a and b and max(a) >= min(b):
            raise LeakageError(
                "%s ends %s but %s starts %s; the blocks must be chronological"
                % (earlier, _iso(max(a)), later, _iso(min(b)))
            )


def assert_not_test(
    split: DateSplit,
    frame: pd.DataFrame,
    *,
    purpose: str,
    date_column: str = DATE_COLUMN,
) -> None:
    """Guard a frame about to be used for model selection.

    Call this on anything handed to CatBoost as `eval_set`, or to Optuna as its
    objective set. It is the check that would have caught both notebook leaks:
    the tuning frame drawn from the full dataframe, and the test pool passed as
    `eval_set` with `use_best_model=True`.
    """
    if frame.empty:
        raise LeakageError("%s frame is empty; there is nothing to select on" % purpose)
    dates = set(pd.to_datetime(frame[date_column], errors="coerce").dt.date.dropna().unique())
    overlap = dates & set(split.test_dates)
    if overlap:
        raise LeakageError(
            "%s would select against %d test date(s), e.g. %s -- use split.frame(df, 'val')"
            % (purpose, len(overlap), _iso(sorted(overlap)[0]))
        )


def split_frames(
    df: pd.DataFrame,
    split: DateSplit,
    *,
    date_column: str = DATE_COLUMN,
) -> Dict[str, pd.DataFrame]:
    """All three frames at once, for callers that genuinely need the test one."""
    return {name: split.frame(df, name, date_column=date_column) for name in SPLIT_NAMES}


def coverage(df: pd.DataFrame, split: DateSplit, *, date_column: str = DATE_COLUMN) -> Dict[str, int]:
    """Row counts per split, plus any rows no block claimed.

    `unassigned` counts rows that reached none of the three blocks. Since
    `train` is bounded rather than enumerated, an early date the split never
    saw lands in `train` and is not unassigned -- only a date at or after
    `val_start` that is in neither `val` nor `test` is, which means the frame
    carries a date inside the holdout window that the split does not know
    about. Rows with an unparseable date are also counted here, so a frame
    with NaT dates does not read as fully assigned.
    """
    frames = split_frames(df, split, date_column=date_column)
    counts = {name: int(len(frame)) for name, frame in frames.items()}
    counts["unassigned"] = int(len(df)) - sum(counts.values())
    return counts

# ---------------------------------------------------------------------------
# Walk-forward evaluation of the current season
#
# `chronological_split` cannot evaluate the current season early in that
# season, and the reason is arithmetic rather than a tuning choice: it holds
# out the last `test_date_count + val_date_count` dates, which is 35 by
# default, and in November the current season has about 16. So every
# current-season date lands in the holdout, the final fit ends in March, and
# the shipped weights contain no current rosters at all. Adding more data does
# not help -- the holdout window grows with it.
#
# The fix is to stop asking one split to do two jobs. Evaluation walks forward
# through the current season in blocks, each fitted only on strictly prior
# dates, which yields an out-of-fold prediction for every current-season date.
# The shipped model is then fitted on everything. The honest number and the
# deployable weights stop competing for the same rows.
#
# This is the pattern the minutes model already uses
# (`_build_minutes_prediction_frame`), applied to the stat heads.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardBlock:
    """One fit: train on `train_dates`, early-stop on `val_dates`, score `predict_dates`.

    `val_dates` is carved from the tail of the training window, never from
    `predict_dates` -- the same rule `assert_not_test` enforces for the
    chronological split.
    """

    label: str
    train_dates: Tuple[date, ...]
    val_dates: Tuple[date, ...]
    predict_dates: Tuple[date, ...]

    def frame(self, df: pd.DataFrame, which: str, *, date_column: str = DATE_COLUMN) -> pd.DataFrame:
        wanted = {
            "train": self.train_dates,
            "val": self.val_dates,
            "predict": self.predict_dates,
        }[which]
        dates = pd.to_datetime(df[date_column], errors="coerce").dt.date
        return df[dates.isin(set(wanted))].copy()


def walk_forward_blocks(
    df: pd.DataFrame,
    *,
    evaluate_dates: Sequence[date],
    block_dates: int = 6,
    val_dates: int = 10,
    min_train_dates: int = 60,
    date_column: str = DATE_COLUMN,
) -> List[WalkForwardBlock]:
    """Blocks covering `evaluate_dates`, each trained only on earlier dates.

    `evaluate_dates` is normally every date of the current season. Each block
    takes the next `block_dates` of them, trains on every date in `df` strictly
    before the block, and early-stops on the last `val_dates` of that training
    window.

    `block_dates` trades cost against staleness: a block's model is up to
    `block_dates` dates behind the rows it predicts, and the number of fits is
    len(evaluate_dates) / block_dates. At 6 a ~16-date season is three fits per
    stat, each at most six dates stale.

    A block whose training window is shorter than `min_train_dates` is skipped
    rather than fitted on too little -- and skipped loudly by the caller, since
    silently returning no blocks is how a run reports coverage of 0.0 as though
    it were a measurement.
    """
    if block_dates < 1 or val_dates < 1:
        raise ValueError("block_dates and val_dates must both be >= 1")

    all_dates = sorted({
        d for d in pd.to_datetime(df[date_column], errors="coerce").dt.date.dropna().unique()
    })
    targets = sorted(set(evaluate_dates) & set(all_dates))
    blocks: List[WalkForwardBlock] = []
    for start in range(0, len(targets), block_dates):
        chunk = tuple(targets[start:start + block_dates])
        if not chunk:
            continue
        prior = tuple(d for d in all_dates if d < chunk[0])
        if len(prior) < min_train_dates:
            continue
        width = min(val_dates, max(1, len(prior) // 5))
        fit_dates, carve = prior[:-width], prior[-width:]
        if not fit_dates:
            continue
        blocks.append(WalkForwardBlock(
            label="%s..%s" % (_iso(chunk[0]), _iso(chunk[-1])),
            train_dates=fit_dates,
            val_dates=carve,
            predict_dates=chunk,
        ))
    return blocks


def season_dates(
    df: pd.DataFrame,
    season_key: int,
    *,
    season_key_fn,
    date_column: str = DATE_COLUMN,
) -> List[date]:
    """Every date in `df` belonging to one season.

    `season_key_fn` is injected rather than imported so this module stays free
    of the feature pipeline -- `injury_feature_config.season_key_for_date` is
    what callers pass, which keeps one definition of where a season starts.
    """
    dates = pd.to_datetime(df[date_column], errors="coerce").dt.date.dropna()
    return sorted({d for d in dates.unique() if season_key_fn(d) == season_key})


def latest_season_key(df: pd.DataFrame, *, season_key_fn, date_column: str = DATE_COLUMN) -> Optional[int]:
    dates = pd.to_datetime(df[date_column], errors="coerce").dt.date.dropna()
    keys = {season_key_fn(d) for d in dates.unique()}
    return max(keys) if keys else None
