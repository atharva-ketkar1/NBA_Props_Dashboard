import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import roster_sync
from utils.roster_sync import sync_roster_membership


SEASON = "2026-27"


def make_index_result(count, source="fetch", season=SEASON, cache_season=None, first_id=1):
    return {
        "players": [
            {
                "PLAYER_ID": first_id + offset,
                "PLAYER_NAME": f"Player {first_id + offset}",
                "TEAM_ABBREVIATION": "BOS",
            }
            for offset in range(count)
        ],
        "source": source,
        "season": season,
        "cache_season": cache_season if cache_season is not None else season,
        "fetched_at": 0,
    }


class FakeResponse:
    def __init__(self, count=None, data=None):
        self.count = count
        self.data = data if data is not None else []


class FakeQuery:
    """Records the call chain instead of talking to PostgREST."""

    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.kind = None
        self.payload = None
        self.returning = None
        self.ids = None
        self.filters = {}

    def select(self, *columns, count=None, head=None):
        self.kind = "select"
        self.columns = columns
        self.count_mode = count
        self.head = head
        return self

    def update(self, payload, returning=None):
        self.kind = "update"
        self.payload = payload
        self.returning = returning
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def in_(self, column, values):
        self.ids = list(values)
        return self

    def execute(self):
        if self.kind == "select":
            self.client.count_calls.append(self.filters)
            if self.client.count_raises:
                raise RuntimeError("count unavailable")
            return FakeResponse(count=self.client.active_count)

        self.client.updates.append({
            "payload": self.payload,
            "returning": self.returning,
            "ids": self.ids,
        })
        if self.client.fail_update_calls and len(self.client.updates) in self.client.fail_update_calls:
            raise RuntimeError("update rejected")
        return FakeResponse(data=[])


class FakeClient:
    def __init__(self, active_count=0, fail_update_calls=None, count_raises=False):
        self.active_count = active_count
        self.fail_update_calls = set(fail_update_calls or [])
        self.count_raises = count_raises
        self.updates = []
        self.count_calls = []

    def table(self, name):
        return FakeQuery(self, name)

    def stamped_ids(self):
        ids = []
        for call in self.updates:
            ids.extend(call["ids"] or [])
        return ids


class GuardTests(unittest.TestCase):
    """Every guard must skip the sweep rather than write a partial one.

    An unstamped player reads as off-roster, so a partial sweep would age out
    whatever it failed to cover -- i.e. most of the league.
    """

    def test_skips_when_index_is_unavailable(self):
        client = FakeClient()

        result = sync_roster_membership(
            season=SEASON,
            index_result={"players": [], "source": "empty", "cache_season": None},
            client=client,
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "player_index_unavailable")
        self.assertEqual(client.updates, [])

    def test_skips_when_cache_is_from_another_season(self):
        client = FakeClient()

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(500, source="cache", cache_season="2025-26"),
            client=client,
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "player_index_cache_season_mismatch")
        self.assertEqual(client.updates, [])

    def test_accepts_cache_from_the_season_being_stamped(self):
        client = FakeClient()

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(500, source="cache", cache_season=SEASON),
            client=client,
        )

        self.assertFalse(result["skipped"])
        self.assertEqual(result["stamped"], 500)

    def test_skips_a_thin_index(self):
        client = FakeClient()

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(41),
            client=client,
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "player_index_too_small")
        self.assertEqual(client.updates, [])

    def test_skips_when_the_active_set_would_shrink_sharply(self):
        # The September case: resolve_season() has rolled over but
        # commonallplayers for the unstarted season is still thin.
        client = FakeClient(active_count=519)

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(410),
            client=client,
        )

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "player_index_would_shrink_active_set")
        self.assertEqual(client.updates, [])

    def test_allows_normal_roster_churn(self):
        client = FakeClient(active_count=519)

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(505),
            client=client,
        )

        self.assertFalse(result["skipped"])
        self.assertEqual(result["stamped"], 505)

    def test_proceeds_when_the_count_is_unavailable(self):
        # A failed count must not block the sweep; the row-count guard still applies.
        client = FakeClient(count_raises=True)

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(500),
            client=client,
        )

        self.assertFalse(result["skipped"])
        self.assertEqual(result["stamped"], 500)
        self.assertIsNone(result["already_active"])


class StampingTests(unittest.TestCase):
    def test_stamps_every_player_in_chunks(self):
        client = FakeClient()
        index_result = make_index_result(450)

        result = sync_roster_membership(season=SEASON, index_result=index_result, client=client)

        expected_ids = sorted(record["PLAYER_ID"] for record in index_result["players"])
        self.assertEqual(sorted(client.stamped_ids()), expected_ids)
        self.assertEqual(result["stamped"], 450)
        self.assertEqual(
            len(client.updates),
            -(-450 // roster_sync.UPDATE_CHUNK_SIZE),  # ceil
        )
        for call in client.updates:
            self.assertLessEqual(len(call["ids"]), roster_sync.UPDATE_CHUNK_SIZE)

    def test_stamp_payload_carries_season_and_timestamp(self):
        client = FakeClient()

        sync_roster_membership(season=SEASON, index_result=make_index_result(400), client=client)

        payload = client.updates[0]["payload"]
        self.assertEqual(payload["roster_season"], SEASON)
        self.assertIn("last_seen_at", payload)
        # Only membership is touched -- never identity or stats.
        self.assertEqual(set(payload), {"roster_season", "last_seen_at"})

    def test_update_does_not_echo_rows_back(self):
        # players rows carry game_log / zone JSONB; the default
        # return=representation would ship all of it back for every chunk.
        client = FakeClient()

        sync_roster_membership(season=SEASON, index_result=make_index_result(400), client=client)

        for call in client.updates:
            self.assertEqual(str(getattr(call["returning"], "value", call["returning"])), "minimal")

    def test_duplicate_ids_are_collapsed(self):
        client = FakeClient()
        index_result = make_index_result(400)
        index_result["players"].append(dict(index_result["players"][0]))

        result = sync_roster_membership(season=SEASON, index_result=index_result, client=client)

        self.assertEqual(result["candidates"], 400)
        self.assertEqual(len(set(client.stamped_ids())), 400)

    def test_dry_run_writes_nothing(self):
        client = FakeClient()

        result = sync_roster_membership(
            season=SEASON,
            index_result=make_index_result(500),
            client=client,
            dry_run=True,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["stamped"], 0)
        self.assertEqual(client.updates, [])

    def test_a_failed_chunk_is_reported_without_raising(self):
        client = FakeClient(fail_update_calls=[2])

        result = sync_roster_membership(season=SEASON, index_result=make_index_result(450), client=client)

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_chunks"], 1)
        self.assertEqual(result["stamped"], 450 - roster_sync.UPDATE_CHUNK_SIZE)


if __name__ == "__main__":
    unittest.main()
