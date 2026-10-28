import importlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

NOW = datetime(2026, 10, 28, 22, 0, tzinfo=timezone.utc)  # 18:00 ET


def schedule(*offsets_minutes, live=False, final=False):
    """A schedule artifact with games at the given offsets from NOW."""
    games = []
    for i, off in enumerate(offsets_minutes):
        tip = NOW + timedelta(minutes=off)
        games.append({
            "game_id": f"00226001{i:02d}",
            "game_time_utc": tip.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "is_live": live,
            "is_final": final,
        })
    return {"games": games}


class CadenceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self._tmp.name, "cadence.json")
        self.sched_path = os.path.join(self._tmp.name, "schedule.json")
        self._env = patch.dict(os.environ, {
            "DK_CADENCE_ENABLED": "true",
            "DK_CADENCE_STATE_PATH": self.state_path,
            "DK_CADENCE_SCHEDULE_PATH": self.sched_path,
            "DK_DAILY_REQUEST_BUDGET": "260",
            "DK_PREGAME_HOURS": "8",
            "DK_HOT_WINDOW_MINUTES": "150",
            "DK_JITTER_MAX_SECONDS": "20",
        })
        self._env.start()
        import utils.scrape_cadence as sc
        self.sc = importlib.reload(sc)
        self.addCleanup(self._teardown)

    def _teardown(self):
        self._env.stop()
        self._tmp.cleanup()
        import utils.scrape_cadence as sc
        importlib.reload(sc)

    def write_schedule(self, payload):
        with open(self.sched_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)


