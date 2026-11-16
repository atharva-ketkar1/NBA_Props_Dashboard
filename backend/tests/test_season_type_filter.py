"""Keeping the postseason out of the training data.

Playoff rotations and usage do not describe the regular-season games the board
prices, so they are excluded as training targets *and* as history -- a playoff
scoring line inside a player's trailing five would carry postseason usage into
a regular-season projection by a longer route.

The filter reads the season type out of the GAME_ID rather than guessing from
the date, because the date boundary moves every year and the play-in sits
between the two. The awkward part is that GAME_ID has been round-tripped
through CSV as an integer, so data/archive/gamelogs_2025-26.csv holds both
"0022400455" and "22400001" for regular-season games; everything here pads to
ten characters first.
"""

import csv
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
PROP_MODELING_DIR = BACKEND_DIR / "prop_modeling"
if str(PROP_MODELING_DIR) not in sys.path:
    sys.path.insert(0, str(PROP_MODELING_DIR))

from scrapers.season_type import (
    ALL_STAR,
    PLAYOFFS,
    PLAY_IN,
    PRE_SEASON,
    REGULAR_SEASON,
    is_regular_season_game_id,
    season_type_from_game_id,
)
from prop_modeling.build_regression_dataset import _build_history


class SeasonTypeFromGameIdTests(unittest.TestCase):
    def test_reads_each_season_type(self):
        self.assertEqual(season_type_from_game_id("0012500001"), PRE_SEASON)
        self.assertEqual(season_type_from_game_id("0022500001"), REGULAR_SEASON)
        self.assertEqual(season_type_from_game_id("0032500001"), ALL_STAR)
        self.assertEqual(season_type_from_game_id("0042500101"), PLAYOFFS)
        self.assertEqual(season_type_from_game_id("0052500001"), PLAY_IN)

    def test_survives_the_csv_integer_round_trip(self):
        # The same file holds both spellings for regular-season games.
        self.assertEqual(season_type_from_game_id("0022400455"), REGULAR_SEASON)
        self.assertEqual(season_type_from_game_id("22400001"), REGULAR_SEASON)
        self.assertEqual(season_type_from_game_id("42500101"), PLAYOFFS)

    def test_accepts_an_integer(self):
        self.assertEqual(season_type_from_game_id(22400001), REGULAR_SEASON)
        self.assertEqual(season_type_from_game_id(42500101), PLAYOFFS)

    def test_unreadable_ids_are_none_not_a_guess(self):
        for bad in (None, "", "   ", "abc", "00224004xx", "00225000011"):
            self.assertIsNone(season_type_from_game_id(bad), bad)

    def test_an_unreadable_id_is_not_regular_season(self):
        # A filter built on this keeps only what it can vouch for.
        self.assertFalse(is_regular_season_game_id("abc"))
        self.assertFalse(is_regular_season_game_id(None))

    def test_an_unknown_type_digit_is_none(self):
        self.assertIsNone(season_type_from_game_id("0092500001"))


