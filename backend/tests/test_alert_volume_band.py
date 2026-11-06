"""Alert volume needs a ceiling, not just a floor.

Before this file the suite held exactly one assertion comparing a composite
against the alert bar (`test_discord_alert_delivery.py:81`), and it was
`assertGreater` -- a one-way ratchet. Nothing could fail when scores or alert
counts went *up*.

That is the gap a reverted `p_over` calibration fell through. Standardizing the
calibrated probability lifted the board top from 68.8 to 74.8 and took alert
volume from 0 to roughly 10 a slate on a channel with no production precedent,
and a spam path let 0.01 of `p_over` move REB+AST by 8.7 edge points against the
repeat-alert delta on a five-minute refresh loop. The 377-test suite passed
throughout. Two of the tests written to cover that risk were themselves blind:
one did arithmetic on constants, the other asserted `signal > 0.70` and so
pinned the saturation as desired.

So these tests are deliberately two-sided. Each band is measured through the
real scoring path rather than re-derived from the weights, because a test that
recomputes the formula agrees with any formula.
"""

import sys
import unittest
from datetime import timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es


def _components(ml_score, *, p_over=None, ml_available=True, other=0.44):
    """A full-coverage component book, with ml_regression under test.

    `other=0.44` is the real raw weighted score of the 2026-10-25 opening
    board, so the composites below land in the range the channel actually sees.
    """
    components = {
        name: {"score": other, "available": True, "details": {}}
        for name in es.COMPONENT_WEIGHTS
    }
    components["ml_regression"] = {
        "score": ml_score,
        "available": ml_available,
        "p_over": p_over,
        "details": {},
    }
    return components


def _tomorrow():
    return (es.get_et_now() + timedelta(days=1)).strftime("%Y-%m-%d")


def _recommendation(*, player_id, game_id, score, rank, book="dk", stat_type="PTS"):
    game_date = _tomorrow()
    return {
        "recommendation_key": f"{player_id}|{game_date}|{stat_type}|over",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "team": "DET",
        "opponent": "PHI",
        "game_id": game_id,
        "game_date": game_date,
        "game_started": False,
        "sportsbook": book,
        "stat_type": stat_type,
        "stat_label": stat_type,
        "pick": "over",
        "pick_label": "Over",
        "line": 20.5,
        "odds": -110.0,
        "edge_score": score,
        "display_edge_score": score,
        "rank": rank,
    }


class AvailableButScorelessIsWorseThanDark(unittest.TestCase):
    """The renormalization trap, which is the dominant risk in recalibrating.

    `_component_available_weight` counts a component toward the denominator
    whenever `available` is truthy, regardless of whether it scored ~0. So a
    well-calibrated probability near 0.5 contributes nothing to the numerator
    while holding 0.25 of the denominator -- strictly worse than being dark,
    because a dark component leaves both sides.
    """

    def test_switching_the_component_off_raises_the_composite(self):
        available = es._composite_edge_score(_components(0.0, p_over=0.50), "over")
        dark = es._composite_edge_score(_components(0.0, ml_available=False), "over")

        self.assertGreater(dark["edge_score"], available["edge_score"])

    def test_the_penalty_for_staying_available_stays_in_a_known_band(self):
        # Measured at 4.3 points. Banded rather than pinned exactly so weight
        # tuning does not fail the test, but a change that doubles the
        # compression does.
        available = es._composite_edge_score(_components(0.0, p_over=0.50), "over")
        dark = es._composite_edge_score(_components(0.0, ml_available=False), "over")
        gap = dark["edge_score"] - available["edge_score"]

        self.assertGreater(gap, 3.0)
        self.assertLess(gap, 6.0)

    def test_coverage_is_reported_separately_from_the_score(self):
        available = es._composite_edge_score(_components(0.0, p_over=0.50), "over")
        dark = es._composite_edge_score(_components(0.0, ml_available=False), "over")

        self.assertEqual(available["confidence"], 100.0)
        self.assertEqual(dark["confidence"], 75.0)


