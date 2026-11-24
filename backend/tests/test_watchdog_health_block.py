"""The watchdog's one generic check: does it escalate the health ledger correctly?

Every other check in watchdog_check.py was written after an incident, for that
incident, which is a pattern that only ever catches the failure you already had.
This check asks the pipeline what it knows about itself, so a new kind of breakage
reaches the phone without a new check being written first.

The things worth pinning are the escalation boundaries, because each one is a way
the alarm could end up muted or crying wolf: severity "warn" units must not fail the
job, a missing block must not fail the job (a pipeline deploy older than the field is
a legitimate reason for it to be absent), and the offseason must not page.

NOTE: `problems`, `warnings` and `notes` are module-level lists in watchdog_check,
so they leak between tests. They are cleared in setUp and again on cleanup.
"""

import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts import watchdog_check as watchdog


def health(*, broken=(), degraded=(), dormant=False, units=22):
    return {
        "evaluated_at": "2026-11-23T14:00:00-05:00",
        "monitored_units": units,
        "broken_count": len(broken),
        "degraded_count": len(degraded),
        "pageable_broken_count": sum(1 for e in broken if e.get("severity") == "page"),
        "broken": list(broken),
        "degraded": list(degraded),
        "dormant": dormant,
    }


def unit(label, *, severity="page", reason="3 consecutive failures over 72.0h",
         age=72.0, failures=12):
    return {
        "unit": f"scraper:{label.lower().replace(' ', '_')}",
        "label": label,
        "severity": severity,
        "reason": reason,
        "consecutive_failures": failures,
        "last_success_age_h": age,
    }


class HealthBlockCheckTests(unittest.TestCase):
    def setUp(self):
        self._clear()
        self.addCleanup(self._clear)

    def _clear(self):
        del watchdog.problems[:]
        del watchdog.warnings[:]
        del watchdog.notes[:]

    def test_a_broken_pageable_unit_fails_the_job(self):
        watchdog.check_health_block(
            {"health": health(broken=[unit("NBA injury report")])},
            season_active=True,
        )
        self.assertEqual(len(watchdog.problems), 1)
        self.assertIn("NBA injury report is broken", watchdog.problems[0])
        self.assertIn("last success 72.0h ago", watchdog.problems[0])

    def test_warn_severity_is_reported_without_failing(self):
        # PrizePicks needs live cookie values that expire by design. Paging on it
        # would be paging on a known manual chore, and a channel that cries wolf
        # gets muted -- which is the original failure, with extra steps.
        watchdog.check_health_block(
            {"health": health(broken=[unit("PrizePicks odds", severity="warn")])},
            season_active=True,
        )
        self.assertEqual(watchdog.problems, [])
        self.assertEqual(len(watchdog.warnings), 1)

    def test_degraded_units_are_information_only(self):
        watchdog.check_health_block(
            {"health": health(degraded=[unit("DraftKings odds", reason="2 failures")])},
            season_active=True,
        )
        self.assertEqual(watchdog.problems, [])
        self.assertEqual(watchdog.warnings, [])
        self.assertTrue(any("degraded: DraftKings odds" in note
                            for note in watchdog.notes))

    def test_a_missing_health_block_warns_rather_than_failing(self):
        # Deploy order is not guaranteed: the watchdog ships from GitHub, the
        # pipeline from the VM's git pull. Failing here would page on a schema gap.
        watchdog.check_health_block({"active_players": 300}, season_active=True)
        self.assertEqual(watchdog.problems, [])
        self.assertEqual(len(watchdog.warnings), 1)
        self.assertIn("no health block", watchdog.warnings[0])

    def test_offseason_does_not_page(self):
        watchdog.check_health_block(
            {"health": health(broken=[unit("NBA injury report")], dormant=True)},
            season_active=True,
        )
        self.assertEqual(watchdog.problems, [])
        self.assertTrue(any("not escalated" in note for note in watchdog.notes))

    def test_no_games_scheduled_does_not_page(self):
        watchdog.check_health_block(
            {"health": health(broken=[unit("NBA injury report")])},
            season_active=False,
        )
        self.assertEqual(watchdog.problems, [])

    def test_a_healthy_ledger_is_silent_but_noted(self):
        # Printed even when healthy, so the next incident is diagnosable from one
        # run summary rather than from three days of nothing.
        watchdog.check_health_block({"health": health()}, season_active=True)
        self.assertEqual(watchdog.problems, [])
        self.assertEqual(watchdog.warnings, [])
        self.assertTrue(any("22 units monitored" in note for note in watchdog.notes))

    def test_several_broken_units_are_all_reported(self):
        watchdog.check_health_block(
            {"health": health(broken=[unit("NBA injury report"),
                                      unit("Season stats"),
                                      unit("Box scores", severity="warn")])},
            season_active=True,
        )
        self.assertEqual(len(watchdog.problems), 2)
        self.assertEqual(len(watchdog.warnings), 1)

    def test_a_malformed_block_does_not_raise(self):
        # The block comes off the network. Anything in it can be the wrong shape.
        watchdog.check_health_block({"health": {"broken": "not a list"}},
                                    season_active=True)
        watchdog.check_health_block({"health": {"broken": [None, 7, "x"]}},
                                    season_active=True)
        self.assertEqual(watchdog.problems, [])


if __name__ == "__main__":
    unittest.main()
