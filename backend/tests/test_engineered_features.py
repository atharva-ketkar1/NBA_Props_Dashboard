"""The eight features the dataset builder does not emit.

These were defined twice -- inline in regression_notebook.py for training and
again by hand in utils/ml_inference.py for serving -- and nothing else knew
they existed. Scoring the builder's output directly therefore fed the models a
vector with all eight as NaN, and since
modeled_minutes_x_recent10_target_per_min has a baseline mean of 7.301 against
a target mean of 7.298, predictions collapsed toward a flat prior: sd 3.04
against an actual sd of 8.46, with per-decile bias from +4.4 to -17.3. That
reads exactly like a model with no signal and was a harness bug.

The formulas here mirror regression_notebook.py, not ml_inference, because the
notebook is what the exported models were fit with -- so it defines what each
feature means. The two copies disagree on expected_possessions and this pins
the trainer's behaviour.
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

import pandas as pd

from prop_modeling.engineered_features import (
    MINUTES_CEILING,
    MINUTES_FLOOR,
    POST_BUILD_FEATURES,
    add_engineered_features,
    attach_modeled_minutes,
)


def _frame(**overrides):
    base = {
        "player_id": ["1"], "game_id": ["0022600001"], "game_date": ["2026-10-20"],
        "recent3_stat_avg": [12.0], "recent5_stat_avg": [10.0],
        "recent10_stat_avg": [9.0], "recent20_stat_avg": [8.0],
        "recent5_minutes_avg": [30.0], "recent10_minutes_avg": [28.0],
        "season_minutes_avg": [26.0], "minutes_cv_recent5": [0.1],
        "team_pace": [100.0], "season_usage_pct_avg": [25.0],
        "is_b2b": [0], "recent10_target_per_min": [0.5],
    }
    base.update({k: [v] for k, v in overrides.items()})
    return pd.DataFrame(base)


class EngineeredFeatures(unittest.TestCase):
    def test_momentum_is_a_signed_difference(self):
        out = add_engineered_features(_frame())
        self.assertAlmostEqual(out["momentum_diff_5v20"].iloc[0], 2.0)
        self.assertAlmostEqual(out["momentum_diff_3v10"].iloc[0], 3.0)

    def test_expected_possessions_is_pace_times_usage_share(self):
        out = add_engineered_features(_frame())
        self.assertAlmostEqual(out["expected_possessions"].iloc[0], 25.0)

    def test_a_missing_usage_becomes_zero_as_the_trainer_does(self):
        """ml_inference skips the feature entirely here; the trainer fills 0.

        The trainer wins: it is what the exported model was fit with.
        """
        out = add_engineered_features(_frame(season_usage_pct_avg=None))
        self.assertAlmostEqual(out["expected_possessions"].iloc[0], 0.0)

    def test_predicted_minutes_is_the_weighted_blend(self):
        out = add_engineered_features(_frame())
        self.assertAlmostEqual(out["predicted_minutes"].iloc[0], 30 * 0.5 + 28 * 0.3 + 26 * 0.2)

    def test_a_back_to_back_shaves_the_blend(self):
        flat = add_engineered_features(_frame())["predicted_minutes"].iloc[0]
        b2b = add_engineered_features(_frame(is_b2b=1))["predicted_minutes"].iloc[0]
        self.assertLess(b2b, flat)
        self.assertAlmostEqual(b2b, flat * (1.0 - 0.035))

    def test_predicted_minutes_is_clipped_to_a_plausible_range(self):
        low = add_engineered_features(
            _frame(recent5_minutes_avg=0.0, recent10_minutes_avg=0.0, season_minutes_avg=0.0)
        )["predicted_minutes"].iloc[0]
        high = add_engineered_features(
            _frame(recent5_minutes_avg=60.0, recent10_minutes_avg=60.0, season_minutes_avg=60.0)
        )["predicted_minutes"].iloc[0]
        self.assertAlmostEqual(low, MINUTES_FLOOR)
        self.assertAlmostEqual(high, MINUTES_CEILING)


class ModeledMinutesAttachment(unittest.TestCase):
    def _preds(self, q25=25.0, q50=30.0, q75=36.0):
        return pd.DataFrame({
            "game_date": ["2026-10-20"], "player_id": ["1"], "game_id": ["0022600001"],
            "modeled_minutes_q25": [q25], "modeled_minutes_q50": [q50],
            "modeled_minutes_q75": [q75],
        })

    def test_a_matched_prediction_is_used(self):
        out = attach_modeled_minutes(add_engineered_features(_frame()), self._preds())
        self.assertAlmostEqual(out["modeled_minutes_q50"].iloc[0], 30.0)
        self.assertAlmostEqual(out["modeled_minutes_iqr"].iloc[0], 11.0)

    def test_a_merge_miss_falls_back_to_the_blend(self):
        """Training rows with no minutes prediction got the blend, so these must too."""
        preds = self._preds()
        preds["player_id"] = ["999"]
        df = add_engineered_features(_frame())
        expected = df["predicted_minutes"].iloc[0]
        out = attach_modeled_minutes(df, preds)
        self.assertAlmostEqual(out["modeled_minutes_q50"].iloc[0], expected)
        self.assertGreater(out["modeled_minutes_iqr"].iloc[0], 0.0)

    def test_the_target_interaction_is_populated(self):
        """This is the feature whose absence flattened the model."""
        out = attach_modeled_minutes(add_engineered_features(_frame()), self._preds())
        self.assertAlmostEqual(
            out["modeled_minutes_x_recent10_target_per_min"].iloc[0], 30.0 * 0.5
        )

    def test_delta_vs_recent5_is_signed(self):
        out = attach_modeled_minutes(add_engineered_features(_frame()), self._preds(q50=35.0))
        self.assertAlmostEqual(out["modeled_minutes_delta_vs_recent5"].iloc[0], 5.0)

    def test_iqr_is_never_negative(self):
        out = attach_modeled_minutes(
            add_engineered_features(_frame()), self._preds(q25=36.0, q50=30.0, q75=25.0)
        )
        self.assertGreaterEqual(out["modeled_minutes_iqr"].iloc[0], 0.0)

    def test_every_post_build_feature_is_present_after_completion(self):
        out = attach_modeled_minutes(add_engineered_features(_frame()), self._preds())
        for col in POST_BUILD_FEATURES:
            self.assertIn(col, out.columns)


if __name__ == "__main__":
    unittest.main()
