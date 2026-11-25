"""Every caller of the Signal Score refresh must record it.

The bug this guards. `score:edge_refresh` declares `expected_interval_s=1800`
and `stale_after_s=3h`, but the only `record_success` for it was in
`run_pipeline.py`, which runs once a day at 06:00 ET. The unit therefore went
stale at 09:00 and reported "broken" for ~15 hours every single day, while the
refresh was actually running every half hour from `cron_line_movement`.

Measured on 2026-11-24 against the live watchdog: 10.4h at 16:31 and 14.3h at
20:26 -- both exactly 06:00 plus the reported gap, which is what identified the
cause.

Why it is worth a test rather than a fix alone: a daily false alarm is worse
than no alarm. The stated reason these alerts go to ntfy instead of Discord is
that the channel has to stay credible enough to read, and nothing erodes that
faster than an alert that is wrong every day. `sync:supabase_edge` was always
recorded correctly from inside `edge_score.py`, which is why this was the only
one of 24 units stuck broken -- so the failure was invisible as a pattern.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import health_ledger as hl  # noqa: E402

UNIT = "score:edge_refresh"


class EveryRefreshCallerRecordsTheUnit(unittest.TestCase):
    """Drives each wrapper with the scorer stubbed out and asserts the record."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # An explicit state path is the opt-in that makes the ledger live under
        # pytest; without it `_inert()` makes every write a no-op.
        env = patch.dict(os.environ, {
            "HEALTH_LEDGER_STATE_PATH": os.path.join(self._tmp.name, "h.json"),
        })
        env.start()
        self.addCleanup(env.stop)

    def _assert_recorded(self, module_name):
        module = __import__(f"cron_jobs.{module_name}", fromlist=["_run_edge_score_refresh"])
        with patch("utils.edge_score.run_edge_score_refresh", return_value={}):
            ok = module._run_edge_score_refresh({}, "intraday")
        self.assertTrue(ok)
        records = hl.load()
        self.assertIn(
            UNIT, records,
            f"cron_jobs/{module_name}.py ran the Signal Score refresh without "
            f"recording {UNIT}. The unit expects a record every 30 minutes, so a "
            f"caller that stays silent makes it look broken for the rest of the day.",
        )
        self.assertEqual(records[UNIT].get("consecutive_failures", 0), 0)

    def test_the_intraday_cron_records_success(self):
        self._assert_recorded("cron_line_movement")

    def test_the_closing_line_cron_records_success(self):
        self._assert_recorded("cron_closing_lines")

    def _assert_failure_recorded(self, module_name):
        module = __import__(f"cron_jobs.{module_name}", fromlist=["_run_edge_score_refresh"])
        with patch("utils.edge_score.run_edge_score_refresh",
                   side_effect=RuntimeError("scoring blew up")):
            ok = module._run_edge_score_refresh({}, "intraday")
        self.assertFalse(ok)
        records = hl.load()
        self.assertIn(UNIT, records)
        self.assertGreaterEqual(records[UNIT].get("consecutive_failures", 0), 1)

    def test_the_intraday_cron_records_failure(self):
        """A swallowed exception that records nothing is the original sin here:
        the refresh is non-fatal by design, so the ledger is the only thing that
        can outlive the process and say it failed."""
        self._assert_failure_recorded("cron_line_movement")

    def test_the_closing_line_cron_records_failure(self):
        self._assert_failure_recorded("cron_closing_lines")


class TheUnitsCadenceMatchesItsRecorders(unittest.TestCase):
    """A unit claiming a sub-hourly cadence needs a sub-hourly recorder.

    This is the shape of the bug rather than the instance: `run_pipeline.py` is
    a daily job, so if it were the ONLY module recording a 30-minute unit, the
    unit is guaranteed to spend most of every day looking broken. Checked by
    grep over the source, because the alternative is re-deriving each job's
    schedule at runtime.
    """

    def test_a_sub_hourly_unit_is_not_recorded_only_by_the_daily_pipeline(self):
        import re

        daily_only = []
        for unit, spec in hl.UNITS.items():
            interval = spec.get("expected_interval_s")
            if not interval or interval > 3600:
                continue

            recorders = set()
            for path in BACKEND_DIR.rglob("*.py"):
                if "tests" in path.parts or path.name == "health_ledger.py":
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if re.search(rf"record_(success|failure)\(\s*[\"']{re.escape(unit)}[\"']", text):
                    recorders.add(path.name)

            if recorders and recorders <= {"run_pipeline.py"}:
                daily_only.append((unit, interval, sorted(recorders)))

        self.assertEqual(
            daily_only, [],
            "these units expect a record at least hourly but are only recorded by "
            "the once-a-day pipeline, so they will report 'broken' for most of "
            f"every day: {daily_only}",
        )


if __name__ == "__main__":
    unittest.main()
