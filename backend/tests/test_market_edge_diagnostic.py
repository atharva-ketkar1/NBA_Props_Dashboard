"""The price taken, recorded against fair value, per published pick.

Additive only: `_market_edge_diagnostic` feeds no score and gates no alert.
Measured, gating on model-minus-market loses 3.8-4.5% ROI and is *worse* than
simply trusting the ml component's direction, which grades at -0.80% on the
shipped book selection. So this records the number rather than acting on it.

What earns its place is `opposite_odds` and the hold. The opposite price reached
the candidate but stopped at the persistence whitelists, so a published pick
could not be re-priced after the fact at all -- which is what closing-line value
needs. And a +2% edge over fair value loses into a 4.76% hold, so an edge
recorded without its hold cannot be read later.

The chain these tests exist for: `_tracker_state_snapshot_for_recommendations`
is *upstream* of `_serialize_recommendation_for_results` via
`_queue_results_recap_payload`, which is fed from
`_tracker_running_recommendations`, which is built out of that snapshot. A key
added only to the second never arrives, and nothing would have failed.
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es

EXPECTED_KEYS = {
    "market_p_over", "market_prob_pick", "model_prob_pick",
    "model_minus_market", "hold", "priced_both_sides",
}


def _chosen(odds=-110, opposite_odds=-110):
    return {"book": "fd", "line": 24.5, "odds": odds, "opposite_odds": opposite_odds}


def _diag(*, side="over", odds=-110, opposite_odds=-110, model_p_over=0.55, ml_available=True):
    return es._market_edge_diagnostic(
        _chosen(odds, opposite_odds),
        side=side,
        model_p_over=model_p_over,
        ml_available=ml_available,
    )


class ABalancedMarket(unittest.TestCase):
    def test_a_minus_110_both_ways_is_a_coin_flip_after_the_vig(self):
        result = _diag()

        self.assertAlmostEqual(result["market_prob_pick"], 0.5)
        self.assertAlmostEqual(result["market_p_over"], 0.5)
        self.assertAlmostEqual(result["hold"], 0.0476, places=4)
        self.assertTrue(result["priced_both_sides"])

    def test_an_asymmetric_market_is_pinned(self):
        # -120 implies 0.545455, +100 implies 0.5 -> 0.545455 / 1.045455
        result = _diag(odds=-120, opposite_odds=100)

        self.assertAlmostEqual(result["market_prob_pick"], 0.5217, places=4)
        self.assertAlmostEqual(result["hold"], 0.0455, places=4)

    def test_the_model_probability_is_oriented_to_the_pick(self):
        over = _diag(side="over", model_p_over=0.55)
        under = _diag(side="under", model_p_over=0.55)

        self.assertAlmostEqual(over["model_prob_pick"], 0.55)
        self.assertAlmostEqual(under["model_prob_pick"], 0.45)

    def test_market_p_over_stays_over_oriented_on_both_sides(self):
        """The two sides are only comparable in one orientation.

        Only one side of a prop survives the board's max(), so an
        over-oriented probability is the only form in which an over pick and an
        under pick can be compared to each other afterwards.
        """
        over = _diag(side="over", odds=-130, opposite_odds=110)
        under = _diag(side="under", odds=110, opposite_odds=-130)

        self.assertAlmostEqual(over["market_p_over"], under["market_p_over"])
        self.assertAlmostEqual(over["market_prob_pick"] + under["market_prob_pick"], 1.0)

    def test_the_edge_is_the_difference_of_the_two_probabilities(self):
        result = _diag(odds=-110, opposite_odds=-110, model_p_over=0.58)
        self.assertAlmostEqual(result["model_minus_market"], 0.08, places=4)


class ADarkModelRecordsNoOpinion(unittest.TestCase):
    """The likeliest way this would have gone quietly wrong.

    `honest_p_over` returns 0.5 for non-finite input, and the dark
    single-output path still populates `p_over`. Recording it unconditionally
    would fill the ledger with rows that look like maximal under-conviction
    from a model that had no opinion at all.
    """

    def test_a_dark_component_records_none_not_a_number(self):
        result = _diag(model_p_over=0.5, ml_available=False)

        self.assertIsNone(result["model_prob_pick"])
        self.assertIsNone(result["model_minus_market"])

    def test_the_market_side_is_still_recorded_when_the_model_is_dark(self):
        # Dark means "this model has no opinion", not "discard the price".
        result = _diag(model_p_over=0.5, ml_available=False)

        self.assertAlmostEqual(result["market_prob_pick"], 0.5)
        self.assertAlmostEqual(result["hold"], 0.0476, places=4)

    def test_the_obvious_wrong_implementation_fails_this(self):
        """`1 - p_over` unconditionally would return 0.5 here, not None.

        Written as its own case so the must-fail property is explicit rather
        than incidental to the case above.
        """
        self.assertIsNot(_diag(model_p_over=0.5, ml_available=False)["model_prob_pick"], 0.5)

    def test_a_missing_model_probability_is_also_none(self):
        result = _diag(model_p_over=None, ml_available=True)
        self.assertIsNone(result["model_prob_pick"])
        self.assertIsNone(result["model_minus_market"])


class OneSidedPricingDegradesCleanly(unittest.TestCase):
    def test_a_missing_opposite_price_yields_no_market_probability(self):
        result = _diag(opposite_odds=None)

        self.assertIsNone(result["market_prob_pick"])
        self.assertIsNone(result["market_p_over"])
        self.assertIsNone(result["model_minus_market"])
        self.assertIsNone(result["hold"])
        self.assertFalse(result["priced_both_sides"])

    def test_a_missing_chosen_price_yields_the_same(self):
        # side_odds can be None while a line exists.
        result = _diag(odds=None)

        self.assertIsNone(result["market_prob_pick"])
        self.assertIsNone(result["model_minus_market"])
        self.assertFalse(result["priced_both_sides"])

    def test_the_model_probability_survives_a_missing_price(self):
        # The two halves are independent: no price does not mean no opinion.
        result = _diag(opposite_odds=None, model_p_over=0.55)
        self.assertAlmostEqual(result["model_prob_pick"], 0.55)

    def test_the_shape_never_varies(self):
        for kwargs in ({}, {"opposite_odds": None}, {"odds": None},
                       {"ml_available": False}, {"model_p_over": None}):
            self.assertEqual(set(_diag(**kwargs)), EXPECTED_KEYS, msg=repr(kwargs))

    def test_the_existing_test_fixture_shape_does_not_raise(self):
        """`odds=-110.0` with no `opposite_odds` at all.

        That is the shape every `_recommendation` fixture in the suite
        produces, so it is the shape most likely to meet a production edge.
        """
        result = es._market_edge_diagnostic(
            {"book": "dk", "line": 20.5, "odds": -110.0},
            side="over", model_p_over=0.55, ml_available=True,
        )

        self.assertIsNone(result["market_prob_pick"])
        self.assertIsNone(result["model_minus_market"])
        self.assertIsNone(result["hold"])
        self.assertFalse(result["priced_both_sides"])


class ItSurvivesThePersistenceChain(unittest.TestCase):
    """Where the diagnostic would otherwise silently die.

    Two whitelists in series, the tracker snapshot upstream of the results
    serializer. Both are explicit key lists, so a new field reaches the graded
    ledger only if it is named in both.
    """

    RECOMMENDATION = {
        "recommendation_key": "1|2026-11-01|PTS|over",
        "player_id": 1, "player_name": "Test Player", "team": "DET", "opponent": "PHI",
        "game_id": "G1", "game_date": "2026-11-01", "stat_type": "PTS", "stat_label": "PTS",
        "pick": "over", "pick_label": "Over", "line": 24.5, "odds": -110.0,
        "opposite_odds": -110.0, "sportsbook": "fd", "sportsbook_label": "FanDuel",
        "edge_score": 80.0, "display_edge_score": 80.0, "rank": 1,
        "market_prob_pick": 0.5, "model_prob_pick": 0.55, "model_minus_market": 0.05,
    }
    CARRIED = ("opposite_odds", "market_prob_pick", "model_prob_pick", "model_minus_market")

    def test_the_tracker_snapshot_carries_them(self):
        snapshot = es._tracker_state_snapshot_for_recommendations([self.RECOMMENDATION])
        entry = snapshot[self.RECOMMENDATION["recommendation_key"]]

        for key in self.CARRIED:
            self.assertIn(key, entry, msg=key)
            self.assertEqual(entry[key], self.RECOMMENDATION[key], msg=key)

    def test_the_results_serializer_carries_them(self):
        serialized = es._serialize_recommendation_for_results(self.RECOMMENDATION)

        for key in self.CARRIED:
            self.assertIn(key, serialized, msg=key)
            self.assertAlmostEqual(serialized[key], self.RECOMMENDATION[key], msg=key)

    def test_they_survive_the_snapshot_then_the_serializer(self):
        """The actual production order, not each stage in isolation.

        The recap queue is built from the tracker snapshot's output, so this is
        the composition that has to work.
        """
        snapshot = es._tracker_state_snapshot_for_recommendations([self.RECOMMENDATION])
        from_snapshot = snapshot[self.RECOMMENDATION["recommendation_key"]]
        serialized = es._serialize_recommendation_for_results(from_snapshot)

        for key in self.CARRIED:
            self.assertIsNotNone(serialized[key], msg=key)
            self.assertAlmostEqual(serialized[key], self.RECOMMENDATION[key], msg=key)

    def test_the_resend_fingerprint_does_not_carry_them(self):
        """Deliberately excluded.

        `_state_snapshot_for_recommendations` decides whether a pick is
        re-published. A probability that moves on every five-minute refresh has
        no business in that comparison.
        """
        snapshot = es._state_snapshot_for_recommendations([self.RECOMMENDATION])
        entry = snapshot[self.RECOMMENDATION["recommendation_key"]]

        for key in ("market_prob_pick", "model_prob_pick", "model_minus_market"):
            self.assertNotIn(key, entry, msg=key)


class ItIsADiagnosticNotAScore(unittest.TestCase):
    def test_the_composite_never_sees_it(self):
        """No component, no weight, no effect on edge_score.

        Asserted by giving the composite the full component book and checking
        the diagnostic's keys are absent from what it reads.
        """
        components = {name: {"score": 0.44, "available": True, "details": {}}
                      for name in es.COMPONENT_WEIGHTS}
        components["ml_regression"] = {**components["ml_regression"], "p_over": 0.5}

        result = es._composite_edge_score(components, "over")

        for key in EXPECTED_KEYS:
            self.assertNotIn(key, result, msg=key)

    def test_the_market_component_weight_is_unchanged(self):
        # The diagnostic sits beside the market component, not inside it.
        self.assertAlmostEqual(es.COMPONENT_WEIGHTS["market"], 0.09)


if __name__ == "__main__":
    unittest.main()
