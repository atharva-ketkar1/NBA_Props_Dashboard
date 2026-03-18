# NBA Props Intelligence Dashboard

An end-to-end basketball analytics product that turns fragmented sportsbook and league data into a fast, research-friendly decision surface.

This project combines scheduled data collection, entity resolution, feature engineering, historical market capture, and a React dashboard tuned for high-density exploration. It is built like a small analytics platform rather than a one-off frontend: the same pipeline can publish a static JSON feed for local/offline use or upsert structured records into Supabase for hosted delivery.

## Why This Project Stands Out

- Built a production-minded ETL workflow around unreliable third-party sports data sources.
- Normalized player identities across sportsbooks and NBA datasets with fuzzy matching plus team-aware disambiguation.
- Enriched raw prop lines with contextual analytics: recent form, opponent tendencies, shot diet, assist zones, play types, and line movement history.
- Designed the frontend to feel instant by shifting heavy work into scheduled preprocessing rather than runtime API calls.
- Added operational safeguards that matter in real systems: lock files, idempotent daily state, fallback snapshots, non-fatal DB writes, and memory-aware execution order.

## What The Dashboard Does

- Aggregates live NBA player props from DraftKings and FanDuel.
- Builds a canonical player feed with season stats, recent game logs, boxscore-derived context, and opponent matchup overlays.
- Captures intraday line movement snapshots and near-tip closing lines.
- Visualizes shot zones, assist zones, shot type splits, play type scoring mix, and prop hit-rate history.
- Supports two delivery modes:
  - Static JSON feed for local development and low-latency browsing
  - Supabase-backed reads for hosted deployments

## End-to-End Pipeline

### 1. Extract

`backend/run_pipeline.py` runs a scheduled sequence of scrapers that pull:

- Sportsbook odds from DraftKings and FanDuel
- NBA schedule and game metadata
- Season-level player stats
- Rolling game logs
- Boxscores for margin and DNP context
- Shooting zones
- Assist zones
- Opponent defensive zone rankings
- Shot type and play type datasets

Several scrapers include proxy/fallback logic because some upstream providers rate-limit aggressively or fail intermittently.

### 2. Transform

`backend/utils/aggregator.py` is the core modeling layer. It:

- Builds a canonical player record keyed by NBA player ID
- Reconciles mismatched naming conventions with `PlayerMatcher`
- Calculates compound stats such as `PTS+REB+AST`, `PTS+AST`, `REB+AST`, and `STL+BLK`
- Injects opponent context for both current and historical matchups
- Merges spatial and play-style features into the same player object
- Shapes a frontend-friendly prop tree by stat type and sportsbook

This is the step that turns a group of unrelated CSV/JSON artifacts into a single analytical object model.

### 3. Load

The pipeline writes to two targets:

- Local artifacts in `backend/data/current/` and `backend/data/archive/`
- Supabase tables when credentials are present

Primary outputs:

- `master_feed.json`
- `nba_dashboard_games.json`
- `line_movements_today.json`
- `historical_odds.json`

Database upserts are intentionally non-fatal so local feed generation still succeeds if cloud persistence is temporarily unavailable.

### 4. Orchestrate

`backend/cron_jobs/master_cron.py` runs every 5 minutes in production and coordinates three priorities:

1. Daily full pipeline refresh after 6:00 AM ET
2. Closing-line capture inside the pre-tip window
3. Intraday line-movement snapshots every 30 minutes

It uses a lock file plus persisted state to avoid duplicate runs and stale overlap.

### 5. Serve And Consume

The React frontend (`frontend/`) is intentionally thin at runtime:

- In JSON mode, it fetches prebuilt files and performs all exploration from in-memory state
- In DB mode, it hydrates lighter player records first and lazily fetches heavier detail fields

That split keeps the interface responsive while still allowing a hosted deployment path.

## Architecture Snapshot

```text
Sportsbooks + NBA data sources
           ↓
   Python scrapers
           ↓
  current CSV/JSON artifacts
           ↓
   aggregator.py canonicalizes
           ↓
 master_feed.json + Supabase upserts
           ↓
 React dashboard (JSON mode or DB mode)
```

## Notable Engineering Decisions

- Sequential scraper execution in `run_pipeline.py` reduces memory pressure on smaller VM instances.
- `SnapshotManager` preserves closing-line immutability and falls back to the last valid intraday snapshot when books move in-play too quickly.
- The frontend uses a static-first interaction model, which makes filtering and tab changes feel immediate even with dense player records.
- Historical game logs are enriched with margin, DNP context, and opponent-rank overlays so the UI can compare performance against matchup texture rather than only raw box scores.
- Supabase support is additive rather than replacing local artifacts, which keeps local development simple and the production path flexible.

## Tech Stack

### Frontend

- React 19
- TypeScript
- Vite
- Framer Motion
- Supabase JS client

### Backend

- Python
- pandas / numpy
- requests / nba_api / pbpstats
- RapidFuzz
- APScheduler-compatible scheduling patterns plus cron orchestration
- Supabase Python client

## Local Development

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd backend
python run_pipeline.py
```

To serve the generated files locally:

```bash
cd backend
npx serve --cors -p 5000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### JSON Mode

Use the locally served files:

```bash
VITE_USE_DB=false
VITE_API_BASE_URL=http://localhost:5000
```

### Supabase Mode

Frontend:

```bash
VITE_USE_DB=true
VITE_SUPABASE_URL=...
VITE_SUPABASE_ANON_KEY=...
VITE_ASSETS_URL=
```

Backend:

```bash
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
```

## Repository Guide

```text
backend/
  cron_jobs/      Scheduling and intraday market capture
  scrapers/       Source-specific extraction modules
  utils/          Aggregation, matching, snapshots, Supabase helpers
frontend/
  components/     Dashboard UI and visual analysis surfaces
  utils/          Environment and Supabase client helpers
docs/
  onboarding/     Fast orientation docs
  audit/          Current implementation audit and roadmap
  deployment/     Deployment and hosting notes
```

## Current Reality

The core ETL and visualization pipeline is real and working. A few UI surfaces are still partially placeholder-driven, most notably the Similar Players panel and some fallback states in analysis cards. Those gaps are documented in [`docs/audit/PROJECT_AUDIT_ROADMAP.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/audit/PROJECT_AUDIT_ROADMAP.md).

## Documentation

- [`LLM_CONTEXT.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/LLM_CONTEXT.md)
- [`docs/onboarding/catch_up_guide.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/onboarding/catch_up_guide.md)
- [`docs/audit/PROJECT_AUDIT_ROADMAP.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/audit/PROJECT_AUDIT_ROADMAP.md)
- [`docs/deployment/supabase_vercel_migration.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/deployment/supabase_vercel_migration.md)
