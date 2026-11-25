import ast
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)
ET_ZONE = ZoneInfo("America/New_York")

ODDS_CSV_COLUMNS = [
    "player",
    "team",
    "prop_type",
    "line",
    "over_odds",
    "under_odds",
    "implied_prob",
    "game",
    "game_date",
    "sportsbook",
    "team_options",
]

def parse_team_options(raw):
    """Read the `team_options` column back into a list of tricodes.

    The column holds a matchup's two clubs for rows whose own team could not be
    determined. It arrives either as a native list (scraper dicts passed
    in-process, e.g. cron_closing_lines) or as the repr pandas wrote to CSV
    ("['UTA', 'MIN']"), so both shapes have to resolve to the same thing.
    Anything unparseable degrades to [], which leaves the matcher exactly as
    unconstrained as it was before the column existed.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(t) for t in raw if t]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return []
            if isinstance(parsed, (list, tuple)):
                return [str(t) for t in parsed if t]
            return []
        # A single tricode written without brackets.
        return [text]
    return []


def _normalize_game_date(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text


def _existing_csv_has_target_date(path: str, target_game_date: str) -> bool:
    if not os.path.exists(path):
        return False

    try:
        existing = pd.read_csv(path, usecols=["game_date"])
    except (ValueError, pd.errors.EmptyDataError, FileNotFoundError):
        return False
    except Exception as exc:
        logger.warning("Could not inspect existing odds CSV (%s): %s", path, exc)
        return False

    if existing.empty or "game_date" not in existing.columns:
        return False

    normalized_target = _normalize_game_date(target_game_date)
    if not normalized_target:
        return False

    existing_dates = {
        _normalize_game_date(value)
        for value in existing["game_date"].dropna().tolist()
    }
    return normalized_target in existing_dates


_REAL_DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _refuse_real_write_under_pytest(path: str) -> None:
    """Stop a test from overwriting a real `backend/data` odds artifact.

    pytest drove this for real. `tests/test_odds_shaping_dates.py` calls
    `cron_closing_lines.scrape_and_shape_odds()`, whose `persist_raw_odds_csvs`
    writes through here, and it patched `SCHEDULE_PATH` but not `DATA_DIR`. The
    result was a header-only `data/current/draftkings.csv`, which then failed five
    `tests/test_book_pause.py` cases through edge_score's odds age gate -- but only
    once the stub was over 90 minutes old, so the suite passed on an immediate
    re-run and failed on the next cold one. Order- and clock-dependent, and
    `data/` is gitignored, so nothing made it visible.

    This is the generalisation `health_ledger._inert()` asked for: its docstring
    names this very bug as the sibling it could not fix from there, on the grounds
    that asking every future test author to remember an env var is not a fix.

    Raises rather than silently skipping. A test that reaches here has an
    isolation bug, and the repair is a tmp dir -- `backend/conftest.py` supplies
    one automatically. A quiet no-op would instead leave that test asserting
    nothing while looking green. Genuine writes opt in with
    ODDS_CSV_ALLOW_REAL_WRITES=1.
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if os.environ.get("ODDS_CSV_ALLOW_REAL_WRITES"):
        return
    try:
        resolved = os.path.realpath(path)
        root = os.path.realpath(_REAL_DATA_ROOT)
    except OSError:
        return
    if resolved == root or resolved.startswith(root + os.sep):
        raise RuntimeError(
            f"refusing to write {path} from a test: it is inside the real "
            f"{_REAL_DATA_ROOT}. Point the writer at a tmp dir (backend/conftest.py "
            f"already redirects cron_closing_lines.DATA_DIR and run_pipeline.DATA_DIR), "
            f"or set ODDS_CSV_ALLOW_REAL_WRITES=1 if the write is genuinely intended."
        )


def write_odds_csv(
    path: str,
    rows,
    *,
    preserve_on_empty: bool = False,
    target_game_date: str | None = None,
    sportsbook_label: str | None = None,
) -> None:
    _refuse_real_write_under_pytest(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame(rows or [])

    if preserve_on_empty and df.empty:
        game_date = target_game_date or datetime.now(ET_ZONE).date().isoformat()
        if _existing_csv_has_target_date(path, game_date):
            book = sportsbook_label or os.path.basename(path)
            logger.warning(
                "Preserving existing %s CSV after empty scrape for %s",
                book,
                game_date,
            )
            return

    if df.empty and not list(df.columns):
        df = pd.DataFrame(columns=ODDS_CSV_COLUMNS)
    df.to_csv(path, index=False)
