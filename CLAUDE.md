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
effect of scoring.** Before running a refresh locally, unset the `EDGE_SCORE_DISCORD_*`
webhook vars or you will alert the live channel.

The composite is scored over the component weight that was actually available, not the full
book (`_normalized_weighted_score`), so a dark component widens the error bars instead of
capping the scale — without it, early-season scores sat ~11 points low and a fixed
`EDGE_SCORE_DISCORD_MIN_SIGNAL_SCORE` silenced the alert channel for the first week.
Coverage is reported separately as `confidence`.

### Three Discord channels, one of them a log

Alerts (≥ `EDGE_SCORE_DISCORD_MIN_SIGNAL_SCORE`) post as new messages, capped at one pick per
game. The tracker and the below-threshold preview are each a single message per slate, edited
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
