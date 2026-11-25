"""FanDuel request-volume controls: caches, deadline, daily ceiling.

Context. FanDuel was the one scraper never hardened. Measured on 2026-11-23 it
ran 37 times against DraftKings' 6, at ~70-81 requests each -- roughly 3,000
requests/day with no ceiling, four times the ~730/day that earned DraftKings a
block. It also paced on its own private 1.1s clock while DraftKings paced on
http_client's, and `cron_closing_lines.scrape_and_shape_odds` submits both to a
ThreadPoolExecutor *concurrently*, so the aggregate against one 60-request/60s
worker bucket was ~109/min.

The fix reduces request COUNT rather than raising request RATE, because rate is
the lever that provokes the block these guards exist to avoid. These tests pin
the three mechanisms that cut the count, the deadline that stops a slow run from
losing everything, and the ceiling that catches a runaway.
"""

import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scrapers import fetch_odds_fanduel as fanduel
from utils import scrape_cadence

BASE = datetime(2099, 1, 1, tzinfo=timezone.utc)
TABS = [
    {"name": "player-points", "title": "Player Points"},
    {"name": "player-rebounds", "title": "Player Rebounds"},
]


def _event(event_id, days_out=1):
    return {
        "eventId": str(event_id),
        "name": f"Team A{event_id} @ Team B{event_id}",
        "openDate": (BASE + timedelta(days=days_out)).isoformat().replace("+00:00", "Z"),
        "inPlay": False,
    }


def _main_page(events):
    return {"attachments": {"events": {e["eventId"]: e for e in events}}}


def _market_payload(event_id, player, line=25.5):
    return {
        "attachments": {
            "events": {str(event_id): {"eventId": str(event_id), "inPlay": False}},
            "markets": {
                "m1": {
                    "marketName": f"{player} - Points",
                    "runners": [
                        {
                            "result": {"type": "OVER"},
                            "handicap": line,
                            "secondaryLogo": "teams/boston_celtics.png",
                            "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}},
                        },
                        {
                            "result": {"type": "UNDER"},
                            "handicap": line,
                            "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}},
                        },
                    ],
                }
            },
        }
    }


def _empty_payload(event_id):
    return {
        "attachments": {
            "events": {str(event_id): {"eventId": str(event_id), "inPlay": False}},
            "markets": {},
        }
    }


class _CacheOnDisk(unittest.TestCase):
    """Base: give each test its own cache and cadence state file.

    Both modules go inert under pytest unless an explicit path is set (the
    `health_ledger._inert()` contract), so setting the paths is also what opts
    these tests in to exercising the real thing.
    """

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_path = os.path.join(self._tmp.name, "tabs.json")
        self.cadence_path = os.path.join(self._tmp.name, "cadence.json")
        env = patch.dict(os.environ, {
            "FANDUEL_TAB_CACHE_PATH": self.cache_path,
            "DK_CADENCE_STATE_PATH": self.cadence_path,
        })
        env.start()
        self.addCleanup(env.stop)


