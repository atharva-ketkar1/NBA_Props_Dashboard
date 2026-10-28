import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from cron_jobs import cron_closing_lines as ccl


ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=ET)
D0 = "2026-10-28"
D1 = "2026-10-29"


def _schedule_game(game_id, game_date, home, away, hour=19):
    tip = datetime.strptime(f"{game_date} {hour:02d}:00", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_team_tricode": home,
        "away_team_tricode": away,
        "matchup": f"{away} @ {home}",
        "game_time_utc": tip.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "game_time_et": tip.strftime("%I:%M %p ET"),
        "closing_scrape_deadline": tip.isoformat(),
        "game_status": 1,
        "is_live": False,
        "is_final": False,
    }


def _row(player, team, game_date, line, *, prop="PTS", game="ATL @ CLE"):
    return {
        "player": player,
        "team": team,
        "prop_type": prop,
        "line": line,
        "over_odds": -110,
        "under_odds": -110,
        "game": game,
        "game_date": game_date,
        "inPlay": False,
    }


class _StubMatcher:
    """Resolve by name, the way PlayerMatcher does for the cases under test."""

    def __init__(self, by_name):
        self._by_name = by_name

    def match_player(self, name, team="UNK", team_options=None):
        # The DraftKings path passes team_options as a third positional arg.
        return self._by_name.get(str(name).strip())


class BackToBackOddsShapingTests(unittest.TestCase):
    """A player whose team plays twice inside the window.

    `players_dict` holds one entry per player, stamped with the first game date
    seen, and props accumulate into it regardless of which date each row belongs
    to. Without a guard the last row read wins and its line is filed under the
    other night, which is worse than dropping it: it is a real line attributed
    to the wrong game.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        schedule_path = Path(self._tmp.name) / "today_schedule.json"
        schedule_path.write_text(json.dumps({"games": [
            _schedule_game("g0", D0, "CLE", "ATL", hour=19),
            _schedule_game("g1", D1, "ATL", "CLE", hour=20),
            _schedule_game("g2", D0, "BOS", "NYK", hour=19),
        ]}))
        self.schedule_path = str(schedule_path)
        self.matcher = _StubMatcher({"Trae Young": 1629027, "Jayson Tatum": 1628369})
        self.id_to_team = {"1629027": "ATL", "1628369": "BOS"}

    def tearDown(self):
        self._tmp.cleanup()

    def _shape(self, fd_rows, dk_rows=()):
        with patch.object(ccl, "SCHEDULE_PATH", self.schedule_path), \
             patch.object(ccl, "load_master_feed_maps",
                          return_value=(self.matcher, self.id_to_team)), \
             patch.object(ccl, "get_et_now", return_value=NOW), \
             patch.object(ccl.fanduel, "fetch_odds", return_value=list(fd_rows)), \
             patch.object(ccl.draftkings, "fetch_dk_odds", return_value=list(dk_rows)):
            return ccl.scrape_and_shape_odds(is_closing=False)

    def test_a_second_date_never_overwrites_the_first_nights_line(self):
        shaped = self._shape([
            _row("Trae Young", "ATL", D0, 24.5),
            _row("Trae Young", "ATL", D1, 31.5),
        ])
        entry = shaped["1629027"]
        self.assertEqual(entry["game_date"], D0)
        self.assertEqual(
            entry["props"]["PTS"]["fanduel"]["line"], 24.5,
            "tomorrow's line must not be filed under tonight's game",
        )

    def test_the_guard_holds_whichever_date_arrives_first(self):
        """Row order is the scraper's, not ours; the entry keeps whichever date
        opened it and stays internally consistent either way."""
        shaped = self._shape([
            _row("Trae Young", "ATL", D1, 31.5),
            _row("Trae Young", "ATL", D0, 24.5),
        ])
        entry = shaped["1629027"]
        self.assertEqual(entry["game_date"], D1)
        self.assertEqual(entry["props"]["PTS"]["fanduel"]["line"], 31.5)

    def test_both_books_are_guarded(self):
        shaped = self._shape(
            [_row("Trae Young", "ATL", D0, 24.5)],
            [_row("Trae Young", "ATL", D1, 31.5)],
        )
        entry = shaped["1629027"]
        self.assertEqual(entry["game_date"], D0)
        self.assertNotIn(
            "draftkings", entry["props"].get("PTS", {}),
            "a DraftKings row for the other date must not attach to this entry",
        )

    def test_a_player_with_one_game_is_untouched(self):
        shaped = self._shape([
            _row("Jayson Tatum", "BOS", D0, 27.5, game="NYK @ BOS"),
        ])
        entry = shaped["1628369"]
        self.assertEqual(entry["game_date"], D0)
        self.assertEqual(entry["props"]["PTS"]["fanduel"]["line"], 27.5)

    def test_the_guard_is_per_player_not_global(self):
        """Two different players on two different dates both survive; the guard
        only ever compares a row against that player's own entry."""
        shaped = self._shape([
            _row("Trae Young", "ATL", D1, 31.5),
            _row("Jayson Tatum", "BOS", D0, 27.5, game="NYK @ BOS"),
        ])
        self.assertEqual(shaped["1629027"]["game_date"], D1)
        self.assertEqual(shaped["1628369"]["game_date"], D0)

    def test_multiple_props_on_the_same_date_still_accumulate(self):
        shaped = self._shape([
            _row("Trae Young", "ATL", D0, 24.5, prop="PTS"),
            _row("Trae Young", "ATL", D0, 9.5, prop="AST"),
        ])
        props = shaped["1629027"]["props"]
        self.assertEqual(props["PTS"]["fanduel"]["line"], 24.5)
        self.assertEqual(props["AST"]["fanduel"]["line"], 9.5)


if __name__ == "__main__":
    unittest.main()
