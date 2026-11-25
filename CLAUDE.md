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

There is no `pytest.ini`/`pyproject.toml`. `backend/conftest.py` puts `backend/` on
`sys.path`, so a new test file no longer needs the four-line preamble the existing 39
carry (those stay; they are harmless).

`conftest.py` also holds an **autouse** fixture that every test gets whether it asks or
not, because both halves of a real bug lived in code a test reaches incidentally:

- It redirects the `DATA_DIR` of `cron_closing_lines`, `master_cron` and `run_pipeline`
  to a tmp dir. `tests/test_odds_shaping_dates.py` calls the real
  `scrape_and_shape_odds()`, whose `persist_raw_odds_csvs` was overwriting
  `data/current/{draftkings,fanduel}.csv` with stubs.
- It points `EDGE_SCORE_ODDS_DIR` at that tmp dir with freshly-written stubs, because
  five `tests/test_book_pause.py` cases *read* those CSVs through
  `book_toggles.stale_books()`, which stats their mtime against a 90-minute bound — so
  they used to fail on a fresh clone and pass only within 90 minutes of a pipeline run.

`utils/odds_csv.write_odds_csv` raises if a test tries to write anywhere inside the real
`backend/data` tree; opt out with `ODDS_CSV_ALLOW_REAL_WRITES=1`. A test that needs to
control odds freshness itself sets `EDGE_SCORE_ODDS_DIR` in its own `setUp`, which runs
inside the fixture and so wins (see `BookFreshnessTests`).

### Frontend (from `frontend/`)

```bash
npm run dev        # NOTE: predev runs backend/scripts/pull_vm_data.sh, which rsyncs over
                   # ssh from the `oracle-nba` host. Without that ssh config it fails.
                   # Use `npx vite` to skip the sync and run on existing local data.
npm run build
npx tsc --noEmit   # typechecks both the app and frontend/api/; run before any push
python3 scripts/verify_design_tokens.py   # diffs Tailwind tokens against propsmadness.com; exits non-zero on drift
npx vite-node scripts/verify_similar_players.ts   # comp-ranking recall vs hand-labelled sets; exits non-zero on regression
```

### Ops

