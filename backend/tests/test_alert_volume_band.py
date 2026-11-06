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

    def test_the_confidence_multiplier_discounts_a_thin_book(self):
        # At full coverage the multiplier is exactly 1.0, so fixtures at 100%
        # and 75% hid it entirely: hardcoding it to 1.0 left the suite green.
        full = {name: {"score": 0.44, "available": True, "details": {}}
                for name in es.COMPONENT_WEIGHTS}
        thin = {name: {"score": 0.44, "available": name in ("projection", "ml_regression",
                                                            "recent_form", "matchup"),
                       "details": {}}
                for name in es.COMPONENT_WEIGHTS}
        for book in (full, thin):
            book["ml_regression"] = {**book["ml_regression"], "p_over": 0.50}

        full_result = es._composite_edge_score(full, "over")
        thin_result = es._composite_edge_score(thin, "over")

        self.assertEqual(full_result["confidence"], 100.0)
        self.assertLess(thin_result["confidence"], 80.0)

        # And the composite goes UP, not down. I assumed the opposite and the
        # test caught it. Renormalizing over 0.72 of available weight is a
        # 1.39x amplification, while the confidence multiplier only discounts
        # by 0.958 -- so at equal component scores, losing coverage *raises*
        # the score by about a third. That is the documented design (a dark
        # component widens the error bars rather than capping the scale), but
        # it means darkening a component is not free, and it is why the
        # available-but-scoreless case above is the bad one: it pays the
        # denominator without the numerator.
        self.assertGreater(thin_result["edge_score"], full_result["edge_score"])

        # Pinned as literals. A ratio band absorbed the multiplier entirely:
        # hardcoding it to 1.0 moves the thin score 76.3 -> 77.5, which any
        # band loose enough to be robust will swallow.
        self.assertAlmostEqual(full_result["edge_score"], 69.8, places=1)
        self.assertAlmostEqual(thin_result["edge_score"], 76.3, places=1)

    def test_coverage_is_reported_separately_from_the_score(self):
        available = es._composite_edge_score(_components(0.0, p_over=0.50), "over")
        dark = es._composite_edge_score(_components(0.0, ml_available=False), "over")

        self.assertEqual(available["confidence"], 100.0)
        self.assertEqual(dark["confidence"], 75.0)


class MlComponentSwingIsBounded(unittest.TestCase):
    def test_the_full_ml_swing_is_bounded_above_and_below(self):
        # The theoretical ceiling: score 1.0 and a saturating p_over. Neither is
        # emittable now (_RANKING_CAP is 0.5 and honest p_over stops at 0.66),
        # so this bounds the component's *definition*, and
        # test_the_reachable_swing_is_half_of_that bounds what ships.
        low = es._composite_edge_score(_components(0.0, p_over=0.50), "over")
        high = es._composite_edge_score(_components(1.0, p_over=0.90), "over")
        swing = high["edge_score"] - low["edge_score"]

        self.assertGreater(swing, 10.0)
        self.assertLess(swing, 16.0)

    def test_the_reachable_swing_is_half_of_that(self):
        """What the serve path can actually produce, which is the real ceiling.

        ranking_signal is capped at 0.5, so the component's reach is half its
        definition. Pinning only the theoretical ceiling left ~2x of slack in
        the guard this file exists to provide.
        """
        from prop_modeling.p_over_calibration import ranking_signal
        reachable = max(abs(ranking_signal(10.0, 20.0, 30.0, line))
                        for line in (-200.0, 0.0, 10.0, 20.0, 30.0, 200.0))
        low = es._composite_edge_score(_components(-reachable, p_over=0.50), "over")
        high = es._composite_edge_score(_components(reachable, p_over=0.50), "over")

        self.assertLessEqual(reachable, 0.5)
        self.assertLess(high["edge_score"] - low["edge_score"], 12.0)

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

    def test_the_boost_branch_is_out_of_reach_on_a_typical_board(self):
        """Typical, not impossible -- the distinction I got wrong.

        I first asserted the boost branch was unreachable for an over pick.
        That is false of the function: honest_p_over tops out at 0.660 and so
        fires the branch once the gap exceeds ~7.5. It is true of the observed
        board, where 0 of 10,625 rows reached 0.60 and |gap| is 5.3 at the 95th
        percentile.
        """
        typical = [self._boost(0.30 + 0.01 * i, "over") for i in range(30)]
        self.assertLessEqual(max(typical), 0.0)

    def test_a_large_enough_gap_does_reach_it(self):
        from prop_modeling.p_over_calibration import honest_p_over
        self.assertGreater(honest_p_over(10.0, 20.0, 30.0, 20.0 - 12.0), 0.60)

    def test_the_boost_branch_still_exists_for_a_confident_probability(self):
        # Not dead code -- it fires if a future model earns it.
        self.assertGreater(self._boost(0.85, "over"), 0.0)

    def test_a_dark_component_gets_no_boost_even_with_a_confident_probability(self):
        # `if p_over is not None and components["ml_regression"].get("available")`
        # -- the second half was untested, because the dark fixture also left
        # p_over None. The real dark path returns a gaussian_legacy p_over, so a
        # boost could leak onto a component that is not ranking at all.
        dark = _components(0.0, p_over=0.95, ml_available=False)
        self.assertEqual(es._composite_edge_score(dark, "over")["ml_boost"], 0.0)

    def test_the_penalty_stays_inside_its_documented_bound(self):
        worst = min(self._boost(0.30 + 0.01 * i, "over") for i in range(30))
        self.assertGreaterEqual(worst, -0.03)


