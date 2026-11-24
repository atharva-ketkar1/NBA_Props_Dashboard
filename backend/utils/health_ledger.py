"""
health_ledger.py
Per-source health accounting: which units of work are succeeding, which have been
failing, and for how long.

Why this exists
---------------
On 2026-11-20 NBA moved the injury report page. The scraper failed, master_cron
burned its four daily retries, logged "Skipping NBA injury report after repeated
failures" on every tick afterwards, and nothing else happened for three days while
the board blocked 533 of 869 candidates on stale injury context.

Every piece of that was already known to the code. `run_pipeline.py` catches each
scraper's exception, logs FAIL and moves on. `master_cron` counts failures per day
and stops retrying once the budget is spent -- which is precisely the "this will not
get another chance today" signal worth waking someone for. None of it was ever
written down anywhere that outlived the process, so nobody could ask the only
question that mattered: *has this been broken since Thursday?*

This module writes it down. One record per unit of work that is supposed to succeed
on a cadence -- a scraper, a cron job, a database sync, an upstream host -- carrying
last success, consecutive failures, when the current failure run began, and whether
an incident has been opened and announced.

Transient vs. structural
------------------------
The whole value is in *not* alerting. A DraftKings 403 that clears in an hour must
be invisible; the injury report page moving must not be. So a failure is promoted to
an incident only after it clears both a duration floor and an attempt floor, each
scaled to that unit's own cadence (they differ by 300x across this pipeline), plus
three shortcuts for states that are already terminal by construction:

  * the scheduler's daily retry budget exhausted on two separate days
  * three consecutive "successes" that returned zero rows (the shape of the injury
    failure, and of every `preserve_on_empty=True` artifact: the file on disk looks
    fine while being three days old)
  * no successful run at all within the unit's staleness window, which catches a job
    that stopped being *attempted* -- master_cron returns after the first job that
    runs, so a job can be starved rather than failed

What lives elsewhere
--------------------
Nothing here sends anything. `alert_push.py` owns the transport and the wording;
`health_monitor.py` is the glue that evaluates and notifies. That split is
deliberate: `edge_score.py` is 7,000 lines because scoring, Discord formatting,
rate-limit handling and dedup state all landed in one module, and this is the same
shape of problem.

Local runs
----------
Durable writes need SUPABASE_URL and SUPABASE_SERVICE_KEY, so the LOCAL_SAFE prefix
in .claude/skills/run-pipeline-locally (which blanks both) already degrades this to
the file cache and cannot pollute production health state. HEALTH_LEDGER_BACKEND=file
pins that explicitly, mirroring CRON_STATE_BACKEND.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("health")

ET_ZONE = ZoneInfo("America/New_York")

STATE_KEY = "ledger"
TABLE = "pipeline_health"

HOUR = 3600.0

_BACKEND = os.environ.get("HEALTH_LEDGER_BACKEND", "auto").strip().lower()

_table_missing_warned = False

# ---------------------------------------------------------------------------
# The monitored surface.
#
# This registry is the answer to "what can break?", so it is meant to be read as
# documentation. A unit not listed here still records fine and gets DEFAULTS, so
# adding a scraper never requires editing this file -- but an unlisted unit pages on
# generic thresholds, which is usually wrong for something that fails routinely.
#
#   expected_interval_s : how often a healthy unit succeeds.
#   stale_after_s       : no success for this long is an incident on its own.
#                         Set explicitly where 3x the interval is the wrong answer
#                         (the daily pipeline must not need three days).
#   min_breakage_s      : floor under the duration test, so a fast-cadence unit
#                         cannot page on a three-minute wobble.
#   severity            : "page" sends a push. "warn" only shows in the health block
#                         and the watchdog's warnings -- for things that fail often
#                         enough that paging would train you to ignore the channel.
#   empty_rows_matter   : whether a zero-row success is evidence of breakage. False
#                         for anything legitimately empty off-slate.
# ---------------------------------------------------------------------------

DEFAULTS = {
    "expected_interval_s": 24 * HOUR,
    "stale_after_s": None,          # falls back to max(3 * interval, min_breakage)
    "min_breakage_s": 3 * HOUR,
    "severity": "page",
    "empty_rows_matter": False,
    "dormancy_exempt": False,
}

UNITS = {
    # --- cron jobs -----------------------------------------------------------
    "job:pipeline": {
        "label": "Daily pipeline",
        "expected_interval_s": 24 * HOUR,
        # One missed daily run plus the retry cooldown, not three days.
        "stale_after_s": 26 * HOUR,
        "min_breakage_s": 2 * HOUR,
        # The pipeline is what writes the schedule every other dormancy check would
        # read, so it must never be silenced by a dormancy signal it produces.
        "dormancy_exempt": True,
    },
    "job:intraday": {
        "label": "Intraday odds snapshot",
        "expected_interval_s": 1800,
        "stale_after_s": 3 * HOUR,
        "min_breakage_s": 45 * 60,
    },
    "job:closing_lines": {
        "label": "Closing-line capture",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 36 * HOUR,
        "severity": "warn",         # no games, no captures; too slate-dependent to page
    },
    "job:game_status": {
        "label": "Game status refresh",
        "expected_interval_s": 300,
        "stale_after_s": 2 * HOUR,
        "min_breakage_s": 45 * 60,
        "severity": "warn",
    },
    "job:action_network": {
        "label": "Action Network refresh",
        "expected_interval_s": HOUR,
        "min_breakage_s": 3 * HOUR,
        "severity": "warn",
    },
    "job:master_cron": {
        "label": "Scheduler tick",
        "expected_interval_s": 300,
        "stale_after_s": HOUR,
        "min_breakage_s": 30 * 60,
        "dormancy_exempt": True,
    },

    # --- scrapers ------------------------------------------------------------
    # The 2026-11-20 incident. Hourly cadence, 15-minute freshness target inside the
    # lock-sensitive window, and a stale report silently blocks most of the board --
    # so this is the shortest page-worthy window of any scraper here.
    "scraper:nba_injury_report": {
        "label": "NBA injury report",
        "expected_interval_s": HOUR,
        "stale_after_s": 3 * HOUR,
        "min_breakage_s": 2.5 * HOUR,
        "empty_rows_matter": True,
    },
    "scraper:schedule": {
        "label": "Game schedule",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 26 * HOUR,
        "min_breakage_s": 2 * HOUR,
        "dormancy_exempt": True,
    },
    "scraper:season_stats": {
        "label": "Season stats",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 26 * HOUR,
        "min_breakage_s": 2 * HOUR,
        "empty_rows_matter": True,
    },
    "scraper:draftkings": {
        "label": "DraftKings odds",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 2 * 24 * HOUR,
        # A day of DK 403s is a Tuesday. Two is an egress block worth knowing about,
        # and the circuit breaker is the faster signal for that anyway.
        "min_breakage_s": 2 * 24 * HOUR,
        "empty_rows_matter": True,
    },
    "scraper:fanduel": {
        "label": "FanDuel odds",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 2 * 24 * HOUR,
        "min_breakage_s": 2 * 24 * HOUR,
        "empty_rows_matter": True,
    },
    "scraper:prizepicks": {
        "label": "PrizePicks odds",
        "expected_interval_s": 24 * HOUR,
        # Behind ENABLE_PRIZEPICKS and needs live cookie/CSRF values that expire by
        # design. Paging on this would be paging on a known manual chore.
        "severity": "warn",
    },
    "scraper:gamelogs": {
        "label": "Game logs",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 3 * 24 * HOUR,
        # Game logs lag upstream routinely; that is why they are not critical_scrapers.
        "min_breakage_s": 2 * 24 * HOUR,
    },
    "scraper:boxscores": {
        "label": "Box scores",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 3 * 24 * HOUR,
        "min_breakage_s": 2 * 24 * HOUR,
        "severity": "warn",
    },
    # The spatial and play-style overlays. None of them breaks a slate on its own,
    # but they feed ~69 model features, so a week of silence degrades every
    # projection without anything looking wrong. Floors are two days: long enough
    # that a bad afternoon at stats.nba.com is invisible.
    **{
        f"scraper:{name}": {
            "label": label,
            "expected_interval_s": 24 * HOUR,
            "stale_after_s": 3 * 24 * HOUR,
            "min_breakage_s": 2 * 24 * HOUR,
            "empty_rows_matter": True,
        }
        for name, label in (
            ("shooting_zones", "Shooting zones"),
            ("assist_zones", "Assist zones"),
            ("opp_assist_zones", "Opponent assist zones"),
            ("opp_def_zones", "Opponent defense zones"),
            ("shot_type", "Shot type analysis"),
            ("opp_shot_type", "Opponent shot type analysis"),
            ("play_type", "Play type analysis"),
        )
    },

    # --- loads and syncs -----------------------------------------------------
    # Supabase is the only durable copy: data/current/ is scratch the next run
    # overwrites and the VM is disposable, so a failing sync loses days permanently.
    "sync:supabase_props": {
        "label": "Supabase props sync",
        "expected_interval_s": 24 * HOUR,
        "stale_after_s": 26 * HOUR,
        "min_breakage_s": 2 * HOUR,
        "dormancy_exempt": True,
    },
    "sync:supabase_edge": {
        "label": "Signal Score sync",
        "expected_interval_s": 1800,
        "stale_after_s": 3 * HOUR,
        "min_breakage_s": HOUR,
    },
    "score:edge_refresh": {
        "label": "Signal Score refresh",
        "expected_interval_s": 1800,
        "stale_after_s": 3 * HOUR,
        "min_breakage_s": HOUR,
    },
    # NOTE: no model-load unit yet. A missing or corrupt .cbm after a bad deploy is
    # exactly the kind of lasting failure this module is for, but edge_score handles
    # a dark ML component gracefully by design (it is dark all of early season), so
    # catching it needs an explicit recorder inside ml_inference rather than a
    # registry entry. Left out instead of declared-and-unrecorded, so this registry
    # never claims coverage it does not have.
}


# Units whose names are discovered at runtime rather than listed above. Upstream
# hosts come from the circuit breaker, which learns them from whatever http_client
# was asked to fetch, so there is no fixed list to enumerate.
PREFIX_DEFAULTS = {
    "upstream:": {
        # health_monitor folds breaker state in once per 5-minute tick.
        "expected_interval_s": 300,
        # Only the failure-duration rule should apply. A host we have simply stopped
        # asking about -- a book turned off, a scraper retired -- must not page
        # forever just because nothing has succeeded against it lately.
        "stale_after_s": 365 * 24 * HOUR,
        "min_breakage_s": 2 * HOUR,
    },
}


def _config(unit):
    merged = dict(DEFAULTS)
    for prefix, overrides in PREFIX_DEFAULTS.items():
        if unit.startswith(prefix):
            merged.update(overrides)
            merged["label"] = f"{unit.split(':', 1)[1]} (upstream)"
            break
    merged.update(UNITS.get(unit, {}))
    merged.setdefault("label", unit)
    if not merged.get("stale_after_s"):
        merged["stale_after_s"] = max(3 * merged["expected_interval_s"],
                                      merged["min_breakage_s"])
    return merged


def _env_int(name, fallback, minimum=0):
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(float(raw)) if raw else fallback
    except ValueError:
        logger.warning("health: %s=%r is not a number, using %s", name, raw, fallback)
        value = fallback
    return max(minimum, value)


def _failure_threshold():
    """Consecutive failed attempts before the duration test is even consulted."""
    return _env_int("HEALTH_MIN_CONSECUTIVE_FAILURES", 3, 1)


def _empty_threshold():
    return _env_int("HEALTH_MIN_EMPTY_RUNS", 3, 1)


def _dormant_months():
    """Calendar months in which staleness is expected and never paged.

    Deliberately calendar-based rather than read from today_schedule.json. A
    schedule-derived check has a feedback loop that defeats the whole module: if the
    schedule scraper breaks, there are no games on file, the ledger reads "offseason"
    and suppresses the alert about the thing that caused it. The NBA plays October
    through June, so July-September is safe to assume quiet without asking anything
    that can itself fail.
    """
    raw = (os.environ.get("HEALTH_DORMANT_MONTHS") or "7,8,9").strip()
    months = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            months.add(int(part))
        except ValueError:
            logger.warning("health: HEALTH_DORMANT_MONTHS has non-numeric %r", part)
    return months


def is_dormant(now=None):
    moment = now or datetime.now(ET_ZONE)
    return moment.month in _dormant_months()


# ---------------------------------------------------------------------------
# Storage. Supabase is the source of truth, the local file is a cache, and a
# missing table degrades to file-only -- the same contract as cron_state_store,
# so deploying this before applying the migration is safe.
# ---------------------------------------------------------------------------

def _inert():
    """True when this process must not touch real health state at all.

    The recording calls sit inside the except blocks of fourteen scrapers and six
    cron jobs, which means the existing test suite drives them: measured here,
    `tests/test_injury_report_retry_budget.py` alone wrote a record claiming the
    injury report had failed eight times and burned its retry budget -- a wholly
    fabricated incident, in the real ledger, which `--report` then displayed and an
    armed notifier would have pushed to a phone.

    The project already has this bug in another form (pytest overwrites the
    data/current odds CSVs), so asking every future test to remember an env var is
    not a fix. A test that genuinely wants to exercise the ledger sets
    HEALTH_LEDGER_STATE_PATH, as tests/test_health_ledger.py does; every other test
    gets a ledger that reads empty and writes nowhere.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get(
        "HEALTH_LEDGER_STATE_PATH")


