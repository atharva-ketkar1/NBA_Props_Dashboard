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
"""

import os
from typing import Set


BOOK_ALIAS = {
    "dk": "dk",
    "draftkings": "dk",
    "fd": "fd",
    "fanduel": "fd",
}


def _normalize(book: str) -> str:
    return BOOK_ALIAS.get(str(book or "").strip().lower(), str(book or "").strip().lower())


def paused_books() -> Set[str]:
    """Books paused for new scoring, read fresh so a change needs no restart."""
    raw = os.getenv("EDGE_SCORE_PAUSED_BOOKS", "") or ""
    return {_normalize(part) for part in raw.split(",") if part.strip()}


def book_is_paused(book: str) -> bool:
    return _normalize(book) in paused_books()
