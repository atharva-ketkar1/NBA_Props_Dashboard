"""Shared schema and feature-group definitions for prop model experiments."""

from __future__ import annotations

from pathlib import Path


PROP_MODELING_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROP_MODELING_DIR.parent
GENERATED_DIR = PROP_MODELING_DIR / "generated"
DEFAULT_DATASET_PATH = GENERATED_DIR / "prop_training_dataset.csv"
DEFAULT_MODEL_DIR = GENERATED_DIR / "catboost_models"

DEFAULT_GAMELOG_PATHS = [
    BACKEND_DIR / "data" / "archive" / "gamelogs_2024-25.csv",
    BACKEND_DIR / "data" / "current" / "gamelogs_2025-26.csv",
]
DEFAULT_HISTORICAL_ODDS_PATH = BACKEND_DIR / "data" / "archive" / "historical_odds.json"
DEFAULT_PRIZEPICKS_ARCHIVE_DIR = BACKEND_DIR / "data" / "archive" / "prizepicks"
DEFAULT_ACTION_NETWORK_ARCHIVE_DIR = BACKEND_DIR / "data" / "archive" / "action_network_odds"

STAT_COLUMNS = {
    "PTS": ["PTS"],
    "AST": ["AST"],
    "REB": ["REB"],
    "FG3M": ["FG3M"],
    "BLK": ["BLK"],
    "STL": ["STL"],
    "STL+BLK": ["STL", "BLK"],
    "PTS+REB+AST": ["PTS", "REB", "AST"],
    "PTS+REB": ["PTS", "REB"],
    "PTS+AST": ["PTS", "AST"],
    "REB+AST": ["REB", "AST"],
}

RESULT_STATUS_VALUES = ("hit", "miss", "push")

IDENTITY_COLUMNS = [
    "game_date",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "game_id",
    "stat_type",
    "sportsbook",
    "side",
]

TARGET_COLUMNS = [
    "line",
    "odds_american",
    "side_implied_prob",
    "opp_implied_prob",
    "no_vig_side_prob",
    "payout_decimal",
    "final_stat_value",
    "result_status",
    "hit_label",
]

MODEL_TARGET_COLUMN = "hit_label"

CATEGORICAL_FEATURE_COLUMNS = [
    "player_id",
    "team",
    "opponent",
    "sportsbook",
    "side",
]

CORE_MARKET_FEATURES = [
    "line",
    "odds_american",
    "side_implied_prob",
    "opp_implied_prob",
    "no_vig_side_prob",
    "consensus_line",
    "book_count",
    "side_line_edge_vs_consensus",
]

CORE_PLAYER_FORM_FEATURES = [
    "prior_games",
    "days_rest",
    "is_b2b",
    "is_home",
    "season_stat_avg",
    "recent5_stat_avg",
    "recent10_stat_avg",
    "recent20_stat_avg",
    "season_stat_std",
    "side_season_gap_vs_line",
    "side_recent5_gap_vs_line",
    "side_recent10_gap_vs_line",
    "side_recent20_gap_vs_line",
    "recent5_side_hit_rate",
    "recent10_side_hit_rate",
    "recent20_side_hit_rate",
]

CORE_USAGE_FEATURES = [
    "season_minutes_avg",
    "recent5_minutes_avg",
    "recent10_minutes_avg",
    "season_usage_pct_avg",
    "recent10_usage_pct_avg",
    "season_ast_pct_avg",
    "recent10_ast_pct_avg",
    "season_reb_pct_avg",
    "recent10_reb_pct_avg",
    "season_ts_pct_avg",
    "recent10_ts_pct_avg",
    "season_potential_ast_rate",
    "recent10_potential_ast_rate",
    "season_reb_chance_rate",
    "recent10_reb_chance_rate",
    "season_drive_rate",
    "recent10_drive_rate",
    "season_fg3a_rate",
    "recent10_fg3a_rate",
]

GAME_MARKET_FEATURES = [
    "game_total_line",
    "team_spread_line",
    "team_is_favorite",
    "spread_abs",
    "team_moneyline_odds",
    "team_moneyline_implied_prob",
    "opponent_moneyline_odds",
    "opponent_moneyline_implied_prob",
    "team_total_line",
    "opponent_team_total_line",
    "team_implied_total",
    "opponent_implied_total",
    "team_prop_line_share_of_team_total",
    "prop_line_share_of_game_total",
    "side_team_spread_signal",
    "side_game_total_signal",
    "side_team_implied_total_signal",
]

FEATURE_GROUPS = {
    "market": CORE_MARKET_FEATURES,
    "player_form": CORE_PLAYER_FORM_FEATURES,
    "usage": CORE_USAGE_FEATURES,
    "game_market": GAME_MARKET_FEATURES,
    # These are placeholders for the next iteration once we archive historical
    # point-in-time snapshots for each feature family.
    "zones": [],
    "similar_players": [],
}

ALL_FEATURE_COLUMNS = [
    *CORE_MARKET_FEATURES,
    *CORE_PLAYER_FORM_FEATURES,
    *CORE_USAGE_FEATURES,
    *GAME_MARKET_FEATURES,
]

MODEL_FEATURE_COLUMNS = [
    *CATEGORICAL_FEATURE_COLUMNS,
    *ALL_FEATURE_COLUMNS,
]

DATASET_COLUMNS = [
    *IDENTITY_COLUMNS,
    *TARGET_COLUMNS,
    *ALL_FEATURE_COLUMNS,
]
