"""Intraday line movements have to survive the midnight roll and the DB purge.

Closing-line value is the only instrument that separates a good bet from a
lucky one, and it was unmeasurable: `line_movements_today.json` is destructively
rolled (the date flips and `_carry_forward_snapshots` drops every player no
longer on the active schedule), and `master_cron` applies a rolling 3-day DELETE
to the `line_movements` table. Every intraday series was gone within days.

These tests pin the durable copy -- an append-only file per date under
`data/archive/line_movements/` -- and the coherence of the pair that replaced
it: the sync state is pruned on the same window the purge deletes on, so a
fingerprint can no longer claim a deleted row is still present.
"""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import snapshot_manager as snapshot_manager_module
from utils.snapshot_manager import SnapshotManager
from utils.upsert_market_history import (
    LINE_MOVEMENT_RETENTION_DAYS,
    _prune_line_movement_sync_state,
)


def _player(line):
    return {
        "name": "jayson tatum",
        "team": "BOS",
        "game_id": "0022600100",
        "game_date": "2026-11-05",
        "props": {"PTS": {"draftkings": {"line": line, "over_odds": -110}}},
    }


class LineMovementArchive(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.data_dir = root / "data"
        self.manager = SnapshotManager(
            data_dir=str(self.data_dir),
            logs_dir=str(root / "logs"),
        )
        self.archive_dir = Path(self.manager.line_movements_archive_dir)

    def _archive_for(self, archive_date):
        path = self.archive_dir / f"{archive_date}.json"
        with path.open() as handle:
            return json.load(handle)

    def test_snapshot_is_archived_under_its_date(self):
        self.assertTrue(self.manager.write_snapshot("intraday", {"1628369": _player(27.5)}))

        today = date.today().isoformat()
        archived = self._archive_for(today)
        self.assertEqual(archived["date"], today)
        self.assertEqual(len(archived["snapshots"]), 1)
        self.assertEqual(archived["snapshots"][0]["label"], "intraday")
        self.assertIn("1628369", archived["snapshots"][0]["players"])

    def test_distinct_snapshots_append_to_one_file(self):
        self.manager.write_snapshot("intraday", {"1628369": _player(27.5)})
        self.manager.write_snapshot(
            "pre_game",
            {"1628369": _player(28.5)},
            bypass_dedupe=True,
        )

        archived = self._archive_for(date.today().isoformat())
        self.assertEqual(len(archived["snapshots"]), 2)
        lines = [
            snapshot["players"]["1628369"]["props"]["PTS"]["draftkings"]["line"]
            for snapshot in archived["snapshots"]
        ]
        self.assertEqual(lines, [27.5, 28.5])
        self.assertEqual(len(list(self.archive_dir.glob("*.json"))), 1)

    def test_identical_recapture_is_not_archived_twice(self):
        # bypass_dedupe is how the pre-tip capture writes, so the live file can
        # legitimately hold a repeat; the archive must not grow for it.
        self.manager.write_snapshot("intraday", {"1628369": _player(27.5)})
        self.manager.write_snapshot(
            "pre_game",
            {"1628369": _player(27.5)},
            bypass_dedupe=True,
        )

        archived = self._archive_for(date.today().isoformat())
        self.assertEqual(len(archived["snapshots"]), 1)

    def test_archive_survives_the_destructive_roll(self):
        self.manager.write_snapshot("intraday", {"1628369": _player(27.5)})
        archive_date = date.today().isoformat()

        # Reproduce the roll: the live file is rewritten for a new date with the
        # player filtered out, exactly as _carry_forward_snapshots leaves it.
        with open(self.manager.line_movements_path, "w") as handle:
            json.dump({"date": "2026-11-06", "snapshots": []}, handle)

        live = self.manager._read_json(self.manager.line_movements_path)
        self.assertEqual(live["snapshots"], [])

        archived = self._archive_for(archive_date)
        self.assertIn("1628369", archived["snapshots"][0]["players"])

    def test_archive_failure_does_not_break_the_live_write(self):
        with patch.object(
            SnapshotManager,
            "_archive_snapshot",
            side_effect=OSError("disk full"),
        ):
            with patch.object(snapshot_manager_module.logger, "warning") as warn:
                self.assertTrue(
                    self.manager.write_snapshot("intraday", {"1628369": _player(27.5)})
                )
                warn.assert_called_once()

        live = self.manager._read_json(self.manager.line_movements_path)
        self.assertEqual(len(live["snapshots"]), 1)

    def test_corrupt_archive_file_is_replaced_not_appended_to(self):
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        (self.archive_dir / f"{today}.json").write_text("{ not json")

        self.assertTrue(self.manager.write_snapshot("intraday", {"1628369": _player(27.5)}))
        self.assertEqual(len(self._archive_for(today)["snapshots"]), 1)

    def test_empty_snapshot_is_never_archived(self):
        self.assertFalse(self.manager.write_snapshot("intraday", {}))
        self.assertFalse(self.archive_dir.exists())


class SyncStatePruneMatchesThePurge(unittest.TestCase):
    """The prune window and master_cron's DELETE cutoff must be one number."""

    def test_master_cron_purges_on_the_shared_constant(self):
        source = (BACKEND_DIR / "cron_jobs" / "master_cron.py").read_text()
        self.assertIn("from utils.upsert_market_history import LINE_MOVEMENT_RETENTION_DAYS", source)
        self.assertIn("timedelta(days=LINE_MOVEMENT_RETENTION_DAYS)", source)

    def test_purged_dates_lose_their_fingerprint(self):
        today = date(2026, 11, 5)
        purged = (today - timedelta(days=LINE_MOVEMENT_RETENTION_DAYS + 1)).isoformat()
        state = {purged: "abc123", today.isoformat(): "def456"}

        pruned = _prune_line_movement_sync_state(state, today=today)

        self.assertNotIn(purged, pruned)
        self.assertEqual(pruned[today.isoformat()], "def456")

    def test_the_oldest_surviving_row_keeps_its_fingerprint(self):
        # master_cron deletes `game_date < today - RETENTION_DAYS`, so the row
        # exactly on the boundary survives and its fingerprint must too.
        today = date(2026, 11, 5)
        boundary = (today - timedelta(days=LINE_MOVEMENT_RETENTION_DAYS)).isoformat()

        pruned = _prune_line_movement_sync_state({boundary: "abc123"}, today=today)

        self.assertEqual(pruned, {boundary: "abc123"})

    def test_unparseable_keys_are_dropped(self):
        pruned = _prune_line_movement_sync_state(
            {"not-a-date": "abc123"},
            today=date(2026, 11, 5),
        )
        self.assertEqual(pruned, {})


if __name__ == "__main__":
    unittest.main()
