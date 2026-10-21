# Improvement Checklist

> **Status:** Section 1 implemented 2026-10-21 and reviewed — changes are in the working
> tree, uncommitted. The `[M]` helper-consolidation item in Section 1 was deliberately
> deferred (see the note on it). Sections 2-6 are untouched.

Actionable follow-ups from the full-flow review of 2026-10-20. Paths are repo-relative;
line numbers were accurate at commit `b9fb890` and may drift.

Tags: **[S]** small (< 1h) · **[M]** medium (a few hours) · **[L]** large (a day or more)

## Suggested order

1. Section 1 (correctness) and Section 2 (ops alerting + artifact validation) — this is where
   data loss and silent wrongness live.
2. Section 3 (revoke anon RLS) — one migration, removes the whole bypass path.
3. Section 6 (CI + tests on pure logic) — makes everything after it safe.
4. Sections 4-5 (refactors, perf) — these buy velocity, not safety. Do them when the
   above is green.

---

## 1. Correctness risks

- [x] **[S] Enforce the team check on tier-2 fuzzy matches.** `backend/utils/player_matcher.py:105-123`
  returns `candidates[0]['id']` at cutoff 85 with no team agreement, although the comment
  below it intends one. A wrong ID attaches a prop line to the wrong player, in both
  `master_feed.json` and the `player_props` table. Tier 3 already does this correctly —
  reuse `_check_team_match` and return `None` when it fails.

- [x] **[S] De-circularize the alias table.** `backend/utils/player_matcher.py:17-29` contains
  both `nic claxton -> nicolas claxton` and the reverse (same for `cam thomas`/`cameron thomas`,
  `nicolas claxton`/`nic claxton`). Lookup is applied once, so one direction of each pair maps
  *away* from the canonical NBA name and falls through to fuzzy, where
  `fuzz.ratio("nicolas claxton", "nic claxton")` is about 84 — under the cutoff. Make aliases
  one-directional into canonical NBA stats names, or resolve to a fixpoint.

- [x] **[S] Log unmatched prop rows.** `backend/utils/aggregator.py:727` and the equivalent in
  `backend/cron_jobs/cron_closing_lines.py` drop rows silently on `if not pid or pid not in
  master_data: continue`. Add a counter plus the top N unmatched names per run so a book
  renaming a player is visible instead of just missing.

- [x] **[S] Single-source `PROP_MAP`.** Defined three times and currently identical:
  `backend/utils/aggregator.py:117`, `backend/cron_jobs/cron_closing_lines.py:44`,
  `backend/utils/upsert_props.py:51`. Drift means the feed and the DB disagree about what a
  stat type is. Move to one module (e.g. `backend/utils/prop_types.py`) and import.

- [ ] **[M] Consolidate the other duplicated helpers.** `get_et_now` has 8 definitions;
  `normalize_game_date` / `safe_float` / `_safe_float` about 22 near-duplicates across
  `scrapers/`, `utils/`, and `cron_jobs/`. A shared `backend/utils/time_utils.py` +
  `numeric_utils.py` removes the drift surface.

- [x] **[S] Paginate the `players` read.** `frontend/api/_lib/dashboardData.ts:580` does a bare
  `.select(selectClause)` while every other hot read loops `PAGE_SIZE = 1000`. PostgREST caps
  the response, so this truncates silently once the table crosses the ceiling.
  - [ ] Run `select count(*) from players;` first to see how close it already is. (Local reference: `season_stats.csv` has 518 rows this season and the upsert writes at least that many every day, against a 1000-row response ceiling.)
  - [x] Decide whether `backend/utils/aggregator.py:822` should upsert `master_data` (includes
        roster-only skeletons with zeroed stats) or the filtered `final_output`. **Resolved:
        keep `master_data`** — the GP==0 skeletons are this season's rookies and are wanted.
        Presentation is handled on read instead, by roster membership (below).
  - [x] Add a retention/prune policy — nothing currently removes players from past seasons.
        **Resolved by roster membership stamping** (`players.roster_season`,
        `backend/utils/roster_sync.py`): retirees age out of the live read without being
        deleted, so archive seasons stay browsable. Hard deletion is still open — see
        Section 7.

- [x] **[S] Fix the `scipy` dependency gap.** `backend/utils/ml_inference.py:1249` imports
  `scipy.stats.norm` inside `hit_probability`, reachable from `backend/utils/edge_score.py:3085`,
  and `scipy` is not in `requirements-vm.txt`. Dormant only because current models emit
  quantiles; a single-output retrain silently kills the edge board, because the caller swallows
  the failure as a WARN. Preferred fix: replace `norm.cdf` with `math.erf` and drop the
  dependency entirely.

