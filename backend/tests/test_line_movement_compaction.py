"""What Supabase holds for the sparkline is transitions, not snapshots.

`line_movements` is one jsonb row per game_date, and Postgres rewrites and
WAL-logs the whole TOASTed value on every upsert. At ~57 upserts a day the
row's size *is* the write cost, which is what took the database down on
2026-11-23. The old compaction kept a trailing window of whole snapshots, so
the cost grew with the square of the day and more history was paid for 57 times
over -- while ~97% of the stored values repeated the line before them.

These tests pin the property that makes the new encoding safe to rely on: the
sparse series must reproduce the full series' line history exactly. If it does,
the window can be the whole day and still cost less than two hours did. They
also pin the two things a reader can get wrong about a sparse series -- the
opening has to survive to the end of the day, and a pulled line has to stay
distinguishable from one that was never quoted.
"""

import json
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.upsert_market_history import _compact_snapshots_for_supabase


def snapshot(timestamp, players, label="intraday"):
    return {"timestamp": timestamp, "label": label, "players": players}


def player(line, over=-110, under=-110, stat="PTS", book="fanduel", **scalars):
    payload = {"line": line, "over": over, "under": under}
    row = {"name": "test player", "team": "BOS", "game_date": "2026-11-23", "props": {stat: {book: payload}}}
    row.update(scalars)
    return row


def line_history(series):
    """(player, stat, book) -> the ordered line transitions the series implies.

    Carries the last value forward the way a reader of a sparse series must,
    so the same function can be applied to a full and a compacted series and
    the results compared.
    """
    history, last = {}, {}
    for entry in series:
        for player_id, player_data in (entry.get("players") or {}).items():
            for stat_type, books in (player_data.get("props") or {}).items():
                for book, payload in (books or {}).items():
                    key = (str(player_id), stat_type, book)
                    value = payload.get("line") if isinstance(payload, dict) else None
                    if last.get(key, "<unseen>") != value:
                        history.setdefault(key, []).append((entry["timestamp"], value))
                        last[key] = value
    return history


