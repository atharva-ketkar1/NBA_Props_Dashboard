# NBA Props Dashboard

## Overview
A high-performance web application designed to be a functional clone of [propsmadness.com](https://propsmadness.com). It aggregates NBA player props from major sportsbooks (DraftKings, FanDuel) alongside rich, dynamically-calculated player stat pipelines and historical game logs to help users identify betting edges instantly.

## Core Features
- **Live Odds Aggregation:** Automatically scrapes and standardizes player prop lines (Points, Assists, Rebounds, PRA, Threes, etc.) from DraftKings and FanDuel.
- **Advanced Player Stats:** Calculates complex seasonal averages and integrates up to 30 past game logs natively.
- **Spatial Analysis:** Visualizes "Shooting Zones" and "Assist Zones" directly on the dashboard.
- **Fuzzy Name Reconciliation:** Automatically reconciles varying player names (e.g., "PJ Washington Jr." vs "P.J. Washington") across disparate betting/stat data sources.
- **Interactive High-Density UI:** Modern, cyberpunk-inspired UI featuring dynamic bar charts for hit-rates and Similar Player comparisons. All handled purely client-side for immediate interactions.

## Tech Stack & Constraints
### Frontend
- **Framework:** React 19 with Vite (`npm run dev`)
- **Language:** TypeScript
- **Styling:** Tailwind CSS
- **Icons:** `lucide-react`
- **Data Flow:** The SPA fetches a static JSON blob `master_feed.json` on initialization, enabling instantaneous filtering and tab-switching without server delay.

### Backend
- **Environment:** Python 3.x
- **Data Processing:** `pandas`, `numpy`
- **Scraping & Connectivity:** `requests`, `nba_api`
- **Data Reconciliation Engine:** `rapidfuzz` (used heavily in the aggregator mapping logic)
- **Concurrency:** `concurrent.futures` (ThreadPoolExecutor manages parallel execution of various domain scrapers)

## Local Setup & Installation

### Prerequisites
- Node.js (v18+ recommended)
- Python (3.9+ recommended)

### 1. Data Pipeline & Backend
The backend does not run a continuous web server by default; it is a pipeline constructed to generate a finalized static feed.

```bash
# From the root directory, create a Virtual Environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the data aggregation pipeline
cd backend
python run_pipeline.py
```
This concurrent script will fetch live odds and statistics, outputting temporary CSVs into `backend/data/current/` and ultimately producing the unified `master_feed.json`.

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

1. **Scraper Domain (`backend/scrapers/`):** Modular Python scripts designed to asynchronously pull isolated streams: DraftKings odds, FanDuel odds, NBA.com seasonal stats, recent game logs, shooting coordinates, and assist vectors.
2. **Aggregator Engine (`backend/utils/aggregator.py`):** The brain of the backend. It ingests all scraped datasets, normalizes disjointed player names into absolute IDs via the `PlayerMatcher` utility, calculates composite props (PRA, Stocks), and emits a massive, securely mapped `master_feed.json`.
3. **Frontend Application (`frontend/App.tsx`):** A stateless React/TypeScript Single Page Application. Upon mount, it pulls the `master_feed.json`. All subsequent state—such as selecting a player, altering the target sportsbook, or changing the stat filter (Points, Rebounds, Assists)—routes strictly through local React state with zero additional networking overhead.