- [x] **[S] Delete the dead fuzzy helper.** `backend/utils/aggregator.py:147` (`get_best_match_id`)
  calls `difflib`, which is never imported — a latent `NameError` if anything ever calls it.
  `PlayerMatcher` superseded it.

## 2. Operational gaps

- [ ] **[M] Add operational alerting.** Discord webhooks are wired for picks only — there is no
  alert path for failures. After `MAX_PIPELINE_FAILURES_PER_DAY` (default 2) the daily refresh
  gives up for the day in silence. Add a failure/heartbeat webhook covering:
  - [ ] daily pipeline failed / gave up for the day
  - [ ] Supabase sync incomplete (`run_pipeline.py` already logs this as ERROR)
  - [ ] closing-line window missed for a game
  - [ ] a daily "pipeline ok, N players, M props" heartbeat so silence is itself a signal

- [ ] **[M] Validate artifacts after the pipeline.** On the 2026-10-20 run, `assist_zones.json`,
  `opp_assist_zones.json`, `opp_def_zones.json`, and `opponent_defensive_ranks.json` were **2
  bytes** and `shot_type_analysis.json` / `play_type_analysis.json` were 38 bytes — the pipeline
  still reported OK, so most matchup overlays were empty and nothing flagged it. Add a post-run
  contract check with a per-artifact row-count floor plus staleness check, wired to the alert
  path above. (Already listed in `PROJECT_AUDIT_ROADMAP.md`; the empty files confirm it.)

- [ ] **[S] Move closing-line capture to the front of the priority chain.**
  `backend/cron_jobs/master_cron.py:618-635` runs game-status unconditionally, then injury (P4)
  returns early, then closing lines (P2). P2 is the only job with a hard, unrecoverable deadline
  (a 12-minute window, roughly 2 cron ticks) and the only one whose output is a permanent
  archive. Check it first, or exempt it from the one-job-per-tick rule.

- [ ] **[S] Collapse the duplicate closing-line state.** `scraped_closing_games` lives in
  Supabase-backed cron state, while `backend/cron_jobs/cron_closing_lines.py:40` keeps its own
  `logs/cron_state_today.json` locally. On a rebuilt VM the two disagree. Keep the durable one.

- [ ] **[S] Rotate and prune logs.** `backend/logs` is 43MB across 71 unrotated
  `master_cron/cron_output_*.log` files. Add logrotate (or a prune step) on the VM.

- [ ] **[S] Stop truncating `pipeline.log`.** `backend/run_pipeline.py:166` opens it with `'w'`
  each run, so a failing run's log is gone by the time you look after the next attempt. Append,
  or write per-run files with retention.

- [ ] **[M] Commit a baseline schema migration.** `.gitignore` asserts migrations are the schema
  source of truth, but only `cron_state` and `edge_scores_current` have DDL. `players`,
  `player_props`, `games`, `line_movements`, `historical_odds`, `archive_gamelogs`,
  `game_markets_current`, `nba_injury_reports_current`, and `historical_player_props` exist only
  in the live Supabase project — the DB is currently not rebuildable. One
  `supabase db dump --schema-only` into `supabase/migrations/` fixes it.

## 3. Security

- [ ] **[S] Revoke `anon` select.** `supabase/migrations/20260321_public_dashboard_readonly_rls.sql`
  grants `anon`/`authenticated` `select ... using (true)` on every table. No client code imports
  supabase-js any more and the built bundle is clean (verified: no anon key, no project URL in
  `dist/`), so the browser does not need anon access at all. Revoking it is strictly stronger
  than the `x-propx-client` header check and the in-memory limiter, and it is one migration.

- [ ] **[S] Remove the unused `VITE_SUPABASE_*` vars** from `frontend/.env` and the Vercel project,
  so a future `import.meta.env.VITE_SUPABASE_ANON_KEY` reference cannot re-inline the key into
  the bundle.

- [ ] **[S] Set a dedicated `API_SIGNING_SECRET`.** `frontend/api/_lib/access.ts:44` falls back to
  signing access tokens with the Supabase secret key. Key reuse, not an exposure — but cheap to
  separate.