class TheHonestMapRemovesTheLegacyLean(unittest.TestCase):
    """The distribution of p_over is what tilts the board, not the function.

    `boost(p, over) == boost(1 - p, under)` always held, so pinning that
    symmetry proved nothing. What changed is the distribution fed through it:
    the legacy map sends a symmetric spread of gaps to an asymmetric spread of
    probabilities, because it centres on the IQR midpoint rather than the
    median and then saturates. The honest map does not.

    This matters because the board publishes one pick per game and one prop per
    player, so a relative shift between the two sides of the same prop changes
    which side publishes, not just its score.
    """

    # Skewed quantiles: q50=20 sits below the (18, 30) midpoint of 24, which is
    # the ordinary shape of a count distribution.
    SKEWED = (18.0, 20.0, 30.0)

    @staticmethod
    def _net_lean(probability_fn, quantiles):
        """mean(boost | under) - mean(boost | over) over a symmetric gap sweep."""
        q25, q50, q75 = quantiles
        gaps = [g * 0.5 for g in range(-12, 13)]
        probs = [probability_fn(q25, q50, q75, q50 - gap) for gap in gaps]
        over = sum(es._composite_edge_score(_components(0.0, p_over=p), "over")["ml_boost"]
                   for p in probs) / len(probs)
        under = sum(es._composite_edge_score(_components(0.0, p_over=p), "under")["ml_boost"]
                    for p in probs) / len(probs)
        return under - over

    def test_the_honest_map_has_no_net_lean(self):
        from prop_modeling.p_over_calibration import honest_p_over
        lean = self._net_lean(honest_p_over, self.SKEWED)
        self.assertLess(abs(lean), 0.001)

    def test_the_legacy_map_did_have_one(self):
        from prop_modeling.p_over_calibration import legacy_p_over
        lean = self._net_lean(legacy_p_over, self.SKEWED)
        self.assertGreater(abs(lean), 0.005)

    def test_the_lean_was_worth_a_fraction_of_an_edge_point(self):
        # 0.019 of weighted score -> ~0.19 composite points of relative shift
        # between the two sides of one prop. Small, but the board is rank-capped.
        from prop_modeling.p_over_calibration import legacy_p_over
        points = abs(self._net_lean(legacy_p_over, self.SKEWED)) * es.COMPONENT_WEIGHTS["ml_regression"] * 45.0
        self.assertGreater(points, 0.05)
        self.assertLess(points, 1.0)


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
        """Correlated legs on one player are never published together.

        Each stat gets its OWN game id. An earlier version put all four in one
        game, so the per-game cap of 1 returned 1 on its own and deleting both
        the per-player cap and the correlated-leg dedupe changed nothing
        observable -- the same blindness as the rank-cap test before it. A
        player cannot really appear in four games at once; the fixture is
        contrived precisely so that only the per-player cap can bind.
        """
        board = [
            _recommendation(player_id=7, game_id=f"G{i}", score=95.0, rank=i, stat_type=stat)
            for i, stat in enumerate(["PTS", "REB", "AST", "PTS+REB+AST"], start=1)
        ]

        selected = es._filter_official_alert_recommendations(board)

        self.assertEqual(len(selected), es.EDGE_DISCORD_PER_PLAYER_LIMIT)

    def test_a_candidate_exactly_on_the_bar_is_selected(self):
        # The gate is `signal_score < min_signal_score`. Nothing sat exactly on
        # 72.5, so flipping it to `<=` silenced a board at the bar undetected.
        board = [_recommendation(player_id=1, game_id="G1",
                                 score=es.EDGE_DISCORD_MIN_SIGNAL_SCORE, rank=1)]

        self.assertEqual(len(es._filter_official_alert_recommendations(board)), 1)

    def test_the_board_ranks_on_the_displayed_score_when_they_differ(self):
        # Every other fixture sets edge_score == display_edge_score, so which
        # one the filter reads was unpinned. The guardrail can lower the
        # displayed score below the bar, and that is the one that must gate.
        below = _recommendation(player_id=1, game_id="G1", score=95.0, rank=1)
        below["display_edge_score"] = es.EDGE_DISCORD_MIN_SIGNAL_SCORE - 1.0

        self.assertEqual(es._filter_official_alert_recommendations([below]), [])


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

    def test_the_delta_is_large_enough_to_stop_refresh_spam(self):
        """Pins the magnitude, not just the boundary.

        Every other test here derives its fixture from
        EDGE_DISCORD_REPEAT_SCORE_DELTA itself, so they pin the operator and
        say nothing about the value: dropping it to 1.0 or raising it to 40.0
        both left the suite green. At 1.0 a one-point drift re-publishes a pick
        on a five-minute refresh loop; at 40.0 repeat alerts never fire at all.
        """
        # Above 6.0, which is the floor EDGE_DISCORD_SCORE_DELTA imposes via
        # max() -- so this pins the 8.0 default rather than the floor.
        self.assertGreater(es.EDGE_DISCORD_REPEAT_SCORE_DELTA, 6.0)
        self.assertLessEqual(es.EDGE_DISCORD_REPEAT_SCORE_DELTA, 15.0)

        sent = self._sent(80.0)
        small = _recommendation(player_id=1, game_id="G1", score=80.0, rank=1)
        small["display_edge_score"] = 85.0          # a 5-point drift, absolute
        self.assertFalse(es._discord_repeat_change_is_major(small, sent))

    def test_a_score_drop_never_re_alerts(self):
        # The gate is one-directional by design: a pick getting worse is not
        # news. Pinned so a refactor to abs() is caught.
        sent = self._sent(95.0)
        dropped = _recommendation(player_id=1, game_id="G1", score=60.0, rank=1)

        self.assertFalse(es._discord_repeat_change_is_major(dropped, sent))


if __name__ == "__main__":
    unittest.main()
