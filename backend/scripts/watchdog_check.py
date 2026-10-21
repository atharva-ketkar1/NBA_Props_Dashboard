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
    """The core check: is anything still writing data?"""
    status, payload, error = _get("/api/edge")
    if status != 200 or payload is None:
        problems.append(f"/api/edge unavailable: {error or f'HTTP {status}'}")
        return

    generated_at = payload.get("generated_at")
    if not generated_at:
        problems.append("/api/edge returned no generated_at, so freshness cannot be established")
        return

    try:
        stamped = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
    except ValueError:
        problems.append(f"/api/edge generated_at is unparseable: {generated_at!r}")
        return

    age_minutes = (datetime.now(timezone.utc) - stamped).total_seconds() / 60.0
    summary = payload.get("summary") or {}
    recommendations = payload.get("recommendations") or []
    notes.append(
        f"edge: generated {age_minutes:.0f}m ago, "
        f"{len(recommendations)} recommendations, "
        f"{summary.get('active_players')} active players"
    )

    if age_minutes <= STALE_MINUTES:
        return

    message = (f"pipeline data is {age_minutes:.0f} minutes stale "
               f"(threshold {STALE_MINUTES}m, last write {generated_at})")
    if season_active:
        problems.append(message)
    else:
        notes.append(f"{message} -- expected, no games scheduled")


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
    print(f"Staleness threshold: {STALE_MINUTES} minutes\n")

    site_ok = check_site_up()
    season_active = check_schedule() if site_ok else True
    if site_ok:
        check_edge_heartbeat(season_active)
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
