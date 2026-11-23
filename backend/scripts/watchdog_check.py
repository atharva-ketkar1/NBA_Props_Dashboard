#!/usr/bin/env python3
"""
watchdog_check.py
Is the pipeline still alive? Answered from outside the VM.

Why this exists
---------------
The previous host (a GCP VM) died quietly and the dashboard went stale for
weeks before anyone noticed. Nothing was watching. The failure mode that
matters here is not a crash -- the cron logs a crash loudly -- it is silence:
the box is deleted, the disk fills, cron stops, the network dies, or the
Cloudflare Worker starts refusing every upstream. In all of those cases the
pipeline writes nothing and nobody is told.

So this check deliberately runs *off* the VM, in GitHub Actions, and looks only
at what a real visitor to the dashboard can see. It needs no SSH key and no
Supabase credentials, which means the watchdog cannot itself be taken down by
the same credential or host problem it is meant to catch.

The heartbeat
-------------
`/api/edge` carries `generated_at`, stamped every time Edge Score refreshes.
master_cron refreshes it on the intraday cycle, which on this deployment is
every 900s inside the active window (3h pre-tip to 2.5h post-tip) and every
1800s when quiet -- so a healthy deployment never exceeds ~30 minutes between
stamps, whatever the hour. STALE_MINUTES defaults to 90, roughly three missed
quiet-period cycles, which tolerates one slow run plus a retry without paging.

Offseason
---------
Between seasons there is nothing to refresh, so staleness is expected and
alerting on it would train you to ignore this. If the schedule shows no games
within UPCOMING_WINDOW_DAYS, the staleness check downgrades to informational
and the job passes.

Exit codes: 0 = healthy (or dormant), 1 = something is wrong.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = os.environ.get("WATCHDOG_BASE_URL", "https://propx-dashboard.vercel.app").rstrip("/")
STALE_MINUTES = int(os.environ.get("WATCHDOG_STALE_MINUTES", "90"))
UPCOMING_WINDOW_DAYS = int(os.environ.get("WATCHDOG_UPCOMING_WINDOW_DAYS", "3"))
TIMEOUT_SECONDS = int(os.environ.get("WATCHDOG_TIMEOUT_SECONDS", "30"))
# Three consecutive missed hourly refreshes. Sized against the pipeline's own
# behaviour, not against this workflow's cadence: steady state is under 20 minutes,
# one missed tick is ~70 and two ~135, so 180 is out of reach of jitter while sitting
# 25x below the 4,480 minutes the 2026-11-20 outage reached.
INJURY_STALE_MINUTES = int(os.environ.get("WATCHDOG_INJURY_STALE_MINUTES", "180"))

# The API rejects requests without this (see frontend/api/_lib/http.ts).
# It is a client-identity marker, not a credential, and is public in the repo.
APP_CLIENT_HEADER = {"x-propx-client": "web", "sec-fetch-site": "same-origin"}

problems = []   # fails the job
warnings = []   # reported, does not fail
notes = []      # context


def _get(path, parse_json=True):
    """Fetch a path, returning (status, payload_or_text, error_string)."""
    url = f"{BASE_URL}{path}"
    request = urllib.request.Request(url, headers={**APP_CLIENT_HEADER,
                                                   "accept": "application/json",
                                                   "user-agent": "propx-watchdog/1"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            if not parse_json:
                return response.status, raw, None
            try:
                return response.status, json.loads(raw), None
            except ValueError as exc:
                return response.status, None, f"response was not JSON: {exc}"
    except urllib.error.HTTPError as exc:
        return exc.code, None, f"HTTP {exc.code}"
    except Exception as exc:  # URLError, timeout, DNS, TLS
        return None, None, f"{type(exc).__name__}: {exc}"


def check_site_up():
    status, _, error = _get("/", parse_json=False)
    if status == 200:
        notes.append("site: 200")
        return True
    problems.append(f"site is not serving: {error or f'HTTP {status}'}")
    return False


def check_edge_heartbeat(season_active):
    """The core check: is anything still writing data?

    Returns the payload's `summary` dict (empty when it could not be read) so
    check_injury_freshness can read it without a second request.
    """
    status, payload, error = _get("/api/edge")
    if status != 200 or payload is None:
        problems.append(f"/api/edge unavailable: {error or f'HTTP {status}'}")
        return {}

    generated_at = payload.get("generated_at")
    if not generated_at:
        problems.append("/api/edge returned no generated_at, so freshness cannot be established")
        return {}

    try:
        stamped = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
    except ValueError:
        problems.append(f"/api/edge generated_at is unparseable: {generated_at!r}")
        return {}

    age_minutes = (datetime.now(timezone.utc) - stamped).total_seconds() / 60.0
    summary = payload.get("summary") or {}
    recommendations = payload.get("recommendations") or []
    notes.append(
        f"edge: generated {age_minutes:.0f}m ago, "
        f"{len(recommendations)} recommendations, "
        f"{summary.get('active_players')} active players"
    )

    if age_minutes <= STALE_MINUTES:
        return summary

    message = (f"pipeline data is {age_minutes:.0f} minutes stale "
               f"(threshold {STALE_MINUTES}m, last write {generated_at})")
    if season_active:
        problems.append(message)
    else:
        notes.append(f"{message} -- expected, no games scheduled")
    return summary


def _report_date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def check_injury_freshness(summary, season_active):
    """Is the injury report still arriving?

    Not hypothetical. NBA moved the report page around 2026-11-20 and nothing
    noticed for three days, while this script printed four healthy-looking lines
    and the board quietly blocked 533 of 869 candidates on stale injury context.
    The /api/edge payload check_edge_heartbeat already fetched carries both
    numbers, so this costs no extra request and no new credential.
    """
    freshness = (summary or {}).get("injury_report_freshness")
    if not isinstance(freshness, dict):
        # A pipeline deploy older than the field is a legitimate reason for this to
        # be missing, and paging on a schema gap is how an alert channel gets muted.
        warnings.append(
            "/api/edge summary carries no injury_report_freshness, "
            "so injury staleness cannot be established"
        )
        return

    query_date = freshness.get("query_date")
    blocked = (summary or {}).get("blocked_stale_injury_context_count")

    try:
        age_minutes = float(freshness.get("age_minutes"))
    except (TypeError, ValueError):
        warnings.append(
            f"/api/edge reports no injury report age (query_date {query_date!r}), "
            "so injury staleness cannot be established"
        )
        return

    # Printed even when healthy, so the next incident is diagnosable from one run
    # summary rather than from three days of silence.
    notes.append(
        f"injury report: {age_minutes:.0f}m old "
        f"(pipeline target {freshness.get('max_age_minutes')}m), "
        f"query_date {query_date}, {blocked} candidates blocked on stale injury context"
    )

    # Deliberately not freshness["is_stale"]. The pipeline's own target is 15 minutes
    # inside the lock-sensitive window, so is_stale flips true on a single slow cron
    # tick; a check that pages on one missed refresh gets muted within a week.
    if age_minutes > INJURY_STALE_MINUTES:
        message = (
            f"injury report is {age_minutes:.0f} minutes stale "
            f"(threshold {INJURY_STALE_MINUTES}m, query_date {query_date}), "
            f"blocking {blocked} candidates on stale injury context"
        )
        if season_active:
            problems.append(message)
        else:
            notes.append(f"{message} -- expected, no games scheduled")

    # The age stamp can look fine while the report is for the wrong day, which is a
    # failure the age alone structurally cannot see. `> 1` absorbs the ET/UTC offset
    # without reaching for zoneinfo, keeping this stdlib-only and dependency-free.
    report_date = _report_date(query_date)
    if report_date is not None and season_active:
        days_behind = (datetime.now(timezone.utc).date() - report_date).days
        if days_behind > 1:
            problems.append(
                f"injury report is for {query_date}, {days_behind} days behind today"
            )


def check_schedule():
    """Return True if games fall inside the upcoming window.

    Doubles as the offseason detector, so the watchdog goes quiet between
    seasons instead of paging every 30 minutes for months.
    """
    today = datetime.now(timezone.utc).date()
    dates = [(today + timedelta(days=offset)).isoformat()
             for offset in range(UPCOMING_WINDOW_DAYS + 1)]
    status, payload, error = _get("/api/games?dates=" + ",".join(dates))

    if status != 200 or payload is None:
        # Not fatal on its own: without this we simply cannot tell season from
        # offseason, so assume in-season and let the heartbeat check decide.
        warnings.append(f"/api/games unavailable ({error or f'HTTP {status}'}); "
                        "assuming in-season")
        return True

    games = payload.get("games") if isinstance(payload, dict) else payload
    games = games if isinstance(games, list) else []
    notes.append(f"schedule: {len(games)} games in the next {UPCOMING_WINDOW_DAYS}d")
    return len(games) > 0


def check_dashboard_payload(season_active):
    """Games on the schedule but no props means the slate is not tradeable."""
    status, payload, error = _get("/api/bootstrap")
    if status != 200 or payload is None:
        problems.append(f"/api/bootstrap unavailable: {error or f'HTTP {status}'}")
        return

    counts = {key: len(payload.get(key) or []) for key in
              ("playersRows", "propsRows", "gamesRows")}
    notes.append("bootstrap: " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if not season_active:
        return
    if counts["playersRows"] == 0:
        problems.append("dashboard has zero players; the feed is empty")
    if counts["propsRows"] == 0:
        warnings.append("dashboard has zero props despite games being scheduled "
                        "(check the odds scrapers and the matcher)")


def main():
    print(f"Watchdog target: {BASE_URL}")
    print(f"Staleness threshold: {STALE_MINUTES} minutes "
          f"(injury report: {INJURY_STALE_MINUTES} minutes)\n")

    site_ok = check_site_up()
    season_active = check_schedule() if site_ok else True
    if site_ok:
        summary = check_edge_heartbeat(season_active)
        check_injury_freshness(summary, season_active)
        check_dashboard_payload(season_active)

    for note in notes:
        print(f"  info  {note}")
    for warning in warnings:
        print(f"  WARN  {warning}")
    for problem in problems:
        print(f"  FAIL  {problem}")

    print()
    if problems:
        print(f"UNHEALTHY: {len(problems)} problem(s)")
        # Surfaced to the workflow so the Discord alert can quote it.
        summary = "; ".join(problems)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
                handle.write(f"summary={summary}\n")
        return 1

    print("HEALTHY" + (" (dormant: no games scheduled)" if not season_active else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