def _state_path():
    if _inert():
        return None
    return os.environ.get("HEALTH_LEDGER_STATE_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "logs", "health_ledger.json",
    )


def _db_enabled():
    if _BACKEND == "file" or _inert():
        return False
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def _client():
    from utils.supabase_client import get_supabase_client
    return get_supabase_client()


def _is_missing_table(exc):
    text = str(exc).lower()
    return ("pgrst205" in text or "could not find the table" in text
            or "does not exist" in text)


def _warn_table_missing_once():
    global _table_missing_warned
    if not _table_missing_warned:
        _table_missing_warned = True
        logger.warning(
            "pipeline_health table not found - health ledger is file-only, so streaks "
            "reset if the VM is replaced. Apply "
            "supabase/migrations/20261123_pipeline_health.sql."
        )


def load():
    """Return {unit: record}. Any damage reads as "no history" rather than raising:
    a corrupt ledger must not be able to stop a scrape."""
    if _db_enabled():
        try:
            resp = (_client().table(TABLE).select("state")
                    .eq("key", STATE_KEY).limit(1).execute())
            rows = getattr(resp, "data", None) or []
            if rows:
                stored = rows[0].get("state")
                if isinstance(stored, str):
                    stored = json.loads(stored)
                if isinstance(stored, dict):
                    return {k: v for k, v in stored.items() if isinstance(v, dict)}
        except Exception as exc:
            if _is_missing_table(exc):
                _warn_table_missing_once()
            else:
                logger.error("health: could not read ledger from Supabase: %s", exc)

    path = _state_path()
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                return {k: v for k, v in raw.items() if isinstance(v, dict)}
        except Exception as exc:
            logger.error("health: could not read local ledger: %s", exc)
    return {}


