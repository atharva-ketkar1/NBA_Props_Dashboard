"""Decide *when* to ask a sportsbook, not just how to ask.

The problem this solves is measured, not theorised. On 2026-10-27 the
DraftKings scrape ran 64 times, and from the outside the traffic looked like
this:

  * all 64 runs started on an exact 5-minute clock boundary (minute mod 5 == 0)
  * 60 of them landed within a 3-second window past that minute, 28 at :03
  * 35 of them ran between 02:00 and 16:00 ET, with no game live or imminent
  * every run fetched the same 11 categories in the same order

That is ~730 requests a day, of which ~385 bought nothing, arriving in
metronomic bursts from one datacenter IP. Akamai scored it and started
refusing -- three escalating waves in eight days (238, then 36/82, then 814
exhausted failures), each recovering less completely than the last.

The fix is mostly not cleverness, it is asking less. The single biggest win is
that over half the daily volume was spent in hours when no NBA game was live or
close to tipping, where a fresh line has no consumer. Cutting that is pure
waste removal: the data loss is zero, and the request budget falls by half.

What the jitter is and is not for
---------------------------------
Breaking the clock alignment matters because a fixed 20-minute period landing
on the same second is the easiest possible thing for a rate heuristic to lock
onto. But jitter alone would not have helped: the volume was the problem, and
spreading 730 requests more artfully is still 730 requests. Jitter is here so
that a *reduced* budget does not still arrive as an obvious drumbeat. Volume
first, pattern second.

Deliberately not done
---------------------
Nothing here touches the request itself -- no header padding, no UA rotation,
no TLS games. There is evidence that would be pointless: plain python-requests
over HTTP/1.1 with five headers and no cookies got a 200 and 272KB from a
residential IP on the same day the VM was refused. The client fingerprint was
never what was being scored, so dressing it up would add complexity for
nothing. Only the IP's request history was.

The pre-tip capture is never delayed or skipped. A closing line that arrives
after tip-off is not a closing line, so `force=True` bypasses every gate here
except the daily budget.
"""

import json
import logging
import os
import random
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("scrape_cadence")

# Knobs are read on use, never captured at import: http_client's load_dotenv()
# runs after this module is imported, so import-time reads would miss
# backend/.env entirely. Same trap the circuit breaker documents.
DEFAULTS = {
    # A runaway guard, NOT the mechanism. ~730/day drew the block; the slate
    # window and the intervals below are what bring the real number down to
    # ~390. This ceiling only catches a bug or a loop, and it is set above the
    # expected day on purpose: when it was tuned down to 260 a simulated slate
    # exhausted it at 20:00 ET and then skipped the 21:00-23:00 live games,
    # which is precisely when lines move. A cap that silences the useful hours
    # is worse than no cap.
    "DK_DAILY_REQUEST_BUDGET": "480",
    # Start paying attention this long before the first tip. Lines that matter
    # for a 7pm game are not set at 4am.
    "DK_PREGAME_HOURS": "8",
    # Keep going a little past the last final, for late line settles.
    "DK_POSTGAME_MINUTES": "45",
    # Close to tip (or mid-game) lines actually move, so look more often.
    "DK_HOT_INTERVAL_MIN": "11",
    "DK_HOT_INTERVAL_MAX": "19",
    # Inside the window but hours out: much slower.
    "DK_COLD_INTERVAL_MIN": "30",
    "DK_COLD_INTERVAL_MAX": "52",
    # "Hot" means any game tipping within this many minutes, or live now.
    "DK_HOT_WINDOW_MINUTES": "150",
    # Random delay before the fetch, so arrivals stop clustering at :02-:04
    # past the 5-minute cron boundary. Kept small on purpose: the intraday
    # path runs this scrape inside a ThreadPoolExecutor with a 120s timeout
    # (cron_closing_lines.py:216) and a healthy 11-category run already spends
    # ~65-75s of that, so a large jitter would start forfeiting whole scrapes
    # to the timeout. Volume is what earned the block; this is a trim, and it
    # is not worth losing data for. Raise it only with that budget in mind.
    "DK_JITTER_MAX_SECONDS": "20",
}

_lock = threading.Lock()