- [ ] **[S] Bound the rate-limit map.** `frontend/api/_lib/http.ts:25` never evicts bucket keys;
  entries accumulate per IP for the life of the serverless instance. Evict empty buckets on
  sweep, or move to the Upstash-backed limiter the README already recommends.

## 4. Architecture

- [ ] **[L] Split `backend/utils/edge_score.py`** (6,540 lines, `run_edge_score_refresh` alone is
  about 480). It currently owns feature context, scoring, eligibility guardrails, Discord
  formatting/dedupe/rate-limiting, results grading, and Supabase sync. Suggested seams:
  - [ ] `edge_score/scoring.py` — components, candidate build, diversification
  - [ ] `edge_score/delivery.py` — Discord payloads, dedupe, message refs, rate limiting
  - [ ] `edge_score/grading.py` — results recaps, boxscore lookups
  - [ ] `edge_score/state.py` — notification state and history snapshots
  - Scoring becomes unit-testable, which is the real point.

- [ ] **[M] Deduplicate the DK/FD row handling.** `backend/cron_jobs/cron_closing_lines.py:238-390`
  is about 160 lines of near-identical loops that must be edited in lockstep. One function
  parameterized by book label.

- [ ] **[L] Decompose `frontend/App.tsx`** (2,328 lines, ~30 `useState`, ~15 effects in one
  component) into `useDashboardData`, `useFilters`, `useSelection` hooks plus memoized selectors.
  Prerequisite for most further UI work.

- [ ] **[S] Simplify the repeated edge-refresh calls** in
  `backend/cron_jobs/cron_line_movement.py` — `_run_edge_score_refresh` is invoked from four
  branches; a single `try/finally` expresses the same intent.

## 5. Performance

- [ ] **[M] Stop computing every ML prediction twice.** `MLPredictor.predict`
  (`backend/utils/ml_inference.py:1164`) takes no `line`/`side`, but
  `_compute_ml_regression_context` is called once per side, so each over/under pair rebuilds an
  identical feature dict and runs an identical single-row `model.predict([vector])`. Memoize per
  `(player_id, stat_type)` per refresh; optionally batch rows into one `Pool`. Current cost is
  modest (2.66s for 335 candidates on a 2-game slate) — this matters on a full 12-game slate.

- [ ] **[S] Build `schedule_context` once.** `backend/utils/edge_score.py:6090` and `:6125` build
  it twice per refresh.

- [ ] **[M] Replace the Tailwind Play CDN with build-time Tailwind.** `frontend/index.html:10`
  loads `cdn.tailwindcss.com` in production with about 450 lines of inline color config. Removes
  a render-blocking third-party script and the in-browser JIT. While there: fonts are hotlinked
  from `propsmadness.com` — self-host them.

- [ ] **[S] Dynamic-import `similarPlayers.ts`.** 1,022 lines, statically imported by
  `frontend/App.tsx:43` but never called in DB mode (`App.tsx:1932` uses `/api/similar`). An
  `await import()` in the JSON-mode branch drops it from the production bundle (currently a
  single 562KB chunk).

- [ ] **[M] Get the model binaries out of git.** `backend/prop_modeling/exported_regression_model`
  is 36MB of tracked `.cbm` files; `.git` is already 68MB and every retrain adds a permanent
  blob. Git LFS, or a versioned Supabase storage object pinned by hash.

## 6. Testing and hygiene

- [ ] **[M] Add CI.** No workflow exists. A GitHub Actions job running `pytest`,
  `npx tsc --noEmit`, and `npm run build` would have caught the `scipy` gap and will catch
  matcher regressions.

- [ ] **[M] Test the pure logic.** Currently 2 unit tests for about 32k lines of Python. Highest
  value per test, in order:
  - [ ] `utils/player_matcher.py` — aliases, suffixes, same-name disambiguation, the tier-2 fix above
  - [ ] `utils/prop_date_resolver.py` — the multi-date/undated prop paths
  - [ ] `utils/pregame_props.py` — window boundaries (partly covered already)
  - [ ] `utils/snapshot_manager.py` — gate 1/gate 2 and fallback selection
  - [ ] `utils/edge_score.py` component functions — once Section 4 splits them out

- [ ] **[S] Fix the requirements split.** `requirements.txt` is a dev-machine `pip freeze`
  (131 lines, jupyter/matplotlib/ipykernel) and is what the README tells you to install. Promote
  `requirements-vm.txt` to the runtime set; move notebook deps to `requirements-dev.txt`.

