"""The scoreboard's own arithmetic, pinned.

These are the functions every later model claim will be judged by, so they are
worth more scrutiny than the model. Two of them encode decisions that are easy
to get wrong and silent when wrong:

* `interval_coverage` must be discreteness-aware. BLK's shipped iqr_coverage of
  0.132 is an artifact of judging a 0.923-wide band against an integer target:
  the band sits strictly between 0 and 1, so it excludes both modal outcomes,
  which are ~90% of BLK games. The band was not mis-estimated; the metric was
  wrong.
* `roi` must report a confidence band. At per-stat sample sizes the 2-sigma band
  runs +/-2% to +/-8%, so an ROI quoted without it is unfalsifiable.
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from prop_modeling.backtest import (
    brier_score,
    build_rows,
    interval_coverage,
    log_loss,
    profit_for_side,
    reliability_table,
    roi,
)


class LogLossAndBrier(unittest.TestCase):
    def test_a_coin_flip_scores_ln_two(self):
        self.assertAlmostEqual(log_loss([0.5, 0.5], [1, 0]), 0.6931, places=3)

    def test_confident_and_correct_beats_a_coin_flip(self):
        self.assertLess(log_loss([0.9, 0.1], [1, 0]), log_loss([0.5, 0.5], [1, 0]))

    def test_confident_and_wrong_is_punished(self):
        self.assertGreater(log_loss([0.9], [0]), log_loss([0.5], [0]))

    def test_certainty_does_not_produce_infinity(self):
        """Clamping matters: one unclipped 0.0 would make the whole run inf."""
        self.assertTrue(math_isfinite(log_loss([0.0, 1.0], [1, 0])))

    def test_brier_is_zero_when_perfect(self):
        self.assertEqual(brier_score([1.0, 0.0], [1, 0]), 0.0)

    def test_none_probabilities_are_skipped_not_counted(self):
        self.assertAlmostEqual(log_loss([0.5, None], [1, 1]), 0.6931, places=3)


def math_isfinite(x):
    import math
    return x is not None and math.isfinite(x)


class DiscretenessAwareCoverage(unittest.TestCase):
    def test_a_band_between_zero_and_one_covers_both_integers(self):
        """The BLK pathology. Continuous scoring says 0%, discrete says 100%."""
        actuals, lows, highs = [0.0, 1.0, 0.0, 1.0], [0.05] * 4, [0.97] * 4
        self.assertEqual(interval_coverage(actuals, lows, highs, discrete=False), 0.0)
        self.assertEqual(interval_coverage(actuals, lows, highs, discrete=True), 1.0)

    def test_an_outcome_outside_the_widened_band_still_misses(self):
        self.assertEqual(
            interval_coverage([5.0], [0.05], [0.97], discrete=True), 0.0
        )

    def test_continuous_band_behaves_normally(self):
        self.assertEqual(
            interval_coverage([10.0, 30.0], [5.0, 5.0], [15.0, 15.0], discrete=False), 0.5
        )


class RoiReportsItsOwnUncertainty(unittest.TestCase):
    def test_a_flat_losing_book_has_no_spread(self):
        out = roi([-1.0, -1.0, -1.0])
        self.assertAlmostEqual(out["roi"], -1.0)
        self.assertAlmostEqual(out["two_sigma"], 0.0)

    def test_the_band_shrinks_as_the_sample_grows(self):
        small = roi([1.0, -1.0] * 25)
        large = roi([1.0, -1.0] * 2500)
        self.assertLess(large["two_sigma"], small["two_sigma"])

    def test_empty_is_reported_not_crashed(self):
        self.assertEqual(roi([])["n"], 0)
        self.assertIsNone(roi([])["roi"])


class GradingAndJoin(unittest.TestCase):
    def _line(self, **kw):
        base = {"player_id": "1", "game_date": "2026-10-20", "sportsbook": "fd",
                "stat_type": "PTS", "line": 20.5, "over_odds": -110,
                "under_odds": -110, "source": "closing_line", "is_closing_line": True}
        base.update(kw)
        return base

    def _game(self, **kw):
        base = {"PLAYER_ID": "1", "GAME_DATE": "2026-10-20", "MIN": 30.0,
                "PTS": 25.0, "REB": 5.0, "AST": 5.0, "STL": 1.0, "BLK": 1.0, "FG3M": 2.0}
        base.update(kw)
        return base

    def test_a_clean_row_grades(self):
        rows, skips = build_rows([self._line()], {("1", "2026-10-20"): self._game()})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "over")
        self.assertAlmostEqual(rows[0]["actual"], 25.0)

    def test_an_exact_landing_is_a_push_and_stakes_nothing(self):
        rows, _ = build_rows(
            [self._line(line=25.0)], {("1", "2026-10-20"): self._game()}
        )
        self.assertEqual(rows[0]["result"], "push")
        self.assertIsNone(profit_for_side(rows[0], "over"))
        self.assertIsNone(profit_for_side(rows[0], "under"))

    def test_an_ungradeable_stat_is_dropped_not_graded_as_zero(self):
        """A missing component must drop the row.

        edge_score's _stat_value_from_game coerces a missing AST to 0.0, which
        would grade a short PRA as a real under. _sum_stat is fail-closed and
        that is why the harness uses it.
        """
        game = self._game()
        del game["AST"]
        rows, skips = build_rows([self._line(stat_type="PTS+REB+AST")],
                                 {("1", "2026-10-20"): game})
        self.assertEqual(rows, [])
        self.assertEqual(skips["ungradeable_stat"], 1)

    def test_a_stat_with_no_column_mapping_is_skipped(self):
        rows, skips = build_rows([self._line(stat_type="TOV")],
                                 {("1", "2026-10-20"): self._game()})
        self.assertEqual(rows, [])
        self.assertEqual(skips["stat_not_gradeable:TOV"], 1)

    def test_a_line_with_no_game_log_is_skipped(self):
        rows, skips = build_rows([self._line()], {})
        self.assertEqual(rows, [])
        self.assertEqual(skips["no_gamelog"], 1)

    def test_market_probability_is_devigged(self):
        """A symmetric -110/-110 pair must come back to 0.5, not 0.524."""
        rows, _ = build_rows([self._line()], {("1", "2026-10-20"): self._game()})
        self.assertAlmostEqual(rows[0]["market_prob_over"], 0.5, places=6)

    def test_winning_and_losing_profits_use_the_right_price(self):
        rows, _ = build_rows(
            [self._line(over_odds=100, under_odds=-130)],
            {("1", "2026-10-20"): self._game()},
        )
        self.assertAlmostEqual(profit_for_side(rows[0], "over"), 1.0)
        self.assertAlmostEqual(profit_for_side(rows[0], "under"), -1.0)


class Reliability(unittest.TestCase):
    def test_buckets_report_predicted_against_realised(self):
        table = reliability_table([0.15, 0.15, 0.85, 0.85], [0, 0, 1, 1], bins=10)
        self.assertEqual(len(table), 2)
        self.assertAlmostEqual(table[0]["hit_rate"], 0.0)
        self.assertAlmostEqual(table[1]["hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
