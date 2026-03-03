# 🔥 Catch-Up Guide: NBA Props Dashboard

Welcome to the NBA Props Dashboard (Propsmadness clone) project! If you've been dropped into this repository and need to get your bearings *instantly*, start here.

## 1. What Are We Building?
We're building a high-density, cyberpunk-themed web application that gives NBA bettors an unparalleled visual edge. Instead of dry tables, we show beautiful hit-rate bar charts, spatial shooting/assist zones, Shot Type Analysis (Catch & Shoot/Pull Ups), and combined stat comparisons (PRA - Points/Rebounds/Assists). 

It looks like magic on the frontend, but beneath the hood, it's driven by a rigorous, battle-tested data pipeline.

## 2. How The Data Actually Flows (Read This First)
The biggest mistake new devs make here is assuming the React frontend talks to a live Python backend. **It does not.** 

1. **Python does the heavy lifting via two execution tracks:** 
   - **The Master Pipeline**: Run `python run_pipeline.py` inside the `backend` folder. This launches roughly 13 scrapers asynchronously. It pulls NBA.com stats, shot charts, boxscores, and game logs. Because upstream services (like PBPStats) often impose **IP bans**, some of these scrapers use Cloudflare Worker proxies and NBA Stats fallbacks to bypass 403 blocks safely. It merges all of this using intense string-matching logic and spits out one giant file: `backend/data/current/master_feed.json`.
   - **The Intraday Scheduler**: Run `python scheduler.py` on the backend. This daemon runs continuously, fetching DraftKings and FanDuel odds at set intraday intervals (11am, 1pm, etc.) and precisely capturing closing lines right before tip-off.

2. **React serves the finalized data locally:**
   The frontend (`npm run dev`) just does a network fetch for `master_feed.json`. That is its entire "backend API." Once that JSON is in `App.tsx` state, flipping between "DraftKings" and "FanDuel", checking "Assist Zones", or swapping to "Rebounds", is entirely instantaneous because all 20,000+ data points are already loaded into browser memory.

## 3. The 3 Things You Will Likely Break First
If you're making modifications, watch out for these landmines:

- **Adding a New Stat (e.g., Turnovers or Triple Doubles):** You must define it in the Python `aggregator.py` first so it gets appended to the JSON. Then, you *must* update `frontend/types.ts` so React knows it exists, and then add it to `Header.tsx` so the user can select it.
- **Triggering Downstream Rate Limits:** Because we pull dense spatial data (like Opponent Assist Zones), firing the pipeline without timeouts or custom headers will get your IP banned. Rely on the incorporated proxies and do NOT remove `time.sleep()` calls indiscriminately.
- **CSS Z-Index and Layouts:** The app uses Tailwind and expects a very specific fixed "Cockpit" viewport style that matches Propsmadness. If you add a new generic div wrapper in `App.tsx` or `Layout.tsx`, you risk breaking the flexbox alignments and chart rendering spaces. Keep formatting consistent.

## 4. Where to Find Specific Logic
- I need to change how PRA is calculated → `backend/utils/aggregator.py`
- I need to add Cloudflare forwarding endpoints to a new scraper → Model it after `backend/scrapers/assist_zones.py`
- I need to fix how the bar chart hits/misses look → `frontend/components/BarChart.tsx`
- I want to scrape ESPN or MGM next → Copy `fetch_odds_draftkings.py`, rename it, and plug it into `run_pipeline.py`
- I want to see the project's recent changes → Read `docs/progress/status.md`

## 5. Development Command Cheat Sheet

### Run the Data Generator (Do this to update the massive stats feed)
```bash
cd backend
source .venv/bin/activate # Assuming you made a venv
python run_pipeline.py
```

### Run the Intraday Server (Keep this running to track odds movement)
```bash
cd backend
source .venv/bin/activate
python scheduler.py
```

### Serve the Backend Data (Leave this running in terminal tab)
```bash
cd backend
npx serve --cors -p 5000
```

### Run the Frontend Server (Leave this running in terminal tab 2)
```bash
cd frontend
npm run dev
# App is available at http://localhost:5173
```
