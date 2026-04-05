# NBA Prop Modeling & Discord Pipeline

This folder is the offline research and training workspace for our player prop model. The core of this system predicts player performance and powers the live Discord alerts. 

---

## 1. How the Machine Learning Model Works

### The Core Engine
I used **CatBoost**, an advanced machine learning algorithm that excels at handling categorical data (like player names or team abbreviations). Instead of simply guessing if a player goes "Over" or "Under," I used a **regression model** to predict raw statistical limits.

### How it Learns
The scripts in this folder (`build_prop_dataset.py` and `train_prop_model.py`) assemble historical datasets. The model analyzes past games, focusing on:
- **Rolling Form:** What the player averaged in their last 5, 10, or 20 games.
- **Opportunity Rates:** Their usage percentage (how much of their team's offense they carry) and play types (drives, assists).
- **Market Context:** It compares historical expectations against actual sportsbook lines and totals from previous games.

### Quantile Predictions (The Magic)
Rather than giving a single flat prediction (e.g., "LeBron will score 26 points"), our model predicts three exact percentiles:
1. **q25:** The pessimistic outcome (25% chance he scores less).
2. **q50 (The Median):** The central expectation.
3. **q75:** The optimistic outcome.

By measuring the gap between these percentiles (*the IQR or spread*), the pipeline figures out exactly how likely a player is to confidently beat a sportsbook's line (e.g., assigning a 70% probability for the Over).

---

## 2. The Real-Time Scoring Pipeline (Under the Hood)

You might wonder: *If the ML model only knows about the past, what happens if a star player gets injured right before tip-off?*

**You DO NOT need to retrain or change the ML model for this.** Our pipeline naturally solves it at the exact moment it runs. Here is the intricate breakdown of the pipeline loop (housed within `backend/utils/edge_score.py`) running every few minutes on our server:

### Step A: Gathering the Slate
The pipeline rapidly pulls in static files: the day's schedule, the canonical "master feed" of player statistics, real-time odds from PrizePicks, DraftKings, and FanDuel, and crucially, the **hourly NBA Injury Report**.

### Step B: The Dynamic Lineup Adjustment
Before ever trusting the ML model, the pipeline checks tonight's active players against the injury report. It acts as the model's eyes and ears for tonight's game:

- **The Usage Vacuum:** If a star (like Cade Cunningham) is OUT, the pipeline calculates exactly how much offensive usage is now freed up. It finds players taking his place (like Daniss Jenkins) and applies a mathematical **Usage Boost**, multiplying the ML model's baseline prediction by up to +40%.
- **Blowout Amplifications:** If an opponent's star is OUT, the game is more likely to turn into a blowout. The pipeline amplifies blowout risk algorithms, heavily cutting down projected playing time for role players who get benched in garbage time.
- **Rest Reversals:** It scans game logs for players who sat out yesterday while their team played (DNP - Rest) and flips the typical back-to-back fatigue penalty into a freshness bonus.
- **The Empty Paint:** If the opposing team sits their starting Center, the pipeline naturally inflates our player's Rebounding (REB) expectations.

### Step C: Scoring the Edge
The pipeline collects the *adjusted* ML predictions, then bundles them with other traditional analytics:
1. **Matchup Grades:** Favorable shot/zone defenses by the opposing team.
2. **Recent Form:** Hard hot/cold streaks.
3. **Line Movement:** Has the "sharp" market shifted the line since this morning?
4. **Similar Players:** How did players of a similar stylistic archetype fare against this specific line recently?

It merges all these sub-components using weighted calculations to generate a final, unified **Edge Score** out of 99.0.

### Step D: The Discord Alerts
The system ranks every candidate across every sportsbook. If a prop crosses our high thresholds for statistical edge and ML conviction, the alert triggers! 

When a payload hits the Discord tracker webhook:
- It posts the direct line (e.g. `O 14.5 Points` for Daniss Jenkins). 
- It bundles visual data like sportsbook pricing.
- Most importantly, it posts a human-readable **Reason Snippet** pieced together by the pipeline: *"The regression model projects 16.5 (lineup-adjusted +20%: Cade Cunningham out) and recent form is averaging 18.0..."* 

This completely automates professional sports-betting research, adapting dynamically as news breaks up until tip-off.
