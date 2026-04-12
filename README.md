# PropX NBA Dashboard

An end-to-end NBA player prop analytics dashboard that collects sportsbook and league data, cleans it into one player view, scores opportunities with machine learning, and presents the results in a fast React interface.

Live project: https://propx-dashboard.vercel.app/

## Preview

Add a screenshot or GIF of the app here:

![PropX Dashboard preview](docs/assets/dashboard-preview.gif)

Tip: create `docs/assets/dashboard-preview.gif` or replace the image path above with a GitHub-uploaded image URL.

## Why This Project Matters

This project is more than a frontend dashboard. It is a full-stack data product built around a real-world problem: sports data arrives from different sources, changes throughout the day, and is often messy or incomplete.

For recruiters, this project demonstrates:

- Full-stack ownership across React, TypeScript, Python, Supabase, and Vercel
- Data engineering with scheduled scraping, normalization, artifact generation, and database upserts
- Practical machine learning with CatBoost models used in a live scoring pipeline
- Product thinking through a dashboard designed for fast filtering, comparison, and decision support
- Production-minded safeguards such as fallback data, non-fatal database writes, deduped alerts, and runtime-friendly payloads

## What It Does

PropX helps users research NBA player props by combining current market lines with basketball context.

The dashboard can show:

- Current NBA player prop lines from supported books
- Player season stats, recent game logs, and matchup context
- Shooting zones, assist zones, shot type splits, and play type analysis
- Line movement and market history throughout the day
- Similar-player comparisons
- Ranked prop opportunities through an explainable Edge Score
- Optional Discord alerts for high-confidence recommendations

The goal is not to blindly tell users what to bet. The goal is to organize a large amount of changing information into a clear research surface.

## Beginner-Friendly Overview

If you are new to full-stack projects, here is the simple version:

- The backend collects raw NBA and sportsbook data.
- The backend cleans that data so player names, teams, stats, and games match across sources.
- The model estimates how attractive each prop looks based on historical and live context.
- The frontend turns the processed data into a dashboard that is quick to explore.
- The deployed app is hosted on Vercel and can read from either static JSON files or Supabase-backed API routes.

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
