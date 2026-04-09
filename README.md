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

## Security Model

This dashboard is a public, read-focused application. That means an important rule applies:

- Any data required to render the anonymous browser experience should be treated as potentially scrapeable.

The goal of the security model is therefore not "make all browser-visible stats secret." The goal is:

- Keep privileged credentials off the client
- Minimize bulk data exposure
- Avoid turning the server into an unrestricted database proxy
- Rate-limit and shape access patterns
- Reserve stronger protection for truly proprietary or paid-only data

### Current Security Boundaries

- Supabase service-role credentials are server-side only and are never shipped to the browser.
- The browser talks to app API routes under `frontend/api/` rather than directly querying privileged Supabase resources.
- Player-detail and archive reads use short-lived, signed tokens tied to an HttpOnly session cookie.
- API routes apply request-shaping checks such as custom app headers, browser request filtering, cache control, and per-route rate limits.
- Security headers are configured in [`vercel.json`](/Users/atharvaketkar/Desktop/NBA_Dashboard/vercel.json).

### Important Limitation

This app is still anonymous/public. So while the database is not directly exposed, data returned by public endpoints can still be reverse engineered or scraped.

Two practical implications:

- Public bootstrap data should be kept as small as possible.
- If a dataset is truly proprietary, it needs real authentication and entitlement checks, not just obscurity or header checks.

## Hardening Work Completed

Recent security-focused changes reduced unnecessary data exposure without changing the user-facing UI:

- Moved similar-player ranking to a server-side endpoint so the browser no longer needs every player's play-style profile just to compute comps.
- Added [`/api/similar`](/Users/atharvaketkar/Desktop/NBA_Dashboard/frontend/api/similar.ts) as a server-ranked similar-player route.
- Reduced the public bootstrap payload by removing bulk `play_type_analysis` from the initial all-player response.
- Moved `play_type_analysis` into the per-player detail fetch path so only the actively viewed player gets that heavier style data.
- Updated the analysis cards to wait for real player detail instead of relying on public placeholder/demo data during DB-mode rendering.

In practice, this means:

- A scraper no longer gets every player's play-type profile from the first page load.
- Similar-player logic can still work, but the underlying cross-player style input stays on the server.
- The dashboard keeps the same UX while exposing less analytical structure up front.

## Remaining Security Gaps

The current hardening is a meaningful improvement, but it is not the same thing as private-data protection.

Remaining gaps:

- Public routes are still anonymous, so determined users can script against them.
- Header checks such as `x-propx-client` are request-shaping measures, not real authentication.
- Current rate limiting is in-memory and therefore weaker in a distributed/serverless environment than a shared Redis-backed limiter.
- If all player detail/archive data should be private, the app will need authentication plus authorization rules.

## Recommended Next Steps

If the hosted version needs stronger protection, the next steps should be:

1. Move public reads onto explicitly limited public views or RLS-safe tables instead of broad privileged reads.
2. Replace in-memory per-instance rate limiting with a shared limiter such as Upstash Redis.
3. Add real auth/entitlement checks for player detail, archives, or premium analytics if those should not be public.
4. Log and monitor abusive request patterns so scraping behavior can be throttled or challenged.

## Verification Before Deployment

Before pushing the current frontend changes, the local verification run completed successfully with:

- `npx tsc --noEmit`
- `npm run build`

Those checks confirm the frontend and API TypeScript compile cleanly and the Vite production bundle builds successfully. They do not replace a final runtime smoke test on the deployed VM with real environment variables.

## Tech Stack

### Frontend

- React 19
- TypeScript
- Vite
- Framer Motion
- Supabase JS client

### Backend

- Python
- pandas / numpy / scipy
- catboost (ML Regression Inference)
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
VITE_ASSETS_URL=
```

Server-side Vercel API routes:

```bash
SUPABASE_URL=...
SUPABASE_SECRET_KEY=...
# or
SUPABASE_SERVICE_ROLE_KEY=...
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

The core ETL and visualization pipeline is real and working. A few UI surfaces still have fallback-driven or coverage-limited states, especially the missing-data fallbacks in the analysis cards and the archive-season or missing-book constraints in Similar Players. Those gaps are documented in [`docs/audit/PROJECT_AUDIT_ROADMAP.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/audit/PROJECT_AUDIT_ROADMAP.md).

## Documentation

- [`LLM_CONTEXT.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/LLM_CONTEXT.md)
- [`docs/onboarding/catch_up_guide.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/onboarding/catch_up_guide.md)
- [`docs/audit/PROJECT_AUDIT_ROADMAP.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/audit/PROJECT_AUDIT_ROADMAP.md)
- [`docs/deployment/supabase_vercel_migration.md`](/Users/atharvaketkar/Desktop/NBA_Dashboard/docs/deployment/supabase_vercel_migration.md)
