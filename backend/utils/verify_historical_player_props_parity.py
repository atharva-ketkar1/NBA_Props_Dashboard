import argparse
import json
import os
import sys
from collections import defaultdict


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from utils.historical_player_props import build_legacy_historical_row, extract_props_tree
from utils.supabase_client import get_supabase_client

PAGE_SIZE = 1000


def fetch_normalized_rows(player_id: int):
    supabase = get_supabase_client()
    rows = []

    for start in range(0, 100_000, PAGE_SIZE):
        response = (
            supabase
            .table("historical_player_props")
            .select("player_id, game_date, sportsbook, stat_type, line, over_odds, under_odds, implied, source, captured_at")
            .eq("player_id", player_id)
            .order("game_date")
            .order("stat_type")
            .order("sportsbook")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        data = response.data or []
        if not data:
            break
        rows.extend(data)
        if len(data) < PAGE_SIZE:
            break

    return rows


def fetch_legacy_rows(player_id: int):
    supabase = get_supabase_client()
    response = (
        supabase
        .table("historical_odds")
        .select("game_date, props, source, captured_at")
        .eq("player_id", player_id)
        .execute()
    )
    return response.data or []


def normalize_props_tree(raw_props):
    return extract_props_tree({"props": raw_props})


def reshape_normalized_rows(rows, player_id: int):
    by_date = defaultdict(list)
    for row in rows or []:
        game_date = row.get("game_date")
        if game_date:
            by_date[str(game_date)].append(row)

    return {
        game_date: build_legacy_historical_row(game_date, player_id, dated_rows)
        for game_date, dated_rows in by_date.items()
    }


def compare_player(player_id: int):
    normalized_rows = fetch_normalized_rows(player_id)
    legacy_rows = fetch_legacy_rows(player_id)

    normalized_by_date = reshape_normalized_rows(normalized_rows, player_id)
    legacy_by_date = {
        str(row.get("game_date")): row
        for row in legacy_rows or []
        if row.get("game_date")
    }

    all_dates = sorted(set(normalized_by_date.keys()) | set(legacy_by_date.keys()))
    mismatches = []

    for game_date in all_dates:
        legacy_record = legacy_by_date.get(game_date)
        normalized_record = normalized_by_date.get(game_date)
        legacy_tree = normalize_props_tree((legacy_record or {}).get("props"))
        normalized_tree = normalize_props_tree((normalized_record or {}).get("props"))

        if legacy_tree != normalized_tree:
            mismatches.append(
                {
                    "game_date": game_date,
                    "legacy_present": legacy_record is not None,
                    "normalized_present": normalized_record is not None,
                    "legacy_props": legacy_tree,
                    "normalized_props": normalized_tree,
                }
            )

    return {
        "player_id": player_id,
        "legacy_dates": len(legacy_by_date),
        "normalized_dates": len(normalized_by_date),
        "normalized_row_count": len(normalized_rows),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compare legacy historical_odds rows to normalized historical_player_props rows for one or more players.",
    )
    parser.add_argument(
        "--player-id",
        type=int,
        action="append",
        required=True,
        dest="player_ids",
        help="Player ID to verify. Repeatable.",
    )
    parser.add_argument(
        "--show-mismatches",
        type=int,
        default=3,
        help="How many mismatch payloads to print per player.",
    )
    args = parser.parse_args()

    results = [compare_player(player_id) for player_id in args.player_ids]
    summaries = [
        {
            "player_id": result["player_id"],
            "legacy_dates": result["legacy_dates"],
            "normalized_dates": result["normalized_dates"],
            "normalized_row_count": result["normalized_row_count"],
            "mismatch_count": result["mismatch_count"],
        }
        for result in results
    ]
    print(json.dumps({"summary": summaries}, indent=2))

    for result in results:
        mismatches = result["mismatches"][: max(0, args.show_mismatches)]
        if not mismatches:
            continue
        print(
            json.dumps(
                {
                    "player_id": result["player_id"],
                    "sample_mismatches": mismatches,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
