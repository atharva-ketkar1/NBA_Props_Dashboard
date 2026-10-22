"""Regression tests for the early-season prior blend.

The bug these pin down: season_stats.csv only carries players with GP > 0, so
aggregator.py gives every other rostered player a zero-filled stats dict. 0.0
is not None, so edge_score read it as a real season average, every under got a
projection_gap equal to the full line, and the whole board became unders
projected for nothing. Measured on 2026-10-21, all 15 recommendations were
unders with baseline_projection=0.0 and edge_score between 63.9 and 65.2.
"""

import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es
from utils import prior_season


# The zero-filled skeleton aggregator.py writes for a rostered player who has
# not played yet (see "Give roster-only players (GP == 0) a skeleton entry").
ZERO_SKELETON = {
    "PLAYER_ID": 1626164, "TEAM_ABBREVIATION": "PHX", "MIN": 0, "GP": 0,
    "PTS": 0, "REB": 0, "AST": 0, "STL": 0, "BLK": 0, "FG3M": 0, "FGA": 0,
    "FGM": 0, "TOV": 0, "PF": 0, "OREB": 0, "DREB": 0, "USG_PCT": 0,
    "POTENTIAL_AST": 0, "DRIVES": 0, "DRIVE_PTS": 0, "REB_CHANCES": 0,
    "PTS+REB+AST": 0, "PTS+REB": 0, "PTS+AST": 0, "REB+AST": 0, "STL+BLK": 0,
}

PRIOR_PROFILE = {
    "GP": 68, "MIN": 33.8, "PTS": 25.8, "REB": 3.9, "AST": 6.0,
    "USG_PCT": 30.5, "PTS+REB+AST": 35.7,
}


def _log(game_date, minutes=34, pts=26, reb=4, ast=6):
    return {
        "GAME_DATE": game_date, "TEAM_ABBREVIATION": "PHX", "MIN": minutes,
        "PTS": pts, "REB": reb, "AST": ast, "STL": 1, "BLK": 0, "FG3M": 2,
        "PTS+REB+AST": pts + reb + ast,
    }


class _StubHistory:
    """Stands in for the Supabase-backed PriorHistory singleton."""

    def __init__(self, profiles=None, logs=None, season="2025-26"):
        self._profiles = profiles or {}
        self._logs = logs or {}
        self._season = season

    def profile(self, player_id):
        return self._profiles.get(str(player_id), {})

    def logs(self, player_id):
        return self._logs.get(str(player_id), [])

    def season_for(self, player_id):
        return self._season if str(player_id) in self._profiles else None

    def __len__(self):
        return len(self._profiles)


