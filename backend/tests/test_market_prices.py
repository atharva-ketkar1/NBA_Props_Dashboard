"""Sportsbook price arithmetic, defined once and pinned.

`market_prices.py` consolidated three formulas that previously lived in two
places: `_american_to_implied` existed in both `utils/edge_score.py` and
`prop_modeling/build_prop_dataset.py`, and `_no_vig_probability` /
`_american_to_payout_decimal` only in the latter. Commit 301a815 did the same
for the quantile-to-p_over map; commit 58f9b49 then had to fix the tests that
could not fail. So this file carries two source-scanning assertions as well as
value tests: one that the module stays importable from the serve path, and one
that the formulas do not get re-duplicated.

The consolidation is behaviour-identical to what shipped: checked against both
HEAD originals over 40,029 numeric inputs, 13 pathological ones and a 121-pair
de-vig grid, with zero mismatches against the `edge_score` copy.

It is NOT identical to the `build_prop_dataset` copy, and that copy was wrong.
Being typed `Optional[float]` it did no coercion, so `nan` in gave **`nan`
out** -- a NaN propagating into the `no_vig_side_prob` training feature -- while
`inf` gave `0.0` and a string raised TypeError. Every one of its call sites
pre-coerces through `_safe_float` (`build_prop_dataset.py:748-749, :797-798,
:578-579`), so the bug was unreachable, which is exactly why it survived.
"""

import ast
import math
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from prop_modeling import market_prices as mp
from prop_modeling.market_prices import (
    american_to_implied,
    american_to_payout_decimal,
    even_money_payout_decimal,
    hold,
    no_vig_from_american,
    no_vig_probability,
)
from utils import edge_score as es

# A spread of real American prices, favourites and dogs.
ODDS_GRID = [-300, -200, -150, -120, -115, -110, -105, 100, 105, 110, 115, 120, 150, 200, 300]


