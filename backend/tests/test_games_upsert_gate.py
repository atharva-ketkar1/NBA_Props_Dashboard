"""The games upsert writes only what changed.

The game-status refresh runs every 10 minutes and used to rewrite all ~49 rows
on every tick -- ~4,700 row versions a day for a table that is static outside
tip-off and final score, each one leaving a dead tuple for autovacuum. That was
disk I/O budget spent rewriting what was already there, and Supabase sent a
budget warning on 2026-11-16.

These tests pin the gate and the two things it must not break: a write that
failed must not be recorded as synced, and the stale-row sweep must still run
on a tick where no row changed.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import scrapers.fetch_todays_games as games_module


def _game(game_id, **overrides):
    row = {
        "game_id": game_id,
        "game_date": "2026-11-17",
        "home_team_id": 1, "home_team_name": "A", "home_team_city": "A",
        "home_team_tricode": "AAA", "home_team_wins": 1, "home_team_losses": 1,
        "home_score": 0,
        "away_team_id": 2, "away_team_name": "B", "away_team_city": "B",
        "away_team_tricode": "BBB", "away_team_wins": 1, "away_team_losses": 1,
        "away_score": 0,
        "arena_name": "X", "arena_city": "X",
        "game_time_utc": "t", "game_time_et": "t",
        "game_status": "1", "game_status_text": "s",
        "is_live": False, "is_final": False, "is_scheduled": True,
        "matchup": "A@B", "closing_scrape_deadline": "d",
        "schedule_source": "nba",
    }
    row.update(overrides)
    return row


class _FakeQuery:
    def __init__(self, recorder, table, existing, fail_upsert):
        self._recorder = recorder
        self._table = table
        self._existing = existing
        self._fail_upsert = fail_upsert

    def select(self, *a, **k): return self
    def gte(self, *a, **k): return self
    def lte(self, *a, **k): return self

    def eq(self, _column, value):
        self._recorder["deleted"].append(value)
        return self

    def delete(self): return self

    def upsert(self, rows, **_k):
        if self._fail_upsert:
            raise RuntimeError("supabase down")
        self._recorder["upserts"].append(list(rows))
        return self

    def execute(self):
        return type("Response", (), {"data": self._existing})()


class _FakeSupabase:
    def __init__(self, recorder, existing=(), fail_upsert=False):
        self._recorder = recorder
        self._existing = list(existing)
        self._fail_upsert = fail_upsert

    def table(self, name):
        return _FakeQuery(self._recorder, name, self._existing, self._fail_upsert)


class GamesUpsertGate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = os.path.join(self._tmp.name, "games_sync_state.json")
        patcher = patch.object(games_module, "GAMES_SYNC_STATE_PATH", self.state_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        window = patch.object(
            games_module, "_get_dashboard_window_dates", lambda: ["2026-11-17"],
        )
        window.start()
        self.addCleanup(window.stop)

    def _run(self, rows, existing=(), fail_upsert=False):
        recorder = {"upserts": [], "deleted": []}
        fake = _FakeSupabase(recorder, existing=existing, fail_upsert=fail_upsert)
        with patch("utils.supabase_client.get_supabase_client", lambda: fake):
            games_module.upsert_games_to_db(rows)
        return recorder

    def _state(self):
        with open(self.state_path) as handle:
            return json.load(handle)

    def test_first_run_writes_every_row(self):
        recorder = self._run([_game("g1"), _game("g2")])
        self.assertEqual(len(recorder["upserts"][0]), 2)

    def test_an_unchanged_slate_is_not_rewritten(self):
        rows = [_game("g1"), _game("g2")]
        self._run(rows)
        recorder = self._run(rows)
        self.assertEqual(recorder["upserts"], [])

    def test_only_the_changed_row_is_written(self):
        self._run([_game("g1"), _game("g2")])
        recorder = self._run([_game("g1"), _game("g2", home_score=55)])
        self.assertEqual([row["game_id"] for row in recorder["upserts"][0]], ["g2"])

    def test_any_column_counts_as_a_change(self):
        """The fingerprint is the whole row, not a chosen subset.

        is_live and game_status are what the frontend switches a slate card on,
        so a gate that only watched scores would freeze a game as scheduled.
        """
        for column, value in (
            ("is_live", True),
            ("game_status", "2"),
            ("is_final", True),
            ("closing_scrape_deadline", "later"),
        ):
            with self.subTest(column=column):
                self._run([_game("g1")])
                recorder = self._run([_game("g1", **{column: value})])
                self.assertEqual(len(recorder["upserts"][0]), 1)

    def test_a_failed_write_is_not_recorded_as_synced(self):
        """Otherwise the row is suppressed forever and silently never lands."""
        self._run([_game("g1")])
        before = self._state()

        self._run([_game("g1", home_score=99)], fail_upsert=True)
        self.assertEqual(self._state(), before)

        recorder = self._run([_game("g1", home_score=99)])
        self.assertEqual(len(recorder["upserts"][0]), 1)

    def test_state_does_not_accumulate_past_the_window(self):
        self._run([_game("g1"), _game("g2")])
        self._run([_game("g9")])
        self.assertEqual(sorted(self._state()), ["g9"])

    def test_the_stale_sweep_still_runs_when_nothing_changed(self):
        """Deliberately ungated: a row vanishing from the schedule is not
        something a fingerprint of the incoming payload can observe."""
        rows = [_game("g1")]
        self._run(rows)
        recorder = self._run(
            rows,
            existing=[{"game_id": "ghost", "game_date": "2026-11-17"},
                      {"game_id": "g1", "game_date": "2026-11-17"}],
        )
        self.assertEqual(recorder["upserts"], [])
        self.assertIn("ghost", recorder["deleted"])


if __name__ == "__main__":
    unittest.main()
