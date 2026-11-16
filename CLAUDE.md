# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend (Python, run from `backend/`)

```bash
source .venv/bin/activate          # from the repo root; .venv lives there, not in backend/
cd backend
python run_pipeline.py             # full daily refresh: scrapers -> aggregate -> Supabase -> Signal Score
python cron_jobs/master_cron.py --dry-run   # show what the scheduler would do without scraping
```

Tests are `unittest` classes run under pytest, and each file inserts `backend/` on
`sys.path` itself, so they must be run from `backend/`:

```bash
cd backend
python3 -m pytest tests/ -q
python3 -m pytest tests/test_prior_season_blend.py -q        # single file
python3 -m pytest tests/test_prior_season_blend.py -q -k combo_stat_sums_only_present_components   # single case
```

There is no `pytest.ini`/`pyproject.toml` and no `conftest.py`; the import path works
only because of the `sys.path` preamble in each test file. Keep that preamble when
adding a test.

### Frontend (from `frontend/`)

```bash
npm run dev        # NOTE: predev runs backend/scripts/pull_vm_data.sh, which rsyncs over
                   # ssh from the `oracle-nba` host. Without that ssh config it fails.
                   # Use `npx vite` to skip the sync and run on existing local data.
npm run build
npx tsc --noEmit   # typechecks both the app and frontend/api/; run before any push
python3 scripts/verify_design_tokens.py   # diffs Tailwind tokens against propsmadness.com; exits non-zero on drift
```

### Ops

```bash
python3 backend/scripts/watchdog_check.py   # stdlib only; same check GitHub Actions runs every 30m
backend/scripts/pull_vm_logs.sh             # pull VM logs into vm_logs/
```

### Retraining on Colab

`train_regression_model.py` runs anywhere; Colab is for not tying up a laptop for an hour.
It is **not** a speedup: CatBoost's `MultiQuantile` loss is CPU-only (the trainer pins
`task_type="CPU"`), so a GPU runtime is pure waste, and Colab's free tier gives ~2 vCPUs
against an M1's 8.

```bash
cd backend
python3 scripts/sync_all_data.py        # inputs current first
python3 scripts/make_colab_bundle.py    # -> backend/generated/colab_input.tar.gz (~4MB)
```

Upload that to Drive, then open `notebooks/retrain_on_colab.ipynb` and pick a Colab
runtime as the kernel (the `google.colab` VS Code extension, or colab.research.google.com).
The bundle carries code *and* the 17MB of inputs — not the 287MB derived dataset, which the
notebook rebuilds — so no git credentials are needed and a Colab run can never train on a
dataset built by different code than it ships.

The notebook pins `catboost==1.2.10` because `requirements-vm.txt` does and the VM has to
load the resulting `.cbm` files. It stamps the input bundle's sha256 into
`provenance.colab.input_bundle_sha256`, since `git_sha` is null on a runtime with no
checkout.

Gate the downloaded bundle with `python3 prop_modeling/backtest.py --model` before
committing it, and keep the previous `exported_regression_model/` until it passes.

### Getting all the data locally

`backend/data` is gitignored, so a checkout starts empty and drifts from the VM with
every cron tick. One command collects everything:

```bash
cd backend
python3 scripts/sync_all_data.py              # VM rsync + Supabase dumps, additive
python3 scripts/sync_all_data.py --inventory   # what is on disk, and how stale
```

It is additive by default — unlike `pull_vm_data.sh`, which rsyncs with `--delete` and
removes local-only files. Use `--mirror` only when you actually want the VM's state to
win. Supabase tables land in `backend/data/supabase/<table>.jsonl` with a `.meta.json`
sidecar; `archive_gamelogs` pages at 200 because one row holds a whole season of logs and
a 1000-row page makes the API answer HTTP 500.

Run `--inventory` before trusting any derived artifact: it prints how many days behind
today each game log is, and whether
`prop_modeling/generated/regression_training_dataset.csv` predates them. A stale dataset
is the single most common cause of a surprising model result.

## Architecture

### Pipeline (extract → transform → load → score)

`backend/run_pipeline.py:main()` runs 14 scrapers **sequentially** — not for simplicity but
for memory headroom on the small VM — in a fixed order (schedule → zones → odds → stats →
logs → boxscores), with `gc.collect()` between each. Only `Schedule` and `Season Stats` are
in `critical_scrapers`; every other scraper can fail without failing the run, because game
logs lag upstream routinely and a same-day props refresh is still useful without them.

