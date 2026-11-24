"""Tests for the health ledger's promotion rule -- the line between a transient
upstream failure and an incident worth waking someone for.

The incident these pin down: NBA moved the injury report page on 2026-11-20. The
scraper failed hourly, master_cron spent its four daily retries and then skipped the
job entirely on every subsequent tick, and nobody found out for three days. Every
number needed to notice was already being computed; none of it outlived the process.

What matters here is the *negative* cases as much as the positive ones. A monitor
that pages on a DraftKings 403 gets muted within a week, and a muted monitor is worse
than none -- it is the same silence with a false sense of coverage. So these tests
assert both that a three-day structural break is caught and that an hour of 403s,
one failed run, and the offseason are not.
"""

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import health_ledger as hl


ET = ZoneInfo("America/New_York")
HOUR = 3600.0
# A fixed November moment: in season, so dormancy never silences these by accident.
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=ET).timestamp()


def record(unit, **overrides):
    """A ledger record, defaulting to healthy."""
    base = hl._blank(unit)
    base.update(overrides)
    return base


def failing(unit, *, failures, since_hours, error="HTTPError: 403"):
    return record(
        unit,
        consecutive_failures=failures,
        first_failure_ts=NOW - since_hours * HOUR,
        last_attempt_ts=NOW,
        last_success_ts=NOW - since_hours * HOUR,
        last_error=error,
    )


class ClassifyTransient(unittest.TestCase):
    """Things that must never page."""

    def test_single_failure_is_not_an_incident(self):
        state, _ = hl.classify(failing("scraper:draftkings", failures=1,
                                       since_hours=0.1), NOW)
        self.assertEqual(state, "degraded")

    def test_an_hour_of_draftkings_403s_is_not_an_incident(self):
        # The 2026-10-28 egress block cleared on its own. DK's floor is two days
        # precisely so that a day of this stays quiet.
        state, _ = hl.classify(failing("scraper:draftkings", failures=8,
                                       since_hours=1), NOW)
        self.assertEqual(state, "degraded")

    def test_healthy_unit_is_ok(self):
        state, reason = hl.classify(
            record("scraper:nba_injury_report", last_success_ts=NOW - 0.5 * HOUR),
            NOW,
        )
        self.assertEqual(state, "ok")
        self.assertEqual(reason, "")

    def test_many_failures_inside_the_duration_floor_stay_degraded(self):
        # Count alone must not promote: the injury report is asked hourly, so four
        # failures can happen inside a window that a slow upstream recovers from.
        state, _ = hl.classify(failing("scraper:nba_injury_report", failures=4,
                                       since_hours=1), NOW)
        self.assertEqual(state, "degraded")

    def test_long_outage_with_too_few_attempts_stays_degraded(self):
        # Duration alone must not promote through the failure rule, or a unit asked
        # once a day would page off a single failed attempt. Chosen inside
        # DraftKings' 48h staleness window so this tests the failure rule and not
        # the staleness net -- which, deliberately, does fire past that window
        # however few attempts were made.
        state, _ = hl.classify(failing("scraper:draftkings", failures=1,
                                       since_hours=30), NOW)
        self.assertEqual(state, "degraded")

    def test_staleness_catches_what_the_failure_rule_misses(self):
        # The other half of the same pair: season stats unrefreshed for three days
        # is broken whether or not anything recorded an exception, because the
        # artifact the whole board reads is three days old either way.
        state, reason = hl.classify(failing("scraper:season_stats", failures=1,
                                            since_hours=72), NOW)
        self.assertEqual(state, "broken")
        self.assertIn("no successful run", reason)

    def test_empty_rows_are_ignored_where_empty_is_normal(self):
        # Game logs legitimately return nothing off-slate, so empty_rows_matter is
        # False and the streak is never even counted.
        rec = record("scraper:gamelogs", last_success_ts=NOW, empty_streak=0)
        for _ in range(5):
            rec["empty_streak"] = 0  # record_success would not increment it
        state, _ = hl.classify(rec, NOW)
        self.assertEqual(state, "ok")