def _write_gamelog(path, rows):
    header = ["PLAYER_ID", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE", "MIN", "PTS"]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _row(pid, gid, date, mins=30.0, pts=20.0, team="BOS"):
    return {"PLAYER_ID": pid, "TEAM_ABBREVIATION": team, "GAME_ID": gid,
            "GAME_DATE": date, "MIN": mins, "PTS": pts}


class BuildHistoryFilterTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "gamelogs_2025-26.csv"

    def tearDown(self):
        self._dir.cleanup()

    def test_playoff_rows_are_dropped_by_default(self):
        _write_gamelog(self.path, [
            _row("1", "0022500001", "2026-01-05"),
            _row("1", "0022500002", "2026-01-07"),
            _row("1", "0042500101", "2026-04-20"),
        ])
        rows = _build_history([self.path])
        self.assertEqual(len(rows), 2)
        kept = {str(r[2]["GAME_ID"]) for r in rows}
        self.assertNotIn("0042500101", kept)

    def test_playoff_rows_are_kept_when_asked(self):
        _write_gamelog(self.path, [
            _row("1", "0022500001", "2026-01-05"),
            _row("1", "0042500101", "2026-04-20"),
        ])
        rows = _build_history([self.path], regular_season_only=False)
        self.assertEqual(len(rows), 2)

    def test_play_in_all_star_and_preseason_also_go(self):
        _write_gamelog(self.path, [
            _row("1", "0022500001", "2026-01-05"),
            _row("1", "0012500001", "2025-10-05"),
            _row("1", "0032500001", "2026-02-15"),
            _row("1", "0052500001", "2026-04-15"),
        ])
        rows = _build_history([self.path])
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0][2]["GAME_ID"]), "0022500001")

    def test_a_playoff_game_cannot_reach_a_rolling_window(self):
        # The point of filtering history and not just targets: with the
        # playoff game dropped, nothing downstream can average it in.
        _write_gamelog(self.path, [
            _row("1", "0022500001", "2026-01-05", pts=10.0),
            _row("1", "0042500101", "2026-04-20", pts=40.0),
            _row("1", "0022600001", "2026-10-21", pts=12.0),
        ])
        rows = _build_history([self.path])
        self.assertNotIn(40.0, [float(r[2]["PTS"]) for r in rows])

    def test_zero_minute_rows_follow_the_same_filter(self):
        # The minutes dataset deliberately keeps DNPs, but a playoff DNP is
        # still a playoff row.
        _write_gamelog(self.path, [
            _row("1", "0022500001", "2026-01-05", mins=0.0),
            _row("1", "0042500101", "2026-04-20", mins=0.0),
        ])
        rows = _build_history([self.path], include_zero_minutes=True)
        self.assertEqual(len(rows), 1)

    def test_too_many_unreadable_ids_fails_the_build(self):
        # Keeping what cannot be classified stops meaning "regular season
        # only" once there is enough of it, so the build refuses.
        _write_gamelog(self.path, [_row(str(i), "xx", "2026-01-05") for i in range(50)])
        with self.assertRaises(ValueError) as ctx:
            _build_history([self.path])
        self.assertIn("season type", str(ctx.exception))

    def test_a_few_unreadable_ids_are_kept_and_reported(self):
        rows_in = [_row(str(i), "0022500001", "2026-01-05") for i in range(200)]
        rows_in.append(_row("999", "xx", "2026-01-06"))
        _write_gamelog(self.path, rows_in)
        rows = _build_history([self.path])
        self.assertEqual(len(rows), 201)

    def test_ordering_does_not_depend_on_leading_zeros(self):
        # "22500002" and "0022500001" sort the wrong way round as raw strings.
        _write_gamelog(self.path, [
            _row("1", "22500002", "2026-01-05"),
            _row("2", "0022500001", "2026-01-05"),
        ])
        rows = _build_history([self.path])
        ids = [str(r[2]["GAME_ID"]).zfill(10) for r in rows]
        self.assertEqual(ids, sorted(ids))


class RealGamelogTests(unittest.TestCase):
    """What the filter actually removes from the files on disk."""

    def test_the_2025_26_archive_carries_playoff_rows(self):
        path = BACKEND_DIR / "data" / "archive" / "gamelogs_2025-26.csv"
        if not path.exists():
            self.skipTest("gamelogs_2025-26.csv not present (backend/data is gitignored)")
        with_playoffs = _build_history([path], regular_season_only=False)
        regular_only = _build_history([path])
        self.assertGreater(len(with_playoffs), len(regular_only))
        # Exactly one regular season, so the game count is the league's 1,230.
        games = {str(r[2]["GAME_ID"]).zfill(10) for r in regular_only}
        self.assertEqual(len(games), 1230)