def save(ledger):
    """Persist to Supabase and to the file cache. Returns True if the durable write
    landed. Never raises: losing a health write is better than failing the run it was
    describing."""
    durable = False
    if _db_enabled():
        try:
            _client().table(TABLE).upsert(
                {"key": STATE_KEY, "state": ledger}, on_conflict="key",
            ).execute()
            durable = True
        except Exception as exc:
            if _is_missing_table(exc):
                _warn_table_missing_once()
            else:
                logger.error("health: could not persist ledger to Supabase: %s", exc)

    path = _state_path()
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(ledger, handle, indent=2, sort_keys=True)
        except Exception as exc:
            logger.error("health: could not write local ledger: %s", exc)
    return durable


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def _blank(unit):
    config = _config(unit)
    return {
        "unit": unit,
        "label": config["label"],
        "last_success_ts": 0.0,
        "last_success_rows": None,
        "last_attempt_ts": 0.0,
        "consecutive_failures": 0,
        "first_failure_ts": 0.0,
        "last_error": "",
        "empty_streak": 0,
        "exhausted_dates": [],
        "state": "ok",
        "incident_opened_ts": 0.0,
        "incident_notified_ts": 0.0,
        "incident_reason": "",
        "detail": "",
    }


def _truncate(text, limit=400):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe(error):
    if isinstance(error, BaseException):
        return _truncate(f"{type(error).__name__}: {error}")
    return _truncate(error)


