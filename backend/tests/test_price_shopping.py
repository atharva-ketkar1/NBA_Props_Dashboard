"""The price-shopping harness, and the one test that keeps its replica a replica.

`price_shopping.serve_path_pick` re-implements
`edge_score._select_market_for_side` offline so the shipped book selection can
be graded -- `backtest.py` grades one row per (player, date, book, stat) and
averages over books, which is not what the pipeline bets. A re-implementation
that drifts from the original measures nothing, and this repo has already shipped
that failure twice: a duplicated p_over map (fixed in 301a815) and a duplicated
`_american_to_implied` (fixed in 3f82bea). So the replica is pinned by calling
the real function and asserting both pick the same book, not by restating its
rules.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROP_MODELING_DIR = BACKEND_DIR / "prop_modeling"
for _p in (str(BACKEND_DIR), str(PROP_MODELING_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from prop_modeling import price_shopping as ps
from utils import edge_score as es


def _row(book, line, over_odds, under_odds, *, q=(15.0, 20.0, 25.0), result="over"):
    return {
        "player_id": "1", "game_date": "2026-11-01", "stat_type": "PTS",
        "sportsbook": book, "line": line,
        "over_odds": over_odds, "under_odds": under_odds,
        "payout_over": ps.american_to_payout_decimal(over_odds),
        "payout_under": ps.american_to_payout_decimal(under_odds),
        "q25": q[0], "q50": q[1], "q75": q[2],
        "actual": 25.0, "result": result,
    }


class TheReplicaMatchesTheServePath(unittest.TestCase):
    """Pinned against the real selector, over cases where the rules disagree.

    The book-toggle gate has to be neutralised first. `_select_market_for_side`
    filters on `_scoring_books()`, which subtracts `book_toggles.stale_books()`
    -- and that stats the mtime of `data/current/draftkings.csv` and
    `fanduel.csv`, treating missing or >90-minute-old files as stale. That
    directory is NOT git-tracked, so on a fresh clone both books are stale,
    `_scoring_books()` is empty, the real selector returns None and this test
    failed 12 subtests. It also failed 90 minutes after the last pipeline run
    on a warm copy, and 7 subtests with `EDGE_SCORE_PAUSED_BOOKS=dk`.

    Disabling the gate is correct here rather than merely convenient: this is a
    harness grading history, and whether a book's *current* CSV is fresh has no
    bearing on which book offered the better price last April. Mirrors the
    setUp in test_book_pause.py.
    """

    def setUp(self):
        self._env = patch.dict(os.environ, {"EDGE_SCORE_ODDS_AGE_GATE": "false"})
        self._env.start()
        os.environ.pop("EDGE_SCORE_PAUSED_BOOKS", None)
        self.addCleanup(self._env.stop)

    def test_the_real_selector_can_actually_see_both_books(self):
        """Guards the guard: if this fails, every subtest below is vacuous.

        A selector that returns None for both books would make
        assertEqual(mine, theirs) unreachable rather than true.
        """
        self.assertEqual(es._scoring_books(), {"dk", "fd"})

    CASES = [
        # (description, dk line/odds, fd line/odds)
        ("over prefers the lower line even at a worse price", (24.5, -130, +105), (25.5, -105, -115)),
        ("under prefers the higher line even at a worse price", (24.5, -110, -110), (25.5, -115, -130)),
        ("equal lines fall through to the cheaper implied", (24.5, -130, -110), (24.5, -105, -130)),
        ("equal lines and equal prices are indifferent", (24.5, -110, -110), (24.5, -110, -110)),
        ("one book missing a side price", (24.5, -110, None), (24.5, -115, -115)),
        ("wide line gap", (20.5, +150, -200), (28.5, -300, +220)),
    ]

    def test_both_selectors_choose_the_same_book(self):
        for description, (dk_line, dk_over, dk_under), (fd_line, fd_over, fd_under) in self.CASES:
            group = [
                _row("dk", dk_line, dk_over, dk_under),
                _row("fd", fd_line, fd_over, fd_under),
            ]
            book_props = {
                "dk": {"line": dk_line, "over": dk_over, "under": dk_under},
                "fd": {"line": fd_line, "over": fd_over, "under": fd_under},
            }
            for side in ("over", "under"):
                with self.subTest(case=description, side=side):
                    mine = ps.serve_path_pick(group, side)
                    theirs = es._select_market_for_side("PTS", book_props, side)

                    self.assertIsNotNone(mine)
                    self.assertIsNotNone(theirs)
                    self.assertEqual(mine["sportsbook"], theirs["chosen"]["book"])
                    self.assertAlmostEqual(mine["line"], theirs["chosen"]["line"])

    def test_an_over_takes_the_lower_line(self):
        group = [_row("dk", 24.5, -130, +105), _row("fd", 25.5, -105, -115)]
        self.assertEqual(ps.serve_path_pick(group, "over")["sportsbook"], "dk")

    def test_an_under_takes_the_higher_line(self):
        group = [_row("dk", 24.5, -110, -110), _row("fd", 25.5, -115, -130)]
        self.assertEqual(ps.serve_path_pick(group, "under")["sportsbook"], "fd")

    def test_the_selection_is_deterministic_on_a_tie(self):
        # Production keeps the first book in the feed's dict order; offline there
        # is no feed, so book-name order is used. Either way it must not vary
        # between runs, or two variants become incomparable.
        group = [_row("fd", 24.5, -110, -110), _row("dk", 24.5, -110, -110)]
        picks = {ps.serve_path_pick(list(reversed(group)), "over")["sportsbook"],
                 ps.serve_path_pick(group, "over")["sportsbook"]}
        self.assertEqual(len(picks), 1)


class BestPriceSelection(unittest.TestCase):
    def test_it_takes_the_higher_payout_for_the_chosen_side(self):
        group = [_row("dk", 24.5, -130, -110), _row("fd", 24.5, -105, -130)]

        self.assertEqual(ps.best_price_row(group, "over")["sportsbook"], "fd")
        self.assertEqual(ps.best_price_row(group, "under")["sportsbook"], "dk")

    def test_worst_is_the_mirror_of_best(self):
        group = [_row("dk", 24.5, -130, -110), _row("fd", 24.5, -105, -130)]

        self.assertEqual(ps.best_price_row(group, "over", worst=True)["sportsbook"], "dk")
        self.assertEqual(ps.best_price_row(group, "under", worst=True)["sportsbook"], "fd")

    def test_it_never_selects_a_row_with_no_price(self):
        """The one way this measurement could come back falsely positive.

        An unpriced row valued at even money pays 1.0, which beats every
        favourite-priced real quote, so a `max(payout)` would pick it every
        time and manufacture a positive return out of a row that had no price.
        """
        group = [_row("dk", 24.5, None, -110), _row("fd", 24.5, -200, -110)]

        pick = ps.best_price_row(group, "over")
        self.assertEqual(pick["sportsbook"], "fd")
        self.assertIsNotNone(pick["payout_over"])

    def test_a_group_with_no_price_at_all_selects_nothing(self):
        group = [_row("dk", 24.5, None, -110), _row("fd", 24.5, None, -110)]
        self.assertIsNone(ps.best_price_row(group, "over"))

    def test_a_single_book_group_returns_that_book(self):
        group = [_row("dk", 24.5, -110, -110)]
        self.assertEqual(ps.best_price_row(group, "over")["sportsbook"], "dk")


class TheServePathAlreadyShopsPrice(unittest.TestCase):
    """The conclusion of the whole measurement, pinned as an invariant.

    On groups where both books quote the same line, variant (B) and variant (A)
    return identical ROI -- measured at -1.34% on 7,170 groups, to the digit.
    Not a coincidence: `_select_market_for_side` breaks a line tie on the
    *cheapest implied*, and implied probability is strictly monotone decreasing
    in payout across the whole odds range. So argmin(implied) is argmax(payout),
    and the shipped selection was already taking the best available price.

    The 3-point gap between best and worst price is therefore value the pipeline
    already captures, not value sitting unclaimed. If this invariant ever breaks,
    the conclusion "there is nothing to gain from shopping two books" stops
    holding and the measurement has to be re-run.
    """

    def test_cheapest_implied_is_always_the_best_payout(self):
        from prop_modeling.market_prices import american_to_implied, american_to_payout_decimal
        priced = sorted(
            (american_to_implied(o), american_to_payout_decimal(o))
            for o in range(-10000, 10001) if abs(o) >= 100
        )
        for (implied_a, payout_a), (implied_b, payout_b) in zip(priced, priced[1:]):
            self.assertGreaterEqual(payout_a, payout_b,
                                    msg="implied %.6f -> %.6f" % (implied_a, implied_b))

    def test_the_two_selectors_agree_whenever_the_lines_match(self):
        cases = [
            ((-130, -110), (-105, -130)),
            ((+150, -200), (+110, -180)),
            ((-110, -110), (-110, -110)),
            ((-250, +200), (-300, +240)),
        ]
        for dk, fd in cases:
            group = [_row("dk", 24.5, dk[0], dk[1]), _row("fd", 24.5, fd[0], fd[1])]
            for side in ("over", "under"):
                with self.subTest(dk=dk, fd=fd, side=side):
                    self.assertEqual(
                        ps.serve_path_pick(group, side)["sportsbook"],
                        ps.best_price_row(group, side)["sportsbook"],
                    )

    def test_they_may_disagree_when_the_lines_differ(self):
        # The serve path trades price for a better line, deliberately. That is
        # where (B) earns its margin over (A), and it is why (B) is run on the
        # full population rather than only same-line groups.
        group = [_row("dk", 24.5, -130, +105), _row("fd", 25.5, -105, -115)]

        self.assertEqual(ps.serve_path_pick(group, "over")["sportsbook"], "dk")   # lower line
        self.assertEqual(ps.best_price_row(group, "over")["sportsbook"], "fd")    # better price


class GradingRefusesAnUnpricedRow(unittest.TestCase):
    def test_a_push_is_not_a_bet(self):
        self.assertIsNone(ps.profit(_row("dk", 25.0, -110, -110, result="push"), "over"))

    def test_a_win_pays_the_net_payout_and_a_loss_costs_one_unit(self):
        row = _row("dk", 24.5, -110, -110, result="over")
        self.assertAlmostEqual(ps.profit(row, "over"), 0.909091, places=6)
        self.assertAlmostEqual(ps.profit(row, "under"), -1.0)

    def test_grading_an_unpriced_row_raises_rather_than_inventing_a_number(self):
        row = _row("dk", 24.5, None, -110, result="over")
        with self.assertRaises(AssertionError):
            ps.profit(row, "over")


class TheMlSideIsTheComponentsSide(unittest.TestCase):
    def test_a_line_below_the_median_is_an_over(self):
        side, magnitude = ps.ml_side_at(_row("dk", 15.0, -110, -110), 15.0)
        self.assertEqual(side, "over")
        self.assertGreater(magnitude, 0.0)

    def test_a_line_above_the_median_is_an_under(self):
        side, magnitude = ps.ml_side_at(_row("dk", 25.0, -110, -110), 25.0)
        self.assertEqual(side, "under")
        self.assertGreater(magnitude, 0.0)

    def test_the_magnitude_is_never_negative(self):
        # It is the component's score for its own preferred side, so the sign
        # lives in the side, not the number. serve_pairs relies on this.
        for line in (0.0, 10.0, 30.0, 100.0):
            opinion = ps.ml_side_at(_row("dk", line, -110, -110), line)
            self.assertIsNotNone(opinion, msg=line)
            self.assertGreater(opinion[1], 0.0, msg=line)

    def test_no_opinion_is_none_rather_than_a_default_over(self):
        """A degenerate interval must not become a real wager.

        `ranking_signal` returns exactly 0.0 for a crossed or zero-width
        interval, and `signal >= 0` previously mapped that to an over bet --
        scoring a bet the serve path would never place, since the ml component
        goes dark there via `has_usable_spread`.
        """
        zero_width = _row("dk", 20.0, -110, -110, q=(20.0, 20.0, 20.0))
        crossed = _row("dk", 20.0, -110, -110, q=(25.0, 20.0, 15.0))
        on_the_median = _row("dk", 20.0, -110, -110, q=(15.0, 20.0, 25.0))

        self.assertIsNone(ps.ml_side_at(zero_width, 20.0))
        self.assertIsNone(ps.ml_side_at(crossed, 20.0))
        # A line exactly on the median is also no opinion, not an over.
        self.assertIsNone(ps.ml_side_at(on_the_median, 20.0))


class BandsAccountForClustering(unittest.TestCase):
    def test_the_clustered_band_is_wider_when_rows_repeat_within_a_cluster(self):
        """Why the bootstrap is there at all.

        Four correlated stats on one player-game carry one game's worth of
        information, not four. An iid band treats them as four.
        """
        pnl = [0.9, 0.9, 0.9, 0.9, -1.0, -1.0, -1.0, -1.0] * 10
        tight = ps.roi_with_bands(pnl, list(range(len(pnl))), draws=400)
        loose = ps.roi_with_bands(pnl, [i // 4 for i in range(len(pnl))], draws=400)

        self.assertAlmostEqual(tight["roi"], loose["roi"])
        self.assertGreater(loose["cluster_2sd"], tight["cluster_2sd"])

    def test_an_empty_sample_reports_nothing_rather_than_zero(self):
        self.assertEqual(ps.roi_with_bands([], [])["roi"], None)

    def test_only_shoppable_books_are_ever_compared(self):
        self.assertEqual(set(ps.SHOPPABLE_BOOKS), {"dk", "fd"})

    def test_an_unshoppable_book_in_the_cache_is_refused(self):
        """Exercises the guard rather than restating the constant.

        The assertion above is satisfied by the tuple's value whether or not
        any code filters on it -- deleting the restriction in build_cache and
        deleting load_cache's raise both survived it. PrizePicks rows carry no
        single-leg price, so one reaching a max(payout) comparison is the one
        way this measurement could come back falsely positive.
        """
        header = ",".join(ps.CACHE_COLUMNS)
        row = "2026-11-01,PTS,1,prizepicks,20.5,,,,,0.5,25.0,over,15.0,20.0,25.0"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.csv"
            path.write_text(header + "\n" + row + "\n")

            with self.assertRaises(AssertionError):
                ps.load_cache(path, since=None)

    def test_a_shoppable_book_loads_normally(self):
        # So the test above fails for the book, not for a malformed row.
        header = ",".join(ps.CACHE_COLUMNS)
        row = "2026-11-01,PTS,1,dk,20.5,-110,-110,0.909091,0.909091,0.5,25.0,over,15.0,20.0,25.0"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.csv"
            path.write_text(header + "\n" + row + "\n")

            self.assertEqual(len(ps.load_cache(path, since=None)), 1)

    def test_the_roi_itself_is_pinned_not_only_its_bands(self):
        """`sum(pnl) / (n + 1)` survived the whole file before this.

        Every band comparison holds under any shared denominator, so the
        headline number each variant reports was unguarded.
        """
        pnl = [0.909091, -1.0, 0.909091, -1.0]
        result = ps.roi_with_bands(pnl, [0, 1, 2, 3], draws=200)

        self.assertEqual(result["n"], 4)
        self.assertAlmostEqual(result["roi"], -0.0454545, places=6)
        # Pins the 2.0 multiplier and the Bessel correction, not just the sign:
        # var(ddof=1) = 1.214876, so 2 * sqrt(var / 4) = 1.102214.
        self.assertAlmostEqual(result["iid_2sd"], 1.102214, places=5)


if __name__ == "__main__":
    unittest.main()