class MlComponentSwingIsBounded(unittest.TestCase):
    def test_the_full_ml_swing_is_bounded_above_and_below(self):
        # 13.1 points measured: the 0.25 weight across the 45-point range plus
        # the 0.04 ml_boost. Upper bound is the guard -- it is what fails if a
        # future calibration widens the component's effective range.
        low = es._composite_edge_score(_components(0.0, p_over=0.50), "over")
        high = es._composite_edge_score(_components(1.0, p_over=0.90), "over")
        swing = high["edge_score"] - low["edge_score"]

        self.assertGreater(swing, 10.0)
        self.assertLess(swing, 16.0)

    def test_the_swing_does_not_depend_on_the_other_components(self):
        swings = []
        for other in (0.2, 0.44, 0.6):
            low = es._composite_edge_score(_components(0.0, p_over=0.50, other=other), "over")
            high = es._composite_edge_score(_components(1.0, p_over=0.90, other=other), "over")
            swings.append(high["edge_score"] - low["edge_score"])

        self.assertLess(max(swings) - min(swings), 1.0)

    def test_a_maximal_ml_component_cannot_alone_clear_the_alert_bar(self):
        # The code comment claims the boost "cannot override weak signals from
        # other components". This is that claim as a test: with the rest of the
        # book weak, a saturated ML probability must not alert on its own.
        saturated = es._composite_edge_score(
            _components(1.0, p_over=0.99, other=0.2), "over"
        )

        self.assertLess(saturated["edge_score"], es.EDGE_DISCORD_MIN_SIGNAL_SCORE)

    def test_the_boost_is_capped_at_its_documented_maximum(self):
        saturated = es._composite_edge_score(_components(1.0, p_over=1.0), "over")
        self.assertLessEqual(saturated["ml_boost"], 0.04)

    def test_a_disagreeing_model_penalises_rather_than_boosts(self):
        disagreeing = es._composite_edge_score(_components(0.44, p_over=0.05), "over")
        self.assertLess(disagreeing["ml_boost"], 0.0)
        self.assertGreaterEqual(disagreeing["ml_boost"], -0.03)


class MlBoostUnderAnHonestProbability(unittest.TestCase):
    """What the narrowed probability range does to the boost, stated not hidden.

    `ml_boost` fires on `p_over >= 0.60` and penalises on `<= 0.40`, thresholds
    set when p_over ranged over [0.25, 0.75]. An honest p_over spans roughly
    [0.30, 0.59], so the boost branch is unreachable for an over pick and the
    penalty branch is unreachable for an under -- the boost is now one-sided
    per side, and near-inert overall.

    That is the correct outcome for a signal whose AUC is 0.53: it should not
    be amplifying anything. What matters is that it does not become a standing
    tilt toward one side of every bet, because blind unders are measured at
    -3.07% ROI and that is the expensive tilt to acquire by accident. The
    guarantee is the symmetry below; the magnitude on real data is -0.0000 for
    overs against +0.0001 for unders.
    """

    @staticmethod
    def _boost(p_over, side):
        return es._composite_edge_score(_components(0.0, p_over=p_over), side)["ml_boost"]

    def test_the_boost_is_symmetric_under_flipping_side_and_probability(self):
        """boost(p, over) == boost(1 - p, under).

        This is what rules out a structural tilt. Any net lean therefore comes
        from the *distribution* of p_over, not from the function -- which is
        exactly where the legacy map's lean came from: its median p_over was
        0.405, well below even money, so the penalty branch fired on 48% of
        over picks and the boost branch on 48% of unders.
        """
        for i in range(0, 101):
            p = i / 100.0
            self.assertAlmostEqual(self._boost(p, "over"), self._boost(1.0 - p, "under"))

    def test_an_honest_probability_never_reaches_the_boost_branch(self):
        # Across the honest range, an over pick can only be penalised.
        boosts = [self._boost(0.30 + 0.01 * i, "over") for i in range(30)]
        self.assertLessEqual(max(boosts), 0.0)

    def test_the_boost_branch_still_exists_for_a_confident_probability(self):
        # Not dead code -- it fires if a future model earns it.
        self.assertGreater(self._boost(0.85, "over"), 0.0)

    def test_the_penalty_stays_inside_its_documented_bound(self):
        worst = min(self._boost(0.30 + 0.01 * i, "over") for i in range(30))
        self.assertGreaterEqual(worst, -0.03)