def _mutate(unit, fn):
    """Read-modify-write one record, writing only if it changed.

    Each cron tick is a fresh process, so there is no in-memory ledger to amend;
    every record call is its own round trip. Skipping unchanged writes keeps that
    from being 288 pointless upserts a day.

    Swallows everything. These calls sit inside the `except` blocks of fourteen
    scrapers and six cron jobs; a monitor that can raise from there would convert a
    recoverable scraper failure into a lost slate, which is a strictly worse bug
    than the blindness it is meant to fix.
    """
    try:
        ledger = load()
        before = ledger.get(unit)
        record = dict(before) if isinstance(before, dict) else _blank(unit)
        record.setdefault("unit", unit)
        record["label"] = _config(unit)["label"]
        fn(record)
        if record == before:
            return record
        ledger[unit] = record
        save(ledger)
        return record
    except Exception as exc:
        logger.warning("health: could not record %s: %s", unit, exc)
        return {}


def record_success(unit, *, rows=None, detail=None, now=None):
    """A unit of work completed.

    `rows` is how many rows it produced, where the caller already knows -- a success
    that produced nothing is the failure mode that looks healthy from the outside, so
    it is tracked separately from exceptions. Pass None to leave the empty-run streak
    alone; the generic recorder in run_pipeline does that, so a wrapper that already
    reported a real count is not overwritten by the loop around it.
    """
    moment = now if now is not None else time.time()

    def apply(record):
        record["last_success_ts"] = moment
        record["last_attempt_ts"] = moment
        record["consecutive_failures"] = 0
        record["first_failure_ts"] = 0.0
        record["last_error"] = ""
        # The retry budget is per-day and master_cron zeroes its own counter on a
        # success, so the ledger must forget the spent days too. Without this, a
        # unit that was budget-exhausted twice stayed `broken` for three days after
        # the fix landed -- a monitor that cannot tell you something is fixed is one
        # you stop believing.
        record["exhausted_dates"] = []
        if detail is not None:
            record["detail"] = _truncate(detail, 200)
        if rows is not None:
            record["last_success_rows"] = int(rows)
            if int(rows) <= 0 and _config(unit)["empty_rows_matter"]:
                record["empty_streak"] = int(record.get("empty_streak", 0)) + 1
            else:
                record["empty_streak"] = 0

    return _mutate(unit, apply)