class AmericanToImplied(unittest.TestCase):
    def test_pinned_values(self):
        # Pinned rather than re-derived, so a rewrite is caught instead of
        # agreeing with itself.
        for odds, expected in (
            (-300, 0.750000),
            (-200, 0.666667),
            (-150, 0.600000),
            (-110, 0.523810),
            (-105, 0.512195),
            (+100, 0.500000),
            (+105, 0.487805),
            (+110, 0.476190),
            (+150, 0.400000),
            (+300, 0.250000),
        ):
            self.assertAlmostEqual(american_to_implied(odds), expected, places=6, msg=repr(odds))

    def test_a_pick_em_is_a_coin_flip_either_way(self):
        self.assertAlmostEqual(american_to_implied(100), 0.5)
        self.assertAlmostEqual(american_to_implied(-100), 0.5)

    def test_it_is_monotone_in_the_price(self):
        # ODDS_GRID runs heaviest favourite to biggest dog, so implied
        # probability must fall monotonically across it.
        implieds = [american_to_implied(o) for o in ODDS_GRID]
        self.assertEqual(implieds, sorted(implieds, reverse=True))
        self.assertGreater(implieds[0], implieds[-1])

    def test_everything_unpriceable_is_none(self):
        # Matches edge_score._safe_float's notion of missing, including its
        # rejection of NaN and infinities. The old dataset copy returned nan
        # for nan and 0.0 for inf.
        for value in (None, 0, 0.0, "", "abc", [], {}, float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(american_to_implied(value), msg=repr(value))

    def test_numeric_strings_are_accepted(self):
        # The edge_score copy coerced; the dataset copy raised TypeError. The
        # permissive form is a strict superset of both.
        self.assertAlmostEqual(american_to_implied("-110"), 0.523810, places=6)
        self.assertAlmostEqual(american_to_implied(" -110 "), 0.523810, places=6)


class NoVig(unittest.TestCase):
    def test_a_balanced_market_is_even(self):
        self.assertAlmostEqual(no_vig_from_american(-110, -110), 0.5)

    def test_the_two_sides_always_sum_to_one(self):
        for a in ODDS_GRID:
            for b in ODDS_GRID:
                self.assertAlmostEqual(
                    no_vig_from_american(a, b) + no_vig_from_american(b, a), 1.0, places=12,
                    msg="%s / %s" % (a, b),
                )

    def test_pinned_asymmetric_market(self):
        # -120 implies 0.545455, +100 implies 0.5 -> 0.545455/1.045455
        self.assertAlmostEqual(no_vig_from_american(-120, 100), 0.521739, places=6)

    def test_one_sided_pricing_yields_nothing_rather_than_a_vigged_number(self):
        # The whole point of returning None: a one-sided quote cannot be
        # de-vigged, and silently handing back the vigged probability would
        # overstate every edge computed from it.
        self.assertIsNone(no_vig_from_american(-110, None))
        self.assertIsNone(no_vig_from_american(None, -110))
        self.assertIsNone(no_vig_from_american(-110, 0))

    def test_degenerate_inputs(self):
        self.assertIsNone(no_vig_probability(0.0, 0.0))
        self.assertIsNone(no_vig_probability(-0.5, 0.5))
        self.assertIsNone(no_vig_probability(None, 0.5))


class Hold(unittest.TestCase):
    def test_a_standard_two_way_market(self):
        # 0.523810 * 2 - 1. Note this is 4.76%, not the 4.5% quoted in the
        # plan that produced this module -- the approximation was mine.
        self.assertAlmostEqual(hold(-110, -110), 0.047619, places=6)

    def test_a_tighter_market_holds_less(self):
        self.assertLess(hold(-105, -105), hold(-110, -110))

    def test_it_is_symmetric(self):
        self.assertAlmostEqual(hold(-120, 100), hold(100, -120))

    def test_one_sided_pricing_has_no_measurable_hold(self):
        self.assertIsNone(hold(-110, None))

    def test_the_hold_is_what_an_edge_has_to_clear(self):
        """Why this function exists at all.

        The model's chosen side beats its own de-vigged price by about +2%.
        That is less than the hold on a -110/-110 market, which is why the
        measured ROI is negative. An edge estimate recorded without its hold
        cannot be read later.
        """
        self.assertGreater(hold(-110, -110), 0.02)


class ThePayoutTrap(unittest.TestCase):
    """Two functions on purpose, because the substitution is right once.

    `even_money_payout_decimal` fabricates a +100 price for an unpriced row.
    That is correct in the dataset builder -- PrizePicks rows carry no
    single-leg American odds and the column is a training/sorting input. It is
    a landmine in a grading path: a selection that maximises payout will pick
    the fabricated price and manufacture a positive return out of nothing.
    A single function with a `default=` argument lets that be re-entered by
    omission.
    """

    def test_both_agree_whenever_there_is_a_price(self):
        for odds in ODDS_GRID:
            self.assertEqual(american_to_payout_decimal(odds), even_money_payout_decimal(odds))

    def test_they_differ_exactly_where_the_price_is_missing(self):
        for missing in (None, 0, "", "abc", float("nan")):
            self.assertIsNone(american_to_payout_decimal(missing), msg=repr(missing))
            self.assertEqual(even_money_payout_decimal(missing), 1.0, msg=repr(missing))

    def test_payout_is_net_profit_not_total_return(self):
        """-110 returns 0.909, not 1.909.

        Getting this backwards turns every win into a small loss. It produced
        an ROI of -53% in an earlier measurement, which is arithmetically
        impossible -- losing every bet is -100% and a -110 coin flip is -4.5%.
        """
        self.assertAlmostEqual(american_to_payout_decimal(-110), 0.909091, places=6)
        self.assertAlmostEqual(american_to_payout_decimal(100), 1.0)
        self.assertAlmostEqual(american_to_payout_decimal(150), 1.5)

    def test_a_losing_unit_stake_and_a_winning_one_are_symmetric_at_evens(self):
        payout = american_to_payout_decimal(100)
        self.assertAlmostEqual(payout, 1.0)          # win: +1.0, loss: -1.0


class TheHarnessRefusesToGradeAnUnpricedRow(unittest.TestCase):
    """`build_rows` must drop a row it cannot price, not value it at evens.

    Latent today: all 60,784 graded rows are dk or fd and fully priced, and
    historical_player_props holds no PrizePicks rows at all. The guard exists
    because price_shopping.py selects by `max(payout)`, and a fabricated +100
    would win that comparison every time and manufacture a positive return out
    of a row with no price.
    """

    @staticmethod
    def _line(**overrides):
        line = {
            "player_id": "1", "game_date": "2026-11-01", "stat_type": "PTS",
            "sportsbook": "dk", "line": 20.5, "over_odds": -110, "under_odds": -110,
            "source": "closing_line", "is_closing_line": True,
        }
        line.update(overrides)
        return line

    @staticmethod
    def _outcomes():
        return {("1", "2026-11-01"): {"PTS": 25.0, "MIN": 30.0}}

    def _build(self, **overrides):
        import prop_modeling.backtest as bt
        return bt.build_rows([self._line(**overrides)], self._outcomes())

    def test_a_fully_priced_row_grades(self):
        rows, skips = self._build()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["payout_over"], 0.909091, places=6)
        self.assertEqual(skips.get("no_price", 0), 0)

    def test_a_row_missing_one_price_keeps_the_side_that_is_priced(self):
        """Refusal is per side, not per row.

        An earlier version dropped the whole row when either price was
        missing. That was stricter than the thing it protected: `build_rows` is
        the single join behind every metric here, and a book pulling one side
        would have removed the row from the blind-under ROI and the market
        calibration tables, neither of which needs the other price.
        """
        import prop_modeling.backtest as bt

        rows, skips = self._build(over_odds=None)
        self.assertEqual(len(rows), 1)
        self.assertEqual(skips.get("no_price", 0), 0)
        self.assertIsNone(rows[0]["payout_over"])
        self.assertAlmostEqual(rows[0]["payout_under"], 0.909091, places=6)

        # The unpriced side is still never graded.
        self.assertIsNone(bt.profit_for_side(rows[0], "over"))
        self.assertAlmostEqual(bt.profit_for_side(rows[0], "under"), -1.0)

    def test_a_row_with_neither_price_is_skipped(self):
        rows, skips = self._build(over_odds=None, under_odds=None)

        self.assertEqual(rows, [])
        self.assertEqual(skips["no_price"], 1)

    def test_no_side_is_ever_graded_at_a_fabricated_even_money(self):
        # The specific failure: payout exactly 1.0 from a missing price. A
        # genuine +100 also pays 1.0, so this asserts on the input, not the
        # output -- and it now asserts per side, which is where the refusal is.
        import prop_modeling.backtest as bt

        for missing, unbettable in (({"over_odds": None}, "over"),
                                    ({"under_odds": None}, "under"),
                                    ({"over_odds": 0}, "over")):
            rows, _ = self._build(**missing)
            self.assertEqual(len(rows), 1, msg=repr(missing))
            self.assertIsNone(bt.profit_for_side(rows[0], unbettable), msg=repr(missing))