class TheTabListIsCachedAcrossProcesses(_CacheOnDisk):
    """The cache must be on DISK, not in memory.

    master_cron runs as a fresh process every five minutes (`*/5 * * * * ...
    master_cron.py`), so a module-global dict would be empty on every tick and
    save exactly nothing -- which was the first version of this change.
    """

    def test_a_second_run_does_not_refetch_the_tab_list(self):
        calls = []

        def fake_fetch(url):
            calls.append(url)
            raise AssertionError("should not be reached in this test")

        with patch.object(fanduel, "_fetch_via_proxy", side_effect=fake_fetch):
            # Seed the cache the way a successful fetch would.
            with fanduel._cache_lock:
                state = fanduel._load_cache()
                state["tabs"]["7"] = {
                    "expires": time.time() + 3600, "tabs": list(TABS)}
                fanduel._save_cache(state)

            got = fanduel.get_all_available_tabs("7")

        self.assertEqual(got, TABS)
        self.assertEqual(calls, [], "a cached tab list must cost no request")

    def test_the_cache_really_is_a_file_a_later_process_could_read(self):
        with fanduel._cache_lock:
            state = fanduel._load_cache()
            state["tabs"]["7"] = {"expires": time.time() + 3600, "tabs": list(TABS)}
            fanduel._save_cache(state)

        on_disk = json.loads(Path(self.cache_path).read_text())
        self.assertEqual(on_disk["tabs"]["7"]["tabs"], TABS)

    def test_an_expired_entry_is_refetched(self):
        with fanduel._cache_lock:
            state = fanduel._load_cache()
            state["tabs"]["7"] = {"expires": time.time() - 1, "tabs": list(TABS)}
            fanduel._save_cache(state)

        payload = {"layout": {"tabs": {"t1": {"title": "Player Points"}}}}

        class _Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return payload

        with patch.object(fanduel, "_fetch_via_proxy", return_value=_Response()) as fetch:
            got = fanduel.get_all_available_tabs("7")

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(got, [{"name": "player-points", "title": "Player Points"}])

    def test_a_failed_fetch_is_not_cached(self):
        """Caching a failure would make one bad response look like an hour of a
        propless game, which is the early-exit scan's worst possible input."""
        with patch.object(fanduel, "_fetch_via_proxy",
                          side_effect=fanduel.FanDuelRequestError("boom")):
            with self.assertRaises(fanduel.FanDuelRequestError):
                fanduel.get_all_available_tabs("9")

        with fanduel._cache_lock:
            self.assertNotIn("9", fanduel._load_cache()["tabs"])

    def test_a_cache_from_another_day_is_discarded(self):
        Path(self.cache_path).write_text(json.dumps({
            "day": "1999-01-01",
            "tabs": {"7": {"expires": time.time() + 3600, "tabs": list(TABS)}},
            "empty": {"7|player-points": time.time() + 3600},
        }))
        with fanduel._cache_lock:
            state = fanduel._load_cache()
        self.assertEqual(state["tabs"], {})
        self.assertEqual(state["empty"], {})

    def test_a_corrupt_cache_costs_requests_not_the_scrape(self):
        Path(self.cache_path).write_text("{ not json")
        with fanduel._cache_lock:
            state = fanduel._load_cache()
        self.assertEqual(state["tabs"], {})


class TheClosingCaptureIgnoresTheCaches(_CacheOnDisk):
    """Completeness beats volume at tip. A line captured late is worthless, and
    a tab skipped at noon may be posted by 7pm."""

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_force_revalidates_the_tab_list(self, main_page, tabs, props):
        main_page.return_value = _main_page([_event(1)])
        tabs.return_value = list(TABS)
        props.side_effect = lambda event_id, tab: _market_payload(event_id, "P")

        fanduel.fetch_odds(force=True)

        self.assertEqual(tabs.call_args.kwargs.get("use_cache"), False)

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_force_rechecks_a_tab_previously_seen_empty(self, main_page, tabs, props):
        main_page.return_value = _main_page([_event(1)])
        tabs.return_value = list(TABS)
        with fanduel._cache_lock:
            state = fanduel._load_cache()
            state["empty"] = {
                "1|player-points": time.time() + 3600,
                "1|player-rebounds": time.time() + 3600,
            }
            fanduel._save_cache(state)

        props.side_effect = lambda event_id, tab: _market_payload(event_id, "P")
        fanduel.fetch_odds(force=True)

        asked = sorted(c.args[1] for c in props.call_args_list)
        self.assertEqual(asked, ["player-points", "player-rebounds"])


class AnEmptyTabIsSkippedThenRechecked(_CacheOnDisk):
    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_a_tab_that_yields_nothing_is_recorded(self, main_page, tabs, props):
        main_page.return_value = _main_page([_event(1)])
        tabs.return_value = list(TABS)

        def fake_props(event_id, tab):
            if tab == "player-rebounds":
                return _empty_payload(event_id)
            return _market_payload(event_id, "P")

        props.side_effect = fake_props
        fanduel.fetch_odds()

        with fanduel._cache_lock:
            empty = fanduel._load_cache()["empty"]
        self.assertIn("1|player-rebounds", empty)
        self.assertNotIn("1|player-points", empty)

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_a_recorded_empty_tab_is_not_requested_again(self, main_page, tabs, props):
        main_page.return_value = _main_page([_event(1)])
        tabs.return_value = list(TABS)
        with fanduel._cache_lock:
            state = fanduel._load_cache()
            state["empty"] = {"1|player-rebounds": time.time() + 3600}
            fanduel._save_cache(state)

        props.side_effect = lambda event_id, tab: _market_payload(event_id, "P")
        fanduel.fetch_odds()

        asked = [c.args[1] for c in props.call_args_list]
        self.assertEqual(asked, ["player-points"])

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_the_skip_expires(self, main_page, tabs, props):
        """The TTL is short on purpose: an empty tab is often a market FanDuel
        has not posted yet, not a tab that will stay empty all day."""
        main_page.return_value = _main_page([_event(1)])
        tabs.return_value = list(TABS)
        with fanduel._cache_lock:
            state = fanduel._load_cache()
            state["empty"] = {"1|player-rebounds": time.time() - 1}
            fanduel._save_cache(state)

        props.side_effect = lambda event_id, tab: _market_payload(event_id, "P")
        fanduel.fetch_odds()

        asked = sorted(c.args[1] for c in props.call_args_list)
        self.assertEqual(asked, ["player-points", "player-rebounds"])

    def test_the_empty_ttl_is_shorter_than_the_tab_list_ttl(self):
        self.assertLess(fanduel.EMPTY_TAB_TTL_SECONDS,
                        fanduel.TAB_LIST_TTL_SECONDS)