```bash
python3 backend/scripts/watchdog_check.py   # stdlib only; same check GitHub Actions runs every 30m
backend/scripts/pull_vm_logs.sh             # pull VM logs into vm_logs/

cd backend
python3 -m utils.health_monitor --report       # every monitored unit and its state
python3 -m utils.health_monitor --test-push    # confirm the phone gets notifications
python3 -m utils.health_monitor --evaluate --dry-run   # what would be alerted, sending nothing
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

### Failure alerting

`utils/health_ledger.py` keeps one record per **unit of work that is supposed to
succeed on a cadence** — each scraper, each cron job, each Supabase sync, each upstream
host the circuit breaker knows about. `UNITS` in that module is the registry of the
monitored surface and is meant to be read as documentation; an unlisted unit still
records and gets generic defaults.

It exists because every one of those failures used to be logged and dropped. NBA moved
the injury report page on 2026-11-20, `master_cron` spent its four daily retries, and
then logged `"Skipping NBA injury report after repeated failures"` on every tick for
three days. Nothing read it.

Three modules, deliberately separate — `edge_score.py` is 7,000 lines because scoring,
Discord formatting and dedup all landed in one file:

- `health_ledger.py` — accounting and the promotion rule. Sends nothing.
- `alert_push.py` — ntfy transport and wording, plus the heartbeat ping. Stdlib only.
- `health_monitor.py` — the glue `master_cron` calls once per tick.

**A failure is not an incident.** Promotion to `broken` needs both 3+ consecutive
failures *and* a duration floor scaled to that unit's own cadence (they differ by 300×),
or one of three states that are already terminal: the scheduler's daily retry budget
exhausted on two separate days, 3 consecutive successes returning zero rows (the shape
of every `preserve_on_empty=True` artifact — the file on disk looks fine while being
days old), or no success at all inside the unit's staleness window, which catches a job
that stopped being *attempted* rather than failing. Severity `warn` units record and
show up in the health block but never push; that is the mute knob that keeps the channel
credible.

Writes are durable in the Supabase `pipeline_health` table with a local JSON cache, same
contract as `cron_state` — a missing table degrades to file-only, so deploying before
the migration is safe. Durability is load-bearing: three days of continuous failure is
precisely what one 5-minute process cannot see.

Alerts go to **ntfy** (`HEALTH_NTFY_TOPIC`; blank disables, and the topic name *is* the
credential on ntfy.sh). Not Discord — that is where the picks go, and an alarm in the
same app as the thing you read for fun is an alarm you scroll past. One push when an
incident opens, one per `HEALTH_REMINDER_HOURS` (default 24) while it stays broken, one
on recovery, and a single combined push when more than three units break at once. The
ledger is marked notified only *after* a push lands, so a failed send is retried rather
than silently swallowed.

The health block rides on `/api/edge`'s `summary.health`, which gives `watchdog_check.py`
**one generic check** instead of a new hand-written one after every incident.

`HEALTH_HEARTBEAT_URL` (healthchecks.io or similar) is the only part that works when the
VM does not: everything else is pushed *from* the box, so it cannot report that the box
is gone, the disk is full, or cron has stopped. The GitHub Actions watchdog has its own
heartbeat secret for the same reason — GitHub drops most `*/30` ticks and disables
scheduled workflows after 60 days of repo inactivity, and the only evidence is an absence.

`docs/alerting_setup.md` has the one-time phone setup.

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

The two homes also hold **different shapes**, and this is the easy thing to get wrong. The local
file and the disk archive hold every snapshot verbatim. What goes to Supabase is
delta-encoded by `_compact_snapshots_for_supabase`: an entry's `players` carries only the props
whose **line** moved since the last entry that mentioned them, so **the series is sparse** and a
reader wanting a prop's value at some moment has to carry the last entry forward rather than
expect every prop in every entry. Both current readers already do —
`LineMovementSparkline` plots the change points as a step, and `applyIntradayOverrides` walks
backwards taking the newest value per `stat:book`.

Odds are deliberately *not* part of the comparison: juice jitters a cent or two on most ticks,
nobody reads a sparkline to learn that -106 became -104, and comparing on the full payload kept
~5× the entries. The odds at each moment the line moved still ride along on the entry.

This exists because `line_movements` is one jsonb row per game_date and Postgres cannot patch a
jsonb value — it rewrites and WAL-logs the whole TOASTed thing on every upsert, ~57 times a day.
The row's size *is* the write cost, which is what took the database down on 2026-11-23. The
previous encoding capped a trailing window of whole snapshots, so cost grew with the square of
the day and keeping more history was paid for 57 times over — while ~97% of stored values
repeated the line before them. Measured over `data/archive/line_movements`: ~1,900 distinct
(player, stat, book) keys across ~56 snapshots is ~106,000 values, of which ~3,500 are a line
that differs from the one before it. A whole day now costs ~0.5MB of jsonb where a two-hour tail
cost 1.3MB. Do not reintroduce a snapshot cap; if the row ever needs to shrink again, the next
move is narrow append-only rows, not a smaller window.

`pre_game` captures are stored whole. They are the authoritative pre-tip read for the games in
that window, and keeping them guarantees a prop still quoted at tip has a point there, so a line
that never moved all day draws as a flat series instead of a lone dot. Costs ~6% more jsonb.

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

### Comparable players rank on archetype, not production

`utils/similarPlayers.ts` scores players on a **scale-free** vector -- per-36 rates, shot
mix and share-of-production, the 11-dim play-type distribution, and listed height/weight --
then z-scores every feature against the league and takes a weighted Euclidean distance.
Points per game carries a deliberately low weight.

That is a correction, not a preference. The previous version averaged
`clamp(stat / magicDenominator)` terms three times over, which compressed the whole league
into a 0.039-0.084 band and let the tiebreak decide the answer -- and the tiebreak was
production level, because `scoringLoad` alone held 0.42 of the role weight. Asked for comps
for a 6'8" combo forward averaging 17.8, it returned Kevin Durant, Paul George, LeBron James
and a centre: every one within +/-3 points of 17.8, none of them the same kind of player.
Recall against hand-labelled sets went 10/20 -> 14/20 (archetype) -> 16/20 (production term).

`height_inches` / `weight_lbs` ride along in the NBA `playerindex` response that
`season_stats_scrape.py` already fetches for `POSITION` and used to discard, so size costs
**no extra request**. It is the largest single contributor to the ranking. A player with no
listed size has the term omitted and the weights renormalised -- never imputed, because a
league-average height would make an unknown player look like a typical one.

The two tabs mean different things: **By Position** is pure archetype (no line, no scoring
level); **By Prop** is archetype plus line proximity.

**Opponent is a HARD FILTER, and this is the panel's actual point.** "Similar players"
means similar players measured against the team this prop is going up against. The table
shows only games vs that opponent, so a comp who has never faced them is not a weaker comp,
it is an empty row. `rankSimilarPlayers` takes `targetOpponent` and drops every candidate
who has not played them. Measured for Alperen Sengun vs Phoenix: 3/12 usable comps before,
12/12 after, with Jokic 1st and Wembanyama in the top page.

Three things follow from that filter, each of which was wrong until it was applied:

- **No line window.** Against one opponent a comp has one or two games all season, so also
  requiring their line to be near the target's throws away most of the evidence. It was
  dropping Wembanyama's 26.5 -> 40 for being 5.0 off a 21.5 line while keeping a backup's
  20.5. `Diff %` is what makes rows at different line levels comparable.
- **Rows order by comp quality**, not line proximity, since every row is already against
  the right team.
- **The `Opp` column becomes `Team`** (the comp's own club), because the opponent is now
  constant down the whole table.

`opponents_played` (sorted tricodes, emitted by the aggregator) is what makes that work in
DB mode, where `PLAYER_SIMILAR_SELECT` deliberately does not fetch game logs. It is sorted
because it feeds `_row_fingerprint`, and unstable ordering would mark every player changed
on every run.

**`productionGap` is a first-class scoring term, not a feature.** The archetype vector is
scale-free on purpose, but taken alone it made a 22-minute backup look like a comp for a
25.6%-usage hub -- Sengun's top eight included a 6.5-line centre. Raising the `usage`
feature weight cannot fix that: one feature out of ~33 is ~7% of the distance however it is
weighted. So production enters as its own term sized against `positionDistance`, using the
book's line when both players have one and per-36 scoring otherwise. Coefficient 0.60 is the
middle of a measured plateau (0.45-0.80 all score 16/20; 0.30 and 1.00 both score 15). This
is the correction to an over-correction: the original bug was production *dominating*, and
removing it entirely was also wrong.

Keep `scripts/verify_similar_players.ts` green -- it carries per-target floors plus the
opponent-anchoring case, so a change that helps wings at the cost of bigs, or that
re-breaks opponent anchoring, fails rather than passing quietly. It folds diacritics when
matching names, because the feed stores `Nikola Jokić` and a plain match on `Jokic` finds
nothing and reports a false regression.

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