class ItStaysImportableFromTheServePath(unittest.TestCase):
    """The invariant that keeps this module usable where it is needed.

    `build_prop_dataset.py` holds the same formulas and cannot be imported at
    serve time: its own import line is a bare `from feature_schema import` and
    `feature_schema` globs the filesystem at import. `p_over_calibration.py` is
    importable because it imports only `math` and `typing`; this module follows
    it. numpy and pandas are both installed on the VM, so the constraint is
    import-time side effects and sys.path, not availability.
    """

    ALLOWED = {"math", "typing"}

    def test_the_module_imports_nothing_but_stdlib_primitives(self):
        source = (BACKEND_DIR / "prop_modeling" / "market_prices.py").read_text()
        imported = set()
        for node in ast.parse(source).body:          # top level only
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(imported - self.ALLOWED, set())

    def test_importing_edge_score_still_pulls_in_nothing_heavy(self):
        """Measured in a subprocess, because sys.modules is shared.

        Asserting this in-process passes alone and fails in the suite: sibling
        test modules import numpy, pandas and catboost, so by the time this
        runs they are already in sys.modules and the assertion is about the
        test runner rather than about edge_score. A fresh interpreter is the
        only place the question has a meaning.
        """
        import subprocess
        probe = (
            "import sys; sys.path.insert(0, %r);"
            "from utils import edge_score;"
            "print(','.join(sorted({'numpy','scipy','sklearn','pandas','catboost'} "
            "& set(sys.modules))))" % str(BACKEND_DIR)
        )
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