- [ ] **[S] Reconcile the docs with reality.**
  - [ ] `docs/progress/automation_guide.md` documents 3 priorities; there are 5 plus a
        game-status refresh.
  - [ ] `PROJECT_AUDIT_ROADMAP.md` gap 6 is fixed — `run_pipeline.py:221` does write
        `today_schedule.json`.
  - [ ] `README.md` links `docs/deployment/` and a root `vercel.json`; both are gitignored and
        absent from the repo (the real one is `frontend/vercel.json`).

- [ ] **[S] Clear the dead weight on disk.** `backend_old/`, `frontend_old/`,
  `Propsmadness-Mock-UI/`, `.pycache/`, `catboost_info/`, and 13MB of
  `backend/data/preserved_pre_2026-10-20_run` are gitignored but still present locally.

## 7. Roster freshness (follow-up)

Implemented 2026-10-21: `players.roster_season` + `last_seen_at`
(`supabase/migrations/20261021_players_roster_membership.sql`), stamped by
`backend/utils/roster_sync.py` from `commonallplayers?IsOnlyCurrentSeason=1`, with the live
reads in `frontend/api/_lib/dashboardData.ts` filtered to the current season and failing open.
Rookies are included (the index ignores games played); retirees age out at the September
rollover without being deleted.

Verified against production on 2026-10-21:

- [x] **`PBPSTATS_PROXY_URL` is set on the VM** (`/home/ubuntu/NBA_Dashboard`, HEAD `b9fb890`).
- [x] **The proxy was set locally too** — `backend/.env` had it all along, but neither
  `http_client.py` nor `player_index.py` loaded `backend/.env`; only `master_cron.py` does,
  which is why the production path worked and every standalone entry point saw "not set".
  Fixed by loading it in `utils/http_client.py` (does not override already-set vars). The
  index now returns **519 players for 2026-27**, rookies included.
- [x] **Measured the actual drift:** `players` holds **665** rows, of which **519** are on a
  2026-27 roster and **146** are off-roster (Chris Paul, Kyle Lowry, Russell Westbrook, Kevin
  Love, …). All 519 current players already exist in the table, so the sweep only ever
  UPDATEs — it never needs to insert.
- [x] **The 1000-row ceiling is not yet biting** (665/1000), so the pagination fix in Section 1
  was preventive rather than a repair.
- [x] **Guards dry-run clean against production:** 519 candidates, cache season matches the
  resolved season. The `head=True`+`count` pattern behind the shrink guard was separately
  confirmed to work on an existing column, so that guard is live rather than silently dead.

Still open:

- [x] **Deploy order no longer matters.** The live read now treats a missing `roster_season`
  column as a fail-open case, not an error (a filter on a non-existent column returns a 400,
  which would otherwise have 500'd `/api/bootstrap`). Verified against production with the
  column absent: bootstrap succeeded and served all 665 rows unfiltered.
- [ ] **[S] Apply the migration.** This cannot be done with the service-role key — PostgREST
  does not execute DDL, and no direct Postgres credential exists in any env file on this
  machine or the VM (neither has `psql` or the Supabase CLI). Run
  `supabase/migrations/20261021_players_roster_membership.sql` in the Supabase SQL editor, or
  `supabase db push` with the project linked. It is additive and idempotent; the index build
  briefly locks writes on a 665-row table, so effectively instantly.
- [ ] **[S] Then run the first sweep.** `python3 -m utils.roster_sync` from `backend/` — expect
  `stamped=519`. Until it runs, every `roster_season` is NULL and the live read is on its
  fail-open path, i.e. current behaviour.
- [ ] **[S] Deploy before relying on the pipeline to do it.** The VM is at `b9fb890`, which
  predates `roster_sync.py`; the daily pipeline cannot stamp until the branch is pushed and
  pulled.
- [ ] **[M] Hard-delete genuinely dead rows, later.** Once stamping has run cleanly for a few
  weeks, delete players whose `roster_season` is older than the oldest entry in
  `ARCHIVE_SEASONS` *and* who have no `archive_gamelogs` / `historical_player_props` rows.
  Drive the cutoff off `ARCHIVE_SEASONS` so the DB and the UI cannot disagree about what is
  still reachable. Check for FK constraints first — no DDL for those tables is in the repo.
- [ ] **[S] Alert when the sync skips.** It is deliberately non-fatal and self-guarding, so a
  persistent skip is invisible. Fold it into the Section 2 alerting work.
