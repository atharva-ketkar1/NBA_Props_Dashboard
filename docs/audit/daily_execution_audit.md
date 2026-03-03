# Daily Execution Audit & Architecture Review

This document serves as an exhaustive audit of the daily execution flow of the NBA Dashboard project. It details exactly what happens when scripts are run, how scheduled tasks operate, the structural bottlenecks, and the potential failure points within the data pipeline.

---

## 1. The Core Execution Flows

The backend of this project is decoupled from the frontend web server. It primarily relies on two distinct execution tracks to assemble the data needed by the React application.

### A. The Master Pipeline (`backend/run_pipeline.py`)
This is a manual, one-off script that generates the foundational `master_feed.json` file. It is the heavy lifter.

**What happens when you run `python run_pipeline.py`?**
1. **Parallel Scraping:** The script spins up a `concurrent.futures.ThreadPoolExecutor` and launches 13 individual Python scrapers simultaneously.
   - Odds: DraftKings (`fetch_dk_odds`) & FanDuel (`fetch_odds`)
   - Traditional Stats: `season_stats_scrape`, `gamelogs`, `boxscores`, `schedule`
   - Spatial/Advanced: `shooting_zones`, `assist_zones`, `opp_assist_zones`, `opp_def_zones`, `shot_type_analysis`, `opp_shot_type_analysis`, `play_type_analysis`
2. **Data Sinks:** Each scraper independently saves a temporary artifact (CSV or JSON) into `backend/data/current/`.
3. **Aggregation (`aggregator.py`):** Once all futures resolve, `aggregator.run_aggregation()` calculates compound props (e.g. PRA), merges spatial metrics to the correct player via the `PlayerMatcher` (to handle name spelling discrepancies between books and stat APIs), and evaluates defensive ranks. It outputs the single `master_feed.json` payload for the Vite frontend.

### B. The Intraday Scheduler (`backend/scheduler.py` & `SnapshotManager`)
While the Master Pipeline is run manually to get the bulk stats, `scheduler.py` is meant to be run constantly as a background daemon (via `APScheduler`). Currently, there are no OS-level `cron` jobs active; everything depends on this Python process remaining alive.

**What `scheduler.py` actually does:**
1. **Intraday Odds Snapshots:** At strictly scheduled times (11:00 AM, 1:00 PM, 3:00 PM, 5:00 PM ET), it spins up *only* the DraftKings and FanDuel scrapers.
   - It formats the odds and uses `SnapshotManager` to append a timestamped block to `line_movements_today.json`.
   - **Guardrail:** It has a deduplication guardrail preventing duplicate snapshotting if another one occurred less than 30 minutes prior.
2. **Closing Lines Capture:** It reads `today_schedule.json` to identify when games actually tip-off. It schedules dynamic jobs precisely at those deadlines.
   - **Gate 1 (Immutability):** Verifies the deadline hasn't fully passed.
   - **Gate 2 (Availability):** Checks if the book marks the bet as `inPlay` or pulls it off the board. If so, it falls back to the *last valid intraday snapshot*. 
   - It appends the definitive line to `data/archive/historical_odds.json`.

---

## 2. Process Audits: The Good, The Bad, and The Risky

### What is going well?
* **Stateless Frontend:** The choice to compile all backend logic into static JSON blobs (`master_feed.json`) means the React application (`npm run dev`) is incredibly fast. Selecting components, switching tabs, and rendering canvas charts requires zero network latency.
* **Aggregator Resilience:** `aggregator.py` successfully mitigates one of the hardest aspects of sports data—name mismatches between DraftKings ("Nic Claxton"), FanDuel ("Nicolas Claxton"), and NBA.com ("Nicolas Claxton"). The `PlayerMatcher` fuzzy-logic implementation holds the structure together securely.
* **Separation of Concerns:** Splitting the heavy static generation (`run_pipeline.py`) from the transient odds tracking (`scheduler.py`) keeps API calls focused and limits bandwidth usage.

### Slow Points & Rate Limiting (The Bottlenecks)
* **The "Parallel Scrape" Spike:** When `run_pipeline.py` executes, 13 concurrent scraping requests are fired. While fast, firing this many requests simultaneously to the same upstream hosts (like NBA Stats or PBPStats) significantly spikes the risk of IP blocks.
* **PBPStats 403 Errors:** Files like `assist_zones.py` have historically triggered severe rate limits from PBPStats. The project currently masks these requests using Cloudflare Worker Proxies to bypass IP bans. 
* **Fallback Executions:** When PBPStats fails, the scrapers have NBA Stats fallbacks. However, these fallbacks are inherently slower and require their own `time.sleep()` delays to avoid secondary bans from the NBA API.

### What is going wrong / What could go wrong?
* **No Daemonization (Missing Cron Jobs):** As confirmed via terminal audit, there are no `crontab` configurations. This means `scheduler.py` has to be run manually in a terminal tab and left open. If the terminal closes, or the machine sleeps, intraday movement tracking and closing line captures vanish completely. `run_pipeline.py` is also entirely manual right now.
* **Data Disconnect in Aggregation:** `aggregator.py` does its best to map data, but relies heavily on fallbacks. If a player acts as a late sub and their `POSITION` is `nan` or missing in the core numeric stats, it falls back to logs. If they lack a log, their spatial maps may fail to render properly on the frontend.
* **OOM (Out of Memory) Risk on Frontend:** As `historical_odds.json` grows infinitely every single day, pulling it eventually into the UI (if implemented) will bloat the memory footprint of the browser. A rolling archiving logic (or keeping only the last 30 days in the active blob) will be necessary.
* **Empty Responses Causing Null Pointers:** If DraftKings decides to change their undocumented API schema, `fetch_dk_odds` will return an empty list. `aggregator.py` will skip them, resulting in a UI that displays absolutely no DraftKings tabs for the day, silently failing unless the terminal logs are checked.

## 3. Summary of Daily Operations

To keep the application perfectly updated in its current state, a user must:
1. Open a terminal and run `python backend/scheduler.py` and leave it running in the background all day.
2. Open another terminal and run `python backend/run_pipeline.py` at least once in the morning to generate `master_feed.json`.
3. Start the node server `npx serve --cors -p 5000` in the backend to serve the files.
4. Run the frontend `npm run dev`.

The immediate next evolution for the project must be containerizing `run_pipeline.py` and `scheduler.py` into cloud chron jobs (AWS Lambda, Google Cloud Run) or a dedicated local Docker deployment.