class TestSlateWindow(CadenceTestCase):
    def test_overnight_with_no_nearby_game_is_refused(self):
        # The measured waste: 35 of 64 runs on 2026-10-27 fired with nothing
        # live or imminent, ~385 requests for data nobody could use.
        self.write_schedule(schedule(20 * 60))  # next tip 20h away
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertFalse(d.allowed)
        self.assertIn("pregame window", d.reason)

    def test_near_tip_is_hot(self):
        self.write_schedule(schedule(70))
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertTrue(d.allowed)
        self.assertEqual(d.window, "hot")

    def test_hours_before_tip_is_cold_but_allowed(self):
        self.write_schedule(schedule(5 * 60))
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertTrue(d.allowed)
        self.assertEqual(d.window, "cold")

    def test_live_game_is_hot(self):
        self.write_schedule(schedule(-30, live=True))
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertTrue(d.allowed)
        self.assertEqual(d.window, "hot")

    def test_hot_interval_is_shorter_than_cold(self):
        self.write_schedule(schedule(70))
        self.sc.should_scrape_dk(now=NOW)
        hot_gap = self.sc.status()["next_gap_minutes"]
        self.sc.reset()
        self.write_schedule(schedule(5 * 60))
        self.sc.should_scrape_dk(now=NOW)
        cold_gap = self.sc.status()["next_gap_minutes"]
        self.assertLess(hot_gap, cold_gap)

    def test_missing_schedule_degrades_to_slow_not_off(self):
        # Refusing on a missing artifact would silently switch DraftKings off
        # for the day, which is worse than asking slowly.
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertTrue(d.allowed)
        self.assertEqual(d.window, "cold")

    def test_corrupt_schedule_degrades_to_slow(self):
        with open(self.sched_path, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertTrue(d.allowed)

    def test_slate_over_eventually_refuses(self):
        self.write_schedule(schedule(-9 * 60, final=True))
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertFalse(d.allowed)

    def test_stale_schedule_degrades_to_slow_not_off(self):
        """A week-old artifact must not silently switch the book off for a day.
        On a real day tomorrow's games are in the file, so the only way to have
        nothing upcoming is a broken artifact -- treat it like a missing one."""
        self.write_schedule(schedule(-30 * 60, final=False))
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertTrue(d.allowed)
        self.assertEqual(d.window, "cold")
        self.assertIn("stale", d.reason)


class TestInterval(CadenceTestCase):
    def test_second_call_too_soon_is_refused(self):
        self.write_schedule(schedule(70))
        self.assertTrue(self.sc.should_scrape_dk(now=NOW).allowed)
        d = self.sc.should_scrape_dk(now=NOW)
        self.assertFalse(d.allowed)
        self.assertIn("since the last scrape", d.reason)

    def test_interval_is_not_a_fixed_period(self):
        # A fixed 20-minute period landing on the same second is the easiest
        # thing for a rate heuristic to lock onto; the gap is redrawn each time.
        self.write_schedule(schedule(70))
        gaps = set()
        for _ in range(12):
            self.sc.reset()
            self.sc.should_scrape_dk(now=NOW)
            gaps.add(self.sc.status()["next_gap_minutes"])
        self.assertGreater(len(gaps), 6, f"gaps barely varied: {gaps}")

    def test_jitter_is_bounded_by_the_executor_budget(self):
        # cron_closing_lines runs this inside a 120s-timeout executor and a
        # healthy run already spends ~65-75s, so jitter must stay small.
        self.write_schedule(schedule(70))
        for _ in range(20):
            self.sc.reset()
            d = self.sc.should_scrape_dk(now=NOW)
            self.assertGreaterEqual(d.jitter_seconds, 0)
            self.assertLessEqual(d.jitter_seconds, 20)


class TestForce(CadenceTestCase):
    def test_force_ignores_the_slate_window(self):
        self.write_schedule(schedule(20 * 60))
        self.assertFalse(self.sc.should_scrape_dk(now=NOW).allowed)
        d = self.sc.should_scrape_dk(now=NOW, force=True)
        self.assertTrue(d.allowed, "the pre-tip capture must never be deferred")

    def test_force_ignores_the_interval(self):
        self.write_schedule(schedule(70))
        self.sc.should_scrape_dk(now=NOW)
        self.assertFalse(self.sc.should_scrape_dk(now=NOW).allowed)
        self.assertTrue(self.sc.should_scrape_dk(now=NOW, force=True).allowed)

    def test_force_never_sleeps(self):
        # A closing line that arrives after tip-off is not a closing line.
        self.write_schedule(schedule(5))
        d = self.sc.should_scrape_dk(now=NOW, force=True)
        self.assertEqual(d.jitter_seconds, 0)

    def test_force_still_respects_the_budget(self):
        self.write_schedule(schedule(70))
        with patch.dict(os.environ, {"DK_DAILY_REQUEST_BUDGET": "5"}):
            d = self.sc.should_scrape_dk(now=NOW, force=True, expected_requests=11)
            self.assertFalse(d.allowed, "budget is the one guard force cannot pass")


class TestBudget(CadenceTestCase):
    def _clear_interval(self):
        """Let the next call through the interval gate so the budget is what
        gets tested, not the spacing."""
        state = self.sc._load_state()
        state["last_scrape_at"] = 0
        self.sc._save_state(state)

    def test_budget_exhaustion_refuses(self):
        self.write_schedule(schedule(70))
        with patch.dict(os.environ, {"DK_DAILY_REQUEST_BUDGET": "22"}):
            self.assertTrue(self.sc.should_scrape_dk(now=NOW).allowed)
            self._clear_interval()
            # That reserved 11; this reserves the last 11, reaching 22.
            self.assertTrue(self.sc.should_scrape_dk(now=NOW).allowed)
            self._clear_interval()
            d = self.sc.should_scrape_dk(now=NOW)
            self.assertFalse(d.allowed)
            self.assertIn("budget", d.reason)

    def test_budget_resets_on_a_new_et_day(self):
        self.write_schedule(schedule(70))
        with patch.dict(os.environ, {"DK_DAILY_REQUEST_BUDGET": "11"}):
            self.assertTrue(self.sc.should_scrape_dk(now=NOW).allowed)
            tomorrow = NOW + timedelta(days=1)
            self.write_schedule({"games": [{
                "game_time_utc": (tomorrow + timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "is_live": False, "is_final": False}]})
            self.assertTrue(self.sc.should_scrape_dk(now=tomorrow).allowed)

    def test_refund_returns_unspent_budget(self):
        self.write_schedule(schedule(70))
        self.sc.should_scrape_dk(now=NOW)
        spent = self.sc.status()["requests_spent"]
        self.sc.record_requests(-8)
        self.assertEqual(self.sc.status()["requests_spent"], spent - 8)

    def test_budget_is_a_ceiling_above_the_expected_day(self):
        """~730/day drew the block and a simulated slate now spends ~363, so
        the cap belongs between those: high enough that it never silences the
        live-game hours, low enough to catch a runaway."""
        # The shipped default, not this test's env override.
        budget = int(self.sc.DEFAULTS["DK_DAILY_REQUEST_BUDGET"])
        self.assertGreater(budget, 363, "a cap that binds mid-slate is worse than none")
        self.assertLess(budget, 730, "must still be below the volume that drew the block")


class TestDisabled(CadenceTestCase):
    def test_disabling_allows_everything(self):
        with patch.dict(os.environ, {"DK_CADENCE_ENABLED": "false"}):
            import utils.scrape_cadence as sc
            off = importlib.reload(sc)
            self.write_schedule(schedule(20 * 60))
            d = off.should_scrape_dk(now=NOW)
            self.assertTrue(d.allowed)
            self.assertEqual(d.jitter_seconds, 0)


class TestScraperWiring(CadenceTestCase):
    def test_closing_path_forces_and_intraday_does_not(self):
        """The two callers must differ, or the closing capture gets throttled."""
        src = (BACKEND_DIR / "cron_jobs" / "cron_closing_lines.py").read_text()
        self.assertIn("force=is_closing", src)
        pipeline = (BACKEND_DIR / "run_pipeline.py").read_text()
        self.assertIn("fetch_dk_odds(force=True)", pipeline)

    def test_scraper_skips_without_requesting_when_gate_refuses(self):
        self.write_schedule(schedule(20 * 60))  # outside the window
        import scrapers.fetch_odds_draftkings as dk
        dk = importlib.reload(dk)
        calls = []

        def spy(*a, **k):
            calls.append(k.get("label"))
            return {}

        with patch.object(dk, "proxied_get_json", side_effect=spy), \
                patch.object(dk, "book_is_paused", return_value=False), \
                patch.object(dk.time, "sleep", return_value=None):
            rows = dk.fetch_dk_odds()
        self.assertEqual(rows, [])
        self.assertEqual(calls, [], "refused run must spend zero requests")

    def test_scraper_forced_run_requests_every_category(self):
        self.write_schedule(schedule(20 * 60))
        import scrapers.fetch_odds_draftkings as dk
        dk = importlib.reload(dk)
        calls = []

        def spy(*a, **k):
            calls.append(k.get("label"))
            return {}

        with patch.object(dk, "proxied_get_json", side_effect=spy), \
                patch.object(dk, "book_is_paused", return_value=False), \
                patch.object(dk.time, "sleep", return_value=None):
            dk.fetch_dk_odds(force=True)
        self.assertEqual(len(calls), len(dk.PLAYER_PROP_CATEGORIES))


if __name__ == "__main__":
    unittest.main()
