"""Per-stat quantile scoring, including the metric that was missing.

The shipped bundle's headline was a mean MAE and mean R-squared pooled across
eleven markets whose scale runs from 0.46 to 5.9, computed with a different
loss on a different feature set. These cases pin the replacement: pinball loss
(the loss the model actually minimises), MAE against an explicit baseline, and
interval coverage reported under both conventions so neither can be quietly
picked.
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
PROP_MODELING_DIR = BACKEND_DIR / "prop_modeling"
if str(PROP_MODELING_DIR) not in sys.path:
    sys.path.insert(0, str(PROP_MODELING_DIR))

import numpy as np

from prop_modeling.quantile_metrics import (
    DEFAULT_ALPHAS,
    _band_indices,
    _median_index,
    baseline_comparison,
    calibration_table,
    pinball_loss,
    quantile_report,
)


class PinballLossTests(unittest.TestCase):
    def test_matches_the_definition_when_under_predicting(self):
        # y - p = 2, alpha = 0.75  ->  0.75 * 2
        self.assertAlmostEqual(pinball_loss([10.0], [8.0], 0.75), 1.5, places=6)

    def test_matches_the_definition_when_over_predicting(self):
        # y - p = -2, alpha = 0.75  ->  (0.75 - 1) * -2 = 0.5
        self.assertAlmostEqual(pinball_loss([10.0], [12.0], 0.75), 0.5, places=6)

    def test_at_the_median_it_is_half_the_absolute_error(self):
        self.assertAlmostEqual(pinball_loss([10.0, 4.0], [8.0, 7.0], 0.5), 1.25, places=6)

    def test_penalises_asymmetrically(self):
        # A high alpha should punish under-prediction harder than over.
        under = pinball_loss([10.0], [8.0], 0.9)
        over = pinball_loss([10.0], [12.0], 0.9)
        self.assertGreater(under, over)

    def test_non_finite_rows_are_skipped(self):
        self.assertAlmostEqual(
            pinball_loss([10.0, float("nan")], [8.0, 1.0], 0.5), 1.0, places=6
        )

    def test_all_non_finite_returns_none(self):
        self.assertIsNone(pinball_loss([float("nan")], [float("nan")], 0.5))


class AlphaIndexingTests(unittest.TestCase):
    def test_median_is_found_by_value_not_position(self):
        nine = (0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 0.95)
        self.assertEqual(_median_index(nine), 4)

    def test_median_index_on_the_shipped_three(self):
        self.assertEqual(_median_index(DEFAULT_ALPHAS), 1)

    def test_median_falls_back_to_the_nearest_alpha(self):
        self.assertEqual(_median_index((0.1, 0.45, 0.9)), 1)

    def test_band_prefers_the_quartiles_when_present(self):
        nine = (0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 0.95)
        self.assertEqual(_band_indices(nine), (2, 6))

    def test_band_falls_back_to_the_outermost_pair(self):
        self.assertEqual(_band_indices((0.1, 0.5, 0.9)), (0, 2))


class QuantileReportTests(unittest.TestCase):
    def _preds(self):
        return np.array([
            [8.0, 10.0, 13.0],
            [9.0, 11.0, 14.0],
            [7.0, 9.0, 11.0],
        ])

    def test_rejects_a_mislabelled_column_count(self):
        with self.assertRaises(ValueError):
            quantile_report([1.0, 2.0, 3.0], self._preds(), alphas=(0.25, 0.5))

    def test_rejects_descending_alphas(self):
        with self.assertRaises(ValueError):
            quantile_report([1.0, 2.0, 3.0], self._preds(), alphas=(0.75, 0.5, 0.25))

    def test_bias_is_signed_and_negative_when_under_predicting(self):
        report = quantile_report([12.0, 13.0, 11.0], self._preds())
        self.assertLess(report["q50_bias"], 0.0)

    def test_bias_is_positive_when_over_predicting(self):
        report = quantile_report([8.0, 9.0, 7.0], self._preds())
        self.assertGreater(report["q50_bias"], 0.0)

    def test_reports_both_coverage_conventions(self):
        report = quantile_report([12.0, 13.0, 11.0], self._preds())
        self.assertIn("coverage_continuous", report["band"])
        self.assertIn("coverage_discrete", report["band"])

    def test_the_two_conventions_disagree_on_a_discrete_target(self):
        # The BLK case: a band of width ~1 on a variable taking 0/1/2. The
        # continuous reading excludes both modal outcomes; the discrete one
        # widens to the enclosing integers and admits them.
        y = np.array([0.0, 1.0, 0.0, 1.0])
        preds = np.array([[0.3, 0.6, 0.9]] * 4)
        report = quantile_report(y, preds)
        # The band sits strictly between 0 and 1, so it admits neither.
        self.assertEqual(report["band"]["coverage_continuous"], 0.0)
        # Widened to [0, 1] it admits both, which is just as uninformative.
        self.assertEqual(report["band"]["coverage_discrete"], 1.0)

    def test_counts_crossed_quantiles(self):
        crossed = np.array([[13.0, 10.0, 8.0]])
        report = quantile_report([10.0], crossed)
        self.assertEqual(report["band"]["crossed_rows"], 1)

    def test_pinball_is_reported_per_alpha(self):
        report = quantile_report([12.0, 13.0, 11.0], self._preds())
        self.assertEqual(set(report["pinball"]), {"0.25", "0.5", "0.75"})

    def test_works_with_nine_alphas(self):
        nine = (0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 0.95)
        preds = np.tile(np.linspace(5.0, 25.0, 9), (4, 1))
        report = quantile_report(np.array([10.0, 15.0, 20.0, 12.0]), preds, alphas=nine)
        self.assertEqual(report["band"]["lo_alpha"], 0.25)
        self.assertEqual(report["band"]["hi_alpha"], 0.75)
        self.assertEqual(len(report["calibration"]), 9)


class CalibrationTableTests(unittest.TestCase):
    def test_a_perfectly_calibrated_head_has_no_gap(self):
        # y uniform on 0..99; the alpha-quantile of that is alpha*100.
        y = np.arange(100.0)
        alphas = (0.25, 0.5, 0.75)
        preds = np.tile(np.array([25.0, 50.0, 75.0]), (100, 1))
        for row in calibration_table(y, preds, alphas):
            self.assertLess(abs(row["gap"]), 0.03)

    def test_a_band_that_is_too_narrow_shows_opposite_signed_gaps(self):
        y = np.arange(100.0)
        preds = np.tile(np.array([45.0, 50.0, 55.0]), (100, 1))
        rows = calibration_table(y, preds, (0.25, 0.5, 0.75))
        self.assertGreater(rows[0]["gap"], 0.0)   # q25 too high
        self.assertLess(rows[2]["gap"], 0.0)      # q75 too low


class BaselineComparisonTests(unittest.TestCase):
    def test_negative_delta_means_the_model_wins(self):
        out = baseline_comparison([10.0, 10.0], [10.0, 10.0], {"b": [12.0, 12.0]})
        self.assertLess(out["b"]["delta"], 0.0)

    def test_positive_delta_means_the_baseline_wins(self):
        # This is the shipped bundle's situation against the closing line.
        out = baseline_comparison([10.0, 10.0], [13.0, 13.0], {"b": [11.0, 11.0]})
        self.assertGreater(out["b"]["delta"], 0.0)

    def test_restricted_to_rows_the_baseline_can_also_score(self):
        # Only the first row has a line, and it is the row the model nails.
        # Scoring the model on both rows would flatter it.
        out = baseline_comparison(
            [10.0, 30.0], [10.0, 20.0], {"line": [11.0, float("nan")]}
        )
        self.assertEqual(out["line"]["n"], 1)
        self.assertAlmostEqual(out["line"]["model_mae"], 0.0, places=6)

    def test_an_entirely_missing_baseline_reports_zero_rows(self):
        out = baseline_comparison([10.0], [10.0], {"line": [float("nan")]})
        self.assertEqual(out["line"]["n"], 0)
        self.assertIsNone(out["line"]["delta"])

    def test_several_baselines_are_scored_independently(self):
        out = baseline_comparison(
            [10.0, 10.0], [10.0, 10.0],
            {"recent10": [12.0, 12.0], "line": [10.0, 10.0]},
        )
        self.assertEqual(set(out), {"recent10", "line"})


if __name__ == "__main__":
    unittest.main()
