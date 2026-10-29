"""The DraftKings scrape has to be stoppable from the inside.

A Python thread cannot be killed, so `future.result(timeout=...)` never stopped
the scrape it timed out on -- it abandoned the *result* and let the requests
run on. Measured on 2026-10-28: the caller logged a timeout at 19:52:10 and the
scraper kept asking a refusing host until 19:53:57, then the rows it did
produce were discarded because `dk_data` had already been left empty.

These tests pin the two halves of the fix: the scraper honours a deadline it is
given, and the caller no longer waits on, or throws away, work it gave up on.
"""

import concurrent.futures
import importlib
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _row(player, prop):
    return {"player": player, "prop_type": prop, "line": 1.5,
            "over_odds": -110, "under_odds": -110, "game": "A @ B",
            "game_date": "2026-10-28", "sportsbook": "draftkings"}


class DeadlineTestCase(unittest.TestCase):
    """Cadence off throughout: these tests are about the time budget, not the
    gate, and the gate would otherwise depend on the real schedule and clock."""

    def setUp(self):
        import scrapers.fetch_odds_draftkings as dk
        self.dk = importlib.reload(dk)
        self._env = patch.dict(os.environ, {"DK_CADENCE_ENABLED": "false"})
        self._env.start()
        self.addCleanup(self._env.stop)
        for target, kwargs in (
            ("book_is_paused", {"return_value": False}),
            ("record_requests", {"return_value": None}),
        ):
            p = patch.object(self.dk, target, **kwargs)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(self.dk.time, "sleep", return_value=None)
        p.start()
        self.addCleanup(p.stop)


class TestScraperHonoursItsDeadline(DeadlineTestCase):
    def test_no_new_category_is_started_past_the_deadline(self):
        attempted = []
        clock = {"t": 1000.0}

        def slow_category(cat_id, prop_label):
            attempted.append(prop_label)
            clock["t"] += 10.0          # each category costs ten seconds
            return {"events": [], "markets": [], "selections": []}

        with patch.object(self.dk, "fetch_category", side_effect=slow_category), \
                patch.object(self.dk.time, "monotonic", lambda: clock["t"]):
            self.dk.fetch_dk_odds(deadline=1000.0 + 25.0)

        # Checked before each category: three start (t=1000, 1010, 1020), and
        # the fourth is refused at t=1030.
        self.assertEqual(len(attempted), 3,
                         f"expected 3 categories, got {attempted}")

    def test_rows_already_collected_are_returned_not_discarded(self):
        clock = {"t": 0.0}
        parsed = {"points": [_row("shai gilgeous-alexander", "points")],
                  "threes": [_row("shai gilgeous-alexander", "threes")]}

        def category(cat_id, prop_label):
            clock["t"] += 10.0
            return {"key": prop_label}

        with patch.object(self.dk, "fetch_category", side_effect=category), \
                patch.object(self.dk, "parse_dk_data",
                             side_effect=lambda data, key: parsed.get(key, [])), \
                patch.object(self.dk.time, "monotonic", lambda: clock["t"]):
            rows = self.dk.fetch_dk_odds(deadline=15.0)

        # Two categories ran before the deadline; their rows survive. The old
        # shape returned nothing at all here.
        self.assertEqual(len(rows), 2, f"partial rows were dropped: {rows}")
        self.assertTrue(all(r["sportsbook"] == "draftkings" for r in rows))

    def test_the_unspent_request_budget_is_refunded(self):
        clock = {"t": 0.0}

        def category(cat_id, prop_label):
            clock["t"] += 10.0
            return {}

        with patch.object(self.dk, "fetch_category", side_effect=category), \
                patch.object(self.dk.time, "monotonic", lambda: clock["t"]), \
                patch.object(self.dk, "record_requests") as refund:
            self.dk.fetch_dk_odds(deadline=15.0)

        # Two of eleven categories ran, so nine reserved requests go back --
        # a truncated run must not also eat into the daily budget.
        refund.assert_called_once_with(-9)

    def test_no_deadline_means_every_category_runs(self):
        attempted = []

        with patch.object(self.dk, "fetch_category",
                          side_effect=lambda c, p: attempted.append(p) or {}):
            self.dk.fetch_dk_odds()

        self.assertEqual(len(attempted), len(self.dk.PLAYER_PROP_CATEGORIES))

    def test_an_already_passed_deadline_spends_nothing(self):
        attempted = []

        with patch.object(self.dk, "fetch_category",
                          side_effect=lambda c, p: attempted.append(p) or {}), \
                patch.object(self.dk.time, "monotonic", lambda: 5_000.0):
            rows = self.dk.fetch_dk_odds(deadline=1_000.0)

        self.assertEqual(attempted, [], "a passed deadline must cost no requests")
        self.assertEqual(rows, [])


