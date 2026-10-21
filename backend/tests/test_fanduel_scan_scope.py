"""FanDuel scan-scope tests.

FanDuel is scanned in start-time order and stops after a run of propless games,
because props are only posted a day or two out. The risk in that optimization is
mistaking a failed request for "this game has no props" -- that would stop the
scan early and silently drop the rest of the slate. These tests pin that apart.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scrapers import fetch_odds_fanduel as fanduel
from scrapers.fetch_odds_fanduel import FanDuelRequestError

BASE = datetime(2099, 1, 1, tzinfo=timezone.utc)


def _event(event_id, days_out):
    return {
        "eventId": str(event_id),
        "name": f"Team A{event_id} @ Team B{event_id}",
        "openDate": (BASE + timedelta(days=days_out)).isoformat().replace("+00:00", "Z"),
        "inPlay": False,
    }


def _market_payload(event_id, player, line=25.5):
    return {
        "attachments": {
            "events": {str(event_id): {"eventId": str(event_id), "inPlay": False}},
            "markets": {
                "m1": {
                    "marketName": f"{player} - Points",
                    "runners": [
                        {
                            "result": {"type": "OVER"},
                            "handicap": line,
                            "secondaryLogo": "teams/boston_celtics.png",
                            "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}},
                        },
                        {
                            "result": {"type": "UNDER"},
                            "handicap": line,
                            "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}},
                        },
                    ],
                }
            },
        }
    }


def _empty_payload(event_id):
    return {
        "attachments": {
            "events": {str(event_id): {"eventId": str(event_id), "inPlay": False}},
            "markets": {},
        }
    }


class FanDuelScanScopeTests(unittest.TestCase):
    def _main_page(self, events):
        return {"attachments": {"events": {e["eventId"]: e for e in events}}}

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_stops_after_consecutive_propless_games(self, main_page, tabs, props):
        # Games 1-2 have props, 3-4 do not, 5-6 would but must never be reached.
        events = [_event(i, i) for i in range(1, 7)]
        main_page.return_value = self._main_page(events)
        tabs.return_value = [{"name": "player-points", "title": "Player Points"}]

        def fake_props(event_id, tab):
            if event_id in ("1", "2"):
                return _market_payload(event_id, f"Player {event_id}")
            if event_id in ("3", "4"):
                return _empty_payload(event_id)
            raise AssertionError(f"event {event_id} should not have been scanned")

        props.side_effect = fake_props

        result = fanduel.fetch_odds()

        self.assertEqual(len(result), 2)
        # 4 events scanned (2 with props + 2 propless), then stop.
        self.assertEqual(props.call_count, 4)

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_failed_requests_do_not_stop_the_scan(self, main_page, tabs, props):
        """A 429 on the nearest games must not be read as 'no props from here on'."""
        events = [_event(i, i) for i in range(1, 5)]
        main_page.return_value = self._main_page(events)
        tabs.return_value = [{"name": "player-points", "title": "Player Points"}]

        def fake_props(event_id, tab):
            if event_id in ("1", "2"):
                raise FanDuelRequestError("429 Too Many Requests")
            return _market_payload(event_id, f"Player {event_id}")

        props.side_effect = fake_props

        result = fanduel.fetch_odds()

        # Events 3 and 4 still got scanned and still produced props.
        self.assertEqual(len(result), 2)
        self.assertEqual(props.call_count, 4)

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_tab_fetch_failure_does_not_stop_the_scan(self, main_page, tabs, props):
        events = [_event(i, i) for i in range(1, 4)]
        main_page.return_value = self._main_page(events)

        def fake_tabs(event_id):
            if event_id == "1":
                raise FanDuelRequestError("429 Too Many Requests")
            return [{"name": "player-points", "title": "Player Points"}]

        tabs.side_effect = fake_tabs
        props.side_effect = lambda event_id, tab: _market_payload(event_id, f"Player {event_id}")

        result = fanduel.fetch_odds()

        self.assertEqual(len(result), 2)  # events 2 and 3

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_events_are_scanned_in_chronological_order(self, main_page, tabs, props):
        # Deliberately supplied out of order; the furthest-out game is propless.
        events = [_event(3, 3), _event(1, 1), _event(2, 2)]
        main_page.return_value = self._main_page(events)
        tabs.return_value = [{"name": "player-points", "title": "Player Points"}]

        seen = []

        def fake_props(event_id, tab):
            seen.append(event_id)
            return _market_payload(event_id, f"Player {event_id}")

        props.side_effect = fake_props

        fanduel.fetch_odds()

        self.assertEqual(seen, ["1", "2", "3"])


if __name__ == "__main__":
    unittest.main()
