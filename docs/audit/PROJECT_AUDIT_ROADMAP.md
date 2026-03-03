# NBA Props Dashboard Audit & Roadmap

## Overview
This document serves as a comprehensive audit of the current state of the NBA Props Dashboard, focusing on the specific data relationship between the backend data pipeline (`master_feed.json`) and the frontend React components. It highlights what is fully implemented, what relies on hardcoded data, placeholders, or missing backend fields, and provides a roadmap for future development.

## Data Field Discrepancies (Missing or Mocked Data)
After a deep dive comparing the backend aggregator output (`aggregator.py` & `master_feed.json`) to the frontend components, several critical data points are missing or improperly mocked:

### 1. Hover Tooltip (`HoverTooltip.tsx`)
The Tooltip component contains the most hardcoded and missing data:
* **Fouls (1Q):** The UI explicitly labels the stat as "Fouls (1Q)", but it is improperly mapped to the full-game personal fouls stat (`game.PF`). The backend game logs do not supply quarter-specific foul splits.
* **Assists (1Q) / Quarter-Specific Props:** Similar to Fouls, the backend only provides full-game `AST` data. If the user selects a "1Q Assists" or "1H Points" tab, the tooltip has no way to display those exact metric splits.
* **Passes Made:** For the Assist tab, the tooltip expects `game.PASSES`, which does not exist in the backend feed. It currently falls back to rendering `'N/A'`.
* **Historical Odds:** The Over/Under odds inside the tooltip header for *past* games are completely hardcoded variables (`const O_ODDS = '-125'; const U_ODDS = '-102';`). The backend only scrapes *current* odds from DraftKings/FanDuel.
* **Won / Lost By:** The tooltip calculates "Won by X" or "Lost by X" simply based on `game.score - lineValue`. Because `lineValue` is the *current* active prop line for today's game, it incorrectly applies today's line to historical games (e.g., if LeBron's line is 25.5 today, it assumes his line was 25.5 two weeks ago).
* **DID NOT PLAY (DNP):** The entire DNP section is completely hardcoded. It uses a mock array called `inactivePlayers` containing "M. MCBRIDE", "L. SHAMET", and "G. YABUSELE" with static points and headshots, regardless of what team or game is being hovered over.

### 2. Header (`Header.tsx`)
* **Usage Rate:** The component expects `seasonStats['usage']` for the player's ticker data. However, the backend stats script does not provide a `usage` metric. It currently falls back to `0.0` or `0.0%`.

### 3. Analysis Components (`PlayTypeAnalysis.tsx` & `ShotTypeAnalysis.tsx`)
* **Fallback Hardcoding:** Both components correctly accept dynamic data passed down from `App.tsx` (`player.play_type_analysis` & `player.shot_type_analysis`). However, if a player does not have this data, the components immediately fall back to importing static `PLAY_TYPES` (Transition, PNR, Spot Up) and `DEFAULT_SHOT_TYPES` (C&S, <10ft) arrays to render mock visualizations.

### 4. Similar Players (`SimilarPlayers.tsx`)
* **Complete Hardcode:** This component is completely disconnected from the backend. `App.tsx` explicitly renders it as `<SimilarPlayers similarGames={undefined} />`. It internally falls back to `SIMILAR_GAMES` from `constants.ts`, rendering placeholder games for completely unrelated players.

## Component Implementation Status
* **Fully Dynamic:** `BarChart.tsx`, `ShootingZones.tsx`, `AssistZones.tsx`. (Note: `AssistZones` correctly implements a graceful "No Data Available" UI overlay instead of faking data).
* **Partially Dynamic:** `Header.tsx`, `PlayTypeAnalysis.tsx`, `ShotTypeAnalysis.tsx`.
* **Fully Mocked:** `SimilarPlayers.tsx`.

## Roadmap & Future Plans

### Short-Term Backend Updates
1. **Fix Missing Stats:** Update `season_stats_scrape.py` and `gamelogs.py` to extract `usage` percentage, `passes` made per game, and parse play-by-play data to isolate quarter-specific stats (1Q/1H splits) for Points, Assists, Rebounds, and Fouls.
2. **Implement Similar Players:** Create a Python processor that searches recent game logs to find players of the same position/archetype who played against the current opponent, and append this to `master_feed.json`.
3. **Historical Odds Tracking:** Implement a database or chron-job system to save daily prop lines so that the `HoverTooltip` can compare a historical game's performance to its *actual* historical line, rather than today's active line.

### Short-Term Frontend Updates
1. **Remove Danger Fallbacks:** Refactor `PlayTypeAnalysis`, `ShotTypeAnalysis`, and `HoverTooltip` (DNP Section) to remove their hardcoded arrays. Implement empty-state UI overlays (similar to `AssistZones`) so users do not accidentally formulate bets based on placeholder mock data.
2. **Dynamic DNP Roster:** Map the HoverTooltip's DNP list to actual injured or inactive players for that specific historical game, utilizing the team's roster data.

## Operational Architecture & File Dependencies
With the recent introduction of PM2 and Cron, the backend is now rapidly shifting toward a fully daemonized, automated steady-state schedule.

### 1. The Cron Job (Daily Master Scrape)
* **Command:** `0 6 * * * /usr/bin/python3 .../backend/run_pipeline.py >> pipeline.log`
* **Purpose:** Every morning at 6:00 AM, the cron job executes the heavy master pipeline. This spins up the 13+ parallel workers to scrape DK, FD, NBA Stats, Spatial Zones, and Game Logs.
* **Dependencies Handled:** 
  * Writes to temporary sinks in `data/current/` (e.g., `nba_dashboard_games.json`, `season_stats.csv`).
  * Aggregates and finalizes `master_feed.json`, which the React frontend instantly consumes.
  * Ensures that day-to-day, the user wakes up to freshly processed stats without manually running scripts.

### 2. PM2 Processes (Steady-State Daemons)
Currently, PM2 manages two critical persistent processes, ensuring continuous uptime:
1. **`nba-json-server` (`npx serve --cors -p 5000`):** Acts as the local area network layer, ensuring the React app can infinitely fetch `master_feed.json` without CORS issues.
2. **`nba-odds-scheduler` (`scheduler.py`):** The beating heart of the intraday tracking. It wakes up at specific intervals (11 AM, 1 PM, 3 PM, 5 PM) to scrape DraftKings and FanDuel and safely append the delta to `line_movements_today.json`.

### 3. File Dependency Bottlenecks & Critical Risks
While PM2 and Cron automate the execution triggers, a major internal file dependency disconnect currently threatens the closing line captures:
* **The Schedule File Disconnect:** The PM2 `scheduler.py` daemon and its `snapshot_manager.py` heavily rely on loading a file named `today_schedule.json`. It expects this file to contain an object array with precisely formatted `closing_scrape_deadline` strings. However, the daily `fetch_todays_games.py` script currently outputs its payload to `nba_dashboard_games.json` and does *not* generate a `closing_scrape_deadline` field. 
* **Impact:** Because of this disjointed dependency, the PM2 `scheduler.py` will actively record standard 2-hour intraday snapshots, but it will continuously fail to execute the dynamic "Closing Lines Scrape" (Gate 1 & Gate 2), silently failing.
* **Day-to-Day Resolution Requirement:** For the system to achieve true "steady-state" perfection where `historical_odds.json` accurately logs closing lines day-to-day, `fetch_todays_games.py` must be refactored to output `today_schedule.json` with strict datetime ISO deadlines.
