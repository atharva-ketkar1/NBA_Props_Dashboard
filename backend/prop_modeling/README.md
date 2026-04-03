# Prop Modeling Workspace

This folder is the offline research/training workspace for a better
Discord prop model. It is intentionally separate from `backend/utils/edge_score.py`
so model experiments do not get mixed into the production refresh path.

## Current layout

- `build_prop_dataset.py` builds a leak-safe historical training dataset.
- `train_prop_model.py` trains one CatBoost classifier per stat type and writes
  `.cbm` model artifacts plus a manifest.
- `feature_schema.py` defines shared dataset columns and feature groups.
- `generated/` holds local datasets/model artifacts and is git-ignored.

## How to build the first training table

Run from the repo root:

```bash
python3 backend/prop_modeling/build_prop_dataset.py
```

That writes:

```text
backend/prop_modeling/generated/prop_training_dataset.csv
```

`build_prop_dataset.py` also reads archived spreads/totals from:

```text
backend/data/archive/action_network_odds/
```

## What the first dataset contains

Each row is one `player_id + game_date + stat_type + sportsbook + side + line`
example with:

- final hit/miss/push label
- market fields such as line, odds, implied probability, consensus line
- game-market fields such as spread, total, team totals, implied team totals,
  and favorite/underdog context from Action Network
- rolling player-form fields computed only from games before that game date
- usage/opportunity-rate fields from prior game logs
- rest/home-away context

## Why zone/similarity features are not in v1 yet

Shooting zones, assist zones, play types, and similar-player comps already exist
in the live Signal Score path, but your repo does not currently keep historical
point-in-time snapshots of those feature families for each slate.

If we train old rows using today's `master_feed.json`, that would leak future
information into past examples and make the backtest look better than reality.

So v1 starts with safe rolling game-log + market + Action Network game-context
features only. Older rows will have blank Action Network columns until the new
Priority 4 archive builds up enough history.

Once we archive daily point-in-time snapshots for zone/style/opponent context,
we can add those groups to `feature_schema.py` and run a proper ablation study:

- baseline model
- baseline + zone/matchup features
- baseline + similar-player features
- baseline + all feature groups

## Next step

After building the dataset, train the first CatBoost models:

```bash
./.venv/bin/python backend/prop_modeling/train_prop_model.py
```

That writes per-stat `.cbm` files and metadata under:

```text
backend/prop_modeling/generated/catboost_models/
```

The intended production handoff is:

1. Train offline here.
2. Save model artifacts under `backend/prop_modeling/generated/` or a dedicated
   model registry folder.
3. Add a tiny inference adapter in `backend/utils/edge_score.py`.

## Environment note

Use `./.venv/bin/python` for this workspace. CatBoost and scikit-learn import
cleanly there.

`lightgbm` and `xgboost` currently fail because `libomp.dylib` is missing on
this machine. If you want those engines too, install OpenMP with:

```bash
brew install libomp
```

`shap` imports, but Matplotlib warns that `/Users/atharvaketkar/.matplotlib` is
not writable. If SHAP plots feel slow or noisy, set `MPLCONFIGDIR` to a
writable local directory before running SHAP-based analysis.
