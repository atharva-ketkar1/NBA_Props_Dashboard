"""A dead injury-report upstream must not be retried every five minutes.

When NBA moved the injury report page on 2026-11-20, Priority 4 kept firing on
every cron tick and failing every time: 169 [FAIL] lines in the log by 14:10 ET
on 2026-11-23, tracking to ~288 a day, each one a doomed worker call charged
against the 60/60s budget DraftKings and FanDuel share. The cause was that the
job returned on failure *before* advancing
`last_nba_injury_report_refresh_time`, so the interval gate never closed.

The first two tests fail against the pre-fix module -- the scraper is called on
every tick there. `test_a_new_day_re_arms_the_job` passes either way and is a
pin: it guards the new cap from darkening the feature permanently, which is the
plausible way this fix goes wrong.

IMPORTANT, for anyone adding to this file: importing cron_jobs.master_cron runs
configure_logging() and load_dotenv on backend/.env, pulling live Supabase and
Discord credentials into the test process. So `save_state` is patched in every
test and `load_state()` is never called -- it would read, and later write, real
scheduler state in Supabase. The state dict is passed in by hand instead, which
the function signature already allows.
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from cron_jobs import master_cron as mc

ET = ZoneInfo("America/New_York")


def _fresh_state():
    # Only the keys the job reads. Deliberately not load_state() -- see the docstring.
    return {
        "last_nba_injury_report_refresh_time": 0,
        "nba_injury_report_failure_date": "",
        "nba_injury_report_failures_today": 0,
        "last_nba_injury_report_attempt_ts": 0,
    }


class InjuryReportRetryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.state = _fresh_state()
        saver = patch.object(mc, "save_state")
        self.save_state = saver.start()
        self.addCleanup(saver.stop)

    def _run(self, now, refresh):
        with patch(
            "scrapers.fetch_nba_injury_report.refresh_nba_injury_report_if_needed",
            side_effect=refresh,
        ) as fake:
            mc.run_nba_injury_report_if_needed(now, self.state, dry_run=False)
        return fake

    def _run_many(self, start, refresh, ticks, step):
        """Drive the job across `ticks` cron ticks and count real scraper calls."""
        calls = 0
        with patch(
            "scrapers.fetch_nba_injury_report.refresh_nba_injury_report_if_needed",
            side_effect=refresh,
        ) as fake:
            for i in range(ticks):
                mc.run_nba_injury_report_if_needed(start + step * i, self.state, dry_run=False)
            calls = fake.call_count
        return calls

    def test_a_failed_refresh_does_not_retry_on_the_next_tick(self):
        boom = RuntimeError("worker refused request: Upstream redirects are blocked.")
        start = datetime(2026, 11, 23, 9, 0, tzinfo=ET)

        calls = self._run_many(start, boom, ticks=2, step=timedelta(minutes=5))

        # This single assertion is the 288-calls-a-day bug.
        self.assertEqual(calls, 1)

    def test_the_day_is_abandoned_after_the_failure_cap(self):
        boom = RuntimeError("worker refused request: Upstream redirects are blocked.")
        start = datetime(2026, 11, 23, 0, 0, tzinfo=ET)
        step = timedelta(seconds=mc.NBA_INJURY_REPORT_RETRY_COOLDOWN_SECONDS + 1)
        # Enough ticks to cover a whole day at the cooldown's own spacing.
        ticks = int(timedelta(days=1).total_seconds() // step.total_seconds())

        calls = self._run_many(start, boom, ticks=ticks, step=step)

        self.assertEqual(calls, mc.MAX_NBA_INJURY_REPORT_FAILURES_PER_DAY)
        self.assertEqual(
            self.state["nba_injury_report_failures_today"],
            mc.MAX_NBA_INJURY_REPORT_FAILURES_PER_DAY,
        )

    def test_the_cap_decision_is_logged(self):
        boom = RuntimeError("upstream is gone")
        start = datetime(2026, 11, 23, 0, 0, tzinfo=ET)
        step = timedelta(seconds=mc.NBA_INJURY_REPORT_RETRY_COOLDOWN_SECONDS + 1)

        for i in range(mc.MAX_NBA_INJURY_REPORT_FAILURES_PER_DAY):
            self._run(start + step * i, boom)

        with self.assertLogs(level="WARNING") as captured:
            self._run(start + step * (mc.MAX_NBA_INJURY_REPORT_FAILURES_PER_DAY + 1), boom)

        self.assertTrue(
            any("after repeated failures" in line for line in captured.output),
            captured.output,
        )

    def test_a_new_day_re_arms_the_job(self):
        boom = RuntimeError("upstream is gone")
        start = datetime(2026, 11, 23, 0, 0, tzinfo=ET)
        step = timedelta(seconds=mc.NBA_INJURY_REPORT_RETRY_COOLDOWN_SECONDS + 1)

        for i in range(mc.MAX_NBA_INJURY_REPORT_FAILURES_PER_DAY + 2):
            self._run(start + step * i, boom)

        # A cap that outlives its day would darken the feature permanently.
        fake = self._run(datetime(2026, 11, 24, 9, 0, tzinfo=ET), boom)
        self.assertEqual(fake.call_count, 1)

    def test_a_successful_refresh_clears_the_failure_budget(self):
        boom = RuntimeError("upstream is gone")
        start = datetime(2026, 11, 23, 9, 0, tzinfo=ET)
        self._run(start, boom)
        self.assertEqual(self.state["nba_injury_report_failures_today"], 1)

        ok = {
            "payload": {"game_count": 13, "player_row_count": 94},
            "refreshed": True,
        }
        with patch("utils.upsert_nba_injury_report.upsert_nba_injury_report_from_file"):
            self._run(start + timedelta(hours=2), lambda **kwargs: ok)

        self.assertEqual(self.state["nba_injury_report_failure_date"], "")
        self.assertEqual(self.state["nba_injury_report_failures_today"], 0)
        self.assertEqual(self.state["last_nba_injury_report_attempt_ts"], 0)
        # The timer half already worked before this change; the three keys above are new.
        self.assertGreater(self.state["last_nba_injury_report_refresh_time"], 0)


if __name__ == "__main__":
    unittest.main()
