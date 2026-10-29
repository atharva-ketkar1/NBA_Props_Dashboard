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
        cap = self.cb._num("PROXY_BREAKER_COOLDOWN_CAP")
        self.assertLessEqual(self.cb._cooldown_for(50), cap * 1.1 + 1)

    def test_the_cap_bounds_how_late_a_recovery_is_noticed(self):
        """Nothing probes during a cooldown, so the cap is the worst-case delay
        in spotting that a host came back. A slate is lost well before the
        request saving is worth it."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROXY_BREAKER_COOLDOWN_CAP", None)
            importlib.reload(self.cb)
            self.assertLessEqual(
                self.cb._num("PROXY_BREAKER_COOLDOWN_CAP"), 3600,
                "a cap above an hour can cost a whole slate of lines")

    def test_success_after_a_trip_closes_the_breaker(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        self.cb.record_success(HOST)
        # Requests resume at once: a host that is answering is never held back.
        self.assertFalse(self.cb.check(HOST))
        # But the trip is remembered, so the next block costs a longer
        # cooldown instead of restarting at the shortest one.
        status = self.cb.status()[HOST]
        self.assertFalse(status["open"])
        self.assertEqual(status["trips"], 1)


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


class TestTripMemory(BreakerTestCase):
    """A host that flaps must not keep re-entering the block at trip-1 prices.

    Regression for 2026-10-28: DraftKings tripped to trip 3 (66 min), one
    probe succeeded at 19:20 and cleared all three, and the 19:53 re-block
    started again at trip 1 (16 min).
    """

    def test_a_single_success_does_not_erase_the_escalation(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        self.assertEqual(self.cb.status()[HOST]["trips"], 1)

        self.cb.record_success(HOST)
        # Requests flow again...
        self.assertFalse(self.cb.check(HOST))
        # ...but the next block is trip 2, not trip 1 all over again.
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        self.assertEqual(self.cb.status()[HOST]["trips"], 2)

    def test_a_flapping_host_backs_off_for_longer_each_time(self):
        spans = []
        for _ in range(3):
            for _ in range(3):
                self.cb.record_failure(HOST, "403")
            spans.append(self.cb.status()[HOST]["minutes_remaining"])
            # One success between blocks, exactly like the real probe.
            self.cb.record_success(HOST)
        for earlier, later in zip(spans, spans[1:]):
            self.assertGreater(later, earlier,
                               f"cooldowns did not escalate across flaps: {spans}")

    def test_staying_clean_sheds_trips_one_per_decay_interval(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        for _ in range(3):
            self.cb.record_success(HOST)
        self.assertEqual(self.cb.status()[HOST]["trips"], 1)

        # Two clean hours forgive two trips, which takes this host to zero.
        later = self.cb._now() + 2 * 3600 + 10
        with patch.object(self.cb, "_now", return_value=later):
            self.assertEqual(self.cb.status().get(HOST, {}).get("trips", 0), 0)
            self.cb.record_success(HOST)
        self.assertEqual(self.cb.status(), {},
                         "a host that stayed clean is forgotten entirely")

    def test_repeated_successes_do_not_restart_the_decay_clock(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        start = self.cb._now()
        self.cb.record_success(HOST)

        # Healthy traffic for an hour. Each success must leave `clean_since`
        # alone, or the host would never finish paying the trip off.
        for minute in (10, 20, 30, 40, 50):
            with patch.object(self.cb, "_now", return_value=start + minute * 60):
                self.cb.record_success(HOST)
        with patch.object(self.cb, "_now", return_value=start + 3600 + 10):
            self.cb.record_success(HOST)
        self.assertEqual(self.cb.status(), {},
                         "an hour of clean traffic should have cleared the trip")

    def test_a_failed_half_open_probe_reopens_on_one_request(self):
        for _ in range(3):
            self.cb.record_failure(HOST, "403")
        later = self.cb._now() + 10_000
        with patch.object(self.cb, "_now", return_value=later):
            self.assertTrue(self.cb.check(HOST), "cooldown elapsed: expect a probe")
            # The probe is one request. It failing is the whole answer, so the
            # breaker must re-open now rather than spend the threshold again.
            self.cb.record_failure(HOST, "403", weight=1)
            with self.assertRaises(self.cb.CircuitOpen):
                self.cb.check(HOST)
            self.assertEqual(self.cb.status()[HOST]["trips"], 2)

    def test_a_failure_is_charged_for_the_requests_it_spent(self):
        # One call that burned a three-attempt ladder is three failed
        # requests, not one "failure".
        self.cb.record_failure(HOST, "403 after 3 attempt(s)", weight=3)
        with self.assertRaises(self.cb.CircuitOpen):
            self.cb.check(HOST)


class TestLadderDepth(BreakerTestCase):
    """403 stops early; the genuinely transient statuses still get the budget."""

    def _client_returning(self, status):
        import utils.http_client as hc
        hc = importlib.reload(hc)

        class Resp:
            status_code = status
            ok = False
            headers = {"Content-Type": "text/html"}
            content = b"nope"
            text = "nope"

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

    def test_a_403_ladder_is_three_attempts(self):
        hc, counter = self._client_returning(403)
        with self.assertRaises(hc.UpstreamFailed):
            hc.proxied_get(f"https://{HOST}/x", label="dk")
        # Past three, the stale-UA theory the retry exists for is ~1.6%.
        self.assertEqual(counter["n"], 3)

    def test_a_500_still_gets_the_full_budget(self):
        hc, counter = self._client_returning(500)
        with self.assertRaises(hc.UpstreamFailed):
            hc.proxied_get("https://stats.nba.com/x", label="nba")
        self.assertEqual(counter["n"], 6, "transient statuses keep six attempts")

    def test_the_403_cap_is_tunable_during_an_incident(self):
        hc, counter = self._client_returning(403)
        with patch.dict(os.environ, {"PROXY_MAX_ATTEMPTS_403": "5"}):
            with self.assertRaises(hc.UpstreamFailed):
                hc.proxied_get(f"https://{HOST}/x", label="dk")
        self.assertEqual(counter["n"], 5)

    def test_a_half_open_probe_still_overrides_the_403_cap(self):
        hc, counter = self._client_returning(403)
        import utils.circuit_breaker as cb
        for _ in range(3):
            cb.record_failure(HOST, "403")
        with patch.object(cb, "_now", return_value=cb._now() + 10_000):
            before = counter["n"]
            with self.assertRaises(hc.UpstreamFailed):
                hc.proxied_get(f"https://{HOST}/x", label="dk")
            self.assertEqual(counter["n"] - before, 1,
                             "the probe is one request, not three")


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
        # This fixture trips after 3 failed requests, and a 403 ladder is 3
        # attempts deep, so the first category alone reaches the threshold and
        # the other ten cost nothing. Before 403 was capped separately this was
        # 18: three categories each burning a six-attempt budget.
        self.assertEqual(counter["n"], 3,
                         f"expected 3 requests, got {counter['n']}")

    def test_production_defaults_cost_six_requests_not_eighteen(self):
        """The numbers that actually ship, rather than the fixture's."""
        hc, counter = self._blocked_client()
        url = f"https://{HOST}/sites/US-IL-SB/api/markets"

        with patch.dict(os.environ, {"PROXY_BREAKER_TRIP_AFTER": "6"}):
            for _ in range(11):
                try:
                    hc.proxied_get(url, label="draftkings-cat")
                except hc.UpstreamFailed:
                    pass
        # Two 403 ladders of three attempts each reach the threshold of six.
        self.assertEqual(counter["n"], 6,
                         f"expected 6 requests, got {counter['n']}")

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