`backend/utils/aggregator.py:run_aggregation()` is the single transform entry point. It takes
one path per artifact and emits `master_feed.json` — a canonical player object keyed by NBA
player ID, with compound stats (`PTS+REB+AST`, etc.), opponent overlays, and spatial/play-style
features merged in. `PlayerMatcher` (`utils/player_matcher.py`) reconciles sportsbook names
against NBA names with fuzzy + team-aware disambiguation.

A run only counts as a success if the data reached Supabase (`REQUIRE_DB_SYNC`, default true);
individual upserts are non-fatal so local artifacts still generate when the DB is down.

### Signal Score has three refresh owners

`utils/edge_score.py:run_edge_score_refresh(refresh_label=...)` is called from exactly three
places, and a change to scoring affects all of them:

- `run_pipeline.py` — after the daily refresh (`"pipeline"`)
- `cron_jobs/cron_line_movement.py` — after each intraday odds snapshot
- `cron_jobs/cron_closing_lines.py` — after the pre-tip capture (`"pre_game"`)

`edge_score.py` is ~7,000 lines and holds scoring, Discord formatting, webhook rate-limit
handling, dedup state, and tracker grading in one module. **It posts to Discord as a side
effect of scoring.** Before running a refresh locally, set the `EDGE_SCORE_DISCORD_*`
webhook vars to the **empty string** or you will alert the live channel.

Do **not** unset them. `edge_score.py:37` calls `load_dotenv` on `backend/.env`, which
holds live webhook URLs, and `load_dotenv` populates any var that is *absent* — so
unsetting hands the real webhook back. An empty string already exists in the
environment, so `load_dotenv` leaves it alone, and every post is gated on
`if not webhook_url`. All five matter, and `EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL`
falls back to `EDGE_SCORE_DISCORD_LOG_WEBHOOK_URL`, so blanking the tracker alone still
posts:

```bash
EDGE_SCORE_DISCORD_WEBHOOK_URL="" EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL="" EDGE_SCORE_DISCORD_PREVIEW_WEBHOOK_URL="" EDGE_SCORE_DISCORD_RESULTS_WEBHOOK_URL="" EDGE_SCORE_DISCORD_LOG_WEBHOOK_URL="" python3 your_script.py
```

Assert inside the script that the module read them as empty, rather than trusting the
shell — `env $VAR_STRING` does not word-split in zsh, which silently leaves them unset
and re-arms the live webhook. Scoring also upserts to the Supabase table the frontend
reads in DB mode (`_sync_payload_to_supabase`), so patch that out too, and restore
`data/current/edge_score_notification_state.json` plus the two archive histories
afterwards — a local run mutates the dedup state and the pick ledger.

The composite is scored over the component weight that was actually available, not the full
book (`_normalized_weighted_score`), so a dark component widens the error bars instead of
capping the scale — without it, early-season scores sat ~11 points low and a fixed
`EDGE_SCORE_DISCORD_MIN_SIGNAL_SCORE` silenced the alert channel for the first week.
Coverage is reported separately as `confidence`.

### Three Discord channels, one of them a log

Alerts (≥ `EDGE_SCORE_DISCORD_MIN_SIGNAL_SCORE`) post as new messages, capped at one pick per
game. The below-threshold preview is **off by default** (`EDGE_SCORE_DISCORD_PREVIEW_ENABLED`)
— it was diagnostic scaffolding for grading the model's ordering while the alert channel was
quiet. The tracker and, when enabled, the preview are each a single message per slate, edited
in place, holding a running log of what was published — so a pick stays visible after its game
tips rather than being rewritten away. Everything either one logs is queued into
`pending_result_recaps` and graded the next day; the recap waits for missing box scores for
`EDGE_SCORE_RESULTS_RECAP_GRACE_HOURS` and then posts what it has. The tracker floor is clamped
to the alert bar: a higher floor would alert picks that are never logged, so never graded.

### ML inference

`utils/ml_inference.py` loads a CatBoost model from `backend/prop_modeling/exported_regression_model/`
through a `get_ml_predictor()` singleton and predicts in pure NumPy over ~69 features.
Projections are then adjusted for tonight's conditions (usage vacuums from the injury report,
blowout penalties, rest bounces) *after* the model, not inside it.

