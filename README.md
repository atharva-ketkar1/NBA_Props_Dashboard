# NBA Props Dashboard

## Overview
A high-performance web application designed to be a functional clone of [propsmadness.com](https://propsmadness.com). It aggregates NBA player props from major sportsbooks (DraftKings, FanDuel) alongside rich, dynamically-calculated player stat pipelines and historical game logs to help users identify betting edges instantly.

## Core Features
- **Live Odds Aggregation:** Automatically scrapes and standardizes player prop lines (Points, Assists, Rebounds, PRA, Threes, etc.) from DraftKings and FanDuel.
- **Advanced Player Stats:** Calculates complex seasonal averages and integrates up to 30 past game logs natively.
- **Historical Closing Lines & Intraday Movement:** Maintains a persistent append-only cache (`historical_odds.json`) for precise pre-game closing lines and records daily fluid odds snapshots via `snapshot_manager.py` and APScheduler.
- **Spatial Analysis & Shot Tracking:** Visualizes "Shooting Zones", "Assist Zones", and "Shot Type Analysis" (Catch & Shoot, Pull Up, <10ft). Evaluates player performance dynamically against **Opponent Defense Ranks**, utilizing a custom **Matchup EV Score** logic that factors in defensive bell curves, player efficiency modifiers, and volume gravity to prevent betting traps.
- **Resilient Pipeline Architecture:** Utilizes Cloudflare Worker proxies and NBA Stats fallbacks to intelligently bypass IP bans from strict upstream data sources like PBPStats.
- **Fuzzy Name Reconciliation:** Automatically reconciles varying player names (e.g., "PJ Washington Jr." vs "P.J. Washington") across disparate betting/stat data sources.
- **Interactive High-Density UI:** Modern, cyberpunk-inspired UI matching the Propsmadness layout precisely, featuring dynamic bar charts for hit-rates, Similar Player comparisons, and multi-view spatial canvases. All handled purely client-side for immediate interactions.

## Recent Architecture Updates
- **GCP VM Deployment & Memory-Safe Pipeline:** The backend is now deployed on a Google Cloud Platform (GCP) Virtual Machine. To prevent OOM crashes on memory-constrained instances, the pipeline uses sequential execution for heavy scrapers, explicit garbage collection, and incremental appending for large temporal datasets like game logs.
- **Unified Cron Director (`master_cron.py`):** Replaced individual crontab schedules with a single, every-5-minute director script. It seamlessly orchestrates **Priority 1** (6:00 AM Full Pipeline), **Priority 2** (Dynamic Pre-game Closing Lines), and **Priority 3** (Intraday Snapshots) while managing state files and process locks to prevent scraper collisions.
- **Log File Organization:** Standardized logging where cron outputs are dynamically piped into timestamped daily files (e.g., `cron_output_YYYY-MM-DD.log`) and isolated into subdirectories like `logs/master_cron/` and `logs/pipeline/`.
- **Proxy Integration:** Deployed a dedicated proxy configuration for the NBA `leaguegamelog` endpoints to resolve persistent connection timeouts from the VM.

## Tech Stack & Constraints
### Frontend
- **Framework:** React 19 with Vite (`npm run dev`)
- **Language:** TypeScript
- **Styling:** Tailwind CSS (Strict adherence to provided PropMadness Mock-up layouts)
- **Icons:** `lucide-react`
- **Data Flow:** The SPA fetches a static JSON blob `master_feed.json` on initialization, enabling instantaneous filtering and tab-switching without server delay.

### Backend
- **Environment:** Python 3.9+
- **Data Processing:** `pandas`, `numpy`
- **Data Caching & Scheduling:** `apscheduler` (for intraday snapshots and closing line captures)
- **Scraping & Connectivity:** `requests`, `nba_api`, Cloudflare Workers (CORS/Proxy bypass)
- **Data Reconciliation Engine:** `rapidfuzz` (used heavily in the aggregator mapping logic)
- **Concurrency:** `concurrent.futures` (ThreadPoolExecutor manages parallel execution of various domain scrapers)

## Local Setup & Installation

### Prerequisites
- Node.js (v18+ recommended)
- Python (3.9+ recommended)

### 1. Data Pipeline & Backend
The backend is fully orchestrated via a unified cron director in production but can be run manually for local development.

```bash
# From the root directory, create a Virtual Environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Manually trigger the full pipeline compilation
cd backend
python run_pipeline.py
```
`run_pipeline.py` sequentially fetches live odds and statistics (to preserve memory), outputting temporary CSVs into `backend/data/current/` and ultimately producing the unified `master_feed.json`.

**Production Cron Setup (GCP VM):**
In production, a single crontab entry executes the `master_cron.py` director every 5 minutes:
```bash
*/5 * * * * cd /home/ketkaravatar/NBA_Props_Dashboard/backend && /usr/bin/timeout 2700 /home/ketkaravatar/NBA_Props_Dashboard/.venv/bin/python cron_jobs/master_cron.py >> /home/ketkaravatar/NBA_Props_Dashboard/backend/logs/master_cron/cron_output_$(date +\%Y-\%m-\%d).log 2>&1
```
The director automatically handles the daily pipeline refresh at 6:00 AM, dynamic tracking of pre-game closing lines 10 minutes before tip-off, and 30-minute intraday snapshots.

**Serving the Data API:**
For local Vite development to access the data without CORS issues, serve the backend directory on port 5000:
```bash
cd backend
npx serve --cors -p 5000
```

### 2. Frontend Setup
```bash
cd frontend

# Install necessary node_modules
npm install

# Start the Vite development server
npm run dev
```
The client will be running at `http://localhost:5173`. Make sure the `.env.local` or `.env` inside `frontend` correctly points to the served backend data source (e.g., `VITE_API_BASE_URL=http://localhost:5000`).

## Project Architecture Overview

The system architecture is a **decoupled, periodic static-generation engine**:

1. **Scraper Domain (`backend/scrapers/`):** Modular Python scripts designed to asynchronously pull isolated streams: DraftKings odds, FanDuel odds, NBA.com seasonal stats, recent game logs, shooting coordinates, assist vectors, shot types, opponent defensive ranks, and the active schedule (outputs `nba_dashboard_games.json` and `today_schedule.json` with closing deadlines). Uses Cloudflare proxies to prevent IP rate-limits.
2. **Scheduling & Caching Pipeline (`backend/cron_jobs/master_cron.py` & `backend/utils/snapshot_manager.py`):** A custom director script (`master_cron.py`) executes every 5 minutes via Linux crontab. It governs state and process locks while executing Intraday Line Movement captures and precise, pre-game Closing Line snapshots with immutability guarantees.
3. **Aggregator Engine (`backend/utils/aggregator.py`):** The brain of the backend. It ingests all scraped datasets, normalizes disjointed player names into absolute IDs via the `PlayerMatcher` utility, calculates composite props, appends spatial structures, and emits `master_feed.json`.
4. **Frontend Application (`frontend/App.tsx`):** A stateless React/TypeScript Single Page Application. Upon mount, it pulls the `master_feed.json`. All subsequent state—such as selecting a player, altering the target sportsbook, expanding Shot Type Analysis, or changing the stat filter—routes strictly through local React state with zero additional networking overhead.
