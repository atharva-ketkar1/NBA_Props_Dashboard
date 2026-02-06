# NBA Dashboard Backend

## Overview
This repository hosts the backend data engineering pipeline for the NBA Dashboard. It is designed to autonomously fetch, process, and aggregate real-time NBA data and sports betting odds from multiple sources. The system produces a unified `master_feed.json` that serves as the single source of truth for the frontend application.

## Features
- **Multi-Source Scraping**: Fetches data from NBA.com (Official Stats) and Sportsbooks (DraftKings, FanDuel).
- **Advanced Aggregation**: Merges disparate datasets (Season Stats, Game Logs, Betting Odds) into a coherent player model.
- **Smart Caching**: Incremental updates for game logs to minimize API load.
- **Resilience**: Implements retries, jitter, and error handling to respect external API rate limits.

## Project Structure

```bash
NBA_Dashboard/
├── backend/
│   ├── data/               # Generated Data Store
│   │   └── current/        # Live CSVs and the Master JSON Feed
│   │
│   ├── scrapers/           # Data Fetching Modules
│   │   ├── fetch_odds_draftkings.py  # Real-time odds from DK
│   │   ├── fetch_odds_fanduel.py     # Real-time odds from FD
│   │   ├── season_stats_scrape.py    # Official NBA Season Averages
│   │   └── gamelogs.py               # Rolling 35-game history (Incremental)
│   │
│   ├── utils/              # Helper Utilities
│   │   └── aggregator.py   # Core logic to merge all CSVs into JSON
│   │
│   ├── maintainence/       # Static Assets
│   │   ├── get_player_headshots.py
│   │   └── get_team_logos.py
│   │
│   └── run_pipeline.py     # Main Orchestrator (Entry Point)
└── README.md
```

## Setup & Installation

1. **Prerequisites**
   - Python 3.9+ recommended.

2. **Install Dependencies**
   ```bash
   pip install -r backend/requirements.txt
   ```
   *Key libraries: `pandas`, `requests`, `nba_api`, `numpy`.*

## Usage

### 1. Run the Data Pipeline
To update all data sources and regenerate the master feed, run the orchestration script:

```bash
python backend/run_pipeline.py
```

**What happens?**
- **Parallel Scraping**: DraftKings, FanDuel, and NBA Stats scrapers run concurrently.
- **Game Log Sync**: The `gamelogs.py` module checks for new games (excluding today) and incrementally updates the history.
- **Aggregation**: The `aggregator.py` reads the fresh CSVs, maps betting (snake_case) to stats (UPPERCASE), and generates `backend/data/current/master_feed.json`.

### 2. Update Assets
To download the latest player headshots or team logos:

```bash
python backend/maintainence/get_player_headshots.py
python backend/maintainence/get_team_logos.py
```

## Data Pipeline Details

### Scrapers
- **Season Stats**: Fetches comprehensive stats (Base, Passing, Drives, Rebounding) to calculate advanced metrics like `AGRESSION_SCORE`.
- **Game Logs**: Maintains a rolling 35-game history for trend analysis. Uses a smart "Update Window" to catch stat corrections.
- **Odds**: Fetches Prop Bets (Points, Rebounds, Assists, Threes, etc.) and sanitizes input (handling "EVEN" odds, unicode minus signs).

### Aggregation Logic
The `aggregator` is the brain of the backend. It:
1.  **Normalizes Names**: Maps "Luka Doncic" (Odds) to "Luka Dončić" (Stats).
2.  **Calculates Combos**: Computes implied stats like `PTS+REB+AST` if not provided.
3.  **Filters**: Excludes players with no active props or stats (removing noise).
4.  **JSON Output**: Produces a clean, frontend-ready JSON object keyed by Player ID.

```json
{
  "2544": {
    "name": "LeBron James",
    "stats": { "PTS": 24.5, "AST": 7.2 ... },
    "game_log": [ ... last 35 games ... ],
    "props": {
      "POINTS": {
         "dk": { "line": 24.5, "over": -110, "under": -110 },
         "fd": { "line": 24.5, "over": -112, "under": -108 }
      }
    }
  }
}
```

## TODO
- [ ] Add the frontend to the project.
- [ ] Add more stats like shooting zones, shot types, etc.
- [ ] Add more features like player comparison, team comparison, etc.
- [ ] Migrate to a database.