class PriorSeasonFilterTests(unittest.TestCase):
    """The serve path's prior-season blend must exclude the postseason too.

    Training drops it, so leaving it here would be a train/serve mismatch --
    and the trimmed window takes the most recent games, which is where the
    playoff games are.
    """

    def setUp(self):
        from utils import prior_season
        self.ps = prior_season

    def _log(self, gid, date, pts=20.0, mins=30.0):
        return {"GAME_ID": gid, "GAME_DATE": date, "MIN": mins, "PTS": pts}

    def test_playoff_logs_are_removed(self):
        logs = [
            self._log("0022500001", "2026-01-05"),
            self._log("0042500101", "2026-04-20"),
            self._log("0022500002", "2026-01-07"),
        ]
        kept = self.ps._regular_season_only(logs)
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(str(l["GAME_ID"]).zfill(10)[2] == "2" for l in kept))

    def test_play_in_is_removed_too(self):
        logs = [self._log("0052500001", "2026-04-15")]
        self.assertEqual(self.ps._regular_season_only(logs), [])

    def test_a_log_without_a_game_id_is_kept(self):
        # master_feed's game_log shape is a fallback source and need not carry
        # one; dropping a player's history over a missing field is worse than
        # keeping a game that might be postseason.
        logs = [{"GAME_DATE": "2026-01-05", "MIN": 30.0, "PTS": 20.0}]
        self.assertEqual(len(self.ps._regular_season_only(logs)), 1)

    def test_an_unreadable_game_id_is_kept(self):
        logs = [self._log("not-an-id", "2026-01-05")]
        self.assertEqual(len(self.ps._regular_season_only(logs)), 1)

    def test_empty_in_empty_out(self):
        self.assertEqual(self.ps._regular_season_only([]), [])

    def test_a_cache_without_the_stamp_is_treated_as_stale(self):
        # The cache is written once and trusted forever, so a payload built
        # before the filter existed has to be rebuilt or the fix never lands.
        import json
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            season = "2025-26"
            stale = {"season": season, "source": "supabase",
                     "profiles": {"1": {"GP": 10}}, "logs": {"1": []}}
            (cache_dir / f"prior_history_{season}.json").write_text(json.dumps(stale))

            rebuilt = {"season": season, "source": "supabase",
                       "profiles": {"2": {"GP": 11}}, "logs": {"2": []},
                       "regular_season_only": True}
            with mock.patch.object(self.ps, "CACHE_DIR", cache_dir), \
                 mock.patch.object(self.ps, "_build_season_payload",
                                   return_value=rebuilt) as build:
                out = self.ps.build_cache(season)
            build.assert_called_once()
            self.assertEqual(set(out["profiles"]), {"2"})

    def test_a_stamped_cache_is_reused(self):
        import json
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            season = "2025-26"
            fresh = {"season": season, "source": "supabase",
                     "profiles": {"1": {"GP": 10}}, "logs": {"1": []},
                     "regular_season_only": True}
            (cache_dir / f"prior_history_{season}.json").write_text(json.dumps(fresh))

            with mock.patch.object(self.ps, "CACHE_DIR", cache_dir), \
                 mock.patch.object(self.ps, "_build_season_payload") as build:
                out = self.ps.build_cache(season)
            build.assert_not_called()
            self.assertEqual(set(out["profiles"]), {"1"})

    def test_the_real_cache_on_disk_agrees_with_its_own_stamp(self):
        """Whichever state the live cache is in, it must match what it claims.

        This started out asserting the on-disk cache *did* carry postseason
        rows, to show the filter had real work to do. That self-invalidates:
        once the fix reaches the VM and the cache is rebuilt, the rows are gone
        and the test fails for the best possible reason. Worse, it fails on
        data that is gitignored and syncs from a disposable host, so the result
        depended on when someone last pulled.

        So it reads the `regular_season_only` stamp and checks the matching
        invariant instead. A stamped cache must be clean -- that is the fix
        holding in production. An unstamped one predates the filter and must
        still have something to remove, or the filter is a no-op and the
        exclusion never mattered.
        """
        import json
        path = BACKEND_DIR / "data" / "prior" / "prior_history_2025-26.json"
        if not path.exists():
            self.skipTest("prior cache not present (backend/data is gitignored)")
        data = json.loads(path.read_text())
        logs = data.get("logs") or {}
        if not logs:
            self.skipTest("prior cache carries no game logs")
        before = sum(len(v) for v in logs.values())
        after = sum(len(self.ps._regular_season_only(v)) for v in logs.values())
        if data.get("regular_season_only"):
            self.assertEqual(
                before, after,
                "cache stamped regular_season_only still holds %d postseason rows"
                % (before - after),
            )
        else:
            self.assertGreater(
                before - after, 0,
                "an unstamped cache should still contain postseason rows to remove",
            )


if __name__ == "__main__":
    unittest.main()
