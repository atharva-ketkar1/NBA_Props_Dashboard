import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import book_toggles
from utils import edge_score as es


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=ET)
D0 = "2026-10-28"


def _pause(value):
    return patch.dict(os.environ, {"EDGE_SCORE_PAUSED_BOOKS": value})


def _recommendation(book, *, player_id=1, stat="PTS", side="over", line=20.5):
    return {
        "recommendation_key": f"{player_id}|{D0}|{stat}|{side}",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "sportsbook": book,
        "sportsbook_label": es.BOOK_LABELS.get(book, book),
        "stat_type": stat,
        "stat_label": stat,
        "pick": side,
        "pick_label": side.title(),
        "line": line,
        "odds": -110.0,
        "game_id": "g0",
        "game_date": D0,
        "game_started": False,
        "edge_score": 80.0,
        "display_edge_score": 80.0,
        "rank": 1,
    }


class ToggleParsingTests(unittest.TestCase):
    def test_unset_pauses_nothing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDGE_SCORE_PAUSED_BOOKS", None)
            self.assertEqual(book_toggles.paused_books(), set())

    def test_full_names_and_short_codes_both_work(self):
        for value in ("dk", "draftkings", "DraftKings", " DK "):
            with self.subTest(value=value), _pause(value):
                self.assertTrue(book_toggles.book_is_paused("dk"))
                self.assertFalse(book_toggles.book_is_paused("fd"))

    def test_several_books_can_be_paused(self):
        with _pause("dk,fanduel"):
            self.assertEqual(book_toggles.paused_books(), {"dk", "fd"})


class ScoringBooksTests(unittest.TestCase):
    def test_a_paused_book_leaves_the_scoring_set(self):
        with _pause("dk"):
            self.assertEqual(es._scoring_books(), {"fd"})

    def test_supported_books_is_untouched(self):
        """The record of what was published is not rewritten by a pause."""
        with _pause("dk"):
            es._scoring_books()
            self.assertEqual(es.SUPPORTED_BOOKS, {"dk", "fd"})

    def test_nothing_is_paused_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDGE_SCORE_PAUSED_BOOKS", None)
            self.assertEqual(es._scoring_books(), {"dk", "fd"})