class PriorBlendTests(unittest.TestCase):
    def setUp(self):
        self._real = prior_season.get_prior_history
        self._install({"1626164": PRIOR_PROFILE},
                      {"1626164": [_log(f"2026-04-{day:02d}") for day in (27, 25, 23, 21, 19)]})

    def tearDown(self):
        prior_season.get_prior_history = self._real

    def _install(self, profiles, logs):
        stub = _StubHistory(profiles, logs)
        prior_season.get_prior_history = lambda: stub

    # ── the original bug ────────────────────────────────────────────────────

    def test_zero_skeleton_no_longer_reads_as_a_real_average(self):
        """A GP==0 player with no prior must report None, never 0.0.

        0.0 would make projection_gap for an under equal the entire line.
        """
        self._install({}, {})
        view = es._apply_prior_season_history({"id": 999, "stats": dict(ZERO_SKELETON), "game_log": []})
        self.assertIsNone(es._stat_value_from_stats(view["stats"], "PTS+REB+AST"))
        self.assertIsNone(es._stat_value_from_stats(view["stats"], "PTS"))

    def test_combo_stat_is_none_when_every_component_is_missing(self):
        """Summing components with a 0.0 default turned an absent profile into
        a confident 0.0 -- the same bug one layer down."""
        self.assertIsNone(es._stat_value_from_stats({}, "PTS+REB+AST"))
        self.assertIsNone(es._stat_value_from_stats({"PTS": None, "REB": None, "AST": None}, "PTS+REB"))
        self.assertIsNone(es._stat_value_from_stats({}, "STL+BLK"))

    def test_combo_stat_sums_only_present_components(self):
        self.assertEqual(es._stat_value_from_stats({"PTS": 20.0, "REB": 5.0}, "PTS+REB"), 25.0)
        self.assertEqual(es._stat_value_from_stats({"PTS": 20.0}, "PTS+REB"), 20.0)

    def test_an_explicit_zero_average_still_survives(self):
        """A player who genuinely averages 0 blocks must not be blanked."""
        self.assertEqual(es._stat_value_from_stats({"BLK": 0.0}, "BLK"), 0.0)

    # ── shrinkage ───────────────────────────────────────────────────────────

    def test_no_current_games_projects_entirely_from_the_prior(self):
        view = es._apply_prior_season_history(
            {"id": 1626164, "stats": dict(ZERO_SKELETON), "game_log": []}
        )
        self.assertEqual(view["history_basis"]["basis"], "prior")
        self.assertEqual(view["history_basis"]["current_weight"], 0.0)
        self.assertAlmostEqual(es._stat_value_from_stats(view["stats"], "PTS"), 25.8, places=6)
        self.assertAlmostEqual(view["stats"]["MIN"], 33.8, places=6)

    def test_prior_weight_decays_as_games_accumulate(self):
        # gp / (gp + k), k = 10
        self.assertEqual(es._prior_current_weight(0), 0.0)
        self.assertAlmostEqual(es._prior_current_weight(10), 0.5, places=6)
        self.assertAlmostEqual(es._prior_current_weight(40), 0.8, places=6)
        self.assertLess(es._prior_current_weight(5), es._prior_current_weight(20))

    def test_current_season_is_blended_not_replaced(self):
        """One 40-point game must not drag the projection to 40."""
        player = {
            "id": 1626164,
            "stats": {**ZERO_SKELETON, "GP": 1, "PTS": 40.0, "MIN": 36.0},
            "game_log": [_log("2026-10-21", minutes=36, pts=40)],
        }
        view = es._apply_prior_season_history(player)
        self.assertEqual(view["history_basis"]["basis"], "blended")
        blended_pts = es._stat_value_from_stats(view["stats"], "PTS")
        # weight = 1/11 on the current game, 10/11 on the prior season.
        self.assertAlmostEqual(blended_pts, 40.0 * (1 / 11) + 25.8 * (10 / 11), places=6)
        self.assertGreater(blended_pts, 25.8)
        self.assertLess(blended_pts, 40.0)

    def test_games_played_falls_back_to_log_count(self):
        """The season-stats scrape lags the gamelog scrape, so GP can read 0
        while logs already exist. The logs win."""
        player = {
            "id": 1626164,
            "stats": {**ZERO_SKELETON, "GP": 0},
            "game_log": [_log("2026-10-21"), _log("2026-10-19"), _log("2026-10-17")],
        }
        view = es._apply_prior_season_history(player)
        self.assertEqual(view["history_basis"]["current_games"], 3)
        self.assertGreater(view["history_basis"]["current_weight"], 0.0)

    # ── log padding ─────────────────────────────────────────────────────────

    def test_prior_logs_pad_the_rolling_windows(self):
        view = es._apply_prior_season_history(
            {"id": 1626164, "stats": dict(ZERO_SKELETON), "game_log": []}
        )
        self.assertEqual(len(es._extract_logs(view)), 5)

    def test_current_games_sort_ahead_of_prior_games(self):
        player = {
            "id": 1626164,
            "stats": {**ZERO_SKELETON, "GP": 1},
            "game_log": [_log("2026-10-21", pts=11)],
        }
        logs = es._extract_logs(es._apply_prior_season_history(player))
        self.assertEqual(logs[0]["GAME_DATE"], "2026-10-21")
        self.assertEqual(logs[0]["PTS"], 11)
        self.assertEqual(len(logs), 6)

    def test_master_feed_entry_is_not_mutated(self):
        """The blend must not leak into players.game_log or the frontend's
        season view, so the source dict stays untouched."""
        player = {"id": 1626164, "stats": dict(ZERO_SKELETON), "game_log": []}
        es._apply_prior_season_history(player)
        self.assertEqual(player["game_log"], [])
        self.assertEqual(player["stats"]["PTS"], 0)

    # ── the no-history gate ─────────────────────────────────────────────────

    def test_unplayed_rookie_is_marked_as_having_no_basis(self):
        self._install({}, {})
        view = es._apply_prior_season_history(
            {"id": 1642999, "stats": dict(ZERO_SKELETON), "game_log": []}
        )
        self.assertEqual(view["history_basis"]["basis"], "none")

    def test_rookie_with_games_played_is_scoreable(self):
        self._install({}, {})
        view = es._apply_prior_season_history({
            "id": 1642999,
            "stats": {**ZERO_SKELETON, "GP": 2, "PTS": 14.0},
            "game_log": [_log("2026-10-21"), _log("2026-10-19")],
        })
        self.assertEqual(view["history_basis"]["basis"], "current")

    def test_missing_prior_history_module_does_not_break_scoring(self):
        def boom():
            raise RuntimeError("supabase down")
        prior_season.get_prior_history = boom
        view = es._apply_prior_season_history({
            "id": 1626164,
            "stats": {**ZERO_SKELETON, "GP": 4, "PTS": 20.0},
            "game_log": [_log("2026-10-21")],
        })
        self.assertEqual(view["history_basis"]["basis"], "current")


