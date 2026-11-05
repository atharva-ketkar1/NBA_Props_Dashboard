"""Serve-path repairs: the model was being fed inputs training never produced.

Four independent defects, all confirmed against the live 2026-27 board before
being fixed here:

* The minutes model never ran for thin-history players. Its live gate required 6
  same-team games while the minutes dataset trains from 3 (3,636 rows in the 3-5
  band), so 118 of 448 players fell through to a heuristic that, with history
  that thin, collapses to exactly recent5_minutes_avg -- leaving
  modeled_minutes_delta_vs_recent5 identically 0 and the promotion guardrail
  unable to fire.
* `is_home` defaulted to HOME for every row whose game_context could not resolve
  it, against a training mean of 0.4987 -- a confident answer that is wrong half
  the time.
* The MultiQuantile heads are fit with no ordering or non-negativity constraint,
  so on the low-signal count markets they crossed and went negative (BLK in 38%
  of rows, STL in 17%). A negative q25 widens the IQR and biases edge_score's
  p_over interpolation toward the over.
* Three `*_x_player_target_per_min` products were built only in
  _build_regression_row's dict literal from vacancy dicts the serve path passes
  empty, so they were hardwired to 0.0 against a training mean of 20.28.
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

from prop_modeling import minutes_model_config as mmc
from prop_modeling.injury_feature_config import apply_injury_feature_values
from utils.ml_inference import MLPredictor, _sanitize_quantiles


class MinutesGateMatchesTrainingFloor(unittest.TestCase):
    def test_live_gate_is_not_stricter_than_the_training_floor(self):
        """The gate must not discard the regime the model was fit on.

        The minutes dataset is built with --minutes-min-prior-games 3, so a live
        gate above 3 throws away rows the model has actually learned.
        """
        self.assertLessEqual(mmc.MINUTES_LIVE_MIN_SAME_TEAM_GAMES, 3)


class QuantileSanitization(unittest.TestCase):
    def test_crossed_quantiles_are_ordered(self):
        self.assertEqual(_sanitize_quantiles(5.0, 3.0, 4.0), (3.0, 4.0, 5.0))

    def test_negative_quantiles_are_floored_at_zero(self):
        self.assertEqual(_sanitize_quantiles(-1.0, 0.5, 2.0), (0.0, 0.5, 2.0))

    def test_sort_happens_before_clamp_so_the_median_keeps_its_rank(self):
        """Clamping first would move the median.

        clamp(-0.3, 0.1, -0.1) -> (0, 0.1, 0), and sorting that puts 0.1 last,
        so the median becomes 0. Sorting first gives (-0.3, -0.1, 0.1), and
        clamping leaves the median at 0.
        """
        low, mid, high = _sanitize_quantiles(-0.3, 0.1, -0.1)
        self.assertEqual((low, mid, high), (0.0, 0.0, 0.1))

    def test_an_already_valid_triple_is_untouched(self):
        self.assertEqual(_sanitize_quantiles(1.0, 2.0, 3.0), (1.0, 2.0, 3.0))

    def test_iqr_can_no_longer_be_inflated_by_a_negative_q25(self):
        q25, _, q75 = _sanitize_quantiles(-2.0, 0.4, 1.0)
        self.assertEqual(q75 - q25, 1.0)


class IsHomeIsNeverAssertedWhenUnknown(unittest.TestCase):
    def test_unresolvable_context_returns_none_rather_than_home(self):
        """None is the signal the feature builder turns into NaN."""
        self.assertIsNone(MLPredictor._game_context_is_home("LAL", None))
        self.assertIsNone(MLPredictor._game_context_is_home("LAL", {}))
        self.assertIsNone(
            MLPredictor._game_context_is_home(
                "LAL", {"home_team_tricode": "BOS", "away_team_tricode": "NYK"}
            )
        )

    def test_resolvable_context_still_resolves_both_ways(self):
        self.assertEqual(MLPredictor._game_context_is_home("LAL", {"is_home": True}), 1)
        self.assertEqual(MLPredictor._game_context_is_home("LAL", {"is_home": False}), 0)
        self.assertEqual(
            MLPredictor._game_context_is_home(
                "LAL", {"home_team_tricode": "LAL", "away_team_tricode": "BOS"}
            ),
            1,
        )
        self.assertEqual(
            MLPredictor._game_context_is_home(
                "LAL", {"home_team_tricode": "BOS", "away_team_tricode": "LAL"}
            ),
            0,
        )


class VacancyInteractionProducts(unittest.TestCase):
    def test_same_position_product_is_computed_not_zeroed(self):
        row = apply_injury_feature_values(
            {"recent10_target_per_min": 0.8},
            same_pos_vacancy_stats={"missing_same_pos_minutes": 30.0},
        )
        self.assertEqual(row["missing_same_pos_minutes_x_player_target_per_min"], 24.0)

    def test_sibling_products_are_computed_too(self):
        row = apply_injury_feature_values(
            {"recent10_target_per_min": 0.5},
            team_vacancy_stats={
                "missing_playmaker_potential_ast_pg": 10.0,
                "missing_onball_drives_pg": 8.0,
            },
        )
        self.assertEqual(row["missing_playmaker_potential_ast_pg_x_player_target_per_min"], 5.0)
        self.assertEqual(row["missing_onball_drives_pg_x_player_target_per_min"], 4.0)

    def test_no_vacancy_still_yields_zero(self):
        """Absence of teammates out must stay 0, not become NaN."""
        row = apply_injury_feature_values({"recent10_target_per_min": 0.8})
        self.assertEqual(row["missing_same_pos_minutes_x_player_target_per_min"], 0.0)

    def test_missing_target_rate_is_treated_as_zero_not_an_error(self):
        row = apply_injury_feature_values(
            {}, same_pos_vacancy_stats={"missing_same_pos_minutes": 30.0}
        )
        self.assertEqual(row["missing_same_pos_minutes_x_player_target_per_min"], 0.0)


if __name__ == "__main__":
    unittest.main()