def record_failure(unit, error, *, detail=None, now=None):
    """A unit of work raised, or returned a definite failure."""
    moment = now if now is not None else time.time()

    def apply(record):
        record["last_attempt_ts"] = moment
        record["consecutive_failures"] = int(record.get("consecutive_failures", 0)) + 1
        if not record.get("first_failure_ts"):
            record["first_failure_ts"] = moment
        record["last_error"] = _describe(error)
        if detail is not None:
            record["detail"] = _truncate(detail, 200)

    return _mutate(unit, apply)


def record_budget_exhausted(unit, date_str, *, detail=None, now=None):
    """The scheduler has spent this unit's retry budget for `date_str`.

    This is the signal the 2026-11-20 incident was screaming and nobody heard: not
    "a request failed" but "no further attempt will be made today". One such day is
    weather. Two is a contract that has changed, so `classify` promotes on the
    second -- no duration test, because waiting for one would just be waiting for
    tomorrow's ticks to not happen either.
    """
    moment = now if now is not None else time.time()

    def apply(record):
        dates = [d for d in record.get("exhausted_dates", []) if isinstance(d, str)]
        if date_str not in dates:
            dates.append(date_str)
        record["exhausted_dates"] = sorted(dates)[-6:]
        record["last_attempt_ts"] = moment
        if detail is not None:
            record["detail"] = _truncate(detail, 200)

    return _mutate(unit, apply)


