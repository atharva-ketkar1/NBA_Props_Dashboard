"""
alert_push.py
Phone notifications for structural pipeline failures, and the outside-in heartbeat.

Transport only: what to say about an incident, not whether something is one. The
decision lives in `health_ledger.classify` and the glue in `health_monitor`. Keeping
the three apart is the lesson of `edge_score.py`, where scoring, Discord formatting,
webhook rate limiting and dedup state share one 7,000-line module.

Why ntfy and not the existing Discord webhook
---------------------------------------------
Discord is already wired and does push to a phone, but it is where the picks go. An
alarm in the same app as the thing you read for fun is an alarm you scroll past. ntfy
is a separate app with its own sound, needs no account, and publishing is one POST of
JSON -- so this module needs nothing outside the standard library and cannot be
broken by a dependency bump on the VM.

    HEALTH_NTFY_TOPIC    the secret topic name you subscribe to in the ntfy app.
                         This IS the credential on ntfy.sh: anyone who knows the
                         topic can read and publish to it, so make it long and
                         random, not "nba-alerts".
    HEALTH_NTFY_SERVER   defaults to https://ntfy.sh; point it at a self-hosted
                         instance to keep alert text off a third party.
    HEALTH_NTFY_TOKEN    optional bearer token for a protected topic.
    HEALTH_HEARTBEAT_URL a healthchecks.io (or equivalent) ping URL.

Blank disables, matching the EDGE_SCORE_DISCORD_* convention -- and with the same
trap: `load_dotenv` populates any variable that is *absent*, so once the topic is in
backend/.env, a local run must shadow it with an **empty string**, never unset it.
See .claude/skills/run-pipeline-locally.

The heartbeat is the half of this that works when the VM does not
-----------------------------------------------------------------
Everything else here is pushed *from* the VM, so it structurally cannot report that
the VM is gone, the disk is full or cron has stopped -- the failure that lost the
previous host. `ping_heartbeat()` reverses the direction: the scheduler checks in on
every tick, and the monitoring service pages when the check-ins stop. That service
is also the only thing watching GitHub Actions, which drops most */30 ticks and
disables scheduled workflows outright after 60 days of repo inactivity.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger("health")

TIMEOUT_SECONDS = 15
ATTEMPTS = 2

# ntfy's scale: 1 min, 3 default, 4 high, 5 urgent (urgent can break through
# Do Not Disturb on both phone apps).
PRIORITY_OPEN = 5
PRIORITY_REMINDER = 4
PRIORITY_RECOVERED = 3


def _clean(name):
    return (os.environ.get(name) or "").strip()


def topic():
    return _clean("HEALTH_NTFY_TOPIC")


def server():
    return _clean("HEALTH_NTFY_SERVER").rstrip("/") or "https://ntfy.sh"


def click_url():
    return _clean("HEALTH_ALERT_CLICK_URL") or "https://propx-dashboard.vercel.app"


def enabled():
    return bool(topic())


def _post(url, data, headers, method="POST"):
    """One request, retried once. Returns True on any 2xx.

    Never raises. A monitoring call that can crash the scheduler is worse than no
    monitoring call, because it takes the slate with it.
    """
    last_error = None
    for _attempt in range(1, ATTEMPTS + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if 200 <= response.status < 300:
                    return True
                last_error = f"HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            # 4xx is a bad topic, a bad token or a malformed body. Retrying cannot
            # fix any of those, and 429 would only make a rate limit worse.
            if 400 <= exc.code < 500:
                break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    logger.error("health: push to %s failed: %s", url, last_error)
    return False


def push(title, message, *, priority=PRIORITY_OPEN, tags=None, click=None):
    """Send one notification. Returns True if it landed, False if it did not or if
    no topic is configured."""
    configured_topic = topic()
    if not configured_topic:
        logger.debug("health: no HEALTH_NTFY_TOPIC, not pushing %r", title)
        return False

    body = {
        "topic": configured_topic,
        "title": title[:250],
        # ntfy truncates very long messages; 1800 leaves room for its own framing
        # while still fitting an error string and the context around it.
        "message": message[:1800],
        "priority": int(priority),
        "tags": list(tags or []),
        "click": click or click_url(),
    }
    headers = {"content-type": "application/json",
               "user-agent": "propx-health/1"}
    token = _clean("HEALTH_NTFY_TOKEN")
    if token:
        headers["authorization"] = f"Bearer {token}"

    # Published as JSON rather than the header form on purpose: ntfy requires header
    # values to be ASCII, and these messages carry arbitrary upstream error strings.
    return _post(server(), json.dumps(body).encode("utf-8"), headers)


# ---------------------------------------------------------------------------
# Wording. One incident per push, with enough to triage from a lock screen:
# what broke, for how long, what the error was, and what it costs.
# ---------------------------------------------------------------------------

def _hours(seconds):
    hours = max(0.0, float(seconds or 0)) / 3600.0
    if hours < 1:
        return f"{hours * 60:.0f}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _lines(record, reason, now=None):
    """The body of an incident push.

    `now` is the moment the ledger was evaluated, not wall-clock time. They are
    within seconds of each other in production, but reading the clock here instead
    produced messages that contradicted their own reason line -- "no successful run
    in 26.2h" above "Last success: 10m ago" -- and an alert that argues with itself
    is one you stop trusting at exactly the wrong moment.
    """
    moment = now if now is not None else time.time()
    lines = [reason or "no reason recorded"]
    last_success = float(record.get("last_success_ts", 0) or 0)
    if last_success:
        lines.append(f"Last success: {_hours(moment - last_success)} ago")
    else:
        lines.append("Last success: never recorded")
    error = record.get("last_error")
    if error and error not in (reason or ""):
        lines.append(f"Error: {error}")
    detail = record.get("detail")
    if detail:
        lines.append(f"Context: {detail}")
    return "\n".join(lines)


def send_incident(action):
    """Push one open/reminder/recovered action from `health_ledger.evaluate`."""
    record = action.get("record") or {}
    label = record.get("label") or record.get("unit") or "pipeline"
    kind = action.get("kind")
    reason = action.get("reason") or ""

    if kind == "recovered":
        return push(
            f"Recovered: {label}",
            f"Back to succeeding after {_hours(action.get('down_for_s'))} down.",
            priority=PRIORITY_RECOVERED,
            tags=["white_check_mark"],
        )

    prefix = "Still broken" if kind == "reminder" else "BROKEN"
    priority = PRIORITY_REMINDER if kind == "reminder" else PRIORITY_OPEN
    return push(
        f"{prefix}: {label}",
        _lines(record, reason, now=action.get("now")),
        priority=priority,
        tags=["rotating_light"],
    )


def send_incident_batch(actions):
    """One push for many simultaneous incidents.

    When the network, the proxy or Supabase goes, a dozen units break in the same
    minute. Twelve notifications is a thing you silence; one that names all twelve
    is a thing you read.
    """
    opened = [a for a in actions if a.get("kind") != "recovered"]
    if not opened:
        return False
    names = [(a.get("record") or {}).get("label") or (a.get("record") or {}).get("unit")
             for a in opened]
    first = opened[0]
    body = [f"{len(opened)} units are broken at once, which usually means one shared "
            f"cause (network, proxy, Supabase or the host itself).", ""]
    body.extend(f"• {name}" for name in names if name)
    body.append("")
    body.append(f"First: {first.get('reason') or ''}")
    return push(
        f"BROKEN: {len(opened)} pipeline units",
        "\n".join(body),
        priority=PRIORITY_OPEN,
        tags=["rotating_light"],
    )


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def heartbeat_configured():
    return bool(_clean("HEALTH_HEARTBEAT_URL"))


def ping_heartbeat(*, failed=False):
    """Check in with the external dead-man's switch.

    `failed=True` posts to the /fail endpoint, which lets the service distinguish
    "the scheduler ran and knows it is unhealthy" from "the scheduler stopped
    running" -- different causes with different fixes, and only the second one is
    invisible from inside the box.
    """
    url = _clean("HEALTH_HEARTBEAT_URL")
    if not url:
        return False
    if failed:
        url = url.rstrip("/") + "/fail"
    # An empty POST body rather than a GET: healthchecks.io and its equivalents
    # accept either, and a POST cannot be served from anything's cache.
    return _post(url, b"", {"user-agent": "propx-health/1",
                            "content-type": "text/plain"})
