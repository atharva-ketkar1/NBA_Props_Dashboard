# NBA Prop Modeling

This folder contains the offline training pipeline, exported model contract, and feature engineering logic that power live prop recommendations in the NBA Dashboard project.

This README is written for future AI agents and engineers. It is intentionally implementation-focused rather than promotional. The goal is to explain:

- what the system does today
- which pieces are runtime-only vs retrain-required
- where the current weak spots are
- how to safely improve it without breaking the live scorer

## Current Architecture

The live system is no longer a simple over/under classifier.

It is a two-stage regression pipeline:

1. A dedicated minutes model predicts player minutes quantiles.
2. Per-stat CatBoost MultiQuantile regressors predict `q25 / q50 / q75` for each stat type.
3. The live scorer converts those quantiles into `p_over`, then combines them with other components into an `Edge Score`.

Core files:

- [build_regression_dataset.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/build_regression_dataset.py)
- [train_regression_model.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/train_regression_model.py)
- [regression_notebook.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/regression_notebook.py)
- [injury_feature_config.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/injury_feature_config.py)
- [minutes_model_config.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/minutes_model_config.py)
- [ml_inference.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/utils/ml_inference.py)
- [edge_score.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/utils/edge_score.py)

## Datasets

There are now two important training datasets:

- `generated/regression_training_dataset.csv`
  - one row per player-game-stat-type
  - target column: `actual_value`
  - used for the per-stat quantile regression models

- `generated/minutes_training_dataset.csv`
  - one row per player-game
  - target column: `actual_minutes`
  - used for the dedicated minutes model

There is also an older classifier-era prop dataset:

- `generated/prop_training_dataset.csv`
  - optional
  - mainly used for backtesting against historical posted lines
  - not the core training dataset for the current live model

## Model Types

### 1. Minutes Model

The minutes model predicts:

- `q25`
- `q50`
- `q75`

for player minutes.

It exists because many bad prop calls were really minutes misses, not stat-rate misses.

Important runtime features include:

- rolling minutes averages
- recent quarter/half minute proxies
- injury vacancy features
- teammate on/off minute deltas
- regime / return-ramp features

The live model also supports fallback behavior:

- if the minutes artifact is missing
- or the player has too little same-team current-season history

then it falls back to a heuristic blended minutes estimate instead of failing.

### 2. Per-Stat Quantile Models

There is one CatBoost MultiQuantile model per stat type.

Supported stat types:

- `PTS`
- `AST`
- `REB`
- `FG3M`
- `STL`
- `BLK`
- `PTS+REB+AST`
- `PTS+REB`
- `PTS+AST`
- `REB+AST`
- `STL+BLK`

Each model predicts:

- `q25`
- `q50`
- `q75`

The live scorer uses these to derive:

- median projection
- spread / uncertainty
- `p_over`

## Injury Modeling: What Exists Right Now

The injury logic has gone through several upgrades. The current state is:

### Team Vacancy Features

The model sees continuous missing-opportunity features, not just a binary teammate-out flag.

Examples:

- `missing_team_usage_pct`
- `missing_team_minutes`
- `missing_guard_minutes`
- `missing_same_pos_minutes`
- `missing_high_usage_usage_pct`
- `missing_playmaker_potential_ast_pg`
- `missing_onball_drives_pg`

These are built from trailing priors only. Historical rows use pre-game trailing windows only, not season-long lookahead values.

### Player-Specific Key-Teammate On/Off Features

The model also sees target-player response features such as:

- `missing_key_teammates_player_stat_delta`
- `missing_key_teammates_player_minutes_delta`
- `missing_key_teammates_player_usage_pct_delta`
- `returning_key_teammates_player_stat_delta`
- `returning_key_teammates_player_minutes_delta`

These try to capture:

- beneficiary boost when a key teammate is out
- role compression when a key teammate returns

### Promotion-to-Production Features

The latest implementation adds direct per-minute conversion features so the retrained model can learn not just “more minutes,” but also “better production per minute under promotion conditions.”

Examples:

- `missing_key_teammates_player_target_per_min_delta`
- `returning_key_teammates_player_target_per_min_delta`
- `missing_playmaker_potential_ast_pg_x_player_target_per_min`
- `missing_onball_drives_pg_x_player_target_per_min`
- `modeled_minutes_q50_x_missing_key_teammates_player_target_per_min_delta`
- `modeled_minutes_q50_x_returning_key_teammates_player_target_per_min_delta`

Important:

- these are implemented in code now
- but they only affect live predictions after retraining and exporting fresh model artifacts

## Position Resolution Contract

Position routing matters because many injury features depend on whether missing players are guards, same-position teammates, creators, etc.

Current precedence:

1. `season_stats.csv["POSITION"]`
2. modal same-team, same-season `START_POSITION` from game logs
3. live-only fallback to `master_feed.json.position`
4. final fallback `G`

The shared implementation lives in [build_regression_dataset.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/build_regression_dataset.py) through `EnrichmentData`, and is also consumed by [ml_inference.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/utils/ml_inference.py).

This was an important bugfix. Previously, too many players collapsed to `F`, which distorted `missing_guard_*` and `missing_same_pos_*`.

## Runtime Safety Layer

There are now two different concepts:

### 1. Guardrail

The promotion guardrail is ranking-only.

It can reduce the displayed score/confidence of obvious promoted-player under picks, but it does not rewrite the model median directly.

### 2. Hard Eligibility Blocking

This is stronger than the guardrail.

Recommendation objects now carry:

- `injury_sensitive`
- `eligibility_blocked`
- `eligibility_block_reason`

Two important blockers exist:

- `blocked_stale_injury_context`
  - if the injury artifact is stale, injury-sensitive picks are blocked from live push surfaces

