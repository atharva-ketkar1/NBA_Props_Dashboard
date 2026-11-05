"""The training set must not be able to silently cover fewer seasons.

DEFAULT_GAMELOG_PATHS was a hardcoded pair pointing at
backend/data/current/gamelogs_2025-26.csv. That file rotated into
backend/data/archive/ and the live season became 2026-27, so the list went
stale -- and _build_history skipped absent paths with a bare `continue`, so
rebuilding the dataset would have produced a 2024-25-only training set that
looked entirely normal. Nothing downstream could have noticed.

These tests pin both halves of the fix: the paths are discovered rather than
hardcoded, and a named-but-absent path is a hard error.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
PROP_MODELING_DIR = BACKEND_DIR / "prop_modeling"
if str(PROP_MODELING_DIR) not in sys.path:
    sys.path.insert(0, str(PROP_MODELING_DIR))

from prop_modeling import feature_schema
from prop_modeling.build_regression_dataset import _build_history


class GamelogDiscovery(unittest.TestCase):
    def _tree(self, root: Path, archive, current):
        (root / "data" / "archive").mkdir(parents=True)
        (root / "data" / "current").mkdir(parents=True)
        for name in archive:
            (root / "data" / "archive" / name).write_text("PLAYER_ID\n")
        for name in current:
            (root / "data" / "current" / name).write_text("PLAYER_ID\n")

    def test_every_season_is_found_across_both_directories(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root,
                       ["gamelogs_2024-25.csv", "gamelogs_2025-26.csv"],
                       ["gamelogs_2026-27.csv"])
            with patch.object(feature_schema, "BACKEND_DIR", root):
                found = feature_schema.discover_gamelog_paths()
            self.assertEqual([p.stem for p in found],
                             ["gamelogs_2024-25", "gamelogs_2025-26", "gamelogs_2026-27"])

    def test_backup_copies_are_excluded(self):
        """gamelogs_2025-26.backup.<ts>.csv must not be ingested twice."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root,
                       ["gamelogs_2025-26.csv",
                        "gamelogs_2025-26.backup.20260423_220018.csv"],
                       [])
            with patch.object(feature_schema, "BACKEND_DIR", root):
                found = feature_schema.discover_gamelog_paths()
            self.assertEqual([p.stem for p in found], ["gamelogs_2025-26"])

    def test_current_wins_over_archive_for_the_same_season(self):
        """A season mid-rotation must not be read twice."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root, ["gamelogs_2026-27.csv"], ["gamelogs_2026-27.csv"])
            with patch.object(feature_schema, "BACKEND_DIR", root):
                found = feature_schema.discover_gamelog_paths()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].parent.name, "current")

    def test_an_empty_tree_discovers_nothing_rather_than_guessing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._tree(root, [], [])
            with patch.object(feature_schema, "BACKEND_DIR", root):
                self.assertEqual(feature_schema.discover_gamelog_paths(), [])

    def test_the_real_tree_resolves_to_files_that_exist(self):
        paths = feature_schema.discover_gamelog_paths()
        self.assertTrue(paths, "no gamelogs discovered in the real tree")
        for p in paths:
            self.assertTrue(p.exists(), "%s does not exist" % p)


class MissingPathsAreLoud(unittest.TestCase):
    def test_a_named_but_absent_gamelog_raises(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            _build_history([Path("/nonexistent/gamelogs_2025-26.csv")])
        self.assertIn("missing", str(ctx.exception).lower())

    def test_an_empty_path_list_raises(self):
        with self.assertRaises(ValueError):
            _build_history([])


if __name__ == "__main__":
    unittest.main()
