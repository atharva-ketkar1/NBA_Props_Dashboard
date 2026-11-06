"""The ml_regression component's score comes from the gap, not the probability.

Separating those two was the whole point of the change, and a mutation test
showed nothing detected reverting it: putting `score = (p_over - 0.5) * 2` back
left all 425 tests passing. The calibration module was well covered; the wiring
in `edge_score` was not covered at all, because `_compute_ml_regression_context`
needs a live predictor and nothing stubbed one.

Also pins the single-output path staying dark. Making it `available` again went
equally undetected, and that is the dilution bug: an available component with a
~0 score keeps its full 0.25 in the renormalization denominator, which is ~4.3
composite points worse than being switched off.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es
from prop_modeling.p_over_calibration import honest_p_over, ranking_signal

PLAYER = {"id": 1, "name": "Test Player", "team": "DET", "position": "G"}
Q25, Q50, Q75 = 15.0, 20.0, 25.0
LINE = 15.0                      # half an IQR below the median


class _QuantileStub:
    def predict(self, *args, **kwargs):
        return {"q25": Q25, "q50": Q50, "q75": Q75}

    def hit_probability(self, *args, **kwargs):      # pragma: no cover
        raise AssertionError("the quantile path must not call hit_probability")


class _SingleOutputStub:
    def predict(self, *args, **kwargs):
        return {"prediction": 20.0, "std_dev": 5.0}

    def hit_probability(self, *args, **kwargs):
        return 0.55


def _context(predictor, line=LINE, side="over"):
    with patch.object(es, "_ml_predictor", predictor), \
         patch.object(es, "_extract_logs", return_value=[{"PTS": 20.0}]), \
         patch.object(es, "_compute_lineup_adjustment",
                      return_value={"q50_multiplier": 1.0, "reason": "test"}):
        return es._compute_ml_regression_context(PLAYER, "PHI", "PTS", line, side)


class TheScoreIsTheRankingSignal(unittest.TestCase):
    def test_the_score_is_exactly_the_ranking_signal(self):
        context = _context(_QuantileStub())
        self.assertAlmostEqual(context["score"], ranking_signal(Q25, Q50, Q75, LINE))

    def test_the_score_is_not_derived_from_the_probability(self):
        """The reverted mapping, pinned so it cannot come back unnoticed."""
        context = _context(_QuantileStub())
        from_probability = (context["p_over"] - 0.5) * 2.0

        self.assertGreater(abs(context["score"] - from_probability), 0.1)

    def test_an_under_pick_mirrors_the_over(self):
        over = _context(_QuantileStub(), side="over")
        under = _context(_QuantileStub(), side="under")

        self.assertAlmostEqual(over["score"], -under["score"])
        self.assertGreater(over["score"], 0.0)

    def test_the_component_is_available_on_the_quantile_path(self):
        self.assertTrue(_context(_QuantileStub())["available"])


class TheProbabilityIsTheHonestOne(unittest.TestCase):
    def test_the_reported_probability_is_the_calibrated_one(self):
        context = _context(_QuantileStub())
        self.assertAlmostEqual(context["p_over"], round(honest_p_over(Q25, Q50, Q75, LINE), 4))

    def test_it_is_labelled_with_what_produced_it(self):
        context = _context(_QuantileStub())
        self.assertEqual(context["details"]["calibration"], "pooled_gap_logistic")

    def test_it_stays_inside_the_range_the_fit_supports(self):
        """An absurd gap must not manufacture conviction.

        This test caught the opposite being true: with the gap unclamped, a
        line 40 below the median reported 0.906 from a model whose directional
        AUC is 0.53, because a logistic extrapolates without bound. The gap is
        now clamped to the fit's support (|gap| <= 12, its 99.9th percentile),
        which bounds the output at [0.340, 0.660].
        """
        for line in (Q50 - 400.0, Q50 - 40.0, Q50 - 10.0, Q50, Q50 + 10.0, Q50 + 400.0):
            probability = _context(_QuantileStub(), line=line)["p_over"]
            self.assertGreaterEqual(probability, 0.33)
            self.assertLessEqual(probability, 0.67)

    def test_the_clamp_binds_rather_than_the_pathology_guard(self):
        # PROB_FLOOR/PROB_CEIL are 0.02/0.98 and must never be what shapes a
        # real answer -- the fit support is.
        extreme = _context(_QuantileStub(), line=Q50 - 10_000.0)["p_over"]
        self.assertLess(extreme, 0.70)


class TheSingleOutputPathIsDark(unittest.TestCase):
    def test_a_model_without_quantiles_goes_dark(self):
        context = _context(_SingleOutputStub())

        self.assertFalse(context["available"])
        self.assertEqual(context["score"], 0.0)

    def test_it_still_reports_its_probability_for_the_record(self):
        # Dark means "do not rank with this", not "discard it".
        context = _context(_SingleOutputStub())

        self.assertIsNotNone(context["p_over"])
        self.assertEqual(context["details"]["calibration"], "gaussian_legacy")

    def test_going_dark_beats_scoring_zero_while_available(self):
        """Why dark rather than available-with-0, measured through the scorer."""
        def book(ml):
            components = {name: {"score": 0.44, "available": True, "details": {}}
                          for name in es.COMPONENT_WEIGHTS}
            components["ml_regression"] = ml
            return es._composite_edge_score(components, "over")["edge_score"]

        dark = book({"score": 0.0, "available": False, "p_over": 0.55, "details": {}})
        available_but_zero = book({"score": 0.0, "available": True, "p_over": 0.55, "details": {}})

        self.assertGreater(dark, available_but_zero)


if __name__ == "__main__":
    unittest.main()
