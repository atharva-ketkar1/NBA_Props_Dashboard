# Catch-Up Guide

Start here if you need the fastest accurate overview of the project.

## What We Built

This repo is an NBA player-props analytics dashboard backed by a scheduled data platform. The frontend is only the last mile. Most of the interesting work happens before the browser ever loads:

- market data extraction from sportsbooks
- stat ingestion from NBA data sources
- player identity reconciliation
- feature engineering for matchup context
- historical snapshotting for line movement and closing lines
- delivery through static JSON or Supabase

## The Most Important Mental Model

The frontend is not driving the backend in real time.

The normal flow is:

1. Python scrapers collect raw data into `backend/data/current/`
2. `backend/utils/aggregator.py` turns that into a canonical player feed
3. `backend/run_pipeline.py` emits `master_feed.json`
4. `backend/cron_jobs/master_cron.py` keeps the day current with intraday and closing-line jobs
5. React reads prebuilt data and focuses on rendering, filtering, and interaction

If you forget that preprocessing-first model, the rest of the codebase feels more confusing than it is.

## Where The Real Logic Lives

### Backend

- `backend/run_pipeline.py`
  - full daily ETL entry point
- `backend/utils/aggregator.py`
  - canonical player assembly and prop shaping
- `backend/utils/player_matcher.py`
  - fuzzy identity resolution across books and stat providers
- `backend/utils/snapshot_manager.py`
  - intraday snapshots and historical closing lines
- `backend/cron_jobs/master_cron.py`
  - production orchestration loop

### Frontend

- `frontend/App.tsx`
  - app bootstrapping, mode switching, lazy loading, UI state
- `frontend/components/BarChart.tsx`
  - prop history visualization and hover inspection
- `frontend/components/ShootingZones.tsx`
  - player and opponent shooting context
- `frontend/components/AssistZones.tsx`
  - player and opponent assist distribution

## Current Runtime Modes

### JSON Mode

- Local, simple, fast to reason about
- Reads:
  - `master_feed.json`
  - `historical_odds.json`
  - `line_movements_today.json`

### Supabase Mode

- Better for hosted deployments
- Loads lighter player records first
- Pulls heavy JSON fields lazily as the user drills in

## Production Automation In One Minute

`master_cron.py` runs every 5 minutes and checks three priorities in order:

1. Run the full pipeline once per day after 6:00 AM ET
2. Capture closing lines when games enter the pre-tip window
3. Record intraday snapshots every 30 minutes

It uses lock files and persisted state so concurrent runs do not collide.

## What Is Fully Real vs Still In Progress

### Real

- Daily ETL flow
- DraftKings and FanDuel prop ingestion
- Fuzzy player matching
- Spatial and play-style enrichment
- Historical line movement and closing-line capture
- JSON-mode frontend and Supabase-mode frontend

### Still In Progress

- `SimilarPlayers.tsx` is not integrated yet
- Some analysis cards still use placeholder fallback arrays when data is missing

## Good First Files To Read

1. `README.md`
2. `LLM_CONTEXT.md`
3. `backend/run_pipeline.py`
4. `backend/utils/aggregator.py`
5. `backend/cron_jobs/master_cron.py`
6. `frontend/App.tsx`

## Local Commands

### Build the daily feed

```bash
cd backend
python run_pipeline.py
```

### Serve local data files

```bash
cd backend
npx serve --cors -p 5000
```

### Run the frontend

```bash
cd frontend
npm install
npm run dev
```
