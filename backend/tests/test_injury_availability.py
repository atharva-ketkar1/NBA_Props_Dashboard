"""A Questionable player is a coin flip, and the serve path used to call it certain.

`UNAVAILABLE_INJURY_STATUSES = {"out", "doubtful"}` meant a Questionable player was
projected at full freight *and* contributed nothing to the usage vacuum their
teammates inherit. Measured against gamelog ground truth on 458 observations /
127 player-game clusters, P(plays | Questionable) is 58.1% +-9.5 -- while
Available (98.5%), Probable (96.4%), Doubtful (1.5%) and Out (0.0%) all round to
the binary the code already assumed.

Both halves are pinned here because fixing one and not the other is a plausible
regression: the own-projection half and the teammate-vacancy half live in
different modules and read the status through different paths.
"""

import json
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es
from utils import injury_availability as ia
from utils.ml_inference import MLPredictor
from prop_modeling.p_over_calibration import honest_p_over

PLAYER = {"id": "77", "name": "Test Player", "team": "DET", "position": "G"}
Q25, Q50, Q75 = 15.0, 20.0, 25.0
LINE = 15.0


class TheMeasuredTable(unittest.TestCase):
    def test_only_questionable_is_fractional(self):
        """The narrowness is the point -- see the module docstring."""
        self.assertEqual(ia.PARTIAL_STATUSES, frozenset({"questionable"}))
        for status in ("out", "doubtful"):
            self.assertEqual(ia.probability_plays(status), 0.0)
            self.assertEqual(ia.vacancy_weight(status), 1.0)
        for status in ("available", "probable"):
            self.assertEqual(ia.probability_plays(status), 1.0)
            self.assertEqual(ia.vacancy_weight(status), 0.0)

    def test_questionable_is_the_measured_coin_flip(self):
        self.assertAlmostEqual(ia.probability_plays("Questionable"), 0.581)
        self.assertAlmostEqual(ia.vacancy_weight("Questionable"), 0.419)

    def test_an_unknown_status_is_none_and_never_manufactures_a_vacancy(self):
        """None, not a default, for the reason season_type_from_game_id returns None.

        Defaulting an unreadable status to "plays" silently projects a missing
        player at full freight; defaulting it to "out" silently deletes them.
        """
        for status in (None, "", "   ", "Probable-ish", "GTD"):
            self.assertIsNone(ia.probability_plays(status))
            self.assertEqual(ia.vacancy_weight(status), 0.0)

    def test_the_env_override_restores_the_old_behaviour_exactly(self):
        """A push to main deploys the pipeline, so this needs a revert switch."""
        with patch.dict("os.environ", {"EDGE_QUESTIONABLE_AVAILABILITY": "1.0"}):
            self.assertEqual(ia.probability_plays("questionable"), 1.0)
            self.assertEqual(ia.vacancy_weight("questionable"), 0.0)

    def test_a_junk_override_falls_back_rather_than_crashing_the_slate(self):
        for raw in ("", "abc", "-0.2", "1.4", "nan"):
            with patch.dict("os.environ", {"EDGE_QUESTIONABLE_AVAILABILITY": raw}):
                self.assertAlmostEqual(ia.probability_plays("questionable"), 0.581)


class _QuantileStub:
    def predict(self, *args, **kwargs):
        return {"q25": Q25, "q50": Q50, "q75": Q75}

    def __init__(self, status=None):
        self._status = status

    def live_injury_status(self, player_id, team=None, opponent=None):
        return self._status


def _context(predictor, side="over"):
    with patch.object(es, "_ml_predictor", predictor), \
         patch.object(es, "_extract_logs", return_value=[{"PTS": 20.0}]), \
         patch.object(es, "_compute_lineup_adjustment",
                      return_value={"q50_multiplier": 1.0, "reason": "test"}):
        return es._compute_ml_regression_context(PLAYER, "PHI", "PTS", LINE, side)


class HalfOne_TheOwnProjection(unittest.TestCase):
    """P(over) = P(plays) * P(over | plays), applied to the probability."""

    def test_a_questionable_player_is_discounted_by_the_measured_rate(self):
        context = _context(_QuantileStub("Questionable"))
        self.assertAlmostEqual(
            context["p_over"], round(honest_p_over(Q25, Q50, Q75, LINE) * 0.581, 4)
        )

    def test_an_unreported_player_is_left_alone(self):
        """Not on the report is "no information", not Available."""
        context = _context(_QuantileStub(None))
        self.assertAlmostEqual(
            context["p_over"], round(honest_p_over(Q25, Q50, Q75, LINE), 4)
        )
        self.assertIsNone(context["details"]["availability_p_plays"])

    def test_available_and_probable_are_not_discounted(self):
        for status in ("Available", "Probable"):
            context = _context(_QuantileStub(status))
            self.assertAlmostEqual(
                context["p_over"], round(honest_p_over(Q25, Q50, Q75, LINE), 4),
                msg="%s must not be discounted" % status,
            )

    def test_the_discount_is_reported_so_it_is_auditable(self):
        details = _context(_QuantileStub("Questionable"))["details"]
        self.assertEqual(details["injury_status"], "Questionable")
        self.assertAlmostEqual(details["availability_p_plays"], 0.581)
        self.assertAlmostEqual(details["hit_probability"], round(details["p_over"] * 100.0, 1))

    def test_the_quantiles_themselves_are_untouched(self):
        """Scaling q25/q50/q75 by P(plays) would be the wrong shape.

        The mixture's median is the 13.8th percentile of the playing
        distribution, not 0.581 * its median, and its q25 is exactly 0. The
        sorted-quantile contract is load-bearing downstream, so the discount
        must not reach it.
        """
        details = _context(_QuantileStub("Questionable"))["details"]
        self.assertAlmostEqual(details["q25"], Q25)
        self.assertAlmostEqual(details["q50"], Q50)
        self.assertAlmostEqual(details["q75"], Q75)
        self.assertAlmostEqual(details["prediction_val"], Q50)

    def test_a_predictor_without_the_lookup_still_scores(self):
        """The predictor is a swappable singleton; absence means no information."""
        class _NoLookup:
            def predict(self, *args, **kwargs):
                return {"q25": Q25, "q50": Q50, "q75": Q75}

        context = _context(_NoLookup())
        self.assertTrue(context["available"])
        self.assertAlmostEqual(
            context["p_over"], round(honest_p_over(Q25, Q50, Q75, LINE), 4)
        )


