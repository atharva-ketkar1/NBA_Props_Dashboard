import argparse
import json
import os
import re
import unicodedata


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_PATH = os.path.join(BASE_DIR, "data", "current", "master_feed.json")
HISTORICAL_ODDS_PATH = os.path.join(BASE_DIR, "data", "archive", "historical_odds.json")


def normalize_lookup_name(name):
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("utf-8")
    name = name.replace(".", "").replace("'", "")
    name = re.sub(r'\b(jr|sr|ii|iii|iv|v)\b', '', name).strip()
    return " ".join(name.split())


def load_name_to_id_map():
    with open(MASTER_PATH, "r") as f:
        master_feed = json.load(f)

    mapping = {}
    for player in master_feed:
        name = normalize_lookup_name(player.get("name", ""))
        player_id = str(player.get("id", ""))
        if name and player_id:
            mapping[name] = player_id
    return mapping


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

    name_to_id = load_name_to_id_map()
    repaired = 0

    for game_date, players in historical.items():
        if not isinstance(players, dict):
            continue

        rewritten = {}
        for raw_key, record in players.items():
            normalized_key = normalize_lookup_name(raw_key)
            player_id = name_to_id.get(normalized_key, str(raw_key))

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