class ClassifyStructural(unittest.TestCase):
    """Things that must page."""

    def test_the_injury_report_incident_is_caught(self):
        # Three days of hourly failures: the actual 2026-11-20 shape.
        state, reason = hl.classify(
            failing("scraper:nba_injury_report", failures=12, since_hours=72),
            NOW,
        )
        self.assertEqual(state, "broken")
        self.assertIn("consecutive failures", reason)

    def test_exhausted_budget_on_two_days_promotes_without_waiting(self):
        # The signal the incident was already producing: master_cron had stopped
        # attempting the job at all, so no amount of further waiting would help.
        rec = record("scraper:nba_injury_report",
                     last_success_ts=NOW - 30 * HOUR,
                     exhausted_dates=["2026-11-22", "2026-11-23"])
        state, reason = hl.classify(rec, NOW)
        self.assertEqual(state, "broken")
        self.assertIn("retry budget exhausted", reason)

    def test_one_exhausted_day_is_not_enough(self):
        rec = record("scraper:nba_injury_report",
                     last_success_ts=NOW - 2 * HOUR,
                     exhausted_dates=["2026-11-23"])
        state, _ = hl.classify(rec, NOW)
        self.assertEqual(state, "degraded")

    def test_stale_exhausted_days_are_forgotten(self):
        # Two exhausted days last month are history, not an open incident.
        rec = record("scraper:nba_injury_report",
                     last_success_ts=NOW - 0.5 * HOUR,
                     exhausted_dates=["2026-10-01", "2026-10-02"])
        state, _ = hl.classify(rec, NOW)
        self.assertEqual(state, "ok")

    def test_three_empty_successes_promote(self):
        # The failure that looks healthy from outside: preserve_on_empty keeps
        # yesterday's file, so the artifact exists and the exit code is zero.
        rec = record("scraper:season_stats", last_success_ts=NOW,
                     last_success_rows=0, empty_streak=3)
        state, reason = hl.classify(rec, NOW)
        self.assertEqual(state, "broken")
        self.assertIn("zero rows", reason)

    def test_starved_job_promotes_on_staleness_alone(self):
        # No failures recorded at all -- master_cron returns after the first job
        # that runs, so a job can simply stop being reached.
        rec = record("job:intraday", last_success_ts=NOW - 5 * HOUR)
        state, reason = hl.classify(rec, NOW)
        self.assertEqual(state, "broken")
        self.assertIn("no successful run", reason)

    def test_never_succeeded_and_failing_promotes(self):
        rec = record("sync:supabase_props", consecutive_failures=5,
                     first_failure_ts=NOW - 10 * HOUR, last_success_ts=0,
                     last_error="EnvironmentError: Missing Supabase credentials")
        state, reason = hl.classify(rec, NOW)
        self.assertEqual(state, "broken")
        self.assertIn("never succeeded", reason)

    def test_blocked_upstream_promotes_after_the_duration_floor(self):
        rec = failing("upstream:api.draftkings.com", failures=30, since_hours=3,
                      error="egress blocked: 6 breaker trips")
        state, _ = hl.classify(rec, NOW)
        self.assertEqual(state, "broken")

    def test_quiet_upstream_never_goes_stale(self):
        # A book we stopped asking about must not page forever.
        rec = record("upstream:api.retired-book.com",
                     last_success_ts=NOW - 90 * 24 * HOUR)
        state, _ = hl.classify(rec, NOW)
        self.assertEqual(state, "ok")