GAME_DATE = "2026-11-14"


class _FakeLoader:
    """Just enough of MLPredictor to run the real injury loader."""

    def __init__(self):
        self.live_injury_report_meta = {}
        self.live_position_resolution_summary = {}
        self.live_player_name_lookup = {}
        self.live_team_player_ids = {}
        self.live_team_vacancy_stats = {}
        self.live_same_pos_vacancy_stats = {}
        self.live_missing_player_priors = {}
        self.live_active_player_priors = {}
        self.live_player_injury_statuses = {}
        self.live_team_game_dates = {}
        self.live_team_game_dates_all = {}
        self.live_team_matchup_dates = {}
        # Truthy only: the loader bails without it, and `_get_live_player_priors`
        # is stubbed below so nothing reads through it.
        self.enrichment = object()
        self.priors = {}

    def _get_live_player_priors(self, player_id, game_date, team_abbr=None):
        return dict(self.priors[str(player_id)])

    @staticmethod
    def _get_et_now():
        return datetime(2026, 11, 14, 12, 0, 0)

    def run(self, payload, tmpdir):
        path = Path(tmpdir) / "nba_injury_report.json"
        path.write_text(json.dumps(payload))
        with patch("utils.ml_inference.CURRENT_DATA_DIR", Path(tmpdir)):
            MLPredictor._load_live_injury_report_context(self)


def _payload(status):
    return {
        "query_date": GAME_DATE,
        "games": [{
            "game_date": GAME_DATE,
            "home_team_tricode": "DET",
            "away_team_tricode": "PHI",
            "teams": {"DET": {"players": [{
                "player_name": "Star Teammate",
                "current_status": status,
                "reason": "Injury/Illness - Left Ankle Sprain",
            }]}},
        }],
    }


STAR_PRIORS = {
    "within_active_roster_window": True,
    "usage_pct": 30.0,
    "minutes": 34.0,
    "ast_pct": 25.0,
    "potential_ast_pg": 8.0,
    "drives_pg": 10.0,
    "pos_group": "G",
    "position_resolution_tier": "live",
}


class HalfTwo_TheTeammateVacancy(unittest.TestCase):
    """A Questionable teammate is 0.419 of a vacancy, not 0 and not 1."""

    def _vacancy(self, status, tmpdir):
        loader = _FakeLoader()
        loader.live_player_name_lookup[("DET", "star teammate")] = "99"
        loader.live_team_player_ids["DET"] = {"99"}
        loader.priors["99"] = STAR_PRIORS
        loader.run(_payload(status), tmpdir)
        return loader

    def test_out_is_a_whole_vacancy_as_before(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            loader = self._vacancy("Out", tmp)
            stats = loader.live_team_vacancy_stats[("DET", date(2026, 11, 14))]
            self.assertAlmostEqual(stats["missing_team_usage_pct"], 30.0)
            self.assertAlmostEqual(stats["missing_team_minutes"], 34.0)

    def test_questionable_is_a_fractional_vacancy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            loader = self._vacancy("Questionable", tmp)
            stats = loader.live_team_vacancy_stats[("DET", date(2026, 11, 14))]
            self.assertAlmostEqual(stats["missing_team_usage_pct"], 30.0 * 0.419, places=3)
            self.assertAlmostEqual(stats["missing_team_minutes"], 34.0 * 0.419, places=3)

    def test_questionable_used_to_contribute_nothing(self):
        """The regression this guards: with the override it is 0 again."""
        import tempfile
        with patch.dict("os.environ", {"EDGE_QUESTIONABLE_AVAILABILITY": "1.0"}), \
             tempfile.TemporaryDirectory() as tmp:
            loader = self._vacancy("Questionable", tmp)
            stats = loader.live_team_vacancy_stats[("DET", date(2026, 11, 14))]
            self.assertAlmostEqual(stats["missing_team_usage_pct"], 0.0)

    def test_available_contributes_nothing(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            loader = self._vacancy("Available", tmp)
            stats = loader.live_team_vacancy_stats[("DET", date(2026, 11, 14))]
            self.assertAlmostEqual(stats["missing_team_usage_pct"], 0.0)

    def test_every_status_is_recorded_not_just_the_unavailable_ones(self):
        """The own-projection half reads this map, so it must cover the healthy."""
        import tempfile
        for status in ("Out", "Questionable", "Probable", "Available"):
            with tempfile.TemporaryDirectory() as tmp:
                loader = self._vacancy(status, tmp)
                self.assertEqual(
                    loader.live_player_injury_statuses.get(("99", date(2026, 11, 14))),
                    status.lower(),
                    msg="%s was not recorded" % status,
                )

    def test_the_weight_is_carried_on_the_missing_prior(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            loader = self._vacancy("Questionable", tmp)
            priors = loader.live_missing_player_priors[("DET", date(2026, 11, 14))]
            self.assertAlmostEqual(priors["99"]["vacancy_weight"], 0.419)


if __name__ == "__main__":
    unittest.main()
