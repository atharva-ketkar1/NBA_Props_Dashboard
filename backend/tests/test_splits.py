"""The split invariants, checked without fitting anything.

These pin the two leaks that produced the shipped bundle's metrics: Optuna
selecting hyperparameters on rows that were also in the test set, and
CatBoost early-stopping against the test pool. Both were possible because the
split was re-derived inline at each call site, so there was no object to make
a claim about. The cases below are the claim.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
PROP_MODELING_DIR = BACKEND_DIR / "prop_modeling"
if str(PROP_MODELING_DIR) not in sys.path:
    sys.path.insert(0, str(PROP_MODELING_DIR))

import pandas as pd

from prop_modeling.splits import (
    DEFAULT_TEST_DATES,
    coverage,
    WalkForwardBlock,
    latest_season_key,
    season_dates,
    walk_forward_blocks,
    DEFAULT_VAL_DATES,
    DateSplit,
    LeakageError,
    assert_disjoint,
    assert_not_test,
    chronological_split,
    coverage,
    split_frames,
)


def _frame(n_dates=60, rows_per_date=3, start=date(2026, 1, 1)):
    """One row per (date, stat_type-ish index), ascending by date."""
    records = []
    for day in range(n_dates):
        game_date = start + timedelta(days=day)
        for i in range(rows_per_date):
            records.append({"game_date": pd.Timestamp(game_date), "value": day * 10 + i})
    return pd.DataFrame(records)


class ChronologicalSplitTests(unittest.TestCase):
    def test_blocks_have_the_requested_widths(self):
        split = chronological_split(_frame(60), test_date_count=20, val_date_count=15)
        self.assertEqual(len(split.test_dates), 20)
        self.assertEqual(len(split.val_dates), 15)
        self.assertEqual(len(split.train_dates), 25)

    def test_defaults_are_the_notebook_widths(self):
        split = chronological_split(_frame(60))
        self.assertEqual(len(split.test_dates), DEFAULT_TEST_DATES)
        self.assertEqual(len(split.val_dates), DEFAULT_VAL_DATES)

    def test_val_sits_between_train_and_test(self):
        split = chronological_split(_frame(60))
        self.assertLess(max(split.train_dates), min(split.val_dates))
        self.assertLess(max(split.val_dates), min(split.test_dates))

    def test_every_row_lands_in_exactly_one_block(self):
        df = _frame(60, rows_per_date=4)
        split = chronological_split(df)
        counts = coverage(df, split)
        self.assertEqual(counts["unassigned"], 0)
        self.assertEqual(counts["train"] + counts["val"] + counts["test"], len(df))

    def test_too_few_dates_raises_rather_than_emptying_val(self):
        # An empty val block would send every caller back to the test set,
        # which is the failure this module exists to prevent.
        with self.assertRaises(LeakageError):
            chronological_split(_frame(20), test_date_count=20, val_date_count=15)

    def test_test_block_is_the_latest_dates(self):
        df = _frame(60)
        split = chronological_split(df)
        latest = pd.to_datetime(df["game_date"]).dt.date.max()
        self.assertEqual(max(split.test_dates), latest)

    def test_unparseable_dates_are_dropped_not_counted(self):
        df = _frame(40)
        df.loc[len(df)] = {"game_date": pd.NaT, "value": -1}
        split = chronological_split(df, test_date_count=5, val_date_count=5)
        self.assertEqual(len(split.train_dates), 30)


class FrameSelectionTests(unittest.TestCase):
    def test_frame_selects_by_date_membership(self):
        df = _frame(60)
        split = chronological_split(df)
        val = split.frame(df, "val")
        got = set(pd.to_datetime(val["game_date"]).dt.date.unique())
        self.assertEqual(got, set(split.val_dates))

    def test_frame_rejects_an_unknown_name(self):
        split = chronological_split(_frame(60))
        with self.assertRaises(KeyError):
            split.frame(_frame(60), "holdout")

    def test_a_per_stat_subset_cannot_pick_up_neighbouring_dates(self):
        # Membership rather than a boundary comparison: a subset missing the
        # first test date must not absorb it into val.
        df = _frame(60)
        split = chronological_split(df)
        dropped = split.test_dates[0]
        subset = df[pd.to_datetime(df["game_date"]).dt.date != dropped]
        val = split.frame(subset, "val")
        self.assertNotIn(dropped, set(pd.to_datetime(val["game_date"]).dt.date.unique()))

    def test_training_claims_dates_earlier_than_the_split_knows_about(self):
        """The minutes-dataset case.

        The split is built from the stat dataset, but the minutes dataset is
        built with --minutes-min-prior-games 3 against the stat dataset's
        --min-prior-games 5, so it legitimately starts earlier. Enumerating
        training dates dropped 1,169 rows of real early-season data for
        belonging to no block; bounding by val_start keeps them.
        """
        df = _frame(60, start=date(2026, 2, 1))
        split = chronological_split(df)
        wider = pd.concat([_frame(5, start=date(2026, 1, 1)), df], ignore_index=True)
        counts = coverage(wider, split)
        self.assertEqual(counts["unassigned"], 0)
        self.assertGreater(counts["train"], coverage(df, split)["train"])

    def test_a_date_inside_the_holdout_window_that_the_split_lacks_is_unassigned(self):
        # The genuine inconsistency the coverage check is for: a frame with a
        # date inside the holdout window that the split never saw. Built on
        # every-other-day dates so there is a real gap to land in -- a
        # same-day timestamp would just be the same date.
        records = []
        for step in range(60):
            day = date(2026, 2, 1) + timedelta(days=step * 2)
            records.append({"game_date": pd.Timestamp(day), "value": step})
        df = pd.DataFrame(records)
        split = chronological_split(df)
        gap_day = split.val_dates[0] + timedelta(days=1)
        self.assertNotIn(gap_day, set(split.val_dates) | set(split.test_dates))
        rogue = pd.DataFrame([{"game_date": pd.Timestamp(gap_day), "value": -1}])
        counts = coverage(pd.concat([df, rogue], ignore_index=True), split)
        self.assertEqual(counts["unassigned"], 1)

    def test_a_date_after_the_test_block_is_not_silently_scored(self):
        df = _frame(60, start=date(2026, 2, 1))
        split = chronological_split(df)
        later = pd.DataFrame([{"game_date": pd.Timestamp("2027-01-01"), "value": -1}])
        wider = pd.concat([df, later], ignore_index=True)
        self.assertEqual(coverage(wider, split)["unassigned"], 1)
        self.assertEqual(len(split.frame(wider, "test")), len(split.frame(df, "test")))

    def test_split_frames_returns_all_three(self):
        df = _frame(60)
        frames = split_frames(df, chronological_split(df))
        self.assertEqual(set(frames), {"train", "val", "test"})


class LeakGuardTests(unittest.TestCase):
    def test_assert_not_test_passes_on_the_val_frame(self):
        df = _frame(60)
        split = chronological_split(df)
        assert_not_test(split, split.frame(df, "val"), purpose="early stopping")

    def test_assert_not_test_catches_the_notebook_eval_set_leak(self):
        # regression_notebook.py:1522 passed the test pool as eval_set with
        # use_best_model=True.
        df = _frame(60)
        split = chronological_split(df)
        with self.assertRaises(LeakageError):
            assert_not_test(split, split.frame(df, "test"), purpose="early stopping")

    def test_assert_not_test_catches_the_optuna_full_frame_leak(self):
        # regression_notebook.py:1433 tuned on `df`, whose last 15 dates are a
        # strict subset of the 20-date test block.
        df = _frame(60)
        split = chronological_split(df)
        with self.assertRaises(LeakageError):
            assert_not_test(split, df, purpose="tuning")

    def test_assert_not_test_rejects_an_empty_frame(self):
        df = _frame(60)
        split = chronological_split(df)
        with self.assertRaises(LeakageError):
            assert_not_test(split, df.iloc[0:0], purpose="early stopping")

    def test_overlapping_blocks_are_rejected(self):
        shared = date(2026, 3, 1)
        bad = DateSplit(
            train_dates=(date(2026, 1, 1), shared),
            val_dates=(shared,),
            test_dates=(date(2026, 4, 1),),
        )
        with self.assertRaises(LeakageError):
            assert_disjoint(bad)

    def test_out_of_order_blocks_are_rejected(self):
        bad = DateSplit(
            train_dates=(date(2026, 5, 1),),
            val_dates=(date(2026, 2, 1),),
            test_dates=(date(2026, 3, 1),),
        )
        with self.assertRaises(LeakageError):
            assert_disjoint(bad)


class ProvenanceTests(unittest.TestCase):
    def test_provenance_records_the_actual_boundaries(self):
        df = _frame(60, start=date(2026, 1, 1))
        split = chronological_split(df, test_date_count=20, val_date_count=15)
        prov = split.provenance()
        self.assertEqual(prov["train_dates"], 25)
        self.assertEqual(prov["train_first"], "2026-01-01")
        self.assertEqual(prov["test_last"], "2026-03-01")
        # val must abut test with no gap and no overlap.
        self.assertLess(prov["val_last"], prov["test_first"])




class WalkForwardBlockTests(unittest.TestCase):
    """Evaluating the current season without putting it in a trailing holdout.

    `chronological_split` holds out 35 dates by default; in November the
    current season has about 16, so every one of them falls in the holdout and
    the shipped weights end up containing no current-season games at all. These
    blocks are the alternative: each fits on strictly prior dates only, so
    every current-season date gets an out-of-fold prediction and the final
    model is free to train on everything.
    """

    def _frame(self, n_dates=120, start=date(2026, 1, 1)):
        rows = []
        for day in range(n_dates):
            rows.append({"game_date": pd.Timestamp(start + timedelta(days=day)), "v": day})
        return pd.DataFrame(rows)

    def test_every_evaluated_date_is_covered_exactly_once(self):
        df = self._frame(120)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())[-18:]
        blocks = walk_forward_blocks(df, evaluate_dates=targets, block_dates=6,
                                     min_train_dates=30)
        covered = [d for b in blocks for d in b.predict_dates]
        self.assertEqual(sorted(covered), targets)
        self.assertEqual(len(covered), len(set(covered)))

    def test_training_dates_are_strictly_before_the_predicted_block(self):
        df = self._frame(120)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())[-18:]
        for block in walk_forward_blocks(df, evaluate_dates=targets, block_dates=6,
                                         min_train_dates=30):
            self.assertLess(max(block.train_dates), min(block.predict_dates))
            self.assertLess(max(block.val_dates), min(block.predict_dates))

    def test_the_validation_carve_comes_out_of_the_training_window(self):
        df = self._frame(120)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())[-12:]
        for block in walk_forward_blocks(df, evaluate_dates=targets, block_dates=6,
                                         min_train_dates=30):
            self.assertFalse(set(block.train_dates) & set(block.val_dates))
            self.assertFalse(set(block.val_dates) & set(block.predict_dates))

    def test_block_count_follows_block_dates(self):
        df = self._frame(120)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())[-18:]
        self.assertEqual(
            len(walk_forward_blocks(df, evaluate_dates=targets, block_dates=6,
                                    min_train_dates=30)), 3)
        self.assertEqual(
            len(walk_forward_blocks(df, evaluate_dates=targets, block_dates=18,
                                    min_train_dates=30)), 1)

    def test_a_block_without_enough_history_is_skipped(self):
        df = self._frame(40)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())
        blocks = walk_forward_blocks(df, evaluate_dates=targets, block_dates=5,
                                     min_train_dates=100)
        self.assertEqual(blocks, [])

    def test_frames_select_the_right_rows(self):
        df = self._frame(120)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())[-6:]
        block = walk_forward_blocks(df, evaluate_dates=targets, block_dates=6,
                                    min_train_dates=30)[0]
        got = set(pd.to_datetime(block.frame(df, "predict")["game_date"]).dt.date.unique())
        self.assertEqual(got, set(targets))

    def test_dates_outside_the_frame_are_ignored(self):
        df = self._frame(60)
        targets = sorted(pd.to_datetime(df["game_date"]).dt.date.unique())[-5:]
        targets.append(date(2030, 1, 1))
        covered = [d for b in walk_forward_blocks(df, evaluate_dates=targets,
                                                  block_dates=5, min_train_dates=30)
                   for d in b.predict_dates]
        self.assertNotIn(date(2030, 1, 1), covered)

    def test_rejects_a_zero_block_width(self):
        with self.assertRaises(ValueError):
            walk_forward_blocks(self._frame(60), evaluate_dates=[], block_dates=0)


class SeasonHelperTests(unittest.TestCase):
    @staticmethod
    def _key(d):
        # Mirrors injury_feature_config.season_key_for_date.
        return d.year if d.month >= 10 else d.year - 1

    def test_season_dates_picks_one_season(self):
        df = pd.DataFrame({"game_date": pd.to_datetime(
            ["2026-03-01", "2026-04-01", "2026-10-25", "2026-11-02"])})
        self.assertEqual(
            season_dates(df, 2026, season_key_fn=self._key),
            [date(2026, 10, 25), date(2026, 11, 2)],
        )

    def test_latest_season_key_is_the_current_one(self):
        df = pd.DataFrame({"game_date": pd.to_datetime(
            ["2025-11-01", "2026-04-01", "2026-10-25"])})
        self.assertEqual(latest_season_key(df, season_key_fn=self._key), 2026)

    def test_latest_season_key_on_an_empty_frame(self):
        df = pd.DataFrame({"game_date": pd.to_datetime([])})
        self.assertIsNone(latest_season_key(df, season_key_fn=self._key))


if __name__ == "__main__":
    unittest.main()
