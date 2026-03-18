import argparse
import json
import os
import unicodedata


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_PATH = os.path.join(BASE_DIR, "data", "current", "master_feed.json")
LINE_MOVEMENTS_PATH = os.path.join(BASE_DIR, "data", "current", "line_movements_today.json")


def normalize_lookup_name(name):
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("utf-8")
    return name.replace(".", "").replace("'", "")


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


def merge_player_records(existing, incoming):
    merged = dict(existing)
    for key, value in incoming.items():
        if key == "props" and isinstance(value, dict) and isinstance(existing.get("props"), dict):
            props = dict(existing["props"])
            for stat_key, stat_val in value.items():
                if stat_key in props and isinstance(props[stat_key], dict) and isinstance(stat_val, dict):
                    sportsbook_map = dict(props[stat_key])
                    sportsbook_map.update(stat_val)
                    props[stat_key] = sportsbook_map
                else:
                    props[stat_key] = stat_val
            merged["props"] = props
        else:
            merged[key] = value
    return merged


def repair_line_movement_keys():
    with open(LINE_MOVEMENTS_PATH, "r") as f:
        data = json.load(f)

    name_to_id = load_name_to_id_map()
    repaired = 0

    for snapshot in data.get("snapshots", []):
        players = snapshot.get("players", {})
        rewritten = {}

        for raw_key, player_data in players.items():
            normalized_key = normalize_lookup_name(raw_key)
            player_id = name_to_id.get(normalized_key, str(raw_key))

            if player_id in rewritten:
                rewritten[player_id] = merge_player_records(rewritten[player_id], player_data)
            else:
                rewritten[player_id] = player_data

            if str(player_id) != str(raw_key):
                repaired += 1

        snapshot["players"] = rewritten

    with open(LINE_MOVEMENTS_PATH, "w") as f:
        json.dump(data, f, indent=2)

    return repaired


def main():
    parser = argparse.ArgumentParser(description="Repair line movement snapshot player keys from names to IDs")
    parser.parse_args()

    repaired = repair_line_movement_keys()
    print(f"Repaired {repaired} player keys in {LINE_MOVEMENTS_PATH}")


if __name__ == "__main__":
    main()
