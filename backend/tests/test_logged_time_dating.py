"""Regression test for the logged-time stamp on off-slate picks.

The bug this pins down: _format_tracker_time rendered a bare clock time, so a
pick the late preview logged at 11:15 PM the night *before* its slate was
indistinguishable from one logged at 11:15 PM the night after those games
finished. On the 2026-10-28 slate that produced a "Watches results 2026-10-28"
recap whose two rows (James Harden AST u7.5, Zion Williamson REB u5.5) both
read "Tracked: 11:15 PM ET" -- correct grading against the Oct 28 box scores,
but it read as Oct 27's slate being settled against the wrong night's games.

Grading was never keyed off the rendered string -- _queue_results_recap_payload
buckets by the recommendation's own game_date -- so this is a display fix, and
the test asserts both halves: the off-slate row carries a date, the same-day
row stays bare.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es


class TestLoggedTimeDating(unittest.TestCase):
    def test_pick_logged_night_before_its_slate_carries_the_date(self):
        # The exact Harden/Zion stamp from the 2026-10-28 preview recap.
        self.assertEqual(
            es._format_tracker_time("2026-10-27T23:15:23.873696-04:00", "2026-10-28"),
            "Oct 27, 11:15 PM ET",
        )

    def test_same_day_pick_stays_a_bare_clock_time(self):
        # The common case, and the reason the date is conditional: dating every
        # row would put a redundant stamp on nearly the whole ledger.
        self.assertEqual(
            es._format_tracker_time("2026-10-28T08:41:58.243227-04:00", "2026-10-28"),
            "8:41 AM ET",
        )

    def test_day_of_month_is_not_zero_padded(self):
        self.assertEqual(
            es._format_tracker_time("2026-11-07T23:15:00-05:00", "2026-11-08"),
            "Nov 7, 11:15 PM ET",
        )

    def test_offset_is_converted_to_et_before_comparing_dates(self):
        # 03:15 UTC on the 28th is 11:15 PM ET on the 27th. Comparing the raw
        # timestamp's own date would call this a same-day pick and drop the
        # date, which is the whole ambiguity this fix removes.
        self.assertEqual(
            es._format_tracker_time("2026-10-28T03:15:24+00:00", "2026-10-28"),
            "Oct 27, 11:15 PM ET",
        )

    def test_missing_game_date_leaves_the_stamp_alone(self):
        self.assertEqual(
            es._format_tracker_time("2026-10-27T23:15:23.873696-04:00"),
            "11:15 PM ET",
        )

    def test_unparseable_timestamp_still_degrades(self):
        self.assertEqual(es._format_tracker_time(None, "2026-10-28"), "Unknown ET")
        self.assertEqual(es._format_tracker_time("not-a-time", "2026-10-28"), "Unknown ET")


if __name__ == "__main__":
    unittest.main()


class TestSlateLabelOnMessageContent(unittest.TestCase):
    """The channel cannot be reordered, so each message names its own slate.

    Discord anchors a message at its creation time, and the tracker and preview
    logs are created when their slate's first pick lands -- the night before,
    whenever the late look-ahead fires. The 2026-10-28 tracker log was therefore
    created at 11:35 PM on Oct 27 and sits *above* the 2026-10-27 results recap
    posted at 2:23 AM on Oct 28. Editing in place is deliberate (a pick stays
    visible after its game tips), so the ordering is structural; what it costs
    is removed by making every message self-identifying instead.
    """

    PAYLOAD = {
        "game_dates": ["2026-10-28"],
        "generated_at": "2026-10-28T20:15:45.794937-04:00",
        "refresh_label": "intraday",
    }

    def _recommendation(self, player_name, stat_type="PTS", line=10.5):
        return {
            "recommendation_key": f"1|2026-10-28|{stat_type}|under",
            "player_name": player_name,
            "stat_type": stat_type,
            "stat_label": stat_type,
            "pick": "under",
            "pick_label": "Under",
            "line": line,
            "odds": -110,
            "edge_score": 75.0,
            "sportsbook": "fd",
            "sportsbook_label": "FanDuel",
            "game_date": "2026-10-28",
            "first_logged_at": "2026-10-27T23:35:43.832395-04:00",
        }

    def test_tracker_content_names_its_slate(self):
        recommendations = [self._recommendation("Giannis Antetokounmpo")]
        payload = es._build_discord_tracker_payload(
            self.PAYLOAD,
            {"new_recommendations": recommendations},
            running_recommendations=recommendations,
        )
        self.assertIn("2026-10-28 slate", payload["content"])
        # "today" was wrong on a log created the night before its slate.
        self.assertNotIn("today", payload["content"].lower())

    def test_preview_content_names_its_slate(self):
        # The preview log carried no slate date anywhere -- not in the content,
        # not in a field -- and it is where the two 11:15 PM picks lived.
        payload = es._build_discord_preview_payload(
            self.PAYLOAD,
            [self._recommendation("James Harden", "AST", 7.5)],
        )
        self.assertIn("2026-10-28 slate", payload["content"])


class TestSlateLabel(unittest.TestCase):
    def test_single_date(self):
        self.assertEqual(es._slate_label({"game_dates": ["2026-10-28"]}), "2026-10-28")

    def test_two_dates_are_listed(self):
        self.assertEqual(
            es._slate_label({"game_dates": ["2026-10-28", "2026-10-29"]}),
            "2026-10-28, 2026-10-29",
        )

    def test_three_or_more_dates_collapse_to_a_range(self):
        # An arrow, not a hyphen: ISO dates are already full of hyphens.
        self.assertEqual(
            es._slate_label({"game_dates": ["2026-10-28", "2026-10-29", "2026-10-30"]}),
            "2026-10-28 → 2026-10-30",
        )

    def test_junk_entries_never_reach_the_label(self):
        # A None here used to render as the literal string "None".
        self.assertEqual(
            es._slate_label({"game_dates": [None, "  ", "2026-10-28", "2026-10-28"]}),
            "2026-10-28",
        )

    def test_absent_or_unusable_dates_yield_no_label(self):
        for payload in ({}, {"game_dates": []}, {"game_dates": "2026-10-28"}):
            self.assertEqual(es._slate_label(payload), "", payload)


class TestDisabledPreviewIsNotRecapped(unittest.TestCase):
    """Switching the preview off has to stop its recap too.

    EDGE_SCORE_DISCORD_PREVIEW_ENABLED was already false on the VM when the
    "Watches results 2026-10-28" message posted at 6:10 AM on Oct 29. Disabling
    the preview only gated the *log*; _process_results_recaps drained whatever
    pending_result_recaps already held and posted one message per origin in
    RESULT_ORIGINS without consulting the flag.
    """

    def _payload(self):
        return {
            "game_date": "2026-10-28",
            "tracked_recommendations": {
                "201935|2026-10-28|AST|under": {
                    "recommendation_key": "201935|2026-10-28|AST|under",
                    "player_name": "James Harden",
                    "first_alert_kind": "preview",
                },
                "203999|2026-10-28|PTS|under": {
                    "recommendation_key": "203999|2026-10-28|PTS|under",
                    "player_name": "Nikola Jokic",
                    "first_alert_kind": "tracker",
                },
            },
            "recommendations": [
                {"recommendation_key": "201935|2026-10-28|AST|under", "first_alert_kind": "preview"},
                {"recommendation_key": "203999|2026-10-28|PTS|under", "first_alert_kind": "tracker"},
            ],
        }

    @patch.object(es, "EDGE_DISCORD_PREVIEW_ENABLED", False)
    def test_preview_picks_are_dropped_when_the_channel_is_off(self):
        filtered, changed = es._filter_recap_payload_to_enabled_origins(self._payload())
        self.assertTrue(changed)
        self.assertEqual(
            set(filtered["tracked_recommendations"]),
            {"203999|2026-10-28|PTS|under"},
        )
        self.assertEqual(len(filtered["recommendations"]), 1)

    @patch.object(es, "EDGE_DISCORD_PREVIEW_ENABLED", False)
    def test_a_preview_only_date_empties_so_it_can_be_dropped(self):
        # Not held open: an empty payload hits the "nothing tracked" branch in
        # _process_results_recaps. Skipping the origin at send time instead
        # would fail the any_sent check and retry the date forever.
        payload = self._payload()
        payload["tracked_recommendations"].pop("203999|2026-10-28|PTS|under")
        payload["recommendations"] = payload["recommendations"][:1]
        filtered, changed = es._filter_recap_payload_to_enabled_origins(payload)
        self.assertTrue(changed)
        self.assertFalse(filtered["tracked_recommendations"])

    @patch.object(es, "EDGE_DISCORD_PREVIEW_ENABLED", True)
    def test_nothing_is_dropped_while_the_preview_is_on(self):
        filtered, changed = es._filter_recap_payload_to_enabled_origins(self._payload())
        self.assertFalse(changed)
        self.assertEqual(len(filtered["tracked_recommendations"]), 2)