class TheDeadlineReturnsPartialRowsNotNone(_CacheOnDisk):
    """The old shape was `f2.result(timeout=120)` against a ~90s scrape with no
    cooperative deadline: a 35% slowdown threw away all ~838 props. A prop whose
    line was already captured is still a usable quote."""

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_an_expired_deadline_returns_what_was_collected(self, main_page, tabs, props):
        main_page.return_value = _main_page([_event(i, i) for i in range(1, 5)])
        tabs.return_value = [TABS[0]]

        seen = []

        def fake_props(event_id, tab):
            seen.append(event_id)
            return _market_payload(event_id, f"Player {event_id}")

        props.side_effect = fake_props

        # A clock that advances 10s per reading. With one tab per event the scan
        # takes two readings per event (the event-level check, then the
        # tab-level one), so a deadline of 25 lets event 1 finish and stops the
        # run at event 2 -- the case that matters, where some rows exist.
        clock = {"t": 0.0}

        def fake_monotonic():
            clock["t"] += 10.0
            return clock["t"]

        with patch.object(fanduel.time, "monotonic", side_effect=fake_monotonic):
            result = fanduel.fetch_odds(deadline=25.0)

        self.assertTrue(result, "partial rows must be returned, not discarded")
        self.assertEqual(len(seen), 1, "exactly the one event that fit should be scanned")
        self.assertLess(len(seen), 4, "the scan must have stopped early")

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_no_deadline_scans_everything(self, main_page, tabs, props):
        main_page.return_value = _main_page([_event(i, i) for i in range(1, 4)])
        tabs.return_value = [TABS[0]]
        props.side_effect = lambda event_id, tab: _market_payload(event_id, "P")

        fanduel.fetch_odds()

        self.assertEqual(props.call_count, 3)


class TheDailyCeilingCatchesARunaway(_CacheOnDisk):
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_an_exhausted_budget_returns_no_rows(self, main_page):
        """Empty list, matching the paused-book contract: no rows means nothing
        refreshes, rather than stale lines re-stamped as current."""
        with patch.dict(os.environ, {"FD_DAILY_REQUEST_BUDGET": "10"}):
            # One reservation of ~90 already exceeds a ceiling of 10.
            result = fanduel.fetch_odds()
        self.assertEqual(result, [])
        main_page.assert_not_called()

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_unspent_requests_are_returned_to_the_day(self, main_page, tabs, props):
        """Otherwise the ceiling would punish the caching: a run that skips 20
        tabs must not be charged for them."""
        main_page.return_value = _main_page([_event(1)])
        tabs.return_value = [TABS[0]]
        props.side_effect = lambda event_id, tab: _market_payload(event_id, "P")

        fanduel.fetch_odds()

        state = json.loads(Path(self.cadence_path).read_text())
        spent = state["books"]["FD"]["requests"]
        self.assertLess(spent, fanduel.EXPECTED_REQUESTS_PER_RUN,
                        "the reservation was never settled against actual usage")

    def test_a_failed_main_page_does_not_cost_the_whole_reservation(self):
        with patch.object(fanduel, "get_nba_main_page_data", return_value=None):
            fanduel.fetch_odds()
        state = json.loads(Path(self.cadence_path).read_text())
        self.assertLess(state["books"]["FD"]["requests"],
                        fanduel.EXPECTED_REQUESTS_PER_RUN)

    def test_the_budget_is_per_book(self):
        scrape_cadence.check_budget("FD", 5)
        state = json.loads(Path(self.cadence_path).read_text())
        self.assertEqual(state["books"]["FD"]["requests"], 5)
        self.assertNotIn("requests", state,
                         "FD accounting must not touch DraftKings' flat counters")

    def test_an_unbudgeted_book_is_allowed_rather_than_invented(self):
        decision = scrape_cadence.check_budget("MGM", 5)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.window, "unbudgeted")


