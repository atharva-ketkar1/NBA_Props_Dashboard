# PropX NBA Dashboard

An end-to-end basketball analytics product that turns fragmented sportsbook and league data into a fast, research-friendly decision surface.

**Live app:** https://propx-dashboard.vercel.app/

**Discord alerts:** https://discord.gg/HV3aG7qw

<!-- ![Dashboard preview](docs/assets/dashboard-preview.gif) -->

This project combines scheduled data collection, entity resolution, feature engineering, historical market capture, and a React dashboard tuned for high-density exploration. It is built like a small analytics platform rather than a one-off frontend: the same pipeline can publish a static JSON feed for local/offline use or upsert structured records into Supabase for hosted delivery.

## Current App Status

### What Works

- Live dashboard browsing for the current NBA slate.
- Player prop exploration across supported sportsbooks.
- Signal Score ranking, matchup context, recent-game history, and player detail views.
- Filtering by team, game, stat type, sportsbook, line value, and teammate availability context.
- Supabase-backed hosted reads plus static JSON mode for local development.

### What Does Not Work Yet

- **Check My Prop** is visible in the navigation, but the full user-facing workflow is not wired up yet.
- **User profiles/accounts** are not implemented yet.
- Saved picks, personalized dashboards, and account-specific history are not available yet.
- Some advanced analysis panels depend on available upstream data and may show empty or partial states when a feed is missing.

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
- Ranks the current slate with an explainable Signal Score powered by a fast, in-memory **CatBoost Regression Pipeline**. The model predicts prop probability via residual analysis over 69+ features (expected minutes, rolling EMAs, spatial matchup zones), and blends those inferences with live market numbers and recent form.
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
- `edge_scores_top15.json`

Database upserts are intentionally non-fatal so local feed generation still succeeds if cloud persistence is temporarily unavailable.

### 4. Orchestrate

`backend/cron_jobs/master_cron.py` runs every 5 minutes in production and coordinates three priorities:

1. Daily full pipeline refresh after 6:00 AM ET
2. Closing-line capture inside the pre-tip window
3. Intraday line-movement snapshots every 30 minutes

Signal Score recomputes inside the same refresh owners:

- after the daily pipeline
- after each intraday odds refresh
- after each pre-tip closing refresh

Its V1 projection layer is no longer just season average plus rolling box-score windows. We have introduced a **V3 ML Inference Engine**: a hyper-optimized CatBoost singleton loading a 30MB+ `.cbm` model that natively merges 69 predictive features (defensive ranks, home/away splits, score context, pacing) in pure-NumPy for 1ms predictions.

#### Dynamic Lineup Adjustments & Discord Delivery
The ML model predicts how a player historically performs, but the pipeline makes it aware of tonight's conditions in real time before creating an edge:

1. **Usage Vacuums:** It cross-references the hourly NBA injury report. If a star teammate is ruled OUT, the pipeline actively intercepts the ML projection and mathematically multiplies the player's performance cap based on freed usage percent.
2. **Blowout Penalties:** If the opposing team is missing stars, it penalizes role players to actively avoid garbage-time risks.
3. **Rest Bounces:** It identifies phantom 'DNP - Rest' games within back-to-back schedules to flip fatigue penalties to freshness bonuses.

If `EDGE_SCORE_DISCORD_WEBHOOK_URL` is configured, the system acts as a highly curated prop alert stream. When the modified ML Edge Score crosses a high-conviction threshold (72.5+), it fires a visual Discord snippet explaining exactly *why* the mathematical edge triggered (e.g., *"The regression model projects 16.5 (lineup-adjusted +20%: Cade Cunningham out)..."*). 

The Discord integration includes full intraday deduplication (it only alerts you when lines or odds officially shift), automated game-finalization cleanup, 429 webhook retry safety, and tracker grading recaps that retry dynamically once prior-day box scores are available.

To manually verify the tracker webhook target, run `python3 test_tracker_discord.py --message "manual tracker test"` from [backend/](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend). The script posts a one-off test message to the tracker webhook and prints the returned Discord `channel_id`/`message_id`.

