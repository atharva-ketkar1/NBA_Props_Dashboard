"""The watchdog has to notice a frozen injury report.

On 2026-11-20 NBA moved its injury report page. The pipeline kept running, the
dashboard kept serving, and this script kept printing four healthy-looking info
lines for three days while the board blocked 533 of 869 candidates on
`blocked_stale_injury_context`. The numbers needed to catch it were already in
the /api/edge payload the heartbeat check fetches; nothing read them.

The threshold is the interesting part, so two tests pin it from both sides: a
three-day-old report must page, and a single missed hourly refresh must not. The
second is why this does not simply trigger on the payload's own `is_stale`, which
flips true at 15 minutes inside the lock-sensitive window -- an alarm that fires
on one slow cron tick gets muted, and then you are back to three days of silence.

NOTE: `problems`, `warnings` and `notes` are module-level lists in
watchdog_check, so they leak between tests. They are cleared in setUp and again
on cleanup; anything added here must keep doing that.
"""

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts import watchdog_check as watchdog


def _today():
    return datetime.now(timezone.utc).date()


class InjuryFreshnessCheckTests(unittest.TestCase):
    def setUp(self):
        self._clear()
        self.addCleanup(self._clear)

    def _clear(self):
        del watchdog.problems[:]
        del watchdog.warnings[:]
        del watchdog.notes[:]

    def test_a_three_day_old_injury_report_is_a_problem(self):
        # The payload as it actually stood on 2026-11-23, three days into the outage.
        summary = {
            "injury_report_freshness": {
                "generated_at": "2026-11-20T11:11:32.527323-05:00",
                "report_timestamp_et": "2026-11-20T11:00:00-05:00",
                "query_date": "2026-11-20",
                "age_minutes": 4480.13,
                "max_age_minutes": 15.0,
                "is_stale": True,
            },
            "blocked_stale_injury_context_count": 533,
        }

        watchdog.check_injury_freshness(summary, season_active=True)

        joined = " | ".join(watchdog.problems)
        self.assertTrue(watchdog.problems, "a three-day-old report must page")
        self.assertIn("4480", joined)
        self.assertIn("533", joined)
        self.assertEqual(watchdog.warnings, [])

    def test_one_missed_refresh_is_not_a_problem(self):
        # 70 minutes is one missed hourly refresh. is_stale is true here -- the
        # pipeline's target is 15 minutes -- and that must not be enough to page.
        summary = {
            "injury_report_freshness": {
                "query_date": _today().isoformat(),
                "age_minutes": 70.0,
                "max_age_minutes": 15.0,
                "is_stale": True,
            },
            "blocked_stale_injury_context_count": 4,
        }

        watchdog.check_injury_freshness(summary, season_active=True)

        self.assertEqual(watchdog.problems, [])
        self.assertEqual(watchdog.warnings, [])
        self.assertEqual(len(watchdog.notes), 1)
        self.assertIn("70m old", watchdog.notes[0])

    def test_two_missed_refreshes_are_still_not_a_problem(self):
        summary = {
            "injury_report_freshness": {
                "query_date": _today().isoformat(),
                "age_minutes": 135.0,
                "max_age_minutes": 60.0,
                "is_stale": True,
            },
            "blocked_stale_injury_context_count": 0,
        }

        watchdog.check_injury_freshness(summary, season_active=True)

        self.assertEqual(watchdog.problems, [])

    def test_the_offseason_does_not_page(self):
        summary = {
            "injury_report_freshness": {
                "query_date": (_today() - timedelta(days=3)).isoformat(),
                "age_minutes": 4480.0,
                "max_age_minutes": 60.0,
                "is_stale": True,
            },
            "blocked_stale_injury_context_count": 0,
        }

        watchdog.check_injury_freshness(summary, season_active=False)

        self.assertEqual(watchdog.problems, [])
        self.assertTrue(any("no games scheduled" in note for note in watchdog.notes))

    def test_a_stale_query_date_pages_even_if_the_stamp_looks_fresh(self):
        # A refresh that keeps succeeding against a stale source: the age stamp is
        # new, the report is for last week. The age check structurally cannot see it.
        stale_date = (_today() - timedelta(days=5)).isoformat()
        summary = {
            "injury_report_freshness": {
                "query_date": stale_date,
                "age_minutes": 3.0,
                "max_age_minutes": 15.0,
                "is_stale": False,
            },
            "blocked_stale_injury_context_count": 0,
        }

        watchdog.check_injury_freshness(summary, season_active=True)

        self.assertEqual(len(watchdog.problems), 1, watchdog.problems)
        self.assertIn(stale_date, watchdog.problems[0])
        self.assertIn("5 days behind", watchdog.problems[0])

    def test_yesterdays_report_does_not_page_on_the_date_alone(self):
        # The ET/UTC offset routinely makes a current report read as yesterday.
        summary = {
            "injury_report_freshness": {
                "query_date": (_today() - timedelta(days=1)).isoformat(),
                "age_minutes": 10.0,
                "max_age_minutes": 15.0,
                "is_stale": False,
            },
            "blocked_stale_injury_context_count": 0,
        }

        watchdog.check_injury_freshness(summary, season_active=True)

        self.assertEqual(watchdog.problems, [])

    def test_a_payload_without_the_key_warns_rather_than_pages(self):
        # A pipeline deploy older than the field is a legitimate cause, and paging on
        # a schema gap is how an alert channel gets muted. Pinned so nobody
        # "upgrades" the severity later without reading this.
        watchdog.check_injury_freshness({}, season_active=True)

        self.assertEqual(watchdog.problems, [])
        self.assertEqual(len(watchdog.warnings), 1)

    def test_an_unparseable_age_warns_rather_than_pages(self):
        summary = {"injury_report_freshness": {"age_minutes": None, "query_date": None}}

        watchdog.check_injury_freshness(summary, season_active=True)

        self.assertEqual(watchdog.problems, [])
        self.assertEqual(len(watchdog.warnings), 1)

    def test_the_live_payload_shape_is_the_one_we_parse(self):
        """Catches a producer-side rename in edge_score's summary block.

        backend/data is gitignored, so a fresh checkout has no artifact and this
        skips rather than failing for the wrong reason.
        """
        artifact = BACKEND_DIR / "data" / "current" / "edge_scores_top15.json"
        if not artifact.exists():
            self.skipTest("no local edge score artifact (backend/data is gitignored)")

        summary = json.loads(artifact.read_text()).get("summary") or {}
        freshness = summary.get("injury_report_freshness")
        self.assertIsInstance(freshness, dict)
        for key in ("age_minutes", "max_age_minutes", "query_date"):
            self.assertIn(key, freshness)
        self.assertIn("blocked_stale_injury_context_count", summary)


if __name__ == "__main__":
    unittest.main()
