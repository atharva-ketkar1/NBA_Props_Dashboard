"""
health_monitor.py
The glue: evaluate the health ledger, push what is due, check in with the
dead-man's switch.

`health_ledger` decides what is broken. `alert_push` knows how to reach a phone.
This module is the only thing that knows about both, and it is what master_cron
calls once per tick.

Ordering matters here. The ledger is marked as notified only *after* a push has
actually landed, so a push that fails (no network, bad topic, ntfy down) is retried
on the next tick rather than being quietly marked as delivered. The cost is a
possible duplicate if the push succeeds and the write that records it does not,
which is the right way round: a repeated alert is annoying, a dropped one is the
whole failure mode this exists to fix.

Run it by hand to see the current picture:

    cd backend && python3 -m utils.health_monitor --report
"""

import logging
import os
import time

from utils import alert_push, health_ledger

logger = logging.getLogger("health")

# Beyond this many simultaneous incidents, send one combined push instead of one
# each. A dozen units breaking in the same minute is one shared cause -- the
# network, the proxy, Supabase, or the host.
BATCH_THRESHOLD = 3


def _env_int(name, fallback, minimum=0):
    raw = (os.environ.get(name) or "").strip()
    try:
        value = int(float(raw)) if raw else fallback
    except ValueError:
        value = fallback
    return max(minimum, value)


def _breaker_trip_alert():
    """Decayed trips that count as "this host is blocking us", not flapping.

    `circuit_breaker._decayed_trips` already forgives one trip per clean hour, so a
    host that keeps climbing has re-blocked after demonstrated clean time. Four is
    about three hours of sustained refusal at the 2700s cooldown cap -- the shape of
    the 2026-10-28 DraftKings egress block, and well clear of a single edge wobble.
    """
    return _env_int("HEALTH_BREAKER_TRIP_ALERT", 4, 1)


def fold_circuit_breaker(now=None):
    """Translate breaker state into ledger records for `upstream:<host>` units.

    The breaker counts refused *requests* and holds cooldowns; it has no opinion
    about whether a human should hear. This is where that opinion lives, so the
    breaker stays a dependency-free module that `http_client` can import freely.

    A host below the trip bar is not recorded at all unless it already has a record
    to close -- otherwise every transient 403 would create a unit to reason about.
    """
    moment = now if now is not None else time.time()
    try:
        from utils import circuit_breaker
        status = circuit_breaker.status()
    except Exception as exc:
        logger.debug("health: could not read circuit breaker state: %s", exc)
        return

    bar = _breaker_trip_alert()
    known = health_ledger.load()

    for host, entry in (status or {}).items():
        unit = f"upstream:{host}"
        trips = int(entry.get("trips", 0) or 0)
        if trips >= bar:
            detail = entry.get("last_detail") or ""
            health_ledger.record_failure(
                unit,
                f"egress blocked: {trips} breaker trips, "
                f"{entry.get('minutes_remaining', 0)}m of cooldown remaining",
                detail=detail,
                now=moment,
            )
        elif unit in known:
            health_ledger.record_success(unit, now=moment)

    # A host that has fully cleared leaves breaker state entirely, so anything in the
    # ledger that the breaker no longer knows about is answering again.
    for unit in known:
        if not unit.startswith("upstream:"):
            continue
        if unit.split(":", 1)[1] not in (status or {}):
            health_ledger.record_success(unit, now=moment)


