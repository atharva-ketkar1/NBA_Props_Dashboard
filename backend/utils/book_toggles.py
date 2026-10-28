"""Pause a sportsbook without erasing what it already published.

A book can become unusable mid-slate -- DraftKings started returning 403 to
both this host's range and the worker's, so every category came back empty and
the preserved CSV went stale while picks kept being scored against it. Pausing
has to stop two things: the doomed scrape, and any new candidate priced off
lines that are no longer real.

It must not stop a third. Picks the channel already published stay in the
tracker and still grade the next day, because the record is of what was
stated, not of which books happen to be reachable now.

    EDGE_SCORE_PAUSED_BOOKS=dk

That switch is manual in both directions, which makes it the wrong tool for an
outage: someone has to notice the book came back and remember to unset it, and
until they do the book stays dark for no reason. So the common case is handled
automatically instead -- `stale_books()` drops a book whose lines have gone
cold and restores it the moment fresh rows land. Reach for the manual pause
only to make a deliberate choice the data cannot infer, such as distrusting a
book that is still answering.
"""

import logging
import os
import time
from typing import Set

logger = logging.getLogger("book_toggles")

BOOK_ALIAS = {
    "dk": "dk",
    "draftkings": "dk",
    "fd": "fd",
    "fanduel": "fd",
}

# The artifact whose last successful write dates a book's lines.
# `write_odds_csv(preserve_on_empty=True)` returns *before* writing when a
# scrape comes back empty, so the file's mtime is the last time real rows
# landed -- exactly the freshness signal, and it needs no schema change.
BOOK_ARTIFACT = {
    "dk": "draftkings.csv",
    "fd": "fanduel.csv",
}


def _max_age_minutes() -> float:
    """How old a book's lines may be and still price a new pick."""
    raw = (os.getenv("EDGE_SCORE_MAX_ODDS_AGE_MINUTES") or "").strip()
    try:
        return float(raw) if raw else 90.0
    except ValueError:
        logger.warning("EDGE_SCORE_MAX_ODDS_AGE_MINUTES=%r is not a number, "
                       "using 90", raw)
        return 90.0


def _artifact_path(book: str) -> str:
    name = BOOK_ARTIFACT.get(book)
    if not name:
        return ""
    base = os.getenv("EDGE_SCORE_ODDS_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "current")
    return os.path.join(base, name)


def book_age_minutes(book: str):
    """Minutes since this book last produced real rows, or None if unknown."""
    path = _artifact_path(_normalize(book))
    if not path:
        return None
    try:
        return (time.time() - os.path.getmtime(path)) / 60.0
    except OSError:
        return None


def stale_books() -> Set[str]:
    """Books whose newest real line is too old to price a new pick from.

    This is the automatic half of pausing, and the reason the manual switch
    should rarely be needed. When DraftKings went dark on 2026-10-28 the
    preserved CSV kept its 11:46 rows all afternoon with no age bound, and
    picks went out against lines built hours earlier. A book drops out here on
    its own once its data goes cold, and comes back on its own the moment a
    scrape writes fresh rows -- no env var to set, and none to remember to
    unset.

    A missing or unreadable artifact counts as stale: the point is to price
    only from lines whose age we can vouch for.
    """
    if (os.getenv("EDGE_SCORE_ODDS_AGE_GATE", "true").strip().lower()
            in {"0", "false", "no", "off"}):
        return set()

    limit = _max_age_minutes()
    stale = set()
    for book in BOOK_ARTIFACT:
        age = book_age_minutes(book)
        if age is None or age > limit:
            stale.add(book)
    return stale


def unusable_books() -> Set[str]:
    """Books that may not price a new pick: manually paused, or gone cold.

    Callers use this instead of `paused_books()` so that the common case --
    a book going dark upstream -- needs no human action in either direction.
    """
    return paused_books() | stale_books()


def _normalize(book: str) -> str:
    return BOOK_ALIAS.get(str(book or "").strip().lower(), str(book or "").strip().lower())


def paused_books() -> Set[str]:
    """Books paused for new scoring, read fresh so a change needs no restart."""
    raw = os.getenv("EDGE_SCORE_PAUSED_BOOKS", "") or ""
    return {_normalize(part) for part in raw.split(",") if part.strip()}


def book_is_paused(book: str) -> bool:
    return _normalize(book) in paused_books()