class Dormancy(unittest.TestCase):
    def test_dormant_suppresses_promotion(self):
        rec = failing("scraper:nba_injury_report", failures=12, since_hours=72)
        state, _ = hl.classify(rec, NOW, dormant=True)
        self.assertEqual(state, "degraded")

    def test_dormancy_exempt_units_still_promote(self):
        # The pipeline and the schedule are what every other dormancy signal would
        # be derived from, so they are never silenced by one.
        rec = failing("job:pipeline", failures=4, since_hours=40)
        state, _ = hl.classify(rec, NOW, dormant=True)
        self.assertEqual(state, "broken")

    def test_dormancy_is_calendar_not_schedule_derived(self):
        # A schedule-derived check would let a broken schedule scraper silence the
        # alert about itself.
        self.assertTrue(hl.is_dormant(datetime(2026, 8, 15, tzinfo=ET)))
        self.assertFalse(hl.is_dormant(datetime(2026, 11, 23, tzinfo=ET)))


class LedgerIO(unittest.TestCase):
    """Recording and evaluation against a temporary file-backed ledger."""

    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self._env = {}
        for key, value in (
            ("HEALTH_LEDGER_BACKEND", "file"),
            ("HEALTH_LEDGER_STATE_PATH", str(Path(self._dir.name) / "health.json")),
            ("HEALTH_REMINDER_HOURS", "24"),
        ):
            import os
            self._env[key] = os.environ.get(key)
            os.environ[key] = value
        hl._BACKEND = "file"
        self.addCleanup(self._restore)

    def _restore(self):
        import os
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_success_clears_a_failure_run(self):
        hl.record_failure("scraper:fanduel", RuntimeError("boom"), now=NOW)
        hl.record_failure("scraper:fanduel", RuntimeError("boom"), now=NOW + 60)
        rec = hl.load()["scraper:fanduel"]
        self.assertEqual(rec["consecutive_failures"], 2)

        rec = hl.record_success("scraper:fanduel", rows=120, now=NOW + 120)
        self.assertEqual(rec["consecutive_failures"], 0)
        self.assertEqual(rec["first_failure_ts"], 0.0)
        self.assertEqual(rec["last_success_rows"], 120)

    def test_rows_none_does_not_disturb_the_empty_streak(self):
        # run_pipeline's wrapper records the real count; the loop around it records
        # a generic success afterwards and must not erase what the wrapper said.
        hl.record_success("scraper:season_stats", rows=0, now=NOW)
        hl.record_success("scraper:season_stats", rows=None, now=NOW + 1)
        self.assertEqual(hl.load()["scraper:season_stats"]["empty_streak"], 1)

    def test_a_real_row_count_resets_the_empty_streak(self):
        hl.record_success("scraper:season_stats", rows=0, now=NOW)
        hl.record_success("scraper:season_stats", rows=540, now=NOW + 1)
        self.assertEqual(hl.load()["scraper:season_stats"]["empty_streak"], 0)

    def test_evaluate_opens_then_reminds_then_recovers(self):
        unit = "scraper:nba_injury_report"
        # Three days of hourly failures.
        for hours in range(0, 72, 6):
            hl.record_failure(unit, RuntimeError("404 Not Found"),
                              now=NOW - 72 * HOUR + hours * HOUR)

        actions, _ = hl.evaluate(now=NOW, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["open"])
        self.assertEqual(hl.load()[unit]["state"], "broken")

        # Nothing further until the push is acknowledged as sent.
        actions, _ = hl.evaluate(now=NOW + 60, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["open"])

        hl.mark_notified([unit], now=NOW)
        actions, _ = hl.evaluate(now=NOW + HOUR, dormant=False)
        self.assertEqual(actions, [], "must not re-page inside the reminder window")

        actions, _ = hl.evaluate(now=NOW + 25 * HOUR, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["reminder"])

        hl.record_success(unit, rows=430, now=NOW + 26 * HOUR)
        actions, _ = hl.evaluate(now=NOW + 26 * HOUR, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["recovered"])
        self.assertEqual(hl.load()[unit]["state"], "ok")

    def test_a_fix_clears_an_exhausted_budget(self):
        # Regression: the budget-exhausted rule reads a list of dates, and nothing
        # used to remove them on a success. A unit that had burned its retries on
        # two days therefore stayed `broken` for three days after the fix landed --
        # and a monitor that cannot tell you something is fixed is one you stop
        # believing. master_cron zeroes its own per-day counter on success, so the
        # ledger has to forget the spent days at the same moment.
        unit = "scraper:nba_injury_report"
        hl.record_budget_exhausted(unit, "2026-11-22", now=NOW)
        hl.record_budget_exhausted(unit, "2026-11-23", now=NOW)
        state, _ = hl.classify({**hl.load()[unit], "unit": unit}, NOW)
        self.assertEqual(state, "broken")

        hl.record_success(unit, rows=430, now=NOW + HOUR)
        record = hl.load()[unit]
        self.assertEqual(record["exhausted_dates"], [])
        state, _ = hl.classify({**record, "unit": unit}, NOW + HOUR)
        self.assertEqual(state, "ok")

    def test_an_announced_incident_recovers_after_an_exhausted_budget(self):
        # The same bug, end to end: open, fix, recovery push.
        unit = "scraper:nba_injury_report"
        for hours in range(0, 72, 6):
            hl.record_failure(unit, RuntimeError("404 Not Found"),
                              now=NOW - 72 * HOUR + hours * HOUR)
        hl.record_budget_exhausted(unit, "2026-11-22", now=NOW)
        hl.record_budget_exhausted(unit, "2026-11-23", now=NOW)

        actions, _ = hl.evaluate(now=NOW, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["open"])
        hl.mark_notified([unit], now=NOW)

        hl.record_success(unit, rows=430, now=NOW + HOUR)
        actions, _ = hl.evaluate(now=NOW + HOUR, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["recovered"])
        self.assertEqual(hl.load()[unit]["state"], "ok")

    def test_recovery_is_silent_when_the_break_was_never_announced(self):
        unit = "job:intraday"
        hl.record_success(unit, now=NOW - 10 * HOUR)
        actions, _ = hl.evaluate(now=NOW, dormant=False)
        self.assertEqual([a["kind"] for a in actions], ["open"])
        # Never marked as notified, so the heal must not be the first thing said.
        hl.record_success(unit, now=NOW + 60)
        actions, _ = hl.evaluate(now=NOW + 60, dormant=False)
        self.assertEqual(actions, [])

    def test_warn_severity_units_are_recorded_but_never_paged(self):
        unit = "scraper:prizepicks"
        for hours in range(0, 96, 24):
            hl.record_failure(unit, RuntimeError("cookie expired"),
                              now=NOW - 96 * HOUR + hours * HOUR)
        actions, _ = hl.evaluate(now=NOW, dormant=False)
        self.assertEqual(actions, [])
        self.assertEqual(hl.load()[unit]["state"], "broken",
                         "still visible in the health block and the watchdog")

    def test_snapshot_reports_broken_units(self):
        hl.record_success("scraper:fanduel", rows=100, now=NOW)
        for hours in range(0, 72, 6):
            hl.record_failure("scraper:nba_injury_report", RuntimeError("404"),
                              now=NOW - 72 * HOUR + hours * HOUR)
        hl.evaluate(now=NOW, dormant=False)

        snap = hl.snapshot(now=NOW)
        self.assertEqual(snap["broken_count"], 1)
        self.assertEqual(snap["pageable_broken_count"], 1)
        self.assertEqual(snap["broken"][0]["unit"], "scraper:nba_injury_report")
        self.assertIn("monitored_units", snap)

    def test_a_corrupt_ledger_reads_as_empty_rather_than_raising(self):
        import os
        path = os.environ["HEALTH_LEDGER_STATE_PATH"]
        with open(path, "w") as handle:
            handle.write("{not json")
        self.assertEqual(hl.load(), {})
        # And recording still works, because a damaged ledger must never be able to
        # stop a scrape.
        hl.record_success("scraper:fanduel", rows=10, now=NOW)
        self.assertIn("scraper:fanduel", hl.load())