def evaluate_and_notify(now=None, dormant=None, dry_run=False):
    """Classify everything, push what is due, and return the actions taken."""
    moment = now if now is not None else time.time()
    fold_circuit_breaker(now=moment)

    actions, _ledger = health_ledger.evaluate(now=moment, dormant=dormant)
    if not actions:
        return []

    for action in actions:
        record = action.get("record") or {}
        logger.warning(
            "health: %s %s - %s",
            action.get("kind"), record.get("unit"), action.get("reason"),
        )

    if dry_run:
        return actions
    if not alert_push.enabled():
        # Loud, because an unconfigured notifier means every incident below is
        # going nowhere -- the exact silence this module was built to end.
        logger.error(
            "health: %d incident(s) need alerting but HEALTH_NTFY_TOPIC is not set",
            len(actions),
        )
        return actions

    opens = [a for a in actions if a.get("kind") in {"open", "reminder"}]
    recoveries = [a for a in actions if a.get("kind") == "recovered"]
    delivered = []

    if len(opens) > BATCH_THRESHOLD:
        if alert_push.send_incident_batch(opens):
            delivered.extend((a.get("record") or {}).get("unit") for a in opens)
    else:
        for action in opens:
            if alert_push.send_incident(action):
                delivered.append((action.get("record") or {}).get("unit"))

    for action in recoveries:
        # Nothing to stamp: evaluate() has already cleared the incident, so a failed
        # recovery push is simply not retried. Being told late that something healed
        # is not worth re-opening closed state for.
        alert_push.send_incident(action)

    health_ledger.mark_notified([u for u in delivered if u], now=moment)
    return actions


def heartbeat(*, healthy=True, dry_run=False):
    """Check in with the external dead-man's switch, if one is configured."""
    if dry_run or not alert_push.heartbeat_configured():
        return False
    return alert_push.ping_heartbeat(failed=not healthy)


def snapshot():
    """Health block for the Signal Score payload. Never raises."""
    try:
        return health_ledger.snapshot()
    except Exception as exc:
        logger.warning("health: could not build snapshot: %s", exc)
        return {}


def _format_report(now=None):
    moment = now if now is not None else time.time()
    ledger = health_ledger.load()
    if not ledger:
        return "No health records yet."

    rows = []
    for unit, record in sorted(ledger.items()):
        if not isinstance(record, dict):
            continue
        config = health_ledger._config(unit)
        state, reason = health_ledger.classify(
            {**record, "unit": unit}, moment,
            dormant=health_ledger.is_dormant(), config=config,
        )
        last_success = float(record.get("last_success_ts", 0) or 0)
        age = f"{(moment - last_success) / 3600:.1f}h" if last_success else "never"
        rows.append((state, unit, config["severity"], age,
                     int(record.get("consecutive_failures", 0) or 0), reason))

    order = {"broken": 0, "degraded": 1, "ok": 2}
    rows.sort(key=lambda row: (order.get(row[0], 3), row[1]))
    width = max(len(row[1]) for row in rows)
    lines = [f"{'state':9} {'unit':{width}} {'sev':5} {'last ok':>8} {'fails':>5}  reason"]
    for state, unit, severity, age, fails, reason in rows:
        lines.append(f"{state:9} {unit:{width}} {severity:5} {age:>8} {fails:>5}  {reason}")
    lines.append("")
    lines.append(f"ntfy configured: {alert_push.enabled()} | "
                 f"heartbeat configured: {alert_push.heartbeat_configured()} | "
                 f"dormant: {health_ledger.is_dormant()}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    from utils.logging_utils import configure_logging

    parser = argparse.ArgumentParser(description="Pipeline health ledger")
    parser.add_argument("--report", action="store_true",
                        help="Print every record and its current classification")
    parser.add_argument("--evaluate", action="store_true",
                        help="Classify and send any due notifications")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --evaluate, log what would be sent and send nothing")
    parser.add_argument("--test-push", action="store_true",
                        help="Send one test notification to confirm the phone setup")
    args = parser.parse_args()

    configure_logging()

    if args.test_push:
        ok = alert_push.push(
            "Test: NBA pipeline health",
            "If this reached your phone, HEALTH_NTFY_TOPIC is wired correctly.",
            priority=alert_push.PRIORITY_RECOVERED,
            tags=["test_tube"],
        )
        print("sent" if ok else "not sent (check HEALTH_NTFY_TOPIC)")
    if args.evaluate:
        for action in evaluate_and_notify(dry_run=args.dry_run):
            print(f"{action.get('kind')}: {(action.get('record') or {}).get('unit')}")
    if args.report or not any((args.report, args.evaluate, args.test_push)):
        print(_format_report())
