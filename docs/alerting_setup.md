# Getting pipeline failures onto your phone

One-time setup. Roughly fifteen minutes, no accounts required for the first part.

## What this gets you

A push notification when something in the pipeline is **structurally** broken — the
injury report page moving, a sportsbook blocking the VM's egress for hours, Supabase
credentials expiring, the box itself dying. Not a notification for a DraftKings 403 that
clears on its own, and not one per five-minute tick for three days.

The thresholds that draw that line live in `backend/utils/health_ledger.py`. Read the
`UNITS` registry there before widening anything: a channel that cries wolf gets muted,
and a muted channel is the same silence as having nothing, with a false sense of cover.

## 1. ntfy, for incident pushes

1. Install **ntfy** (free, App Store / Play Store / F-Droid).
2. Pick a topic name that nobody could guess. On the public `ntfy.sh` server the topic
   name *is* the credential — anyone who knows it can read your alerts and publish fake
   ones. Generate one rather than inventing it:

   ```bash
   echo "propx-health-$(openssl rand -hex 12)"
   ```

3. In the app: **+** → paste the topic name → Subscribe. Enable its notification sound,
   and allow it to break through Do Not Disturb if you want to be woken (incidents are
   sent at ntfy priority 5, recoveries at 3).
4. On the VM, add it to `backend/.env`:

   ```bash
   HEALTH_NTFY_TOPIC=propx-health-xxxxxxxxxxxxxxxxxxxxxxxx
   # Optional:
   # HEALTH_NTFY_SERVER=https://ntfy.example.com   # self-hosted, keeps alert text off ntfy.sh
   # HEALTH_NTFY_TOKEN=tk_...                      # for an access-controlled topic
   # HEALTH_REMINDER_HOURS=24                      # repeat cadence while still broken
   # HEALTH_ALERT_CLICK_URL=https://propx-dashboard.vercel.app
   ```

5. Confirm it reaches you:

   ```bash
   cd backend && python3 -m utils.health_monitor --test-push
   ```

Nothing else is needed — `master_cron` evaluates the ledger and pushes on every tick.

## 2. healthchecks.io, for the VM dying

Everything in step 1 is pushed *from* the VM, so it structurally cannot tell you the VM
is gone, the disk is full, or cron has stopped. That is the failure that killed the
previous host, and it needs a check that runs somewhere else and alerts on **silence**.

1. Sign up at [healthchecks.io](https://healthchecks.io) (free tier: 20 checks, with its
   own phone app).
2. Create a check named `nba-master-cron`. Period **15 minutes**, grace **60 minutes**.

   Why not 5 minutes to match the cron: a tick that cannot get the lock does not ping,
   and a long pipeline run holds that lock for many minutes. 15/60 tolerates a slow daily
   run without paging, and still catches a dead box within the hour.

3. Copy its ping URL into `backend/.env`:

   ```bash
   HEALTH_HEARTBEAT_URL=https://hc-ping.com/your-uuid-here
   ```

4. In healthchecks.io, add ntfy as an integration on that check so this lands in the same
   app as everything else.

A tick that runs but throws pings `/fail`, so "the scheduler is broken" and "the
scheduler is gone" arrive as different notifications. They have different fixes.

## 3. The watchdog's own heartbeat

The GitHub Actions watchdog has the same blind spot pointed at itself: GitHub drops most
`*/30` ticks (measured: roughly one run every 6–7 hours) and **disables scheduled
workflows entirely after 60 days of repository inactivity**. When that happens the
workflow stops running and stops alerting, and the only evidence is an absence.

Create a second healthchecks.io check, `nba-watchdog`, period **6 hours**, grace **6
hours** — sized against GitHub's real cadence, not the `*/30` in the cron expression.
Then add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `WATCHDOG_NTFY_TOPIC` | the same topic as step 1 |
| `WATCHDOG_HEARTBEAT_URL` | the `nba-watchdog` ping URL |
| `WATCHDOG_NTFY_SERVER` | only if self-hosting ntfy |

Both steps are skipped when their secret is empty, so adding them one at a time is safe.

## 4. Apply the migration

```sql
-- supabase/migrations/20261123_pipeline_health.sql
```

Run it by hand in the Supabase SQL editor, like the other migrations. Until it is
applied, the ledger runs file-only on the VM and logs one warning saying so — which
works, but every failure streak resets if the VM is ever replaced, and that streak is the
whole signal.

## Checking on it

```bash
cd backend
python3 -m utils.health_monitor --report              # every unit, its state, its reason
python3 -m utils.health_monitor --evaluate --dry-run  # what would be pushed, pushing nothing
curl -s https://propx-dashboard.vercel.app/api/edge \
  -H 'x-propx-client: web' -H 'sec-fetch-site: same-origin' \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["summary"]["health"], indent=2))'
```

## Tuning, after the first couple of weeks

Start with the defaults and change them in response to real incidents, not in
anticipation of imagined ones. The knobs:

| Variable | Default | What it does |
| --- | --- | --- |
| `HEALTH_REMINDER_HOURS` | 24 | How often an unresolved incident re-pushes. `0` disables reminders. |
| `HEALTH_MIN_CONSECUTIVE_FAILURES` | 3 | Attempts before the duration test is consulted. |
| `HEALTH_MIN_EMPTY_RUNS` | 3 | Zero-row "successes" that count as broken. |
| `HEALTH_BREAKER_TRIP_ALERT` | 4 | Circuit-breaker trips that count as an egress block rather than flapping. |
| `HEALTH_DORMANT_MONTHS` | `7,8,9` | Months where staleness is expected and never pages. |
| `HEALTH_LEDGER_BACKEND` | `auto` | `file` pins to the local cache. |

If a unit is paging when it should not, move it to `"severity": "warn"` in
`health_ledger.UNITS` rather than raising a global threshold. Global changes blunt the
checks that are working.