class GamelessDaysDoNotPage(unittest.TestCase):
    """An empty scrape is the correct answer when there are no games.

    Every odds and zone scraper returns zero rows on a gameless day. The All-Star
    break is four or five in a row, which is past the empty-streak threshold -- so
    without a guard the monitor's first act of the season would be to page about
    February. The guard lives at the recording site (run_pipeline passes rows=None
    when the schedule is empty or unreadable) rather than in `classify`, because
    only the caller knows whether zero was expected.
    """

    def test_the_pipeline_reports_no_row_count_without_games(self):
        import json
        import tempfile
        import run_pipeline

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "today_schedule.json"
            real = run_pipeline.SCHEDULE_PATH
            run_pipeline.SCHEDULE_PATH = str(path)
            try:
                path.write_text(json.dumps({"games": []}))
                self.assertEqual(run_pipeline._games_scheduled_today(), 0)
                self.assertFalse(run_pipeline._games_scheduled_today())

                path.write_text(json.dumps({"games": [{"game_id": "1"},
                                                      {"game_id": "2"}]}))
                self.assertEqual(run_pipeline._games_scheduled_today(), 2)

                # Unreadable suppresses too: scraper:schedule is monitored on its
                # own and dormancy-exempt, so that failure needs no inference from
                # everything downstream of it.
                path.unlink()
                self.assertIsNone(run_pipeline._games_scheduled_today())
                path.write_text("{not json")
                self.assertIsNone(run_pipeline._games_scheduled_today())
            finally:
                run_pipeline.SCHEDULE_PATH = real