class TheFormulasAreNotDefinedTwice(unittest.TestCase):
    """A source scan, because re-duplication is how this came back before.

    301a815 removed a duplicated p_over map; nothing stopped a second copy
    appearing, and this module exists because `_american_to_implied` had
    quietly become two subtly different functions. Only a scan detects that.
    """

    NAMES = {"american_to_implied", "no_vig_probability", "no_vig_from_american",
             "american_to_payout_decimal", "even_money_payout_decimal"}
    SKIP = ("backend_old", "frontend_old", "__pycache__", ".venv", "Propsmadness-Mock-UI")

    def test_only_market_prices_defines_them(self):
        owner = BACKEND_DIR / "prop_modeling" / "market_prices.py"
        offenders = {}
        for path in BACKEND_DIR.rglob("*.py"):
            if path == owner or any(part in str(path) for part in self.SKIP):
                continue
            try:
                tree = ast.parse(path.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.lstrip("_") in self.NAMES:
                    offenders.setdefault(str(path.relative_to(BACKEND_DIR)), []).append(node.name)
        self.assertEqual(offenders, {})


class TheMarketComponentScoreIsUnchanged(unittest.TestCase):
    """The coercion swap's only real risk surface, pinned.

    `american_to_implied` now feeds the `market` and `line_movement` component
    scores, which feed `edge_score`, which is what every Discord threshold is
    denominated in. A coercion difference here moves alert volume. The existing
    band tests hand synthetic component scores to `_composite_edge_score` and
    so cannot see this.
    """

    SELECTION = {
        "chosen": {"book": "fd", "line": 24.5, "odds": -118},
        "consensus_line": 25.5,
        "average_side_implied": 0.56,
        "available_books": 2,
        "books": {},
    }

    def test_an_over_at_a_better_line_scores_positive(self):
        result = es._compute_market_context(self.SELECTION, "over")

        self.assertAlmostEqual(result["score"], 0.759358, places=6)
        self.assertAlmostEqual(result["details"]["line_delta_vs_consensus"], 1.0)
        self.assertAlmostEqual(result["details"]["chosen_implied"], 0.541)
        self.assertAlmostEqual(result["details"]["price_delta"], 0.019)

    def test_the_same_selection_mirrors_for_an_under(self):
        result = es._compute_market_context(self.SELECTION, "under")

        self.assertAlmostEqual(result["score"], -0.740642, places=6)
        self.assertAlmostEqual(result["details"]["line_delta_vs_consensus"], -1.0)

    def test_an_unpriceable_quote_drops_the_price_term_rather_than_the_score(self):
        # Each of these coerces to None, leaving only the 0.75-weighted line
        # term. If the new coercion disagreed with _safe_float on any of them,
        # this score would move.
        for odds in (None, "", "abc", float("nan"), float("inf")):
            selection = {**self.SELECTION, "chosen": {**self.SELECTION["chosen"], "odds": odds}}
            result = es._compute_market_context(selection, "over")

            self.assertAlmostEqual(result["score"], 0.75, places=6, msg=repr(odds))
            self.assertIsNone(result["details"]["chosen_implied"], msg=repr(odds))
            self.assertIsNone(result["details"]["price_delta"], msg=repr(odds))


if __name__ == "__main__":
    unittest.main()