- `blocked_promotion_under`
  - if the slate strongly suggests a promotion role and the pick is an under, the pick can be blocked from tracker/Discord

Blocked picks are:

- kept in local JSON diagnostics
- excluded from tracker payloads
- excluded from Discord delta payloads

This is intentional. The local files should stay rich for analysis; the live UX should stay curated.

## Atomic Artifact Contract

This was added to prevent live readers from loading half-written files.

Writers now use temp-file writes plus atomic replace semantics for:

- `season_stats.csv`
- injury report JSON
- exported model files
- exported metadata JSON

Important runtime assumption:

- `model_metadata.json` and `minutes_model_metadata.json` act as commit markers for model-bundle reloads

For the notebook/export path, metadata files should be written last.

## Hot Reload Behavior

[ml_inference.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/utils/ml_inference.py) now supports hot reload for long-lived processes.

It can reload when these change:

- `season_stats.csv`
- `model_metadata.json`
- `minutes_model_metadata.json`

On reload it rebuilds:

- enrichment data
- player-name and position caches
- stat models
- minutes model
- feature columns
- quantile stats

This matters mainly for long-lived API/scorer processes. Fresh cron processes naturally pick up new files on next run anyway.

## Live Scoring Path

The live path is:

1. `edge_score.py` builds candidate props from current boards
2. `ml_inference.py` builds the feature row
3. minutes model runs first
4. stat model runs second
5. `edge_score.py` turns quantiles into `p_over`
6. non-ML components are added
7. guardrail and eligibility blocking are applied
8. only eligible recommendations reach tracker/Discord

The JSON output now separates:

- `recommendations`
- `blocked_recommendations`

This is important for debugging.

## What Requires Retraining vs What Does Not

### Runtime-only changes

These do not require retraining:

- stale injury blocking
- payload separation into eligible vs blocked recommendations
- atomic file swap safety
- season-stats hot reload
- model bundle hot reload
- log readability improvements

### Retrain-required changes

These only matter after rebuilding datasets and exporting fresh models:

- new promotion-to-production per-minute features
- any new feature columns added to regression/minutes datasets
- any shift in model metadata feature lists

If you change feature engineering in:

- [build_regression_dataset.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/build_regression_dataset.py)
- [train_regression_model.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/train_regression_model.py)
- [regression_notebook.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/regression_notebook.py)
- [ml_inference.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/utils/ml_inference.py)

then you must keep training and inference in parity.

## Current Known Weak Spots

This is the most important section for future agents.

The current system is much better than the original injury-blind version, but it is still not perfect.

Known weak spots:

- promoted players can still be under-projected even when the model sees large missing-team opportunity
- the system still does not know confirmed starters, coach intent, or depth-chart decisions
- no external projected-lineup or projected-rotation feed is used
- low-volume stats like `STL`, `BLK`, and `STL+BLK` remain noisier than core stats
- injury freshness is critical; stale injury artifacts intentionally suppress injury-sensitive recommendations

This means the system is currently best described as:

- strong within the current repo/data architecture
- not omniscient about lineup intent

## Best Next Improvement Areas

If another agent is taking over, these are the highest-signal future improvement areas.

### 1. Better promotion-to-production learning

The main remaining miss pattern is:

- model sees the minutes/vacancy signal
- but still converts that into too-low stat medians

Good next work:

- stronger player-specific `target_per_min` response modeling
- better creator-vacancy to raw production conversion

### 2. Better minutes-role inference

The minutes model helped, but role changes still need stronger signals.

Potential future ideas:

- starter-likelihood proxies
- closing-lineup proxies
- rotation tier / recent substitution pattern features

### 3. Better live freshness guarantees

The stale-injury safety is good, but future agents can improve:

- cron sequencing guarantees
- artifact age visibility
- explicit no-push behavior when freshness prerequisites fail

### 4. External data sources

If the project ever allows new data, the biggest likely jump would come from:

- projected starters
- confirmed starting lineups
- depth charts
- rotation projections

At the moment, those are not used.

## How To Retrain

Standard flow:

1. Run [build_regression_dataset.py](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/build_regression_dataset.py)
2. Upload:
   - `regression_training_dataset.csv`
   - `minutes_training_dataset.csv`
   - [regression_notebook.ipynb](/Users/atharvaketkar/Desktop/NBA_Dashboard/backend/prop_modeling/regression_notebook.ipynb)
3. Run the notebook in Colab
4. Export the new model bundle
5. Replace the contents of `exported_regression_model`

When deploying:

- copy all `.cbm` files and support JSON first
- copy `minutes_model_metadata.json` and `model_metadata.json` last

That preserves the hot-reload commit-marker contract.

## Rules For Future Agents

If you modify this system, follow these rules:

1. Do not add new training features in only one place.
Training, notebook export, and live inference must stay aligned.

2. Do not silently break runtime safety.
If a change affects stale injury handling or model artifact loading, preserve the hard-block behavior.

3. Do not assume cron == long-lived process.
Cron jobs usually start fresh; long-lived scorers may need hot-reload-safe logic.

4. Keep blocked picks diagnostic-only unless explicitly requested otherwise.
They should not leak into tracker or Discord payloads.

5. Prefer atomic file commits for artifacts.
Do not write live-consumed files directly if they are used as runtime inputs.

6. When changing position logic, keep historical and live resolution rules identical unless there is a very explicit reason not to.

7. When adding new features, document whether they are runtime-only or retrain-required.

## Bottom Line

Right now, this folder contains a fairly advanced prop-model stack with:

- a minutes model
- per-stat quantile regressors
- injury vacancy modeling
- player-specific teammate on/off features
- promotion-aware runtime blocking
- hot-reload-safe artifact handling

The biggest future gains will probably come from better promotion-to-production learning and better lineup-role information, not from returning to the old binary injury heuristics.
