# Daily Execution Audit

This document describes what actually runs each day in the current project.

## The Daily System

The application is powered by one main scheduled controller: `backend/cron_jobs/master_cron.py`.

It is expected to run every 5 minutes and evaluate three priorities in order:

1. full pipeline refresh
2. closing-line capture
3. intraday snapshotting

That ordering matters because the project treats fresh daily artifacts as the base layer and market snapshots as incremental updates on top.

## Priority 1: Full Pipeline

### Trigger

- Once per day
- At or after 6:00 AM ET
- Guarded by `last_pipeline_date` state

### What Runs

- `backend/run_pipeline.py`

### What It Does

- pulls sportsbook odds
- pulls schedule data
- pulls season stats
- updates game logs
- updates boxscores
- updates spatial and play-style analytics
- aggregates everything into `master_feed.json`
- upserts player and prop records into Supabase when credentials exist

### Important Detail

The scrapers run sequentially, not in a large parallel burst. That is a deliberate memory and stability tradeoff.

## Priority 2: Closing Lines

### Trigger

- When a scheduled game enters the pre-tip capture window
- The logic checks `today_schedule.json` deadlines and skips games already recorded for the day

### What Runs

- `backend/cron_jobs/cron_closing_lines.py`

### What It Does

- scrapes current DK and FD odds
- maps sportsbook player names back to canonical player IDs
- attaches the right `game_id`
- writes immutable historical records to `historical_odds.json`
- falls back to the most recent intraday snapshot if a clean closing scrape is unavailable

### Why It Matters

This is the part that turns “today’s odds” into a reusable historical market archive.

### Current Caveat

- The closing-line flow depends on `backend/data/current/today_schedule.json`.
- The main pipeline currently writes `nba_dashboard_games.json`, while `today_schedule.json` is written by the standalone `fetch_todays_games.py` path.
- Unless that schedule file is generated separately, Priority 2 can exist in code while being partially inert in practice.

## Priority 3: Intraday Movement

### Trigger

- Every 30 minutes based on the last successful snapshot time

### What Runs

- `backend/cron_jobs/cron_line_movement.py`

### What It Does

- scrapes current odds
- writes a timestamped snapshot to `line_movements_today.json`
- upserts the latest prop rows and the snapshot blob to Supabase

## State And Safety Mechanisms

### Mutual Exclusion

- `master_cron.py` uses a lock file
- stale locks are pruned after the timeout window

### Persisted State

- last full pipeline date
- already-scraped closing games
- last intraday snapshot time

### Failure Tolerance

- many cloud writes are non-fatal
- local artifact generation remains the main success criterion
- closing-line capture can fall back to the last valid market snapshot

## Data Artifacts Produced

### Current

- `backend/data/current/master_feed.json`
- `backend/data/current/nba_dashboard_games.json`
- `backend/data/current/draftkings.csv`
- `backend/data/current/fanduel.csv`
- `backend/data/current/season_stats.csv`
- `backend/data/current/gamelogs_2025-26.csv`
- `backend/data/current/line_movements_today.json`

### Archive

- `backend/data/archive/historical_odds.json`

## Main Risks To Watch

### Upstream Instability

- sportsbook schemas can change
- NBA and PBP-style sources can rate-limit or fail intermittently

### Documentation Drift

- older docs still referred to `scheduler.py` and PM2
- the current operating model is `master_cron.py` plus local/current artifacts

### Schedule Artifact Drift

- the full pipeline and the closing-line cron are not yet perfectly aligned on which schedule file is produced and consumed

### UI Trust Gaps

- some dashboard cards still have placeholder fallbacks
- the ETL core is stronger than a few remaining presentation surfaces

## Net Assessment

The project now behaves like a compact scheduled analytics platform:

- one orchestrator
- one canonical aggregation step
- one local artifact layer
- one optional hosted persistence layer

That is a much cleaner operating model than the earlier split between a manual pipeline and a separate always-on scheduler daemon.
