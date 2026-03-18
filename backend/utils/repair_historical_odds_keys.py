import argparse
import json
import os
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_PATH = os.path.join(BASE_DIR, "data", "current", "master_feed.json")
HISTORICAL_ODDS_PATH = os.path.join(BASE_DIR, "data", "archive", "historical_odds.json")

sys.path.append(BASE_DIR)
from utils.player_matcher import PlayerMatcher

def load_matcher():
    with open(MASTER_PATH, "r") as f:
        master_feed = json.load(f)

    players_metadata = []
    for player in master_feed:
        players_metadata.append({
            "PLAYER_ID": str(player.get("id", "")),
            "PLAYER_NAME": player.get("name", ""),
            "TEAM_ABBREVIATION": player.get("team", ""),
        })
    return PlayerMatcher(players_metadata)


def merge_records(existing, incoming):
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "props" and isinstance(value, dict) and isinstance(existing.get("props"), dict):
            props = dict(existing["props"])
            props.update(value)
            merged["props"] = props
        else:
            merged[key] = value
    return merged


def repair_historical_odds_keys():
    with open(HISTORICAL_ODDS_PATH, "r") as f:
        historical = json.load(f)

    matcher = load_matcher()
    repaired = 0

    for game_date, players in historical.items():
        if not isinstance(players, dict):
            continue

        rewritten = {}
        for raw_key, record in players.items():
            player_id = matcher.match_player(
                record.get("name") or str(raw_key),
                record.get("team", "UNK"),
            )
            player_id = str(player_id) if player_id else str(raw_key)

            if player_id in rewritten:
                rewritten[player_id] = merge_records(rewritten[player_id], record)
            else:
                rewritten[player_id] = record

            if str(player_id) != str(raw_key):
                repaired += 1

        historical[game_date] = rewritten

    with open(HISTORICAL_ODDS_PATH, "w") as f:
        json.dump(historical, f, indent=2)

    return repaired


def main():
    parser = argparse.ArgumentParser(description="Repair historical odds player keys from names to IDs")
    parser.parse_args()

    repaired = repair_historical_odds_keys()
    print(f"Repaired {repaired} player keys in {HISTORICAL_ODDS_PATH}")


if __name__ == "__main__":
    main()
