import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

HOST = "sportsbook-nash.draftkings.com"


class BreakerTestCase(unittest.TestCase):
    """Each test gets its own state file; the breaker persists to disk because
    every cron tick is a fresh process."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self._tmp.name, "circuit_breaker.json")
        env = {
            "PROXY_BREAKER_STATE_PATH": self.state_path,
            "PROXY_BREAKER_ENABLED": "true",
            "PROXY_BREAKER_TRIP_AFTER": "3",
            "PROXY_BREAKER_COOLDOWN": "900",
            "PROXY_BREAKER_COOLDOWN_CAP": "7200",
        }
        self._env = patch.dict(os.environ, env)
        self._env.start()
        import utils.circuit_breaker as cb
        self.cb = importlib.reload(cb)
        self.addCleanup(self._teardown)

    def _teardown(self):
        self._env.stop()
        self._tmp.cleanup()
        import utils.circuit_breaker as cb
        importlib.reload(cb)


class TestTripping(BreakerTestCase):
    def test_closed_breaker_allows_requests(self):
        self.assertFalse(self.cb.check(HOST))

    def test_does_not_trip_before_the_threshold(self):
        self.cb.record_failure(HOST, "403")
        self.cb.record_failure(HOST, "403")
        # Two is ordinary upstream flakiness, not a block.
        self.assertFalse(self.cb.check(HOST))

    def test_trips_on_the_nth_consecutive_failure(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        with self.assertRaises(self.cb.CircuitOpen):
            self.cb.check(HOST)

    def test_a_success_resets_the_count(self):
        self.cb.record_failure(HOST, "403")
        self.cb.record_failure(HOST, "403")
        self.cb.record_success(HOST)
        self.cb.record_failure(HOST, "403")
        # The streak restarted, so this is failure 1 of 3, not 3 of 3.
        self.assertFalse(self.cb.check(HOST))

    def test_trip_is_per_host(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        # FanDuel was healthy throughout the DraftKings block; one book being
        # refused must not stop the other.
        self.assertFalse(self.cb.check("api.sportsbook.fanduel.com"))


class TestCooldown(BreakerTestCase):
    def test_cooldown_elapsing_yields_a_half_open_probe(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        with self.assertRaises(self.cb.CircuitOpen):
            self.cb.check(HOST)
        # Jump past the cooldown rather than sleeping through it.
        with patch.object(self.cb, "_now", return_value=self.cb._now() + 10_000):
            self.assertTrue(self.cb.check(HOST), "should offer a single probe")

    def test_cooldown_grows_with_each_trip(self):
        spans = []
        for trip in range(1, 5):
            spans.append(self.cb._cooldown_for(trip))
        for earlier, later in zip(spans, spans[1:]):
            self.assertGreater(later, earlier)

    def test_cooldown_is_capped(self):
        self.assertLessEqual(self.cb._cooldown_for(50), 7200 * 1.1 + 1)

    def test_success_after_a_trip_closes_the_breaker(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        self.cb.record_success(HOST)
        self.assertFalse(self.cb.check(HOST))
        self.assertEqual(self.cb.status(), {})


class TestPersistenceAndSafety(BreakerTestCase):
    def test_state_survives_a_new_process(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        # A fresh import is what the next cron tick does five minutes later.
        import utils.circuit_breaker as cb
        reloaded = importlib.reload(cb)
        with self.assertRaises(reloaded.CircuitOpen):
            reloaded.check(HOST)

    def test_corrupt_state_file_reads_as_no_cooldown(self):
        with open(self.state_path, "w", encoding="utf-8") as fh:
            fh.write("{not json at all")
        self.assertFalse(self.cb.check(HOST))

    def test_missing_state_file_is_fine(self):
        self.assertFalse(os.path.exists(self.state_path))
        self.assertFalse(self.cb.check(HOST))

    def test_disabling_the_breaker_never_blocks(self):
        with patch.dict(os.environ, {"PROXY_BREAKER_ENABLED": "false"}):
            import utils.circuit_breaker as cb
            disabled = importlib.reload(cb)
            for _ in range(10):
                disabled.record_failure(HOST, "403")
            self.assertFalse(disabled.check(HOST))

    def test_status_reports_an_open_host(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403 Access Denied")
        snapshot = self.cb.status()
        self.assertIn(HOST, snapshot)
        self.assertTrue(snapshot[HOST]["open"])
        self.assertEqual(snapshot[HOST]["trips"], 1)
        self.assertGreater(snapshot[HOST]["minutes_remaining"], 0)

    def test_reset_clears_a_host(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        self.cb.reset(HOST)
        self.assertFalse(self.cb.check(HOST))


class TestCallerContract(BreakerTestCase):
    def test_circuit_open_is_an_upstream_failure(self):
        from utils.http_client import CircuitOpen, UpstreamFailed
        # The scrapers all catch UpstreamFailed already. If backing off were a
        # separate exception type, a paused host would crash the run instead of
        # producing an empty scrape.
        self.assertTrue(issubclass(CircuitOpen, UpstreamFailed))

    def test_dk_scraper_abandons_the_slate_when_backed_off(self):
        from utils.http_client import CircuitOpen
        import scrapers.fetch_odds_draftkings as dk

        calls = []

        def blocked(*args, **kwargs):
            calls.append(kwargs.get("label"))
            raise CircuitOpen("draftkings-points: egress is blocked upstream")

        # Cadence off: this test is about the breaker, and the gate would
        # otherwise make it depend on the real schedule file and the clock.
        with patch.dict(os.environ, {"DK_CADENCE_ENABLED": "false"}), \
                patch.object(dk, "proxied_get_json", side_effect=blocked), \
                patch.object(dk, "book_is_paused", return_value=False), \
                patch.object(dk.time, "sleep", return_value=None):
            rows = dk.fetch_dk_odds()

        self.assertEqual(rows, [])
        # One category attempted, not all eleven: the block is per-host, so the
        # other ten would only add volume to the thing that caused it.
        self.assertEqual(len(calls), 1, f"expected 1 request, got {len(calls)}")


class TestRequestVolume(BreakerTestCase):
    """The whole point: a blocked host must stop costing requests."""

    def _blocked_client(self):
        """http_client wired to a worker that always relays a 403, with no
        sleeping and the request count exposed."""
        import utils.http_client as hc
        hc = importlib.reload(hc)

        class Resp:
            status_code = 403
            ok = False
            headers = {"Content-Type": "text/html"}
            content = (b"<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD>"
                       b"<BODY>\n<H1>Access Denied</H1>\nerrors.edgesuite.net\n"
                       b"</BODY></HTML>\n")
            text = content.decode()

        counter = {"n": 0}

        class Session:
            headers = {}

            def get(self, *args, **kwargs):
                counter["n"] += 1
                return Resp()

        patches = [
            patch.object(hc, "_get_session", return_value=Session()),
            patch.object(hc, "get_worker_url", return_value="https://worker.example/"),
            patch.object(hc, "_pace", return_value=None),
            patch.object(hc.time, "sleep", return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return hc, counter

    def test_eleven_categories_stop_costing_sixty_six_requests(self):
        hc, counter = self._blocked_client()
        url = f"https://{HOST}/sites/US-IL-SB/api/markets"

        failures = 0
        for _ in range(11):
            try:
                hc.proxied_get(url, label="draftkings-cat")
            except hc.UpstreamFailed:
                failures += 1

        self.assertEqual(failures, 11, "every category still reports failure")
        # Three categories burn the full six-attempt budget (18), then the
        # breaker is open and the remaining eight cost nothing at all.
        self.assertEqual(counter["n"], 18,
                         f"expected 18 requests, got {counter['n']}")

    def test_a_later_run_during_cooldown_costs_nothing(self):
        hc, counter = self._blocked_client()
        url = f"https://{HOST}/sites/US-IL-SB/api/markets"

        for _ in range(11):
            try:
                hc.proxied_get(url, label="draftkings-cat")
            except hc.UpstreamFailed:
                pass
        spent = counter["n"]

        # The next cron tick, five minutes later, still inside the cooldown.
        for _ in range(11):
            with self.assertRaises(hc.CircuitOpen):
                hc.proxied_get(url, label="draftkings-cat")
        self.assertEqual(counter["n"], spent, "cooldown must cost zero requests")

    def test_half_open_probe_spends_one_request_not_six(self):
        hc, counter = self._blocked_client()
        url = f"https://{HOST}/sites/US-IL-SB/api/markets"

        for _ in range(3):
            try:
                hc.proxied_get(url, label="draftkings-cat")
            except hc.UpstreamFailed:
                pass
        spent = counter["n"]

        import utils.circuit_breaker as cb
        with patch.object(cb, "_now", return_value=cb._now() + 10_000):
            with self.assertRaises(hc.UpstreamFailed):
                hc.proxied_get(url, label="draftkings-cat")
        self.assertEqual(counter["n"] - spent, 1,
                         "a probe is one request, not a fresh retry budget")

    def test_a_healthy_host_is_untouched(self):
        import utils.http_client as hc
        hc = importlib.reload(hc)

        class Resp:
            status_code = 200
            ok = True
            headers = {"Content-Type": "application/json"}
            content = b'{"events": []}'
            text = content.decode()

        counter = {"n": 0}

        class Session:
            headers = {}

            def get(self, *args, **kwargs):
                counter["n"] += 1
                return Resp()

        for p in [patch.object(hc, "_get_session", return_value=Session()),
                  patch.object(hc, "get_worker_url", return_value="https://worker.example/"),
                  patch.object(hc, "_pace", return_value=None),
                  patch.object(hc.time, "sleep", return_value=None)]:
            p.start()
            self.addCleanup(p.stop)

        for _ in range(11):
            hc.proxied_get("https://api.sportsbook.fanduel.com/x", label="fd")
        self.assertEqual(counter["n"], 11, "one request per call, no interference")


if __name__ == "__main__":
    unittest.main()
