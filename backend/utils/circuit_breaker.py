"""Stop hammering an upstream that has blocked this host's egress.

Why this is not just "retry fewer times"
----------------------------------------
Measured on 2026-10-28, DraftKings' edge (Akamai: `text/html` "Access Denied",
`errors.edgesuite.net`) refused every request from both the VM's range and the
worker's. The same URL returned 200 from a residential IP, and every state site
path -- US-IL-SB, US-VA-SB, US-NJ-SB, US-SB -- returned a byte-identical
denial, so neither the path nor a credential was ever involved: there is no key
on that endpoint. The block is scored against the egress IP.

`http_client` retries 403 on purpose -- for nba.com a 403 is usually a stale UA
from the worker's pool and re-rolling it succeeds, which is the most valuable
behaviour in that module. Against an IP-level block the same policy inverted:
eleven DraftKings categories times six attempts was 66 doomed requests per run,
every one of them feeding `_note_throttled` until the global pacing hit its 12s
ceiling and slowed FanDuel and nba.com alongside it.

The block is transient, which is the whole reason to back off rather than give
up. Oct 21-22 produced 238 then 74 exhausted failures; Oct 23 then pulled 1129
props with zero 403s. Volume is what keeps a reputation score bad, so the
cheapest way to get DraftKings back is to stop asking for a while.

What this does NOT do
---------------------
It does not sniff response bodies to classify a 403 as "permanent". cdn.nba.com
and stats.nba.com sit behind Akamai too, so an "Access Denied" body is not
evidence that re-rolling the UA would fail -- body-matching would disable the
one retry that module depends on. This trips on *behaviour* instead: a host that
has exhausted every attempt N times in a row is blocking us, whatever it says.

State lives on disk because each cron tick is a fresh process. An in-memory
breaker would reset every five minutes and never actually hold a cooldown.
"""

import json
import logging
import os
import random
import tempfile
import threading
import time

logger = logging.getLogger("http_client")

# Every knob is read on use, never captured at import. Two reasons: http_client
# imports this module *above* its own load_dotenv() call, so import-time reads
# would miss anything set only in backend/.env; and a cooldown is something you
# want to shorten during an incident without restarting the scheduler.

DEFAULTS = {
    # Three consecutive exhaustions, not one: a single category dying is
    # ordinary upstream flakiness, three in a row is the host refusing us.
    "PROXY_BREAKER_TRIP_AFTER": "3",
    # First cooldown. Long enough to stop feeding the reputation score, short
    # enough that a slate is not written off over a brief edge wobble.
    "PROXY_BREAKER_COOLDOWN": "900",
    # Each further trip doubles the wait, so a host that stays hostile is asked
    # about progressively less often rather than every tick forever.
    "PROXY_BREAKER_COOLDOWN_CAP": "7200",
}


def _num(name):
    """Read a numeric knob, falling back to the default if it is unparseable."""
    raw = (os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else float(DEFAULTS[name])
    except ValueError:
        logger.warning("circuit breaker: %s=%r is not a number, using %s",
                       name, raw, DEFAULTS[name])
        value = float(DEFAULTS[name])
    return value


def _enabled():
    return (os.environ.get("PROXY_BREAKER_ENABLED", "true").strip().lower()
            not in {"0", "false", "no", "off"})


def _trip_after():
    return max(1, int(_num("PROXY_BREAKER_TRIP_AFTER")))


def _state_path():
    return os.environ.get("PROXY_BREAKER_STATE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "circuit_breaker.json",
    )


_lock = threading.Lock()


class CircuitOpen(RuntimeError):
    """The breaker is holding a cooldown for this host; no request was made.

    `http_client` re-raises this as an `UpstreamFailed` subclass so that every
    caller's existing `except UpstreamFailed` already handles it -- a paused
    host looks exactly like an upstream that failed, which is what it is.
    """


def _now():
    return time.time()


def _load():
    """Read breaker state, treating any damage as "no state"."""
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save(state):
    """Atomic replace. A torn breaker file would read as "no cooldown" and let
    the retry storm straight back out, so never write in place."""
    path = _state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(path), prefix=".circuit_breaker.", suffix=".tmp")
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
        # Losing the cooldown is bad but not worth failing a scrape over.
        logger.warning("circuit breaker: could not persist state: %s", exc)


def _cooldown_for(trips):
    """Exponential in the number of consecutive trips, capped, with jitter so
    a fleet of callers does not re-probe in lockstep."""
    span = min(_num("PROXY_BREAKER_COOLDOWN") * (2 ** max(0, trips - 1)),
               _num("PROXY_BREAKER_COOLDOWN_CAP"))
    return span * random.uniform(0.9, 1.1)


def check(host):
    """Raise CircuitOpen if `host` is in cooldown, else return.

    Returns True when this call is the half-open probe (the cooldown has just
    elapsed), so the caller can spend a single cheap attempt instead of the
    full retry budget on a host that is probably still blocking.
    """
    if not _enabled():
        return False
    with _lock:
        entry = _load().get(host)
        if not entry or not entry.get("open_until"):
            return False
        remaining = float(entry.get("open_until") or 0) - _now()
        if remaining <= 0:
            return True
        raise CircuitOpen(
            f"{host}: egress is blocked upstream; backing off for another "
            f"{remaining / 60:.0f} min (trip {entry.get('trips', 1)}, "
            f"{entry.get('failures', 0)} consecutive failures). "
            f"Not retried because volume is what sustains the block."
        )


def record_failure(host, detail=""):
    """Count one *exhausted* failure. Trips the breaker on the Nth in a row."""
    if not _enabled():
        return
    with _lock:
        state = _load()
        entry = state.get(host) or {}
        failures = int(entry.get("failures", 0)) + 1
        entry["failures"] = failures
        entry["last_failure_at"] = _now()
        if detail:
            entry["last_detail"] = detail[:300]

        trip_after = _trip_after()
        if failures >= trip_after:
            trips = int(entry.get("trips", 0)) + 1
            cooldown = _cooldown_for(trips)
            entry["trips"] = trips
            entry["open_until"] = _now() + cooldown
            entry["failures"] = 0
            logger.error(
                "circuit breaker OPEN for %s after %d consecutive failures: "
                "pausing all requests for %.0f min (trip %d). %s",
                host, failures, cooldown / 60, trips, detail or "")
        else:
            logger.warning(
                "circuit breaker: %s failure %d/%d before backing off",
                host, failures, trip_after)
        state[host] = entry
        _save(state)


def record_success(host):
    """A success clears everything: the host is serving this egress again."""
    if not _enabled():
        return
    with _lock:
        state = _load()
        entry = state.get(host)
        if not entry:
            return
        was_open = bool(entry.get("open_until"))
        if was_open or entry.get("failures") or entry.get("trips"):
            logger.info("circuit breaker CLOSED for %s: upstream is answering "
                        "again, cleared %d trip(s)", host, int(entry.get("trips", 0)))
        state.pop(host, None)
        _save(state)


def status():
    """Current breaker state, for diagnostics."""
    out = {}
    for host, entry in _load().items():
        remaining = float(entry.get("open_until") or 0) - _now()
        out[host] = {
            "open": remaining > 0,
            "minutes_remaining": round(max(0.0, remaining) / 60, 1),
            "trips": int(entry.get("trips", 0)),
            "consecutive_failures": int(entry.get("failures", 0)),
            "last_detail": entry.get("last_detail", ""),
        }
    return out


def reset(host=None):
    """Clear one host or all of them. For ops and tests."""
    with _lock:
        if host is None:
            _save({})
            return
        state = _load()
        state.pop(host, None)
        _save(state)