class TestsCannotFabricateIncidents(unittest.TestCase):
    """Regression: the test suite used to write real health records.

    The recording calls sit inside the except blocks of fourteen scrapers and six
    cron jobs, so the existing suite drives them as a side effect.
    `tests/test_injury_report_retry_budget.py` alone wrote a record claiming the
    injury report had failed eight times and spent its retry budget -- entirely
    fabricated, in the real ledger, which `--report` displayed and an armed
    notifier would have pushed to a phone. With production credentials in
    backend/.env it would have reached the shared table too.

    This project already has that bug in another form (pytest overwrites the
    data/current odds CSVs), so the guard cannot rely on every future test author
    remembering an env var.
    """

    def test_the_ledger_is_inert_under_pytest_by_default(self):
        import os
        previous = os.environ.pop("HEALTH_LEDGER_STATE_PATH", None)
        try:
            self.assertTrue(os.environ.get("PYTEST_CURRENT_TEST"),
                            "this test only means anything under pytest")
            self.assertTrue(hl._inert())
            self.assertIsNone(hl._state_path())
            self.assertFalse(hl._db_enabled())

            # And the write path is a no-op rather than an error.
            hl.save({"scraper:fanduel": hl._blank("scraper:fanduel")})
            self.assertEqual(hl.load(), {})
        finally:
            if previous is not None:
                os.environ["HEALTH_LEDGER_STATE_PATH"] = previous

    def test_a_test_that_opts_in_still_gets_a_working_ledger(self):
        # The opt-in is an explicit state path, which every test in this file sets.
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("HEALTH_LEDGER_STATE_PATH")
            os.environ["HEALTH_LEDGER_STATE_PATH"] = str(Path(directory) / "h.json")
            try:
                self.assertFalse(hl._inert())
                hl.record_success("scraper:fanduel", rows=5, now=NOW)
                self.assertIn("scraper:fanduel", hl.load())
            finally:
                if previous is None:
                    os.environ.pop("HEALTH_LEDGER_STATE_PATH", None)
                else:
                    os.environ["HEALTH_LEDGER_STATE_PATH"] = previous


if __name__ == "__main__":
    unittest.main()
