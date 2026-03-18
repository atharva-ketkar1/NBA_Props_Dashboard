# Project Audit And Roadmap

This audit reflects the current codebase, not the earlier scheduler/PM2 era.

## Current Architecture Health

### Solid Areas

- The extraction and aggregation backbone is real, broad, and well connected.
- `PlayerMatcher` is doing meaningful entity-resolution work across messy source systems.
- `master_cron.py` gives the project a clean operational center of gravity.
- The frontend reads like a serious analytics surface rather than a toy CRUD UI.
- JSON mode and Supabase mode coexist cleanly enough to support both local iteration and hosted delivery.

### Product Surfaces That Are Actually Backed By Data

- Prop ingestion from DraftKings and FanDuel
- Season stats and rolling game logs
- Boxscore-enriched historical logs with margin and DNP context
- Shooting zones and opponent defensive zone overlays
- Assist zones and opponent assist overlays
- Shot type analysis
- Play type analysis
- Intraday line movement snapshots
- Historical closing lines

## Current Gaps And Risks

### 1. Similar Players Is Still Placeholder UI

- `frontend/components/SimilarPlayers.tsx` still renders `SIMILAR_GAMES` fallback content.
- `App.tsx` passes `similarGames={undefined}`, so there is no live integration path yet.
- This is the clearest product surface that should not be described as production-complete.

### 2. Analysis Cards Still Have Demo Fallbacks

- `frontend/components/ShotTypeAnalysis.tsx` ships with `DEFAULT_SHOT_TYPES`.
- `frontend/components/PlayTypeAnalysis.tsx` falls back to `PLAY_TYPES`.
- The live path works when backend data exists, but missing-data states still render illustrative defaults rather than a true empty state.

### 3. Tooltip Data Is Better, But Not Perfect

- `HoverTooltip.tsx` now uses historical odds if present and reads DNP context dynamically from game logs.
- Some quarter and half fields still depend on stats that may be absent for a given row.
- A few labels expose partial split logic rather than a guaranteed, fully modeled quarter-by-quarter schema.

### 4. Frontend Tab Coverage Outruns Current Feed Coverage

- The UI advertises more tabs than the backend clearly models end to end for every player and every game row.
- This is manageable because the frontend guards missing data fairly well, but it is still a documentation and trust risk.

### 5. Large-Artifact Strategy Still Matters

- The project can run in static JSON mode, but the monolithic feed pattern remains expensive for public hosting at scale.
- Supabase mode reduces the need to hydrate every heavy field on first load, which is the more scalable direction.

### 6. Closing-Line Automation Still Depends On A Separate Schedule Artifact

- `master_cron.py` and `cron_closing_lines.py` expect `backend/data/current/today_schedule.json`.
- `run_pipeline.py` currently writes `nba_dashboard_games.json` during the schedule step, but does not itself write `today_schedule.json`.
- `fetch_todays_games.py` writes `today_schedule.json` only in its standalone execution path.
- That means the closing-line machinery is implemented, but its fully reliable end-to-end automation still depends on that extra schedule artifact being present.

## Operational Audit

### What Changed From Older Docs

- The project is no longer centered around a long-running `scheduler.py` daemon.
- The current source of truth for automation is `backend/cron_jobs/master_cron.py`.
- The production rhythm is now:
  - daily full pipeline after 6:00 AM ET
  - pre-game closing-line sweeps
  - 30-minute intraday snapshots

### What Is Strong

- Lock-file mutual exclusion
- Persisted cron state
- Non-fatal Supabase writes
- Snapshot fallback logic when books move in-play
- Rolling DB cleanup for short-lived market tables

### What Still Needs Attention

- Align schedule generation so the main pipeline always produces the file the closing-line cron consumes
- More explicit monitoring and alerting around scraper drift
- Better empty states instead of fallback demo data
- Clearer provenance in the UI for current vs historical vs fallback market values

## Recommended Roadmap

### Near Term

1. Make the main schedule pipeline write `today_schedule.json` in the same path the cron jobs consume.
2. Replace Similar Players placeholder content with a real backend-generated dataset.
3. Remove illustrative fallback arrays from analysis cards and switch to honest empty states.
4. Tighten tooltip support for quarter and half splits so labels only appear when the fields exist.
5. Add a lightweight quality check that validates output artifacts after the pipeline completes.

### Mid Term

1. Expand sportsbook coverage using the existing matcher and prop-normalization patterns.
2. Split feed hydration more aggressively for hosted mode so the initial payload stays lean.
3. Add automated regression checks for critical artifact shapes consumed by `App.tsx`.

### Longer Term

1. Introduce richer player archetype or matchup clustering to power a true comps engine.
2. Add observability around scrape failures, stale data, and missing-book conditions.
3. Move from “best effort doc knowledge” toward a defined data contract for every major UI surface.