class PausedBookStopsNewCandidatesTests(unittest.TestCase):
    """A paused book's prices must not reach the board."""

    def _props_tree(self):
        return {"PTS": {
            "dk": {"line": 25.5, "over": -110, "under": -110, "game_date": D0},
            "fd": {"line": 24.5, "over": -115, "under": -105, "game_date": D0},
        }}

    def test_stale_prices_are_dropped_from_the_prop_tree(self):
        with _pause("dk"):
            tree = es._normalize_props_tree_for_game_date(
                self._props_tree(), target_game_date=D0, active_dates={D0})
        self.assertEqual(sorted(tree.get("PTS", {})), ["fd"])

    def test_both_books_survive_when_nothing_is_paused(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDGE_SCORE_PAUSED_BOOKS", None)
            tree = es._normalize_props_tree_for_game_date(
                self._props_tree(), target_game_date=D0, active_dates={D0})
        self.assertEqual(sorted(tree.get("PTS", {})), ["dk", "fd"])

    def test_the_overlay_index_also_drops_the_paused_book(self):
        players = {"1": {"game_date": D0, "game_id": "g0", "props": {
            "PTS": {"dk": {"line": 25.5}, "fd": {"line": 24.5}}}}}
        with _pause("dk"):
            overlay = es._build_overlay_props_index(players)
        self.assertEqual(sorted(overlay["1"]["PTS"]), ["fd"])


class PausedBookKeepsItsPublishedPicksTests(unittest.TestCase):
    """The reason the pause is split from SUPPORTED_BOOKS.

    Eight DraftKings picks were already published the day DK started blocking.
    Pausing the book must not retract them: the tracker should still show them
    and the next day's recap should still grade them, because the record is of
    what the channel stated.
    """

    def test_a_published_pick_still_counts_as_supported(self):
        with _pause("dk"):
            self.assertTrue(
                es._recommendation_uses_supported_book(_recommendation("dk")))

    def test_the_tracker_ledger_keeps_them(self):
        published = [_recommendation("dk", player_id=1), _recommendation("fd", player_id=2)]
        with _pause("dk"):
            kept = es._filter_tracker_ledger_recommendations(published, slate_date=D0)
        self.assertEqual(
            sorted(r["sportsbook"] for r in kept), ["dk", "fd"],
            "pausing a book must not erase picks it already published",
        )

    def test_the_grading_queue_keeps_them(self):
        payload = {
            "game_date": D0,
            "tracked_recommendations": {
                "1|2026-10-28|PTS|over": _recommendation("dk", player_id=1),
                "2|2026-10-28|PTS|over": _recommendation("fd", player_id=2),
            },
            "recommendations": [_recommendation("dk", player_id=1),
                                _recommendation("fd", player_id=2)],
        }
        with _pause("dk"):
            filtered, pruned = es._filter_recap_payload_to_supported_books(payload)
        self.assertEqual(len(filtered["tracked_recommendations"]), 2)
        self.assertFalse(pruned, "nothing should be pruned by a pause")


class DraftKingsScraperPauseTests(unittest.TestCase):
    def test_a_paused_scraper_makes_no_requests(self):
        """The point of gating inside fetch_dk_odds: 11 categories x 6 retries
        of a doomed request was blowing the 120s scrape budget every 15 min."""
        from scrapers import fetch_odds_draftkings as dk
        with _pause("dk"), patch.object(dk, "fetch_category") as fetch_category:
            rows = dk.fetch_dk_odds()
        self.assertEqual(rows, [])
        fetch_category.assert_not_called()

    def test_an_unpaused_scraper_still_runs(self):
        from scrapers import fetch_odds_draftkings as dk
        # The cadence gate is switched off here deliberately: this test is
        # about the pause switch, and leaving cadence on would make it depend
        # on the real schedule artifact and the wall clock. Cadence has its own
        # tests in test_scrape_cadence.py.
        with patch.dict(os.environ, {"DK_CADENCE_ENABLED": "false"}, clear=False):
            os.environ.pop("EDGE_SCORE_PAUSED_BOOKS", None)
            with patch.object(dk, "fetch_category", return_value=None) as fetch_category:
                dk.fetch_dk_odds()
        self.assertTrue(fetch_category.called)




class OddsAgeGateTests(unittest.TestCase):
    """The automatic half of pausing: a book that goes cold drops out and comes
    back on its own, so an outage needs no human action in either direction."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {
            "EDGE_SCORE_ODDS_DIR": self._tmp.name,
            "EDGE_SCORE_ODDS_AGE_GATE": "true",
            "EDGE_SCORE_MAX_ODDS_AGE_MINUTES": "90",
        })
        self._env.start()
        os.environ.pop("EDGE_SCORE_PAUSED_BOOKS", None)
        self.addCleanup(self._teardown)

    def _teardown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _age(self, name, minutes):
        import time
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("player,prop_type\n")
        stamp = time.time() - minutes * 60
        os.utime(path, (stamp, stamp))

    def test_fresh_books_are_usable(self):
        from utils import book_toggles as bt
        self._age("draftkings.csv", 5)
        self._age("fanduel.csv", 5)
        self.assertEqual(bt.unusable_books(), set())

    def test_a_cold_book_drops_out(self):
        """The live failure: the preserved CSV held 11:46 rows all afternoon
        with no age bound, and picks were priced off them."""
        from utils import book_toggles as bt
        self._age("draftkings.csv", 360)
        self._age("fanduel.csv", 5)
        self.assertEqual(bt.unusable_books(), {"dk"})

    def test_a_book_restores_itself_when_rows_land(self):
        from utils import book_toggles as bt
        self._age("draftkings.csv", 360)
        self._age("fanduel.csv", 5)
        self.assertIn("dk", bt.unusable_books())
        self._age("draftkings.csv", 1)  # a scrape succeeds
        self.assertEqual(bt.unusable_books(), set(),
                         "recovery must need no env change")

    def test_missing_artifact_counts_as_stale(self):
        from utils import book_toggles as bt
        self._age("fanduel.csv", 5)
        self.assertIn("dk", bt.unusable_books())

    def test_manual_pause_is_independent_of_age(self):
        from utils import book_toggles as bt
        self._age("draftkings.csv", 5)
        self._age("fanduel.csv", 5)
        with _pause("fd"):
            self.assertEqual(bt.unusable_books(), {"fd"})

    def test_gate_can_be_switched_off(self):
        from utils import book_toggles as bt
        self._age("draftkings.csv", 9999)
        self._age("fanduel.csv", 9999)
        with patch.dict(os.environ, {"EDGE_SCORE_ODDS_AGE_GATE": "false"}):
            self.assertEqual(bt.stale_books(), set())

    def test_scoring_books_reflects_a_cold_book(self):
        from utils import edge_score as es
        self._age("draftkings.csv", 360)
        self._age("fanduel.csv", 5)
        self.assertEqual(es._scoring_books(), {"fd"})

    def test_bad_age_value_falls_back_to_the_default(self):
        from utils import book_toggles as bt
        self._age("draftkings.csv", 5)
        self._age("fanduel.csv", 5)
        with patch.dict(os.environ, {"EDGE_SCORE_MAX_ODDS_AGE_MINUTES": "soon"}):
            self.assertEqual(bt.stale_books(), set())


if __name__ == "__main__":
    unittest.main()