class ThereIsExactlyOnePaceGovernor(unittest.TestCase):
    """The reason FanDuel was migrated onto http_client at all.

    `cron_closing_lines.scrape_and_shape_odds` submits both books to a
    ThreadPoolExecutor and awaits them afterwards, so they run CONCURRENTLY --
    the old comment in fetch_odds_fanduel claiming DraftKings "runs immediately
    before FanDuel" was wrong. While FanDuel kept a private 1.1s clock and
    DraftKings used http_client's, the aggregate against one 60-request/60s
    worker bucket was ~2 req/s, roughly double the ceiling, and neither half
    could see the other. One shared lock makes the total correct by
    construction rather than by two modules agreeing on a number.
    """

    def test_fanduel_no_longer_owns_a_private_pace_clock(self):
        for attribute in ("_pace", "_get_session", "REQUEST_DELAY_SECONDS",
                          "_last_request_at", "_pace_lock"):
            self.assertFalse(
                hasattr(fanduel, attribute),
                f"fetch_odds_fanduel.{attribute} is back; that is a second "
                f"pace clock and the aggregate rate stops being knowable",
            )

    def test_fanduel_requests_go_through_the_shared_governor(self):
        from utils import http_client
        with patch.object(http_client, "proxied_get") as proxied:
            proxied.return_value = object()
            fanduel._fetch_via_proxy("https://api.sportsbook.fanduel.com/x")
        self.assertEqual(proxied.call_count, 1)
        self.assertEqual(proxied.call_args.kwargs.get("label"), "fanduel")

    def test_both_books_pace_through_the_same_module(self):
        """Not just "both do some pacing" -- the same module, and therefore the
        same `_pace_lock` instance and the same `_pace_interval`, which is what
        actually serialises two concurrent threads and lets a 429 seen by one
        book widen the spacing for the other.

        DraftKings imports the function rather than the module, so the identity
        check is on where the function came from.
        """
        from scrapers import fetch_odds_draftkings as dk
        self.assertEqual(dk.proxied_get_json.__module__, "utils.http_client")
        self.assertEqual(fanduel.http_client.__name__, "utils.http_client")

        # One interval shared by both, not two numbers that happen to match.
        from utils import http_client
        self.assertIs(fanduel.http_client, http_client)

    def test_the_fanduel_timeout_exceeds_the_workers_own_worst_case(self):
        """The worker allows 2 upstream attempts at 15s, so a client timeout
        under ~30s cancels work that was about to succeed. This was 15."""
        self.assertGreaterEqual(fanduel.REQUEST_TIMEOUT, 30)


class DraftKingsCadenceIsUntouched(_CacheOnDisk):
    """The FD budget was added as a separate namespace precisely so DraftKings'
    tuned window/interval behaviour could not be perturbed. This is the guard on
    that claim."""

    def test_dk_still_uses_its_flat_counters(self):
        decision = scrape_cadence.should_scrape_dk(force=True, expected_requests=11)
        self.assertTrue(decision.allowed)
        state = json.loads(Path(self.cadence_path).read_text())
        self.assertEqual(state["requests"], 11)

    def test_fd_accounting_does_not_consume_dk_budget(self):
        scrape_cadence.check_budget("FD", 500)
        decision = scrape_cadence.should_scrape_dk(force=True, expected_requests=11)
        self.assertTrue(decision.allowed,
                        "FanDuel's spend must not exhaust DraftKings' ceiling")

    def test_dk_budget_exhaustion_still_works(self):
        with patch.dict(os.environ, {"DK_DAILY_REQUEST_BUDGET": "5"}):
            decision = scrape_cadence.should_scrape_dk(force=True, expected_requests=11)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.window, "budget")


if __name__ == "__main__":
    unittest.main()
