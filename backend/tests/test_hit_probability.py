import math
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.ml_inference import MLPredictor


def hit_probability(prediction, std_dev, line, side):
    """Call the method without constructing a predictor.

    MLPredictor.__new__ loads ~36MB of CatBoost models; hit_probability itself
    touches no instance state, so the unbound call keeps this a pure math test.
    """
    return MLPredictor.hit_probability(None, prediction, std_dev, line, side)


class HitProbabilityTests(unittest.TestCase):
    def test_line_at_projection_is_a_coin_flip(self):
        self.assertAlmostEqual(hit_probability(16.5, 4.0, 16.5, "over"), 0.5, places=12)
        self.assertAlmostEqual(hit_probability(16.5, 4.0, 16.5, "under"), 0.5, places=12)

    def test_one_sigma_above_projection(self):
        # P(X > mu + sigma) for a normal distribution.
        expected = 0.15865525393145707
        self.assertAlmostEqual(hit_probability(20.0, 4.0, 24.0, "over"), expected, places=12)
        self.assertAlmostEqual(
            hit_probability(20.0, 4.0, 24.0, "under"), 1.0 - expected, places=12,
        )

    def test_one_sigma_below_projection(self):
        expected = 0.8413447460685429
        self.assertAlmostEqual(hit_probability(20.0, 4.0, 16.0, "over"), expected, places=12)

    def test_sides_are_complementary(self):
        for line in (0.5, 8.5, 16.5, 31.5):
            over = hit_probability(18.2, 5.5, line, "over")
            under = hit_probability(18.2, 5.5, line, "under")
            self.assertAlmostEqual(over + under, 1.0, places=12)

    def test_probability_stays_in_range(self):
        for line in (-50.0, 0.0, 18.0, 500.0):
            for std_dev in (0.1, 1.0, 25.0):
                value = hit_probability(18.0, std_dev, line, "over")
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_degenerate_std_dev_falls_back_to_a_coarse_split(self):
        # Preserved from the original implementation.
        self.assertEqual(hit_probability(20.0, 0.0, 16.5, "over"), 0.6)
        self.assertEqual(hit_probability(20.0, 0.0, 16.5, "under"), 0.4)
        self.assertEqual(hit_probability(20.0, None, 24.5, "over"), 0.4)
        self.assertEqual(hit_probability(20.0, -1.0, 24.5, "under"), 0.6)

    def test_matches_the_normal_cdf_identity(self):
        # The erf form must equal 1 - norm.cdf(line, loc, scale); scipy is not a
        # runtime dependency, so assert against the identity directly.
        for prediction in (0.0, 8.0, 16.5, 42.0):
            for std_dev in (0.5, 3.2, 20.0):
                for line in (-5.0, 0.0, 16.5, 100.0):
                    expected = 0.5 * (
                        1.0 - math.erf((line - prediction) / (std_dev * math.sqrt(2.0)))
                    )
                    self.assertAlmostEqual(
                        hit_probability(prediction, std_dev, line, "over"),
                        expected,
                        places=12,
                    )


if __name__ == "__main__":
    unittest.main()
