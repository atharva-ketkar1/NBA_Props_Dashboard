"""An empty tracking payload has to look like a gap, not like a finished date.

NBA player-tracking aggregates (leaguedashptstats: Passing/Rebounding/Drives)
publish hours after the box scores, and the daily pipeline runs in the
early-morning ET window against the previous night's games. At that hour the
endpoint answers HTTP 200 with an empty rowSet.

The scraper used to `break` on that without recording anything, so
`failed_endpoints` stayed empty, the other three endpoint groups kept
`daily_merged` non-None, and the date logged as "Completed". Nothing retried it
and nothing wrote it to the failed manifest, so the eight tracking columns
never once appeared in gamelogs_2026-27.csv -- which left six ML features NaN
at serve time for the whole season.

These tests pin the fix: an empty payload on a date that has games is reported
as missing, so the existing manifest-recovery path re-targets the date on a
later run once the aggregates have published.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scrapers import gamelogs


PT_HEADERS = ["PLAYER_ID", "POTENTIAL_AST", "PASSES_MADE", "AST_POINTS_CREATED",
              "REB_CHANCES", "REB_CONTEST_PCT", "DRIVES", "DRIVE_PTS", "DRIVE_PASSES"]
BASE_HEADERS = ["PLAYER_ID", "MIN", "PTS", "AST", "REB", "FG3M", "FGM", "FTM", "PF"]
ADV_HEADERS = ["PLAYER_ID", "USG_PCT", "AST_PCT", "REB_PCT", "TS_PCT", "PIE"]


def _payload(headers, rows):
    return {"resultSets": [{"headers": headers, "rowSet": rows}]}


def _responder(*, pt_rows, other_rows):
    """Stand in for fetch_nba_json, keyed off the endpoint in the URL."""
    def _fake(url, **kwargs):
        if "leaguedashptstats" in url:
            return _payload(PT_HEADERS, pt_rows), "direct"
        if "MeasureType=Advanced" in url:
            return _payload(ADV_HEADERS, [[1, 20.0, 10.0, 8.0, 55.0, 12.0]] if other_rows else []), "direct"
        return _payload(BASE_HEADERS, [[1, 30.0, 25.0, 5.0, 7.0, 2.0, 9.0, 4.0, 1.0]] if other_rows else []), "direct"
    return _fake


class EmptyTrackingPayloadIsAGap(unittest.TestCase):
    def test_empty_tracking_rows_are_reported_missing(self):
        """200-with-no-rows must surface, or the date is never retried."""
        with patch.object(gamelogs, "fetch_nba_json",
                          side_effect=_responder(pt_rows=[], other_rows=True)), \
             patch.object(gamelogs.time, "sleep"):
            merged, missing = gamelogs.fetch_tracking_data_for_date("11/02/2026", season="2026-27")

        # The other endpoint groups still landed, which is exactly why this used
        # to pass as a success.
        self.assertIsNotNone(merged)
        for measure in gamelogs.PT_MEASURE_TYPES:
            self.assertIn(measure, missing)

    def test_populated_tracking_rows_report_nothing_missing(self):
        with patch.object(gamelogs, "fetch_nba_json",
                          side_effect=_responder(
                              pt_rows=[[1, 6.0, 40.0, 12.0, 9.0, 55.0, 11.0, 7.0, 3.0]],
                              other_rows=True)), \
             patch.object(gamelogs.time, "sleep"):
            merged, missing = gamelogs.fetch_tracking_data_for_date("11/02/2026", season="2026-27")

        self.assertEqual(missing, [])
        self.assertIsNotNone(merged)
        for col in ("POTENTIAL_AST", "DRIVES", "REB_CHANCES"):
            self.assertIn(col, merged.columns)

    def test_empty_segment_endpoints_are_reported_too(self):
        """1Q/1H/Advanced had the same silent-skip shape, via a bare `break`."""
        with patch.object(gamelogs, "fetch_nba_json",
                          side_effect=_responder(
                              pt_rows=[[1, 6.0, 40.0, 12.0, 9.0, 55.0, 11.0, 7.0, 3.0]],
                              other_rows=False)), \
             patch.object(gamelogs.time, "sleep"):
            _, missing = gamelogs.fetch_tracking_data_for_date("11/02/2026", season="2026-27")

        for label in ("1Q", "1H", "Advanced"):
            self.assertIn(label, missing)


class TrackingColumnContract(unittest.TestCase):
    def test_schema_guard_covers_every_column_the_scraper_keeps(self):
        """TRACKING_COLS is what the write-time guard checks, so it must match
        the columns the PT block actually extracts -- otherwise a partially
        narrowed feed slips past the guard."""
        self.assertEqual(
            sorted(gamelogs.TRACKING_COLS),
            sorted(c for c in PT_HEADERS if c != "PLAYER_ID"),
        )


if __name__ == "__main__":
    unittest.main()
