"""Build a point-in-time-safe historical prop dataset for model training.

This script uses only information that would have existed before each game:

- historical prop lines/odds from archived sportsbook snapshots
- player game logs strictly before the target game date

It intentionally does not read today's ``master_feed.json`` for old rows,
because doing that would leak future player form and matchup information into
past training examples.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from bisect import bisect_left
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from feature_schema import (
    DATASET_COLUMNS,
    DEFAULT_DATASET_PATH,
    DEFAULT_GAMELOG_PATHS,
    DEFAULT_HISTORICAL_ODDS_PATH,
    DEFAULT_PRIZEPICKS_ARCHIVE_DIR,
    GENERATED_DIR,
    STAT_COLUMNS,
)


SIDE_MULTIPLIERS = {
    "over": 1.0,
    "under": -1.0,
}

SUPPORTED_SIDE_KEYS = ("over", "under")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a historical player-prop training dataset under backend/prop_modeling/generated/.",
    )
    parser.add_argument(
        "--gamelogs",
        nargs="*",
        default=[str(path) for path in DEFAULT_GAMELOG_PATHS],
        help="One or more game-log CSVs ordered arbitrarily; rows are sorted internally by game date.",
    )
    parser.add_argument(
        "--historical-odds-json",
        default=str(DEFAULT_HISTORICAL_ODDS_PATH),
        help="Archived DK/FD odds JSON path.",
    )
    parser.add_argument(
        "--prizepicks-archive-dir",
        default=str(DEFAULT_PRIZEPICKS_ARCHIVE_DIR),
        help="Directory of archived PrizePicks daily JSON snapshots.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(DEFAULT_DATASET_PATH),
        help="Destination CSV path.",
    )
    parser.add_argument(
        "--min-prior-games",
        type=int,
        default=3,
        help="Skip rows unless a player has at least this many games before the target date.",
    )
    return parser.parse_args()


def _parse_date(value: Any) -> Optional[date]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _round_optional(value: Optional[float], digits: int = 4) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean_values = [value for value in values if value is not None]
    if not clean_values:
        return None
    return fmean(clean_values)


def _std(values: Sequence[Optional[float]]) -> Optional[float]:
    clean_values = [value for value in values if value is not None]
    if len(clean_values) < 2:
        return None
    return pstdev(clean_values)


def _sum_stat(row: Dict[str, Any], stat_type: str) -> Optional[float]:
    columns = STAT_COLUMNS.get(stat_type)
    if not columns:
        return None
    total = 0.0
    for column in columns:
        value = _safe_float(row.get(column))
        if value is None:
            return None
        total += value
    return total


def _american_to_implied(odds: Optional[float]) -> Optional[float]:
    if odds is None or odds == 0:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def _american_to_payout_decimal(odds: Optional[float]) -> float:
    if odds is None or odds == 0:
        # PrizePicks rows do not carry single-leg American odds in this archive.
        # Use even-money net payout as a neutral placeholder for hit-probability
        # model training and top-K backtest sorting.
        return 1.0
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)


def _no_vig_probability(side_implied: Optional[float], opp_implied: Optional[float]) -> Optional[float]:
    if side_implied is None or opp_implied is None:
        return None
    denominator = side_implied + opp_implied
    if denominator <= 0:
        return None
    return side_implied / denominator


def _parse_matchup(matchup: Any, team: str) -> Tuple[Optional[str], Optional[int]]:
    raw_matchup = str(matchup or "").strip()
    if not raw_matchup or not team:
        return None, None

    if " vs. " in raw_matchup:
        left, right = raw_matchup.split(" vs. ", 1)
        opponent = right if left == team else left
        return opponent.strip() or None, 1

    if " @ " in raw_matchup:
        left, right = raw_matchup.split(" @ ", 1)
        opponent = right if left == team else left
        return opponent.strip() or None, 0

    return None, None


def _extract_metric(row: Dict[str, Any], metric_name: str) -> Optional[float]:
    value = _safe_float(row.get(metric_name))
    if value is None:
        return None
    return value


def _metric_rate(row: Dict[str, Any], numerator_key: str) -> Optional[float]:
    numerator = _safe_float(row.get(numerator_key))
    minutes = _safe_float(row.get("MIN"))
    if numerator is None or minutes is None or minutes <= 0:
        return None
    return numerator / minutes


def _build_history_index(
    gamelog_paths: Sequence[Path],
) -> Tuple[Dict[str, List[Tuple[date, Dict[str, Any]]]], Dict[Tuple[str, date], Dict[str, Any]]]:
    histories: Dict[str, List[Tuple[date, Dict[str, Any]]]] = defaultdict(list)
    exact_rows: Dict[Tuple[str, date], Dict[str, Any]] = {}

    for path in gamelog_paths:
        if not path.exists():
            continue
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                player_id = str(row.get("PLAYER_ID") or "").strip()
                game_date = _parse_date(row.get("GAME_DATE"))
                if not player_id or game_date is None:
                    continue
                histories[player_id].append((game_date, row))
                exact_rows[(player_id, game_date)] = row

    for player_id in list(histories.keys()):
        histories[player_id].sort(key=lambda item: item[0])

    return dict(histories), exact_rows


def _prior_rows_for_player(
    player_history: Sequence[Tuple[date, Dict[str, Any]]],
    target_date: date,
) -> List[Dict[str, Any]]:
    game_dates = [game_date for game_date, _ in player_history]
    split_index = bisect_left(game_dates, target_date)
    return [row for _, row in player_history[:split_index]]


def _side_hit_rate(
    stat_values: Sequence[Optional[float]],
    line: float,
    side: str,
) -> Optional[float]:
    clean_values = [value for value in stat_values if value is not None]
    if not clean_values:
        return None
    if side == "over":
        hit_count = sum(1 for value in clean_values if value > line)
    else:
        hit_count = sum(1 for value in clean_values if value < line)
    return hit_count / len(clean_values)


def _build_rolling_features(
    player_history: Sequence[Tuple[date, Dict[str, Any]]],
    target_date: date,
    stat_type: str,
    line: float,
    side: str,
    team: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    prior_rows = _prior_rows_for_player(player_history, target_date)
    if not prior_rows:
        return None, None

    prior_dates = [_parse_date(row.get("GAME_DATE")) for row in prior_rows]
    final_prior_date = next((value for value in reversed(prior_dates) if value is not None), None)
    days_rest = (target_date - final_prior_date).days if final_prior_date else None

    season_values = [_sum_stat(row, stat_type) for row in prior_rows]
    recent5_values = season_values[-5:]
    recent10_values = season_values[-10:]
    recent20_values = season_values[-20:]

    side_multiplier = SIDE_MULTIPLIERS[side]
    season_avg = _mean(season_values)
    recent5_avg = _mean(recent5_values)
    recent10_avg = _mean(recent10_values)
    recent20_avg = _mean(recent20_values)
    season_std = _std(season_values)

    features = {
        "prior_games": len(prior_rows),
        "days_rest": days_rest,
        "is_b2b": 1 if days_rest == 1 else 0,
        "is_home": None,
        "season_stat_avg": _round_optional(season_avg),
        "recent5_stat_avg": _round_optional(recent5_avg),
        "recent10_stat_avg": _round_optional(recent10_avg),
        "recent20_stat_avg": _round_optional(recent20_avg),
        "season_stat_std": _round_optional(season_std),
        "side_season_gap_vs_line": _round_optional(
            None if season_avg is None else side_multiplier * (season_avg - line),
        ),
        "side_recent5_gap_vs_line": _round_optional(
            None if recent5_avg is None else side_multiplier * (recent5_avg - line),
        ),
        "side_recent10_gap_vs_line": _round_optional(
            None if recent10_avg is None else side_multiplier * (recent10_avg - line),
        ),
        "side_recent20_gap_vs_line": _round_optional(
            None if recent20_avg is None else side_multiplier * (recent20_avg - line),
        ),
        "recent5_side_hit_rate": _round_optional(_side_hit_rate(recent5_values, line, side)),
        "recent10_side_hit_rate": _round_optional(_side_hit_rate(recent10_values, line, side)),
        "recent20_side_hit_rate": _round_optional(_side_hit_rate(recent20_values, line, side)),
        "season_minutes_avg": _round_optional(_mean([_extract_metric(row, "MIN") for row in prior_rows])),
        "recent5_minutes_avg": _round_optional(_mean([_extract_metric(row, "MIN") for row in prior_rows[-5:]])),
        "recent10_minutes_avg": _round_optional(_mean([_extract_metric(row, "MIN") for row in prior_rows[-10:]])),
        "season_usage_pct_avg": _round_optional(_mean([_extract_metric(row, "USG_PCT") for row in prior_rows])),
        "recent10_usage_pct_avg": _round_optional(_mean([_extract_metric(row, "USG_PCT") for row in prior_rows[-10:]])),
        "season_ast_pct_avg": _round_optional(_mean([_extract_metric(row, "AST_PCT") for row in prior_rows])),
        "recent10_ast_pct_avg": _round_optional(_mean([_extract_metric(row, "AST_PCT") for row in prior_rows[-10:]])),
        "season_reb_pct_avg": _round_optional(_mean([_extract_metric(row, "REB_PCT") for row in prior_rows])),
        "recent10_reb_pct_avg": _round_optional(_mean([_extract_metric(row, "REB_PCT") for row in prior_rows[-10:]])),
        "season_ts_pct_avg": _round_optional(_mean([_extract_metric(row, "TS_PCT") for row in prior_rows])),
        "recent10_ts_pct_avg": _round_optional(_mean([_extract_metric(row, "TS_PCT") for row in prior_rows[-10:]])),
        "season_potential_ast_rate": _round_optional(_mean([_metric_rate(row, "POTENTIAL_AST") for row in prior_rows])),
        "recent10_potential_ast_rate": _round_optional(_mean([_metric_rate(row, "POTENTIAL_AST") for row in prior_rows[-10:]])),
        "season_reb_chance_rate": _round_optional(_mean([_metric_rate(row, "REB_CHANCES") for row in prior_rows])),
        "recent10_reb_chance_rate": _round_optional(_mean([_metric_rate(row, "REB_CHANCES") for row in prior_rows[-10:]])),
        "season_drive_rate": _round_optional(_mean([_metric_rate(row, "DRIVES") for row in prior_rows])),
        "recent10_drive_rate": _round_optional(_mean([_metric_rate(row, "DRIVES") for row in prior_rows[-10:]])),
        "season_fg3a_rate": _round_optional(_mean([_metric_rate(row, "FG3A") for row in prior_rows])),
        "recent10_fg3a_rate": _round_optional(_mean([_metric_rate(row, "FG3A") for row in prior_rows[-10:]])),
    }

    context = {
        "opponent": None,
        "is_home": None,
    }
    return features, context


def _build_base_row(
    *,
    game_date: date,
    player_id: str,
    player_name: str,
    team: str,
    opponent: Optional[str],
    game_id: Optional[str],
    stat_type: str,
    sportsbook: str,
    side: str,
    line: float,
    odds_american: Optional[float],
    opp_odds_american: Optional[float],
    consensus_line: Optional[float],
    book_count: int,
    prior_features: Dict[str, Any],
    current_game_row: Dict[str, Any],
) -> Dict[str, Any]:
    final_value = _sum_stat(current_game_row, stat_type)
    result_status = "push"
    hit_label: Optional[int] = None
    if final_value is not None:
        if side == "over":
            if final_value > line:
                result_status = "hit"
                hit_label = 1
            elif final_value < line:
                result_status = "miss"
                hit_label = 0
        else:
            if final_value < line:
                result_status = "hit"
                hit_label = 1
            elif final_value > line:
                result_status = "miss"
                hit_label = 0

    side_implied = _american_to_implied(odds_american)
    opp_implied = _american_to_implied(opp_odds_american)
    no_vig_prob = _no_vig_probability(side_implied, opp_implied)
    side_multiplier = SIDE_MULTIPLIERS[side]

    return {
        "game_date": game_date.isoformat(),
        "player_id": player_id,
        "player_name": player_name,
        "team": team,
        "opponent": opponent,
        "game_id": game_id,
        "stat_type": stat_type,
        "sportsbook": sportsbook,
        "side": side,
        "line": _round_optional(line),
        "odds_american": _round_optional(odds_american),
        "side_implied_prob": _round_optional(side_implied),
        "opp_implied_prob": _round_optional(opp_implied),
        "no_vig_side_prob": _round_optional(no_vig_prob),
        "payout_decimal": _round_optional(_american_to_payout_decimal(odds_american)),
        "final_stat_value": _round_optional(final_value),
        "result_status": result_status,
        "hit_label": hit_label,
        "consensus_line": _round_optional(consensus_line),
        "book_count": book_count,
        "side_line_edge_vs_consensus": _round_optional(
            None if consensus_line is None else side_multiplier * (consensus_line - line),
        ),
        **prior_features,
    }


def _consensus_for_book_map(book_map: Dict[str, Any]) -> Tuple[Optional[float], int]:
    lines = []
    for raw_book_payload in book_map.values():
        if not isinstance(raw_book_payload, dict):
            continue
        line = _safe_float(raw_book_payload.get("line"))
        if line is not None:
            lines.append(line)
    return _mean(lines), len(lines)


def _iter_historical_book_examples(
    historical_odds_path: Path,
) -> Iterable[Dict[str, Any]]:
    if not historical_odds_path.exists():
        return

    with historical_odds_path.open() as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        return

    for raw_game_date, day_payload in sorted(payload.items()):
        game_date = _parse_date(raw_game_date)
        if game_date is None or not isinstance(day_payload, dict):
            continue

        for raw_player_id, record in day_payload.items():
            if not isinstance(record, dict):
                continue

            nested_props = record.get("props") if isinstance(record.get("props"), dict) else {}
            prop_tree = nested_props.get("props") if isinstance(nested_props.get("props"), dict) else {}
            player_name = str(record.get("name") or nested_props.get("name") or "").strip()
            team = str(record.get("team") or nested_props.get("team") or "").strip()
            game_id = record.get("game_id") or nested_props.get("game_id")

            for stat_type, book_map in prop_tree.items():
                if stat_type not in STAT_COLUMNS or not isinstance(book_map, dict):
                    continue

                consensus_line, book_count = _consensus_for_book_map(book_map)
                for raw_book, market in book_map.items():
                    if not isinstance(market, dict):
                        continue
                    line = _safe_float(market.get("line"))
                    if line is None:
                        continue
                    for side in SUPPORTED_SIDE_KEYS:
                        yield {
                            "game_date": game_date,
                            "player_id": str(raw_player_id),
                            "player_name": player_name,
                            "team": team,
                            "game_id": game_id,
                            "stat_type": stat_type,
                            "sportsbook": str(raw_book).strip().lower(),
                            "side": side,
                            "line": line,
                            "odds_american": _safe_float(market.get(side)),
                            "opp_odds_american": _safe_float(market.get("under" if side == "over" else "over")),
                            "consensus_line": consensus_line,
                            "book_count": book_count,
                        }


def _iter_prizepicks_examples(prizepicks_archive_dir: Path) -> Iterable[Dict[str, Any]]:
    if not prizepicks_archive_dir.exists():
        return

    for path in sorted(prizepicks_archive_dir.glob("*.json")):
        game_date = _parse_date(path.stem)
        if game_date is None:
            continue
        with path.open() as handle:
            day_payload = json.load(handle)
        if not isinstance(day_payload, dict):
            continue

        for raw_player_id, record in day_payload.items():
            if not isinstance(record, dict):
                continue
            player_name = str(record.get("name") or "").strip()
            team = str(record.get("team") or "").strip()
            game_id = record.get("game_id")
            prop_tree = record.get("props") if isinstance(record.get("props"), dict) else {}

            for stat_type, book_map in prop_tree.items():
                if stat_type not in STAT_COLUMNS or not isinstance(book_map, dict):
                    continue
                market = book_map.get("pp")
                if not isinstance(market, dict):
                    continue
                line = _safe_float(market.get("line"))
                if line is None:
                    continue

                for side in SUPPORTED_SIDE_KEYS:
                    yield {
                        "game_date": game_date,
                        "player_id": str(raw_player_id),
                        "player_name": player_name,
                        "team": team,
                        "game_id": game_id,
                        "stat_type": stat_type,
                        "sportsbook": "pp",
                        "side": side,
                        "line": line,
                        "odds_american": _safe_float(market.get(side)),
                        "opp_odds_american": _safe_float(market.get("under" if side == "over" else "over")),
                        "consensus_line": line,
                        "book_count": 1,
                    }


def build_dataset(
    *,
    gamelog_paths: Sequence[Path],
    historical_odds_path: Path,
    prizepicks_archive_dir: Path,
    min_prior_games: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    histories, exact_rows = _build_history_index(gamelog_paths)
    examples = list(_iter_historical_book_examples(historical_odds_path))
    examples.extend(_iter_prizepicks_examples(prizepicks_archive_dir))

    output_rows: List[Dict[str, Any]] = []
    stats = {
        "candidate_examples": len(examples),
        "written_rows": 0,
        "skipped_missing_final": 0,
        "skipped_no_prior_history": 0,
        "skipped_too_few_prior_games": 0,
        "push_rows": 0,
    }

    for example in examples:
        player_id = str(example["player_id"])
        game_date = example["game_date"]
        current_game_row = exact_rows.get((player_id, game_date))
        if current_game_row is None:
            stats["skipped_missing_final"] += 1
            continue

        prior_history = histories.get(player_id, [])
        prior_features, context = _build_rolling_features(
            prior_history,
            game_date,
            example["stat_type"],
            example["line"],
            example["side"],
            example["team"],
        )
        if prior_features is None or context is None:
            stats["skipped_no_prior_history"] += 1
            continue
        if int(prior_features.get("prior_games") or 0) < min_prior_games:
            stats["skipped_too_few_prior_games"] += 1
            continue

        current_opponent, current_is_home = _parse_matchup(
            current_game_row.get("MATCHUP"),
            example["team"],
        )
        if current_opponent:
            context["opponent"] = current_opponent
        if current_is_home is not None:
            prior_features["is_home"] = current_is_home

        row = _build_base_row(
            game_date=game_date,
            player_id=player_id,
            player_name=example["player_name"],
            team=example["team"],
            opponent=context.get("opponent"),
            game_id=example.get("game_id") or current_game_row.get("GAME_ID"),
            stat_type=example["stat_type"],
            sportsbook=example["sportsbook"],
            side=example["side"],
            line=example["line"],
            odds_american=example.get("odds_american"),
            opp_odds_american=example.get("opp_odds_american"),
            consensus_line=example.get("consensus_line"),
            book_count=int(example.get("book_count") or 0),
            prior_features=prior_features,
            current_game_row=current_game_row,
        )
        if row["result_status"] == "push":
            stats["push_rows"] += 1
        output_rows.append(row)

    stats["written_rows"] = len(output_rows)
    return output_rows, stats


def _write_dataset(output_path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DATASET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = _parse_args()
    gamelog_paths = [Path(path) for path in args.gamelogs]
    output_csv = Path(args.output_csv)

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    rows, stats = build_dataset(
        gamelog_paths=gamelog_paths,
        historical_odds_path=Path(args.historical_odds_json),
        prizepicks_archive_dir=Path(args.prizepicks_archive_dir),
        min_prior_games=max(0, args.min_prior_games),
    )
    _write_dataset(output_csv, rows)

    print(f"wrote_rows={stats['written_rows']}")
    print(f"candidate_examples={stats['candidate_examples']}")
    print(f"push_rows={stats['push_rows']}")
    print(f"skipped_missing_final={stats['skipped_missing_final']}")
    print(f"skipped_no_prior_history={stats['skipped_no_prior_history']}")
    print(f"skipped_too_few_prior_games={stats['skipped_too_few_prior_games']}")
    print(f"output_csv={output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