class TestEmptyScrapeIsNotLoggedAsSuccess(DeadlineTestCase):
    def test_a_fully_failed_run_warns(self):
        """Nine `[OK] ... props=0` lines are how five hours of outage on
        2026-10-28 read as success in the cron log."""
        with patch.object(self.dk, "fetch_category", return_value=None), \
                self.assertLogs(self.dk.logger, level="WARNING") as logs:
            rows = self.dk.fetch_dk_odds()

        self.assertEqual(rows, [])
        self.assertTrue(any("[EMPTY]" in m for m in logs.output),
                        f"expected an [EMPTY] warning, got {logs.output}")
        self.assertFalse(any("[OK]" in m for m in logs.output))

    def test_an_empty_board_is_not_a_failure(self):
        """No games tonight is not an outage: every category answered, there
        was just nothing on it."""
        with patch.object(self.dk, "fetch_category", return_value={"events": []}), \
                patch.object(self.dk, "parse_dk_data", return_value=[]), \
                self.assertLogs(self.dk.logger, level="INFO") as logs:
            rows = self.dk.fetch_dk_odds()

        self.assertEqual(rows, [])
        self.assertFalse(any("[EMPTY]" in m for m in logs.output),
                         f"an empty board must not warn: {logs.output}")
        self.assertTrue(any("board is empty" in m for m in logs.output))

    def test_a_run_with_rows_still_logs_ok(self):
        with patch.object(self.dk, "fetch_category", return_value={"x": 1}), \
                patch.object(self.dk, "parse_dk_data",
                             return_value=[_row("luka doncic", "points")]), \
                self.assertLogs(self.dk.logger, level="INFO") as logs:
            rows = self.dk.fetch_dk_odds()

        self.assertTrue(rows)
        self.assertTrue(any("[OK]" in m for m in logs.output))


class TestCallerDoesNotWaitOnWorkItAbandoned(unittest.TestCase):
    def test_shutdown_does_not_block_on_a_running_future(self):
        """`with ThreadPoolExecutor()` calls shutdown(wait=True) on exit, so it
        waited for the very thread the timeout had just given up on. That is
        what turned a 120s safeguard into a 227s call."""
        released = []

        def slow():
            time.sleep(2.0)
            released.append(True)
            return ["rows"]

        executor = concurrent.futures.ThreadPoolExecutor()
        started = time.monotonic()
        try:
            future = executor.submit(slow)
            with self.assertRaises(concurrent.futures.TimeoutError):
                future.result(timeout=0.2)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 1.5,
                        f"shutdown blocked on the abandoned thread ({elapsed:.1f}s)")
        self.assertEqual(released, [], "the thread had not finished yet")

    def test_the_caller_passes_a_deadline_inside_its_own_timeout(self):
        """The backstop must be the deadline plus margin, never shorter -- a
        shorter one would fire before the scraper's own gate could act."""
        import cron_jobs.cron_closing_lines as ccl
        ccl = importlib.reload(ccl)
        self.assertGreater(ccl.SCRAPE_BACKSTOP_GRACE_SECONDS, 0)
        self.assertGreater(
            ccl.DK_SCRAPE_BUDGET_SECONDS + ccl.SCRAPE_BACKSTOP_GRACE_SECONDS,
            ccl.DK_SCRAPE_BUDGET_SECONDS)


if __name__ == "__main__":
    unittest.main()