class ReasonStringTests(unittest.TestCase):
    """projection_gap is side-signed, so describing it raw made every under
    read 'above the line' when the projection sat below it."""

    @staticmethod
    def _components(gap, model_type="opportunity_v1"):
        empty = {"available": False, "score": 0.0, "details": {}}
        return {
            "projection": {"available": True, "score": 0.0,
                           "details": {"projection_gap": gap, "model_type": model_type}},
            "recent_form": dict(empty, details={"averages": {}, "hit_rates": {}}),
            "matchup": dict(empty),
            "market": dict(empty),
            "ml_regression": dict(empty),
            "line_movement": dict(empty),
            "similar_players": dict(empty),
            "head_to_head": dict(empty),
            "back_to_back": dict(empty),
        }

    def test_under_with_projection_below_the_line_reads_below(self):
        # proj 35.6 vs line 36.5 -> raw gap -0.9 -> side-signed +0.9
        reasons = es._build_reason_strings("under", 36.5, "PTS+REB+AST", self._components(0.9))
        self.assertIn("0.9 below the line", reasons[0])

    def test_over_with_projection_above_the_line_reads_above(self):
        reasons = es._build_reason_strings("over", 23.5, "PTS+AST", self._components(6.0))
        self.assertIn("6.0 above the line", reasons[0])

    def test_prior_basis_is_disclosed(self):
        reasons = es._build_reason_strings(
            "under", 36.5, "PTS+REB+AST", self._components(0.9),
            {"basis": "prior", "prior_games": 68, "prior_season": "2025-26", "current_games": 0},
        )
        self.assertTrue(any("2025-26" in reason and "68 games" in reason for reason in reasons))

    def test_blended_basis_reports_the_weight(self):
        reasons = es._build_reason_strings(
            "over", 23.5, "PTS+AST", self._components(6.0),
            {"basis": "blended", "prior_games": 70, "prior_season": "2025-26",
             "current_games": 1, "current_weight": 0.091},
        )
        self.assertTrue(any("Early sample" in reason for reason in reasons))

    def test_settled_season_adds_no_prior_disclosure(self):
        reasons = es._build_reason_strings(
            "over", 23.5, "PTS+AST", self._components(6.0),
            {"basis": "current", "prior_games": 0, "prior_season": None, "current_games": 41},
        )
        self.assertFalse(any("Early sample" in reason or "No PTS+AST history" in reason
                             for reason in reasons))


class PriorAggregateTests(unittest.TestCase):
    def test_season_aggregates_skip_games_that_were_not_played(self):
        logs = [_log("2026-01-01", minutes=30, pts=20), _log("2026-01-03", minutes=0, pts=0)]
        stats = prior_season._season_aggregates(logs)
        self.assertEqual(stats["GP"], 1)
        self.assertEqual(stats["PTS"], 20)

    def test_shooting_percentages_come_from_totals_not_a_mean_of_percentages(self):
        logs = [
            {"GAME_DATE": "2026-01-01", "MIN": 30, "FGM": 10, "FGA": 10},
            {"GAME_DATE": "2026-01-03", "MIN": 30, "FGM": 0, "FGA": 90},
        ]
        stats = prior_season._season_aggregates(logs)
        # 10/100, not mean(1.0, 0.0) = 0.5
        self.assertAlmostEqual(stats["FG_PCT"], 0.1, places=6)

    def test_combos_are_derived_from_component_means(self):
        stats = prior_season._season_aggregates([_log("2026-01-01", pts=20, reb=5, ast=5)])
        self.assertAlmostEqual(stats["PTS+REB+AST"], 30.0, places=6)
        self.assertAlmostEqual(stats["PTS+REB"], 25.0, places=6)

    def test_empty_history_yields_no_profile(self):
        self.assertEqual(prior_season._season_aggregates([]), {})
        self.assertEqual(prior_season._season_aggregates([_log("2026-01-01", minutes=0)]), {})

    def test_dnps_are_stripped_from_cached_logs(self):
        trimmed = prior_season._trim_log({"PTS": 10, "dnps": [{"name": "x"}]})
        self.assertNotIn("dnps", trimmed)
        self.assertEqual(trimmed["PTS"], 10)

    def test_prior_season_strings_walk_backwards(self):
        self.assertEqual(prior_season.prior_season_strings("2026-27", depth=2), ["2025-26", "2024-25"])
        self.assertEqual(prior_season.prior_season_strings("2000-01", depth=1), ["1999-00"])

    def test_unparseable_season_yields_nothing(self):
        self.assertEqual(prior_season.prior_season_strings("garbage"), [])