class AlertVolumeStaysInABand(unittest.TestCase):
    """Upper bounds on how many picks one snapshot can publish."""

    def test_the_rank_cap_binds_once_the_books_are_spread(self):
        # 40 candidates, 40 separate games, all far above the bar, alternating
        # books so the per-book cap (8 each, 16 together) cannot be what binds.
        # Then the rank cap is the only thing between this and 40 messages.
        #
        # Asserted as equality, not `<=`. An upper bound here passes while a
        # *different* cap quietly does the work: an earlier version of this
        # test spread nothing and asserted `<= 15`, so the per-book cap held it
        # at 8 and deleting the rank cap entirely did not fail anything.
        board = [
            _recommendation(
                player_id=i,
                game_id=f"G{i}",
                score=95.0,
                rank=i,
                book="dk" if i % 2 else "fd",
            )
            for i in range(1, 41)
        ]

        selected = es._filter_official_alert_recommendations(board)

        self.assertEqual(len(selected), es.EDGE_DISCORD_MAX_RANK)

    def test_the_per_book_cap_binds_on_a_single_book(self):
        board = [
            _recommendation(player_id=i, game_id=f"G{i}", score=95.0, rank=i)
            for i in range(1, 41)
        ]

        selected = es._filter_official_alert_recommendations(board)

        self.assertEqual(len(selected), es.EDGE_DISCORD_PER_BOOK_LIMIT)

    def test_lifting_every_score_cannot_raise_alert_volume(self):
        # The calibration failure mode: the whole board shifts up. Volume must
        # be decided by the caps, not by how high the scores went.
        at_bar = [
            _recommendation(player_id=i, game_id=f"G{i}", score=73.0, rank=i)
            for i in range(1, 41)
        ]
        inflated = [{**rec, "edge_score": 95.0, "display_edge_score": 95.0} for rec in at_bar]

        self.assertEqual(
            len(es._filter_official_alert_recommendations(inflated)),
            len(es._filter_official_alert_recommendations(at_bar)),
        )

    def test_a_board_below_the_bar_alerts_nothing(self):
        board = [
            _recommendation(player_id=i, game_id=f"G{i}", score=es.EDGE_DISCORD_MIN_SIGNAL_SCORE - 0.1, rank=i)
            for i in range(1, 11)
        ]

        self.assertEqual(es._filter_official_alert_recommendations(board), [])

    def test_one_game_cannot_fill_the_slate(self):
        board = [
            _recommendation(player_id=i, game_id="G1", score=95.0, rank=i)
            for i in range(1, 21)
        ]

        selected = es._filter_official_alert_recommendations(board)

        self.assertEqual(len(selected), es.EDGE_DISCORD_PER_GAME_LIMIT)

    def test_one_player_cannot_be_stacked_across_stats(self):
        # Correlated legs on the same player are never published together.
        board = [
            _recommendation(player_id=7, game_id="G1", score=95.0, rank=i, stat_type=stat)
            for i, stat in enumerate(["PTS", "REB", "AST", "PTS+REB+AST"], start=1)
        ]

        selected = es._filter_official_alert_recommendations(board)

        self.assertEqual(len(selected), 1)


class RepeatAlertsNeedAMaterialChange(unittest.TestCase):
    """The spam path: a tiny probability nudge must not re-publish a pick."""

    @staticmethod
    def _sent(score, line=20.5, odds=-110.0, stat_type="PTS"):
        return {
            "stat_type": stat_type,
            "line": line,
            "odds": odds,
            "display_edge_score": score,
        }

    def test_a_small_score_drift_is_not_a_major_change(self):
        sent = self._sent(80.0)
        nudged = _recommendation(player_id=1, game_id="G1", score=80.0, rank=1)
        nudged["display_edge_score"] = 80.0 + (es.EDGE_DISCORD_REPEAT_SCORE_DELTA - 0.1)

        self.assertFalse(es._discord_repeat_change_is_major(nudged, sent))

    def test_a_score_jump_past_the_delta_is_a_major_change(self):
        sent = self._sent(80.0)
        jumped = _recommendation(player_id=1, game_id="G1", score=80.0, rank=1)
        jumped["display_edge_score"] = 80.0 + es.EDGE_DISCORD_REPEAT_SCORE_DELTA

        self.assertTrue(es._discord_repeat_change_is_major(jumped, sent))

    def test_a_score_drop_never_re_alerts(self):
        # The gate is one-directional by design: a pick getting worse is not
        # news. Pinned so a refactor to abs() is caught.
        sent = self._sent(95.0)
        dropped = _recommendation(player_id=1, game_id="G1", score=60.0, rank=1)

        self.assertFalse(es._discord_repeat_change_is_major(dropped, sent))


if __name__ == "__main__":
    unittest.main()
