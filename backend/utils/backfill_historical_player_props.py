import argparse
import os
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HISTORICAL_PATH = os.path.join(BASE_DIR, "data", "archive", "historical_odds.json")

sys.path.append(BASE_DIR)

from utils.upsert_market_history import backfill_historical_player_props_from_file


def main():
    parser = argparse.ArgumentParser(
        description="Backfill normalized historical_player_props rows from the local historical_odds archive.",
    )
    parser.add_argument(
        "--path",
        default=DEFAULT_HISTORICAL_PATH,
        help="Path to historical_odds.json",
    )
    parser.add_argument(
        "--game-date",
        action="append",
        dest="game_dates",
        help="Backfill a specific YYYY-MM-DD date. Repeatable.",
    )
    parser.add_argument(
        "--include-pp",
        action="store_true",
        help="Include PrizePicks historical rows if they exist in the archive.",
    )
    args = parser.parse_args()

    ok = backfill_historical_player_props_from_file(
        historical_odds_path=args.path,
        game_dates=args.game_dates,
        include_pp=args.include_pp,
    )
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
