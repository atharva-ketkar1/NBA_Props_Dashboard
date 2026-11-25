"""Shared pytest setup for the backend suite.

Three jobs, each one fixing something that used to be every test author's
responsibility to remember:

1. `backend/` on `sys.path`, so a new test file does not need the four-line
   preamble the existing 39 all carry. The preambles stay -- they are harmless
   and documented in CLAUDE.md -- but nothing new needs one.

2. Redirecting the writers that point at `backend/data/current`, so no test can
   clobber a real artifact. This is the bug `health_ledger._inert()` could only
   name from a distance: `tests/test_odds_shaping_dates.py` calls the real
   `scrape_and_shape_odds()`, which persisted stub odds CSVs over the live ones,
   which then failed five `test_book_pause.py` cases through edge_score's 90-minute
   odds age gate. It passed on an immediate re-run and failed on the next cold
   one, and `data/` is gitignored so there was nothing to notice.

3. One opt-in fixture for the odds age gate, replacing the identical setUp
   duplicated in `test_price_shopping.py` and `test_book_pause.py`.
"""

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.odds_csv import ODDS_CSV_COLUMNS  # noqa: E402 - needs sys.path above


def _data_dir_holders():
    """Import the modules that hold a `data/current` path, once per session.

    Imported eagerly and at module scope rather than looked up in `sys.modules`
    from inside the fixture. The lookup version only patched modules a test had
    *already* imported, so a test importing one inside its own function body --
    which `test_data_write_isolation.py` does -- silently got the real directory
    back. Measured cost of importing all three is ~0.6s, paid once for the
    session because `import` is cached, not per test.

    A module that fails to import is skipped rather than failing collection: the
    tripwire in `utils.odds_csv` still covers it, and a missing optional
    dependency should not take the whole suite down.
    """
    holders = []
    for module_name, attribute in (
        ("cron_jobs.cron_closing_lines", "DATA_DIR"),
        ("cron_jobs.master_cron", "DATA_DIR"),
        ("run_pipeline", "DATA_DIR"),
    ):
        try:
            __import__(module_name)
        except Exception:  # noqa: BLE001 - see docstring
            continue
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attribute):
            holders.append((module, attribute))
    return holders


_DATA_DIR_HOLDERS = _data_dir_holders()


@pytest.fixture(autouse=True)
def isolate_current_data_dir(tmp_path, monkeypatch):
    """Point every known `data/current` writer at a per-test tmp directory.

    Autouse, because the write happens deep inside production code that a test
    reaches incidentally -- `persist_raw_odds_csvs` is four calls below
    `scrape_and_shape_odds` -- so opting in would require knowing about a
    coupling that is not visible from the test.

    Module globals are resolved at call time, so patching the attribute is
    enough and the production functions need no change.

    `utils.odds_csv` carries an independent tripwire that raises on a real-path
    write. That guard is the backstop for a writer this list does not know
    about; this fixture is what makes the common case simply work.
    """
    current = tmp_path / "current"
    current.mkdir(parents=True, exist_ok=True)

    for module, attribute in _DATA_DIR_HOLDERS:
        monkeypatch.setattr(module, attribute, str(current), raising=False)

    # The other half of the same bug. Stopping the writes is not enough: five
    # test_book_pause.py cases *read* data/current/{draftkings,fanduel}.csv
    # through book_toggles.stale_books(), which stats their mtime against a
    # 90-minute bound. So they depended on this machine having run the pipeline
    # within the last 90 minutes -- they fail on a fresh clone, where `data/` is
    # gitignored and the files do not exist at all.
    #
    # Giving every test a tmp odds dir with just-written stubs makes both books
    # usable by default and removes the dependency on machine state entirely.
    # Only the mtime is read by the freshness gate, but the real header is
    # written so anything that parses the file sees a valid empty CSV rather
    # than a malformed one.
    #
    # A test that wants to control freshness itself -- BookFreshnessTests sets
    # EDGE_SCORE_ODDS_DIR to its own tmp dir and backdates with os.utime -- still
    # wins: its setUp runs inside this fixture, so its patch.dict takes effect
    # after this setenv.
    header = ",".join(ODDS_CSV_COLUMNS) + "\n"
    for name in ("draftkings.csv", "fanduel.csv"):
        (current / name).write_text(header, encoding="utf-8")
    monkeypatch.setenv("EDGE_SCORE_ODDS_DIR", str(current))

    return current


#
# On the `EDGE_SCORE_ODDS_AGE_GATE=false` setUp duplicated in
# test_price_shopping.py and test_book_pause.py: it is no longer load-bearing,
# because the fixture above gives every test a fresh odds dir, so the gate it
# disables now passes on its own. Both setUps are left in place deliberately --
# they are explicit about intent, their docstrings record why the gate is wrong
# for a harness grading *historical* price selection, and deleting working
# isolation to remove redundancy is a poor trade.

