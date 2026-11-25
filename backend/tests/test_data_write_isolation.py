"""The suite must not be able to write real `backend/data` artifacts.

Regression for an order- and clock-dependent failure that lived in the suite for
weeks. `tests/test_odds_shaping_dates.py` calls the real
`cron_closing_lines.scrape_and_shape_odds()`, which calls `persist_raw_odds_csvs`,
which wrote stub CSVs over `data/current/draftkings.csv` and `fanduel.csv`. Five
`tests/test_book_pause.py` cases then failed, because `edge_score._scoring_books()`
subtracts `book_toggles.stale_books()` and that stats those files' mtimes against a
90-minute bound.

What made it hard to see: the stub it wrote was *fresh*, so an immediate re-run
passed. Only a run starting more than 90 minutes later failed. And `data/` is
gitignored, so the clobbering left no trace in `git status`.

Two independent defences, one test class each -- the fixture is what makes the
common case work, the tripwire is what catches a writer the fixture has never
heard of.
"""

import os
import sys
import unittest
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import odds_csv  # noqa: E402

REAL_DATA_ROOT = BACKEND_DIR / "data"
ROW = {
    "player": "Test Player", "team": "ATL", "prop_type": "PTS", "line": 20.5,
    "over_odds": -110, "under_odds": -110, "implied_prob": 0.5,
    "game": "ATL @ CLE", "game_date": "2026-11-01", "sportsbook": "dk",
    "team_options": None,
}


class TheTripwireRefusesRealPaths(unittest.TestCase):
    """`write_odds_csv` itself rejects a real-tree path while under pytest."""

    def test_a_real_data_path_raises(self):
        target = REAL_DATA_ROOT / "current" / "draftkings.csv"
        before = target.read_bytes() if target.exists() else None

        with self.assertRaises(RuntimeError) as caught:
            odds_csv.write_odds_csv(str(target), [ROW])
        self.assertIn("refusing to write", str(caught.exception))

        # The point of the guard: the real file is untouched either way.
        after = target.read_bytes() if target.exists() else None
        self.assertEqual(before, after)

    def test_the_archive_tree_is_protected_too(self):
        """`data/` entire, not just `data/current` -- the archive is the durable
        copy closing-line value is computed from."""
        with self.assertRaises(RuntimeError):
            odds_csv.write_odds_csv(
                str(REAL_DATA_ROOT / "archive" / "fanduel.csv"), [ROW])

    def test_a_tmp_path_is_allowed(self):
        """So the guard fails for the path, not for every call."""
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "draftkings.csv")
            odds_csv.write_odds_csv(target, [ROW])
            self.assertTrue(os.path.exists(target))

    def test_an_explicit_opt_in_still_writes(self):
        """Production code paths that genuinely want a real write are not broken
        by the guard -- they are only required to say so."""
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            # Uses a tmp path but exercises the opt-in branch, so the test never
            # depends on being allowed to touch the real tree.
            target = os.path.join(directory, "fanduel.csv")
            with patch.dict(os.environ, {"ODDS_CSV_ALLOW_REAL_WRITES": "1"}):
                odds_csv.write_odds_csv(target, [ROW])
            self.assertTrue(os.path.exists(target))

    def test_the_guard_is_inert_outside_pytest(self):
        """It keys on PYTEST_CURRENT_TEST, so production is unaffected."""
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "draftkings.csv")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PYTEST_CURRENT_TEST", None)
                odds_csv.write_odds_csv(target, [ROW])
            self.assertTrue(os.path.exists(target))


def test_the_autouse_fixture_redirects_the_closing_line_writer(isolate_current_data_dir):
    """The primary defence: `persist_raw_odds_csvs` lands in tmp, not `data/`.

    A plain pytest function rather than a unittest method, because the fixture
    value is needed and unittest classes cannot take fixture arguments.
    """
    from cron_jobs import cron_closing_lines as ccl

    assert ccl.DATA_DIR == str(isolate_current_data_dir)

    ccl.persist_raw_odds_csvs([ROW], [ROW])

    written = sorted(p.name for p in isolate_current_data_dir.iterdir())
    assert written == ["draftkings.csv", "fanduel.csv"]


def test_the_real_odds_csvs_are_never_touched_by_the_suite():
    """Belt and braces: assert the real paths are outside the redirect target.

    If someone reintroduces a hardcoded path, the tripwire raises -- but
    `persist_raw_odds_csvs` wraps its body in `except Exception` and only logs a
    warning, so the raise alone would be swallowed. This asserts the redirect is
    in force, which is what actually keeps the file safe.
    """
    from cron_jobs import cron_closing_lines as ccl

    resolved = os.path.realpath(ccl.DATA_DIR)
    real = os.path.realpath(str(REAL_DATA_ROOT))
    assert not resolved.startswith(real), (
        f"cron_closing_lines.DATA_DIR still points inside {real}; the autouse "
        f"fixture in backend/conftest.py is not taking effect"
    )


if __name__ == "__main__":
    unittest.main()