class CompactionPreservesLineHistory(unittest.TestCase):
    def test_repeated_lines_collapse_to_one_entry(self):
        series = [snapshot(f"2026-11-23T{hour:02d}:00", {"1": player(25.5)}) for hour in range(6, 12)]
        compacted = _compact_snapshots_for_supabase(series)

        self.assertEqual(len(compacted), 1, "six identical lines are one transition")
        self.assertEqual(compacted[0]["timestamp"], "2026-11-23T06:00", "kept at the opening, not the latest")

    def test_odds_moving_alone_is_not_a_transition(self):
        # The whole point of comparing on the line: juice jitters most ticks and
        # storing it was ~5x the entries for something nobody reads off a chart.
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5, over=-110)}),
            snapshot("2026-11-23T07:00", {"1": player(25.5, over=-104)}),
            snapshot("2026-11-23T08:00", {"1": player(25.5, over=+118)}),
        ]
        self.assertEqual(len(_compact_snapshots_for_supabase(series)), 1)

    def test_odds_ride_along_on_a_line_change(self):
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5, over=-110, under=-110)}),
            snapshot("2026-11-23T07:00", {"1": player(24.5, over=-132, under=+105)}),
        ]
        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual(len(compacted), 2)
        kept = compacted[1]["players"]["1"]["props"]["PTS"]["fanduel"]
        self.assertEqual((kept["over"], kept["under"]), (-132, 105), "the odds at the moment it moved are stored")

    def test_opening_survives_a_full_day(self):
        # The failure the trailing window had: a long day pushed the morning out,
        # so the chart's "Open" was whatever happened to be 32 snapshots back.
        series = [snapshot("2026-11-23T06:00", {"1": player(25.5)})]
        series += [snapshot(f"2026-11-23T{hour:02d}:00", {"1": player(30.5)}) for hour in range(7, 23)]

        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual(
            [entry["timestamp"] for entry in compacted],
            ["2026-11-23T06:00", "2026-11-23T07:00"],
            "the opening and the one move, nothing else",
        )

    def test_compacted_series_reproduces_the_full_line_history(self):
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5), "2": player(10.5)}),
            snapshot("2026-11-23T07:00", {"1": player(25.5), "2": player(11.5)}),
            snapshot("2026-11-23T08:00", {"1": player(26.5), "2": player(11.5)}),
            snapshot("2026-11-23T09:00", {"1": player(26.5), "2": player(10.5)}),
            snapshot("2026-11-23T10:00", {"1": player(25.5), "2": player(10.5)}),
        ]
        self.assertEqual(
            line_history(_compact_snapshots_for_supabase(series)),
            line_history(series),
            "a sparse series read with carry-forward is the full series",
        )

    def test_a_pulled_line_is_a_transition(self):
        # None is a state a book can be in, not an absence. If it compared equal
        # to "never seen", a pull-down would vanish and the chart would show the
        # stale line as current.
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5)}),
            snapshot("2026-11-23T07:00", {"1": player(None)}),
            snapshot("2026-11-23T08:00", {"1": player(None)}),
        ]
        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual([entry["timestamp"] for entry in compacted], ["2026-11-23T06:00", "2026-11-23T07:00"])

    def test_pre_game_is_kept_whole(self):
        # So every prop still quoted at tip has a point there, and a line that
        # never moved all day still draws as a series instead of a lone dot.
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5)}),
            snapshot("2026-11-23T19:00", {"1": player(25.5)}, label="pre_game"),
        ]
        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual(len(compacted), 2, "the pre-tip read is stored even though the line never moved")
        self.assertEqual(compacted[1]["label"], "pre_game")

    def test_player_scalars_ride_on_every_entry(self):
        # game_date especially: the sparkline filters on it, so an entry missing
        # it is silently dropped from the chart.
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5, game_id="0022600288")}),
            snapshot("2026-11-23T07:00", {"1": player(24.5, game_id="0022600288")}),
        ]
        for entry in _compact_snapshots_for_supabase(series):
            row = entry["players"]["1"]
            self.assertEqual(row["game_date"], "2026-11-23")
            self.assertEqual(row["game_id"], "0022600288")
            self.assertEqual(row["name"], "test player")

    def test_a_player_with_no_moves_is_left_out_of_the_entry(self):
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5), "2": player(10.5)}),
            snapshot("2026-11-23T07:00", {"1": player(24.5), "2": player(10.5)}),
        ]
        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual(set(compacted[1]["players"]), {"1"}, "only the player who moved")

    def test_an_entry_with_no_moves_at_all_is_dropped(self):
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5)}),
            snapshot("2026-11-23T07:00", {"1": player(25.5)}),
            snapshot("2026-11-23T08:00", {"1": player(24.5)}),
        ]
        self.assertEqual(
            [entry["timestamp"] for entry in _compact_snapshots_for_supabase(series)],
            ["2026-11-23T06:00", "2026-11-23T08:00"],
        )

    def test_out_of_order_snapshots_are_sorted_before_diffing(self):
        # Diffing in file order would make the later line look like the opening.
        series = [
            snapshot("2026-11-23T08:00", {"1": player(26.5)}),
            snapshot("2026-11-23T06:00", {"1": player(25.5)}),
        ]
        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual([entry["timestamp"] for entry in compacted], ["2026-11-23T06:00", "2026-11-23T08:00"])
        self.assertEqual(compacted[0]["players"]["1"]["props"]["PTS"]["fanduel"]["line"], 25.5)

    def test_the_same_line_on_two_books_is_tracked_separately(self):
        series = [
            snapshot("2026-11-23T06:00", {"1": {
                "game_date": "2026-11-23",
                "props": {"PTS": {"fanduel": {"line": 25.5}, "draftkings": {"line": 25.5}}},
            }}),
            snapshot("2026-11-23T07:00", {"1": {
                "game_date": "2026-11-23",
                "props": {"PTS": {"fanduel": {"line": 24.5}, "draftkings": {"line": 25.5}}},
            }}),
        ]
        compacted = _compact_snapshots_for_supabase(series)
        self.assertEqual(set(compacted[1]["players"]["1"]["props"]["PTS"]), {"fanduel"})

    def test_a_line_that_returns_to_its_opening_value_is_still_a_transition(self):
        series = [
            snapshot("2026-11-23T06:00", {"1": player(25.5)}),
            snapshot("2026-11-23T07:00", {"1": player(26.5)}),
            snapshot("2026-11-23T08:00", {"1": player(25.5)}),
        ]
        self.assertEqual(len(_compact_snapshots_for_supabase(series)), 3)

    def test_string_and_float_lines_compare_equal(self):
        # The scrapers are not consistent about this and "25.5" vs 25.5 would
        # otherwise store a phantom move on every tick.
        series = [
            snapshot("2026-11-23T06:00", {"1": player("25.5")}),
            snapshot("2026-11-23T07:00", {"1": player(25.5)}),
        ]
        self.assertEqual(len(_compact_snapshots_for_supabase(series)), 1)

    def test_degenerate_input_does_not_raise(self):
        self.assertEqual(_compact_snapshots_for_supabase([]), [])
        self.assertEqual(_compact_snapshots_for_supabase(None), [])
        self.assertEqual(_compact_snapshots_for_supabase([{"timestamp": "x"}]), [])
        self.assertEqual(_compact_snapshots_for_supabase([{"players": "nope"}]), [])
        self.assertEqual(_compact_snapshots_for_supabase([None, 5]), [])
        self.assertEqual(
            _compact_snapshots_for_supabase([snapshot("2026-11-23T06:00", {"1": {"props": "nope"}})]),
            [],
        )

    def test_entries_keep_the_snapshot_shape_readers_expect(self):
        series = [snapshot("2026-11-23T06:00", {"1": player(25.5)})]
        entry = _compact_snapshots_for_supabase(series)[0]
        self.assertEqual(set(entry), {"timestamp", "label", "players"})
        self.assertTrue(json.dumps(entry), "an entry has to be jsonb-serializable")


