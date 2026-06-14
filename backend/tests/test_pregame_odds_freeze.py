import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scrapers import fetch_odds_fanduel as fanduel
from scrapers.fetch_odds_fanduel import is_event_pregame, payload_marks_event_in_play
from utils.pregame_props import has_game_started, is_within_pregame_window


class FanDuelPregameTests(unittest.TestCase):
    def test_event_is_frozen_at_scheduled_tip(self):
        tip = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)
        event = {"inPlay": False, "openDate": tip.isoformat()}

        self.assertTrue(is_event_pregame(event, now=tip - timedelta(seconds=1)))
        self.assertFalse(is_event_pregame(event, now=tip))

    def test_live_event_payload_is_rejected(self):
        payload = {
            "attachments": {
                "events": {
                    "123": {"eventId": 123, "inPlay": True},
                },
            },
        }

        self.assertTrue(payload_marks_event_in_play(payload, "123"))

    @patch.object(fanduel, "get_player_props")
    @patch.object(fanduel, "get_all_available_tabs")
    @patch.object(fanduel, "get_nba_main_page_data")
    def test_event_batch_is_discarded_if_later_tab_turns_live(
        self,
        mock_main_page,
        mock_tabs,
        mock_props,
    ):
        event = {
            "eventId": "123",
            "name": "Cleveland Cavaliers @ Boston Celtics",
            "openDate": "2099-10-20T23:30:00Z",
            "inPlay": False,
        }
        mock_main_page.return_value = {
            "attachments": {"events": {"123": event}},
        }
        mock_tabs.return_value = [
            {"name": "player-points", "title": "Player Points"},
            {"name": "player-rebounds", "title": "Player Rebounds"},
        ]
        mock_props.side_effect = [
            {
                "attachments": {
                    "events": {"123": {**event, "inPlay": False}},
                    "markets": {
                        "market-1": {
                            "marketName": "Donovan Mitchell - Points",
                            "runners": [
                                {
                                    "result": {"type": "OVER"},
                                    "handicap": 27.5,
                                    "secondaryLogo": "teams/cleveland_cavaliers.png",
                                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}},
                                },
                                {
                                    "result": {"type": "UNDER"},
                                    "handicap": 27.5,
                                    "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": -110}},
                                },
                            ],
                        },
                    },
                },
            },
            {
                "attachments": {
                    "events": {"123": {**event, "inPlay": False}},
                    "markets": {
                        "market-live": {"inPlay": True},
                    },
                },
            },
        ]

        self.assertEqual(fanduel.fetch_odds(), [])


class ClosingWindowTests(unittest.TestCase):
    def test_window_closes_exactly_at_tip(self):
        tip = datetime(2026, 10, 20, 19, 30, tzinfo=timezone(timedelta(hours=-4)))
        game = {
            "closing_scrape_deadline": tip.isoformat(),
            "is_live": False,
            "is_final": False,
        }

        self.assertTrue(
            is_within_pregame_window(game, 12, now_et=tip - timedelta(minutes=10))
        )
        self.assertFalse(is_within_pregame_window(game, 12, now_et=tip))
        self.assertTrue(has_game_started(game, now_et=tip))

    def test_live_status_closes_window_even_before_scheduled_tip(self):
        now = datetime(2026, 10, 20, 19, 20, tzinfo=timezone(timedelta(hours=-4)))
        game = {
            "closing_scrape_deadline": (now + timedelta(minutes=10)).isoformat(),
            "is_live": True,
        }

        self.assertFalse(is_within_pregame_window(game, 12, now_et=now))


if __name__ == "__main__":
    unittest.main()
