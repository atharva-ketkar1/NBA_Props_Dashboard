# 🔥 Catch-Up Guide: NBA Props Dashboard

Welcome to the NBA Props Dashboard (Propsmadness clone) project! If you've been dropped into this repository and need to get your bearings *instantly*, start here.

## 1. What Are We Building?
We're building a high-density, cyberpunk-themed web application that gives NBA bettors an unparalleled visual edge. Instead of dry tables, we show beautiful hit-rate bar charts, spatial shooting/assist zones, and combined stat comparisons (PRA - Points/Rebounds/Assists). 

It looks like magic on the frontend, but beneath the hood, it's driven by a rigorous data pipeline.

## 2. How The Data Actually Flows (Read This First)
The biggest mistake new devs make here is assuming the React frontend talks to a live Python backend. **It does not.** 

1. **Python does the heavy lifting via cron/manual execution:** 
   Run `python run_pipeline.py` inside the `backend` folder. This launches roughly 7 scrapers synchronously/asynchronously. It pulls DraftKings/FanDuel odds, NBA.com stats, game logs, and shot charts, merges them using some intense string-matching logic (because betting sites spell names differently), and spits out one giant file: `backend/data/current/master_feed.json`.

2. **React serves the finalized data locally:**
   The frontend (`npm run dev`) just does a network fetch for `master_feed.json`. That is its entire "backend API." Once that JSON is in `App.tsx` state, flipping between "DraftKings" and "FanDuel", or checking "Rebounds" instead of "Points", is entirely instantaneous because all 20,000+ data points are already loaded into browser memory.

## 3. The 3 Things You Will Likely Break First
If you're making modifications, watch out for these landmines:

- **Adding a New Stat (e.g., Turnovers or Triple Doubles):** You must define it in the Python `aggregator.py` first so it gets appended to the JSON. Then, you *must* update `frontend/types.ts` so React knows it exists, and then add it to `Header.tsx` so the user can select it.
- **Player Matcher Errors:** "Why is Nick Claxton missing odds?" Because FanDuel calls him "Nic Claxton" and NBA.com calls him "Nicolas Claxton". Look at `backend/utils/player_matcher.py`. The fuzzy matching handles 95% of cases, but hardcoded aliases are sometimes required.
- **CSS Z-Index and Layouts:** The app uses Tailwind and expects a very specific fixed "Cockpit" viewport style. If you add a new generic div wrapper in `App.tsx` or `Layout.tsx`, you risk breaking the flexbox alignments and chart rendering spaces.

## 4. Where to Find Specific Logic
- I need to change how PRA is calculated → `backend/utils/aggregator.py`
- I need to fix how the bar chart hits/misses look → `frontend/components/BarChart.tsx`
- I want to scrape ESPN or MGM next → Copy `fetch_odds_draftkings.py`, rename it, and plug it into `run_pipeline.py`
- The sidebar isn't filtering properly → `frontend/components/Sidebar.tsx` or `frontend/App.tsx`

## 5. Development Command Cheat Sheet

### Run the Data Generator (Do this to update the data feed)
```bash
cd backend
source .venv/bin/activate # Assuming you made a venv
python run_pipeline.py
```

### Serve the Backend Data (Leave this running in terminal tab 1)
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
