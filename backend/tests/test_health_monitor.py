"""Tests for the notification glue: what actually reaches the phone, and once.

Two properties matter more than the rest, and both are about not being muted:

  * A push is marked as delivered only after it lands, so a failed send is retried
    on the next tick instead of being recorded as sent. The cost is a possible
    duplicate, which is the right way round.
  * Many simultaneous incidents collapse into one notification. When the network or
    Supabase goes, a dozen units break in the same minute; twelve pushes is a thing
    you silence, and a silenced channel is the original three-day outage again.
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import alert_push, health_ledger as hl, health_monitor


ET = ZoneInfo("America/New_York")
HOUR = 3600.0
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=ET).timestamp()


class FakePush:
    """Stands in for ntfy. Records what would have been sent."""

    def __init__(self, *, succeed=True):
        self.succeed = succeed
        self.incidents = []
        self.batches = []
        self.heartbeats = []

    def send_incident(self, action):
        self.incidents.append(action)
        return self.succeed

    def send_incident_batch(self, actions):
        self.batches.append(actions)
        return self.succeed

    def enabled(self):
        return True

    def heartbeat_configured(self):
        return True

    def ping_heartbeat(self, *, failed=False):
        self.heartbeats.append(failed)
        return True


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._env = {}
        for key, value in (
            ("HEALTH_LEDGER_BACKEND", "file"),
            ("HEALTH_LEDGER_STATE_PATH", str(Path(self._dir.name) / "health.json")),
            ("HEALTH_REMINDER_HOURS", "24"),
            ("HEALTH_DORMANT_MONTHS", "7,8,9"),
        ):
            self._env[key] = os.environ.get(key)
            os.environ[key] = value
        hl._BACKEND = "file"

        self.fake = FakePush()
        self._real = {}
        for name in ("send_incident", "send_incident_batch", "enabled",
                     "heartbeat_configured", "ping_heartbeat"):
            self._real[name] = getattr(alert_push, name)
            setattr(alert_push, name, getattr(self.fake, name))
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._real.items():
            setattr(alert_push, name, value)
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _break(self, unit, hours=72):
        for offset in range(0, hours, 6):
            hl.record_failure(unit, RuntimeError("404 Not Found"),
                              now=NOW - hours * HOUR + offset * HOUR)

    def test_an_incident_is_pushed_once(self):
        self._break("scraper:nba_injury_report")
        health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        self.assertEqual(len(self.fake.incidents), 1)
        self.assertEqual(self.fake.incidents[0]["kind"], "open")

        # Second tick, nothing new to say.
        health_monitor.evaluate_and_notify(now=NOW + 300, dormant=False)
        self.assertEqual(len(self.fake.incidents), 1)

    def test_a_failed_push_is_retried_next_tick(self):
        self.fake.succeed = False
        self._break("scraper:nba_injury_report")
        health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        self.assertEqual(len(self.fake.incidents), 1)

        self.fake.succeed = True
        health_monitor.evaluate_and_notify(now=NOW + 300, dormant=False)
        self.assertEqual(len(self.fake.incidents), 2,
                         "a push that did not land must not count as delivered")

        health_monitor.evaluate_and_notify(now=NOW + 600, dormant=False)
        self.assertEqual(len(self.fake.incidents), 2)

    def test_simultaneous_incidents_collapse_into_one_push(self):
        for unit in ("scraper:nba_injury_report", "scraper:season_stats",
                     "scraper:shooting_zones", "scraper:assist_zones",
                     "scraper:play_type"):
            self._break(unit, hours=96)
        health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        self.assertEqual(len(self.fake.batches), 1)
        self.assertEqual(self.fake.incidents, [])
        self.assertGreaterEqual(len(self.fake.batches[0]), 5)

    def test_a_few_incidents_are_pushed_individually(self):
        # Individually is more useful when there are few enough to read; the batch
        # exists for the "one shared cause" case, not as a general cap.
        self._break("scraper:nba_injury_report", hours=96)
        self._break("scraper:season_stats", hours=96)
        health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        self.assertEqual(self.fake.batches, [])
        self.assertEqual(len(self.fake.incidents), 2)

    def test_nothing_is_pushed_in_dry_run(self):
        self._break("scraper:nba_injury_report")
        actions = health_monitor.evaluate_and_notify(now=NOW, dormant=False,
                                                     dry_run=True)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.fake.incidents, [])

    def test_recovery_is_pushed_after_an_announced_break(self):
        unit = "scraper:nba_injury_report"
        self._break(unit)
        health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        hl.record_success(unit, rows=430, now=NOW + HOUR)
        health_monitor.evaluate_and_notify(now=NOW + HOUR, dormant=False)
        self.assertEqual([a["kind"] for a in self.fake.incidents],
                         ["open", "recovered"])

    def test_breaker_trips_become_an_upstream_unit(self):
        from utils import circuit_breaker

        def fake_status():
            return {"api.draftkings.com": {"open": True, "minutes_remaining": 41.0,
                                           "trips": 6, "consecutive_failures": 0,
                                           "last_detail": "Akamai Access Denied"}}

        real_status = circuit_breaker.status
        circuit_breaker.status = fake_status
        self.addCleanup(lambda: setattr(circuit_breaker, "status", real_status))

        # Three ticks past the duration floor: a sustained egress block, not a wobble.
        for offset in (0, 300, 600):
            health_monitor.fold_circuit_breaker(now=NOW - 3 * HOUR + offset)
        record = hl.load()["upstream:api.draftkings.com"]
        self.assertEqual(record["consecutive_failures"], 3)

        health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        self.assertEqual(len(self.fake.incidents), 1)
        self.assertIn("draftkings", self.fake.incidents[0]["record"]["unit"])

    def test_a_flapping_host_below_the_bar_is_not_recorded(self):
        from utils import circuit_breaker

        def fake_status():
            return {"api.draftkings.com": {"open": False, "minutes_remaining": 0.0,
                                           "trips": 2, "consecutive_failures": 1,
                                           "last_detail": "403"}}

        real_status = circuit_breaker.status
        circuit_breaker.status = fake_status
        self.addCleanup(lambda: setattr(circuit_breaker, "status", real_status))

        health_monitor.fold_circuit_breaker(now=NOW)
        self.assertNotIn("upstream:api.draftkings.com", hl.load())

    def test_a_cleared_host_closes_its_incident(self):
        from utils import circuit_breaker

        hl.record_failure("upstream:api.draftkings.com", "egress blocked", now=NOW)
        real_status = circuit_breaker.status
        circuit_breaker.status = lambda: {}
        self.addCleanup(lambda: setattr(circuit_breaker, "status", real_status))

        health_monitor.fold_circuit_breaker(now=NOW + 60)
        record = hl.load()["upstream:api.draftkings.com"]
        self.assertEqual(record["consecutive_failures"], 0)

    def test_an_unconfigured_notifier_does_not_swallow_the_incident(self):
        # The one failure mode with no backstop: incidents detected and nowhere to
        # send them. It has to be loud in the log at least.
        alert_push.enabled = lambda: False
        self._break("scraper:nba_injury_report")
        actions = health_monitor.evaluate_and_notify(now=NOW, dormant=False)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.fake.incidents, [])

    def test_the_message_body_uses_the_evaluation_moment(self):
        # Regression: the body read wall-clock time while the reason line was
        # computed from the evaluation moment, so a push could say "no successful
        # run in 26.2h" directly above "Last success: 10m ago". An alert that
        # argues with itself is one you stop trusting mid-incident.
        unit = "job:intraday"
        hl.record_success(unit, now=NOW - 30 * HOUR)
        future = NOW + 48 * HOUR
        actions = health_monitor.evaluate_and_notify(now=future, dormant=False)
        self.assertEqual(len(actions), 1)
        self.assertEqual(self.fake.incidents[0]["now"], future)

        body = alert_push._lines(self.fake.incidents[0]["record"],
                                 self.fake.incidents[0]["reason"],
                                 now=self.fake.incidents[0]["now"])
        self.assertIn("Last success: 3.2d ago", body)  # 78h, matching the reason

    def test_heartbeat_reports_an_unhealthy_tick(self):
        health_monitor.heartbeat(healthy=False)
        self.assertEqual(self.fake.heartbeats, [True])
        health_monitor.heartbeat(healthy=True)
        self.assertEqual(self.fake.heartbeats, [True, False])

    def test_heartbeat_is_silent_in_dry_run(self):
        health_monitor.heartbeat(healthy=True, dry_run=True)
        self.assertEqual(self.fake.heartbeats, [])


if __name__ == "__main__":
    unittest.main()