def _num(name):
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else float(DEFAULTS[name])
    except ValueError:
        logger.warning("cadence: %s=%r is not a number, using %s",
                       name, raw, DEFAULTS[name])
        return float(DEFAULTS[name])


def _enabled():
    return (os.environ.get("DK_CADENCE_ENABLED", "true").strip().lower()
            not in {"0", "false", "no", "off"})


def _state_path():
    return os.environ.get("DK_CADENCE_STATE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "scrape_cadence.json",
    )


def _schedule_path():
    return os.environ.get("DK_CADENCE_SCHEDULE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "current", "today_schedule.json",
    )


def _load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError, UnicodeDecodeError):
        return {}


def _save_state(state):
    path = _state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                                   prefix=".scrape_cadence.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        # A lost budget counter must not fail a scrape. It fails open, which is
        # safe here only because the circuit breaker is the backstop.
        logger.warning("cadence: could not persist state: %s", exc)


def _tips(now):
    """Tip-off times for games on the current slate, as aware UTC datetimes.

    A missing or unreadable schedule returns None, which callers treat as "no
    opinion" rather than "no games" -- guessing "no games" would silently
    switch the scraper off for the day.
    """
    try:
        with open(_schedule_path(), "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (FileNotFoundError, ValueError, OSError, UnicodeDecodeError):
        return None

    games = payload.get("games") if isinstance(payload, dict) else payload
    if not isinstance(games, list):
        return None

    out = []
    for game in games:
        if not isinstance(game, dict):
            continue
        raw = game.get("game_time_utc")
        if not raw:
            continue
        try:
            text = str(raw).replace("Z", "+00:00")
            when = datetime.fromisoformat(text)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when = when.astimezone(timezone.utc)
        # The schedule file carries a week of games; only the ones near now
        # describe the slate being priced.
        if abs((when - now).total_seconds()) <= 36 * 3600:
            out.append((when, bool(game.get("is_live")), bool(game.get("is_final"))))
    return out


def _window(now):
    """Classify now against the slate: 'hot', 'cold' or None (outside)."""
    tips = _tips(now)
    if tips is None:
        # No schedule to reason about. Treat as cold rather than refusing, so a
        # missing artifact degrades to "ask slowly", not "never ask".
        return "cold", "no schedule artifact; defaulting to slow cadence"
    if not tips:
        return None, "no games within 36h of now"

    hot_window = timedelta(minutes=_num("DK_HOT_WINDOW_MINUTES"))
    pregame = timedelta(hours=_num("DK_PREGAME_HOURS"))
    postgame = timedelta(minutes=_num("DK_POSTGAME_MINUTES"))

    live = [t for t, is_live, _ in tips if is_live]
    if live:
        return "hot", "a game is live"

    upcoming = [t for t, _, is_final in tips if not is_final and t >= now]
    if upcoming:
        nearest = min(upcoming)
        delta = nearest - now
        if delta <= hot_window:
            return "hot", f"tip in {delta.total_seconds() / 60:.0f} min"
        if delta <= pregame:
            return "cold", f"nearest tip in {delta.total_seconds() / 3600:.1f}h"
        return None, (f"nearest tip is {delta.total_seconds() / 3600:.1f}h out, "
                      f"beyond the {_num('DK_PREGAME_HOURS'):.0f}h pregame window")

    # Everything has tipped. Allow a short tail for late settles.
    last = max(t for t, _, _ in tips)
    if now - last <= postgame + timedelta(hours=3):
        return "cold", "slate finishing; short post-game tail"

    # Nothing upcoming and the newest game is long past. On a normal day the
    # artifact holds a week, so tomorrow's games would be here and we would
    # have returned "beyond the pregame window" above. Reaching this point
    # means the file no longer describes reality -- a stale or truncated
    # schedule. Treat that like a missing one and ask slowly, rather than
    # letting a broken artifact silently switch the book off for a day.
    if now - last > timedelta(hours=24):
        logger.warning(
            "cadence: schedule artifact looks stale (newest game %.0fh in the "
            "past, nothing upcoming); defaulting to slow cadence",
            (now - last).total_seconds() / 3600)
        return "cold", "schedule artifact is stale; defaulting to slow cadence"
    return None, "slate is over for the day"


def _et_day(now):
    """Budget resets on the ET calendar day, matching how the slate is keyed."""
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return now.strftime("%Y-%m-%d")


class Decision:
    def __init__(self, allowed, reason, jitter_seconds=0.0, window=None,
                 spent=0, budget=0):
        self.allowed = allowed
        self.reason = reason
        self.jitter_seconds = jitter_seconds
        self.window = window
        self.spent = spent
        self.budget = budget

    def __repr__(self):
        return (f"Decision(allowed={self.allowed}, window={self.window!r}, "
                f"spent={self.spent}/{self.budget}, reason={self.reason!r})")


def should_scrape_dk(now=None, force=False, expected_requests=11):
    """Decide whether to run the DraftKings scrape now.

    `force` is for the pre-tip closing-line capture: it bypasses the slate
    window and the interval, because a closing line captured late is worthless.
    It still respects the daily budget, which is the one guard that exists to
    stop a runaway.
    """
    now = now or datetime.now(timezone.utc)
    if not _enabled():
        return Decision(True, "cadence control disabled", window="disabled")

    with _lock:
        state = _load_state()
        day = _et_day(now)
        if state.get("day") != day:
            state = {"day": day, "requests": 0}

        spent = int(state.get("requests", 0))
        budget = int(_num("DK_DAILY_REQUEST_BUDGET"))
        if spent + expected_requests > budget:
            return Decision(
                False,
                f"daily budget spent ({spent}/{budget} requests); "
                f"the block followed ~730/day, so this ceiling is the point",
                window="budget", spent=spent, budget=budget)

        window, why = (("hot", "forced") if force else _window(now))
        if not force and window is None:
            return Decision(False, why, window=None, spent=spent, budget=budget)

        # Measured against `now`, not time.time(): the slate logic above is
        # driven by `now`, and mixing the two clocks made the interval gate
        # both untestable and wrong whenever a caller passed an explicit time.
        last = float(state.get("last_scrape_at") or 0)
        gap_needed = float(state.get("next_gap_seconds") or 0)
        waited = now.timestamp() - last
        if not force and last and waited < gap_needed:
            return Decision(
                False,
                f"only {waited / 60:.0f} min since the last scrape, waiting "
                f"{gap_needed / 60:.0f} ({window}: {why})",
                window=window, spent=spent, budget=budget)

        # Draw the NEXT gap now, so the cadence is decided fresh each time
        # rather than being a fixed period anyone could lock onto.
        if window == "hot":
            lo, hi = _num("DK_HOT_INTERVAL_MIN"), _num("DK_HOT_INTERVAL_MAX")
        else:
            lo, hi = _num("DK_COLD_INTERVAL_MIN"), _num("DK_COLD_INTERVAL_MAX")
        next_gap = random.uniform(lo, hi) * 60

        jitter = 0.0 if force else random.uniform(0, _num("DK_JITTER_MAX_SECONDS"))

        state["last_scrape_at"] = now.timestamp()
        state["next_gap_seconds"] = next_gap
        state["requests"] = spent + expected_requests
        state["day"] = day
        _save_state(state)

        return Decision(True, f"{window}: {why}", jitter_seconds=jitter,
                        window=window, spent=spent + expected_requests,
                        budget=budget)


def record_requests(count):
    """Correct the budget after the fact when a run spent more or fewer
    requests than `should_scrape_dk` reserved."""
    if not _enabled() or not count:
        return
    with _lock:
        state = _load_state()
        state["requests"] = max(0, int(state.get("requests", 0)) + int(count))
        _save_state(state)


def status():
    state = _load_state()
    spent = int(state.get("requests", 0))
    budget = int(_num("DK_DAILY_REQUEST_BUDGET"))
    last = float(state.get("last_scrape_at") or 0)
    return {
        "day": state.get("day"),
        "requests_spent": spent,
        "budget": budget,
        "remaining": max(0, budget - spent),
        "minutes_since_last": round((time.time() - last) / 60, 1) if last else None,
        "next_gap_minutes": round(float(state.get("next_gap_seconds") or 0) / 60, 1),
    }


def reset():
    _save_state({})