# ---------------------------------------------------------------------------
# The promotion rule. Pure function of (record, now, dormant) so it is testable
# against a fake clock without touching Supabase or the filesystem.
# ---------------------------------------------------------------------------

def _recent_exhausted_days(record, now):
    """Distinct days inside the last three on which the retry budget was spent."""
    cutoff = (datetime.fromtimestamp(now, ET_ZONE) - timedelta(days=3)).date()
    days = set()
    for value in record.get("exhausted_dates", []):
        try:
            parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if parsed >= cutoff:
            days.add(parsed)
    return sorted(days)


def _hours(seconds):
    return seconds / 3600.0


def classify(record, now, *, dormant=False, config=None):
    """Return (state, reason) for one record.

    states: "ok", "degraded" (something is wrong, not yet worth waking anyone),
    "broken" (structural: it will not fix itself).
    """
    unit = record.get("unit") or ""
    config = config or _config(unit)
    failures = int(record.get("consecutive_failures", 0) or 0)
    first_failure = float(record.get("first_failure_ts", 0) or 0)
    last_success = float(record.get("last_success_ts", 0) or 0)
    empty_streak = int(record.get("empty_streak", 0) or 0)

    suppress = dormant and not config["dormancy_exempt"]

    # 1. Budget exhausted on two separate recent days: already terminal.
    exhausted_days = _recent_exhausted_days(record, now)
    if len(exhausted_days) >= 2 and not suppress:
        return "broken", (
            f"retry budget exhausted on {len(exhausted_days)} separate days "
            f"({', '.join(d.isoformat() for d in exhausted_days)}); the scheduler has "
            f"stopped attempting it"
        )

    # 2. Succeeding, but producing nothing. The artifact on disk looks fine.
    if empty_streak >= _empty_threshold() and not suppress:
        return "broken", (
            f"{empty_streak} consecutive runs returned zero rows, so the artifact is "
            f"being preserved rather than refreshed"
        )

    # 3. Failing hard, long enough and often enough to not be a wobble.
    if failures >= _failure_threshold() and first_failure:
        failing_for = max(0.0, now - first_failure)
        floor = max(3 * config["expected_interval_s"], config["min_breakage_s"])
        if failing_for >= floor and not suppress:
            return "broken", (
                f"{failures} consecutive failures over {_hours(failing_for):.1f}h "
                f"(floor {_hours(floor):.1f}h); last error: "
                f"{record.get('last_error') or 'unknown'}"
            )

    # 4. No success in the staleness window. Catches a unit that stopped being
    #    attempted at all, which no failure counter can see.
    if last_success:
        stale_for = max(0.0, now - last_success)
        if stale_for >= config["stale_after_s"] and not suppress:
            return "broken", (
                f"no successful run in {_hours(stale_for):.1f}h "
                f"(expected every {_hours(config['expected_interval_s']):.1f}h)"
            )
    elif failures >= _failure_threshold() and not suppress:
        # Never succeeded and failing: a first deploy of something misconfigured.
        return "broken", (
            f"has never succeeded, {failures} consecutive failures; last error: "
            f"{record.get('last_error') or 'unknown'}"
        )

    if failures > 0 or empty_streak > 0 or exhausted_days:
        reason_bits = []
        if failures:
            reason_bits.append(f"{failures} consecutive failures")
        if empty_streak:
            reason_bits.append(f"{empty_streak} empty runs")
        if exhausted_days:
            reason_bits.append(f"budget spent {len(exhausted_days)}d")
        return "degraded", ", ".join(reason_bits)

    return "ok", ""


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _reminder_seconds():
    raw = (os.environ.get("HEALTH_REMINDER_HOURS") or "24").strip()
    try:
        hours = float(raw) if raw else 24.0
    except ValueError:
        hours = 24.0
    return max(0.0, hours) * HOUR