It uses a lock file plus persisted state to avoid duplicate runs and stale overlap.

### 5. Serve And Consume

The React frontend (`frontend/`) is intentionally thin at runtime:

- In JSON mode, it fetches prebuilt files and performs all exploration from in-memory state
- In DB mode, it hydrates lighter player records first and lazily fetches heavier detail fields

That split keeps the interface responsive while still allowing a hosted deployment path.

## How The System Works

```text
NBA + sportsbook sources
        |
Python scrapers
        |
Clean CSV / JSON artifacts
        |
Aggregator + feature engineering
        |
CatBoost scoring + Edge Score
        |
Supabase and/or static JSON
        |
React + TypeScript dashboard
```

## Core Features

- Multi-source data pipeline: pulls schedule, sportsbook odds, player stats, game logs, box scores, injury context, and advanced style data.
- Player identity matching: handles messy sportsbook names with fuzzy matching and team-aware cleanup.
- ML-assisted scoring: uses exported CatBoost models and feature engineering to support prop ranking.
- Market tracking: captures intraday line movement and closing-line snapshots.
- Responsive frontend: keeps the dashboard fast by doing heavy processing before data reaches the browser.
- Flexible data delivery: supports local JSON mode for development and Supabase-backed mode for hosted usage.
- Alerting support: can send curated Discord alerts when configured with a webhook.

## Tech Stack

Frontend:

- React 19
- TypeScript
- Vite
- Framer Motion
- Supabase JS
- Vercel

Backend:

- Python
- pandas, NumPy, SciPy
- CatBoost
- nba_api, pbpstats, requests
- RapidFuzz for player matching
- Supabase Python client
- Cron-style orchestration for scheduled refreshes

## Project Structure

```text
backend/
  run_pipeline.py              Main pipeline entrypoint
  cron_jobs/                   Scheduled refresh and alert jobs
  scrapers/                    NBA, sportsbook, and context data collectors
  utils/                       Aggregation, scoring, matching, Supabase helpers
  prop_modeling/               Training, feature engineering, and exported models

frontend/
  App.tsx                      Main dashboard shell
  components/                  Dashboard panels, filters, charts, and detail views
  api/                         Vercel API routes
  utils/                       API, config, filtering, and display helpers
  public/                      Static assets served by Vite/Vercel

docs/
  assets/                      Suggested place for screenshots or GIFs
```

## Running Locally

Prerequisites:

- Node.js
- Python 3.11+
- A Supabase project if you want to test database-backed mode
- API access or network availability for the live scrapers

Install and run the frontend:

```bash
cd frontend
npm install
npm run dev
```

Set up the backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd backend
python run_pipeline.py
```

The backend writes generated files into `backend/data/current/`. The frontend can be configured to read static generated files for local development or use the Vercel API routes for production-style reads.

## Environment Variables

The exact values depend on how you run the app, but the project commonly uses:

```text
VITE_API_BASE_URL=
VITE_USE_DB=
SUPABASE_URL=
SUPABASE_SECRET_KEY=
SUPABASE_SERVICE_ROLE_KEY=
EDGE_SCORE_DISCORD_WEBHOOK_URL=
```

Keep service-role keys and webhooks server-side. Do not expose secrets in browser-facing `VITE_*` variables.

## Deployment

The public app is deployed here:

https://propx-dashboard.vercel.app/

The frontend is built with Vite and deployed through Vercel. The backend pipeline is designed to run on a scheduled machine or VM that refreshes data artifacts and/or writes to Supabase.

## What I Would Improve Next

- Add a small public demo dataset so new contributors can run the app without live scraper access
- Add CI checks for frontend build, TypeScript, and backend formatting
- Add stronger shared rate limiting for public API routes
- Expand model evaluation docs with backtest summaries and sample prediction reports
- Add authentication if private or premium data ever needs to be protected

## Disclaimer

This project is for analytics, research, and portfolio demonstration. It is not financial advice, betting advice, or a guarantee of outcomes.
