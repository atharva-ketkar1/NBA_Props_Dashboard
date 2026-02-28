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
