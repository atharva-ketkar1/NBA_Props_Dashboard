import os

import pandas as pd

ODDS_CSV_COLUMNS = [
    "player",
    "team",
    "prop_type",
    "line",
    "over_odds",
    "under_odds",
    "implied_prob",
    "game",
    "game_date",
    "sportsbook",
    "team_options",
]


def write_odds_csv(path: str, rows) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df = pd.DataFrame(rows or [])
    if df.empty and not list(df.columns):
        df = pd.DataFrame(columns=ODDS_CSV_COLUMNS)
    df.to_csv(path, index=False)