A player listed **Questionable** is a coin flip, not a certainty: measured against
gamelog ground truth, P(plays) is 58.1% (Available 98.5%, Probable 96.4%, Doubtful 1.5%,
Out 0.0%). `utils/injury_availability.py` holds that table and is the only place it
lives. Two consequences, in different modules: `ml_inference` weights a Questionable
teammate at `1 - P(plays)` in the usage-vacuum features, and `edge_score` discounts the
player's own `p_over` by `P(plays)` — at the probability, never on the quantiles, because
the mixture's median is the 13.8th percentile of the playing distribution rather than
`P(plays)` times its median. `EDGE_QUESTIONABLE_AVAILABILITY=1.0` reverts both without a
deploy. `_load_tonight_dnps` is intentionally still binary; its consumers read absences as
facts, not quantities.

Early in a season the ML term stays dark until a player has 3+ same-season games, and
`utils/prior_season.py` shrinks projections toward `archive_gamelogs`. `run_pipeline.py` warms
that cache before scoring because the first build pulls ~550 players and takes about a minute.

### Scheduling and state

`cron_jobs/master_cron.py` runs every 5 minutes on the VM and picks one of three jobs per tick:
daily pipeline (after 6:00 AM ET), closing-line capture (pre-tip window), or an intraday
line-movement snapshot. It guards with a lock file (`backend/logs/master_cron.lock`, stale after
45 min) and persists scheduler state through `utils/cron_state_store.py`, which treats the
Supabase `cron_state` table as the source of truth and the local JSON as a cache — the VM is
disposable and one has already been lost. Missing table or missing credentials degrades to
file-only, so deploying before the migration is safe.

The intraday odds series has two homes with different jobs. `line_movements_today.json` and the
Supabase `line_movements` table are both **ephemeral** — the local file's date flips at midnight
and `_carry_forward_snapshots` drops players off the active schedule, and `master_cron` purges the
table on a rolling `LINE_MOVEMENT_RETENTION_DAYS` window. The durable copy is
`backend/data/archive/line_movements/<date>.json`, appended by
`SnapshotManager._archive_snapshot` on every snapshot write and never purged; it is what closing-line
value is computed from. `line_movements_sync_state.json` is pruned on the same window as the table,
so a fingerprint can never claim a deleted row is still synced. `backend/data` is gitignored, so the
archive lives on the VM — `backend/scripts/pull_vm_data.sh` already rsyncs it down.

### Two delivery modes

The frontend switches on `VITE_USE_DB`:

- **JSON mode** (`false`/unset) — fetches `master_feed.json` and friends from `VITE_API_BASE_URL`,
  then does all filtering in memory.
- **DB mode** (`true`) — calls the Vercel serverless routes in `frontend/api/`, which are the only
  thing holding the Supabase service key. They hydrate light player rows first and lazily fetch
  heavy detail. Every route applies the same gauntlet from `api/_lib/http.ts`: cross-site
  rejection, navigation rejection, an `x-propx-client` header check, and a per-route in-memory
  rate limit. Player-detail and archive reads additionally need a short-lived signed token from
  `api/access.ts` tied to an HttpOnly cookie.

`USE_DB` is read independently in `App.tsx`, `Sidebar.tsx`, and `BarChart.tsx`, so a mode-dependent
change usually needs touching more than one of them.

Charts are hand-written SVG (`components/BarChart.tsx`), not a chart library. Tailwind comes from
the CDN with its config embedded in `index.html`; `utils/TAILWIND_REFERENCE.md` records the
class-by-class translation of the design template.

## Conventions and gotchas

- **Single branch.** `main` only. The VM deploys by `git pull`, so a push to `main` is a
  production deploy of both Vercel and the pipeline at once. Do not add a second branch.
- **Season is resolved, never hardcoded.** Use `scrapers/season_type.resolve_season()`
  (`NBA_SEASON`/`NBA_SEASON_TYPE` override it). Current season artifacts are
  `backend/data/current/`; `backend/data/archive/` holds prior seasons.
- The on-disk `backend/data/archive/season_2025-26` files are a May **playoffs** snapshot, not a
  full regular season — re-scrape rather than treating them as prior-season baselines.
- Supabase migrations in `supabase/migrations/` are applied by hand; code must tolerate a table
  that does not exist yet.
- Scrapers that hit rate-limiting providers go through `utils/http_client.py` with the
  `PROXY_*`/`PBPSTATS_PROXY_*` env vars. PrizePicks is behind `ENABLE_PRIZEPICKS` and needs
  live cookie/CSRF values.
- `backend_old/`, `frontend_old/`, and `Propsmadness-Mock-UI/` are dead references kept for
  comparison. Do not edit them.
- `LLM_CONTEXT.md` predates the current ML engine and the VM migration; prefer `README.md`
  and this file.