class CompactionShrinksTheRow(unittest.TestCase):
    """The row size is the write cost, so the saving is the point, not a bonus."""

    def _day(self, snapshots=56, players=200, stats=5, books=2, move_every=16):
        """A day shaped like a real one: many keys, few of them moving per tick."""
        series = []
        for index in range(snapshots):
            rows = {}
            for player_id in range(players):
                props = {}
                for stat in range(stats):
                    # Each key moves once every `move_every` ticks, staggered.
                    steps = (index + player_id + stat) // move_every
                    props[f"STAT{stat}"] = {
                        f"book{book}": {"line": 20.5 + steps, "over": -110 - (index % 7), "under": -110}
                        for book in range(books)
                    }
                rows[str(player_id)] = {
                    "name": f"player {player_id}", "team": "BOS",
                    "game_date": "2026-11-23", "props": props,
                }
            series.append(snapshot(f"2026-11-23T{index:02d}:00", rows))
        return series

    def test_a_whole_day_costs_less_than_the_full_snapshots_it_replaces(self):
        series = self._day()
        full = len(json.dumps(series, separators=(",", ":")))
        compacted = len(json.dumps(_compact_snapshots_for_supabase(series), separators=(",", ":")))
        self.assertLess(compacted * 5, full, "a day of transitions is a fraction of a day of snapshots")

    def test_the_rewritten_row_stops_growing_with_the_number_of_ticks(self):
        # The old encoding's real defect: the row grew every tick and each tick
        # rewrote all of it, so a day cost O(ticks^2). Transitions make the row
        # grow with moves instead, and a tick with no moves rewrites nothing new.
        series = self._day()
        half = len(json.dumps(_compact_snapshots_for_supabase(series[:28]), separators=(",", ":")))
        whole = len(json.dumps(_compact_snapshots_for_supabase(series), separators=(",", ":")))
        self.assertLess(whole, half * 2.5, "doubling the day does not double the row")


if __name__ == "__main__":
    unittest.main()