if __name__ == "__main__":
    unittest.main()


class TeamChangeDisclosureTests(unittest.TestCase):
    """Shrinkage fixes sample size, not role. 42 of the 171 players with props
    on 2026-10-21 had changed teams, so the prior carries a role they may no
    longer have. That has to be visible, not hidden."""

    def setUp(self):
        self._real = prior_season.get_prior_history
        stub = _StubHistory(
            {"2544": {"GP": 67, "MIN": 33.7, "PTS": 21.4}},
            {"2544": [dict(_log("2026-04-10"), TEAM_ABBREVIATION="LAL")]},
        )
        prior_season.get_prior_history = lambda: stub

    def tearDown(self):
        prior_season.get_prior_history = self._real

    def test_team_change_is_flagged(self):
        view = es._apply_prior_season_history(
            {"id": 2544, "team": "PHI", "stats": dict(ZERO_SKELETON), "game_log": []}
        )
        self.assertTrue(view["history_basis"]["team_changed"])
        self.assertEqual(view["history_basis"]["prior_teams"], ["LAL"])

    def test_same_team_is_not_flagged(self):
        view = es._apply_prior_season_history(
            {"id": 2544, "team": "LAL", "stats": dict(ZERO_SKELETON), "game_log": []}
        )
        self.assertFalse(view["history_basis"]["team_changed"])

    def test_team_change_adds_a_caution_reason(self):
        reasons = es._build_reason_strings(
            "over", 15.5, "PTS", ReasonStringTests._components(7.1),
            {"basis": "prior", "prior_games": 67, "prior_season": "2025-26",
             "current_games": 0, "team_changed": True, "prior_teams": ["LAL"]},
        )
        self.assertTrue(any("LAL" in reason and "role" in reason for reason in reasons))

    def test_no_caution_when_the_team_is_unchanged(self):
        reasons = es._build_reason_strings(
            "over", 15.5, "PTS", ReasonStringTests._components(7.1),
            {"basis": "prior", "prior_games": 67, "prior_season": "2025-26",
             "current_games": 0, "team_changed": False, "prior_teams": ["PHI"]},
        )
        self.assertFalse(any("may no longer hold" in reason for reason in reasons))


class PercentScaleTests(unittest.TestCase):
    """season_stats.csv stores USG_PCT as a fraction (0.194), the game logs as
    a percentage (19.4). The prior profile is blended into a season_stats-shaped
    dict, so mixing the two scales lands the blend 100x out."""

    def test_usage_is_converted_to_the_season_stats_fraction_scale(self):
        logs = [
            {"GAME_DATE": "2026-01-01", "MIN": 30, "USG_PCT": 24.0},
            {"GAME_DATE": "2026-01-03", "MIN": 30, "USG_PCT": 26.0},
        ]
        stats = prior_season._season_aggregates(logs)
        self.assertAlmostEqual(stats["USG_PCT"], 0.25, places=6)

    def test_fraction_scale_rates_are_left_alone(self):
        logs = [{"GAME_DATE": "2026-01-01", "MIN": 30, "TS_PCT": 0.6, "FGM": 6, "FGA": 10}]
        stats = prior_season._season_aggregates(logs)
        self.assertAlmostEqual(stats["TS_PCT"], 0.6, places=6)
        self.assertAlmostEqual(stats["FG_PCT"], 0.6, places=6)

    def test_blended_usage_stays_on_one_scale(self):
        real = prior_season.get_prior_history
        try:
            stub = _StubHistory({"1": {"GP": 60, "MIN": 30.0, "PTS": 20.0, "USG_PCT": 0.24}})
            prior_season.get_prior_history = lambda: stub
            view = es._apply_prior_season_history({
                "id": 1, "team": "PHX",
                "stats": {**ZERO_SKELETON, "GP": 1, "USG_PCT": 0.20, "PTS": 18.0},
                "game_log": [_log("2026-10-21")],
            })
            # Both inputs are fractions, so the blend must stay one too.
            self.assertLess(view["stats"]["USG_PCT"], 1.0)
            self.assertGreater(view["stats"]["USG_PCT"], 0.20)
        finally:
            prior_season.get_prior_history = real
