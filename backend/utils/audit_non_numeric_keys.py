import json
import os


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE_MOVEMENTS_PATH = os.path.join(BASE_DIR, "data", "current", "line_movements_today.json")
HISTORICAL_ODDS_PATH = os.path.join(BASE_DIR, "data", "archive", "historical_odds.json")


def is_numeric_key(value):
    return str(value).isdigit()


def audit_line_movements():
    with open(LINE_MOVEMENTS_PATH, "r") as f:
        data = json.load(f)

    findings = []
    for idx, snapshot in enumerate(data.get("snapshots", [])):
        for player_key in snapshot.get("players", {}).keys():
            if not is_numeric_key(player_key):
                findings.append({
                    "snapshot_index": idx,
                    "timestamp": snapshot.get("timestamp"),
                    "label": snapshot.get("label"),
                    "key": str(player_key),
                })
    return findings


def audit_historical_odds():
    with open(HISTORICAL_ODDS_PATH, "r") as f:
        data = json.load(f)

    findings = []
    for game_date, players in data.items():
        if not isinstance(players, dict):
            continue
        for player_key in players.keys():
            if not is_numeric_key(player_key):
                findings.append({
                    "game_date": game_date,
                    "key": str(player_key),
                })
    return findings


def print_section(title, findings, formatter):
    print(f"\n== {title} ==")
    print(f"count: {len(findings)}")
    for row in findings[:50]:
        print(formatter(row))
    if len(findings) > 50:
        print(f"... and {len(findings) - 50} more")


def main():
    line_findings = audit_line_movements()
    historical_findings = audit_historical_odds()

    print_section(
        "Line Movements Non-Numeric Keys",
        line_findings,
        lambda row: f"{row['timestamp']} [{row['label']}] key={row['key']}",
    )
    print_section(
        "Historical Odds Non-Numeric Keys",
        historical_findings,
        lambda row: f"{row['game_date']} key={row['key']}",
    )


if __name__ == "__main__":
    main()
