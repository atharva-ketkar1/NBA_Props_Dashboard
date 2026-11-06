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

from prop_modeling.p_over_calibration import honest_p_over, ranking_signal
from prop_modeling.backtest import (
    _p_over_from_quantiles,
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


class IncompleteFeatureSetIsRefused(unittest.TestCase):
    """Scoring a dataset that lacks trained features must fail, not guess.

    build_regression_dataset.py does not emit every trained feature: the four
    momentum/possession terms are hand-engineered inside ml_inference after
    _build_regression_row returns, and the four modeled_minutes_* columns come
    from a separate minutes model attached by the training notebook. Scoring
    without them silently fills NaN and the model collapses toward a flat prior
    -- measured once at sd(q50)=3.04 against sd(actual)=8.46, which reads
    exactly like "this model has no signal" and is instead a harness bug.
    """

    def test_a_dataset_missing_trained_features_raises(self):
        import csv as _csv
        import tempfile

        from prop_modeling.backtest import score_dataset

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tiny.csv"
            with path.open("w", newline="") as fh:
                w = _csv.writer(fh)
                w.writerow(["player_id", "game_date", "stat_type", "actual_value"])
                w.writerow(["1", "2026-10-20", "PTS", "25"])
            with self.assertRaises(RuntimeError) as ctx:
                score_dataset(path)
        message = str(ctx.exception)
        self.assertIn("missing", message)
        self.assertIn("modeled_minutes_q50", message)


class LegacyPOverMap(unittest.TestCase):
    """Pins the map the live scorer used to use, including its flaws.

    No longer what ships -- `edge_score` now calls `honest_p_over`. Kept
    because the harness still reports it as the "before", and because these
    cases document precisely what was wrong: it saturates past q75, and it
    centres 0.5 on the midpoint of [q25, q75] rather than on the median, which
    is only the same point when the distribution is symmetric.
    """

    def test_a_line_at_the_median_is_a_coin_flip(self):
        self.assertAlmostEqual(_p_over_from_quantiles(10.0, 20.0, 30.0, 20.0), 0.50)

    def test_a_line_at_q25_leans_over(self):
        self.assertAlmostEqual(_p_over_from_quantiles(10.0, 20.0, 30.0, 10.0), 0.75)

    def test_a_line_at_q75_leans_under(self):
        self.assertAlmostEqual(_p_over_from_quantiles(10.0, 20.0, 30.0, 30.0), 0.25)

    def test_it_saturates_past_q75_which_is_the_defect(self):
        """0.04 past q75 and 20 past q75 score identically."""
        just_past = _p_over_from_quantiles(10.0, 20.0, 30.0, 30.04)
        far_past = _p_over_from_quantiles(10.0, 20.0, 30.0, 50.0)
        self.assertAlmostEqual(just_past, far_past)
        self.assertAlmostEqual(just_past, 0.25)

    def test_a_degenerate_interval_falls_back_to_the_median(self):
        self.assertAlmostEqual(_p_over_from_quantiles(5.0, 5.0, 5.0, 4.0), 0.60)
        self.assertAlmostEqual(_p_over_from_quantiles(5.0, 5.0, 5.0, 6.0), 0.40)

    def test_it_centres_on_the_iqr_midpoint_not_the_median(self):
        """The definitional error, on skewed quantiles where the two differ.

        A line sitting exactly on the model's median must be a coin flip. With
        q25=18, q50=20, q75=30 the midpoint is 24, so this map reports 0.667 at
        the median -- a systematic lean toward the over on every right-skewed
        count distribution, which is all of them.
        """
        self.assertAlmostEqual(_p_over_from_quantiles(18.0, 20.0, 30.0, 20.0), 2.0 / 3.0)


class HonestPOverMap(unittest.TestCase):
    """What ships now: calibrated rather than confident.

    A pooled logistic on the raw gap, scoring 0.6912 against the legacy map's
    0.7164 on the current season (n=10,625) -- a 0.0252 improvement at 10.1
    sigma. It is also statistically indistinguishable from a constant at the
    base rate (0.1 sigma), which is the finding: no fitted probability beats a
    constant on this signal, so these tests pin modesty, not accuracy.
    """

    def test_a_line_at_the_median_is_near_even_money(self):
        # Not exactly 0.5: the fitted intercept carries the base rate, which
        # favours unders (realised over-rate 0.4656).
        at_median = honest_p_over(18.0, 20.0, 30.0, 20.0)
        self.assertGreater(at_median, 0.47)
        self.assertLess(at_median, 0.50)

    def test_it_centres_on_the_median_even_when_skewed(self):
        # The same skewed quantiles the legacy map got wrong above.
        symmetric = honest_p_over(10.0, 20.0, 30.0, 20.0)
        skewed = honest_p_over(18.0, 20.0, 30.0, 20.0)
        self.assertAlmostEqual(symmetric, skewed)

    def test_it_keeps_responding_past_q75(self):
        """The legacy saturation, gone."""
        just_past = honest_p_over(10.0, 20.0, 30.0, 30.04)
        far_past = honest_p_over(10.0, 20.0, 30.0, 50.0)
        self.assertLess(far_past, just_past)

    def test_it_is_monotone_in_the_gap(self):
        probs = [honest_p_over(10.0, 20.0, 30.0, line) for line in (40, 30, 25, 20, 15, 10, 0)]
        self.assertEqual(probs, sorted(probs))

    def test_it_stays_modest_across_a_wide_range_of_gaps(self):
        # Claiming 90% on a signal whose AUC is 0.53 was the old defect. Over a
        # +/-15 gap the honest map never leaves a believable band.
        probs = [honest_p_over(10.0, 20.0, 30.0, 20.0 - g) for g in range(-15, 16)]
        self.assertGreater(min(probs), 0.25)
        self.assertLess(max(probs), 0.75)

    def test_non_finite_inputs_give_even_money(self):
        self.assertEqual(honest_p_over(float("nan"), float("nan"), 2.0, 3.0), 0.5)
        self.assertEqual(honest_p_over(1.0, None, 2.0, 3.0), 0.5)


class RankingSignalIsNotTheProbability(unittest.TestCase):
    """The component score, which has a different job to the probability."""

    def test_it_is_zero_when_the_line_sits_on_the_median(self):
        self.assertAlmostEqual(ranking_signal(10.0, 20.0, 30.0, 20.0), 0.0)

    def test_it_is_signed_by_the_direction_of_the_gap(self):
        self.assertGreater(ranking_signal(10.0, 20.0, 30.0, 15.0), 0.0)
        self.assertLess(ranking_signal(10.0, 20.0, 30.0, 25.0), 0.0)

    def test_it_is_monotone_and_bounded_by_the_legacy_cap(self):
        signals = [ranking_signal(10.0, 20.0, 30.0, line)
                   for line in (60, 40, 30, 25, 20, 15, 10, 0, -20)]
        self.assertEqual(signals, sorted(signals))
        # The legacy component could not exceed 0.5 either, which is why the
        # board distribution and every alert threshold survive the change.
        self.assertLessEqual(max(abs(s) for s in signals), 0.5)

    def test_a_degenerate_interval_is_neutral_rather_than_a_guess(self):
        # The legacy map guessed +/-0.2 from the median with no spread to
        # justify it. There is no honest magnitude without an IQR.
        self.assertEqual(ranking_signal(5.0, 5.0, 5.0, 4.0), 0.0)
        self.assertEqual(ranking_signal(5.0, 5.0, 5.0, 6.0), 0.0)

    def test_a_wider_interval_damps_the_same_raw_gap(self):
        """Standardizing by the IQR is the point: the model's own uncertainty
        has to discount its own gap, or a vague prediction ranks as high as a
        sharp one."""
        tight = ranking_signal(19.0, 20.0, 21.0, 18.0)
        wide = ranking_signal(5.0, 20.0, 35.0, 18.0)
        self.assertGreater(tight, wide)

    def test_non_finite_inputs_are_neutral(self):
        self.assertEqual(ranking_signal(float("nan"), 1.0, 2.0, 3.0), 0.0)
        self.assertEqual(ranking_signal(1.0, 2.0, None, 3.0), 0.0)

    def test_the_scale_keeps_the_component_magnitude_where_it_was(self):
        """The scale is what stops the board moving, so it is pinned.

        0.445 was solved for so that 0.5*tanh(gap_over_iqr / k) reproduces the
        legacy component's magnitude distribution on the current season: mean
        |score| 0.2510 against 0.2511, correlation 0.973. Verified end to end,
        the composite median moved 62.7 -> 62.2 and alert volume 0 -> 0.

        Banded, not pinned to the third decimal, so re-solving on more data
        does not fail the test -- but a scale that changes the component's
        reach by a third does.
        """
        # q25..q75 spans 10, so a line 5 below the median is a gap of half an
        # IQR -- a typical board row.
        half_an_iqr = ranking_signal(15.0, 20.0, 25.0, 15.0)
        self.assertGreater(half_an_iqr, 0.35)
        self.assertLess(half_an_iqr, 0.45)

    def test_a_gap_of_one_full_iqr_is_near_the_cap(self):
        one_iqr = ranking_signal(15.0, 20.0, 25.0, 10.0)
        self.assertGreater(one_iqr, 0.45)
        self.assertLessEqual(one_iqr, 0.5)

    def test_a_quarter_iqr_gap_is_well_short_of_the_cap(self):
        quarter = ranking_signal(15.0, 20.0, 25.0, 17.5)
        self.assertGreater(quarter, 0.20)
        self.assertLess(quarter, 0.30)


if __name__ == "__main__":
    unittest.main()