def evaluate(now=None, dormant=None):
    """Re-classify every record and return the notifications that are due.

    Returns (actions, ledger). Each action is a dict with `kind` in
    {"open", "reminder", "recovered"}, the record, and a human reason. Nothing is
    sent here and nothing is marked as notified -- `health_monitor` does that only
    after a push actually lands, so a failed push is retried on the next tick
    instead of being silently swallowed.
    """
    moment = now if now is not None else time.time()
    quiet = is_dormant() if dormant is None else dormant
    ledger = load()
    actions = []
    changed = False

    for unit, stored in list(ledger.items()):
        if not isinstance(stored, dict):
            continue
        record = dict(stored)
        record.setdefault("unit", unit)
        config = _config(unit)
        state, reason = classify(record, moment, dormant=quiet, config=config)
        previous_state = record.get("state", "ok")
        notified_ts = float(record.get("incident_notified_ts", 0) or 0)

        if state == "broken":
            if previous_state != "broken":
                record["incident_opened_ts"] = moment
                record["incident_notified_ts"] = 0.0
                notified_ts = 0.0
            record["incident_reason"] = reason
            if config["severity"] == "page":
                reminder_after = _reminder_seconds()
                if not notified_ts:
                    actions.append({"kind": "open", "record": record,
                                    "reason": reason, "config": config,
                                    "now": moment})
                elif reminder_after and moment - notified_ts >= reminder_after:
                    actions.append({"kind": "reminder", "record": record,
                                    "reason": reason, "config": config,
                                    "now": moment})
        elif previous_state == "broken":
            # Only announce a recovery that was announced as a failure, so the first
            # thing this module ever says is never "all clear".
            if notified_ts and config["severity"] == "page":
                actions.append({"kind": "recovered", "record": record,
                                "reason": reason or "succeeding again",
                                "config": config, "now": moment,
                                "down_for_s": max(0.0, moment - float(
                                    record.get("incident_opened_ts", 0) or moment))})
            record["incident_opened_ts"] = 0.0
            record["incident_notified_ts"] = 0.0
            record["incident_reason"] = ""

        record["state"] = state
        if record != stored:
            ledger[unit] = record
            changed = True

    if changed:
        save(ledger)
    return actions, ledger


def mark_notified(units, now=None):
    """Stamp the units whose push actually landed."""
    moment = now if now is not None else time.time()
    units = [u for u in units if u]
    if not units:
        return
    ledger = load()
    changed = False
    for unit in units:
        record = ledger.get(unit)
        if isinstance(record, dict):
            record["incident_notified_ts"] = moment
            changed = True
    if changed:
        save(ledger)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _age_hours(timestamp, now):
    if not timestamp:
        return None
    return round(max(0.0, now - float(timestamp)) / 3600.0, 1)


def snapshot(now=None, ledger=None):
    """A compact health block for the Signal Score payload, and through it /api/edge.

    This is what turns the watchdog from one hand-written check per past incident
    into a single generic one. Deliberately small: it rides on a payload the
    dashboard already serves, so it costs no extra request and no new credential.
    """
    moment = now if now is not None else time.time()
    records = ledger if ledger is not None else load()
    broken, degraded = [], []
    for unit, record in sorted(records.items()):
        if not isinstance(record, dict):
            continue
        state = record.get("state", "ok")
        if state not in {"broken", "degraded"}:
            continue
        entry = {
            "unit": unit,
            "label": record.get("label") or unit,
            "severity": _config(unit)["severity"],
            "reason": _truncate(record.get("incident_reason")
                                or record.get("last_error"), 240),
            "consecutive_failures": int(record.get("consecutive_failures", 0) or 0),
            "last_success_age_h": _age_hours(record.get("last_success_ts"), moment),
        }
        (broken if state == "broken" else degraded).append(entry)

    return {
        "evaluated_at": datetime.fromtimestamp(moment, ET_ZONE).isoformat(),
        "monitored_units": len(records),
        "broken_count": len(broken),
        "degraded_count": len(degraded),
        "pageable_broken_count": sum(1 for e in broken if e["severity"] == "page"),
        "broken": broken,
        "degraded": degraded,
        "dormant": is_dormant(datetime.fromtimestamp(moment, ET_ZONE)),
    }
