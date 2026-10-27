"""Regression tests for tracker/recap parity.

The bug these pin down: the tracker's accumulated running list was re-filtered
through _filter_tracker_recommendations before rendering, and that gate carries
the per-player cap inside _filter_discord_recommendations
(EDGE_DISCORD_PER_PLAYER_LIMIT, 1 by default). A slate holding several picks on
one player therefore rendered the earliest and dropped the rest, while
_queue_results_recap_payload queued tracker_delta["new_recommendations"] — a
parallel list — so the dropped picks were still graded the next day and showed
up in results the tracker never stated.

The drop fed itself: an evicted pick never reached sent_snapshot, so the next
_compute_tracker_delta saw nothing logged for that player and admitted another.
Measured on the 2026-05-09 slate, four Austin Reaves props were graded
(PTS+REB+AST u32.5, FG3M u2.5, AST u6.5, PTS+REB u26.5) while the tracker
displayed only PTS+REB+AST.
"""

import sys
import unittest
from datetime import timedelta
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es


def _tomorrow() -> str:
    return (es.get_et_now() + timedelta(days=1)).strftime("%Y-%m-%d")


def _recommendation(
    *,
    player_id: int,
    stat_type: str,
    game_date: str,
    book: str = "dk",
    side: str = "under",
    line: float = 26.5,
    score: float = 80.0,
    rank: int = 1,
    game_id: str = "0042500001",
    first_logged_at: str = "",
) -> dict:
    recommendation = {
        "recommendation_key": f"{player_id}|{game_date}|{stat_type}|{side}",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "team": "LAL",
        "opponent": "CLE",
        "game_id": game_id,
        "game_date": game_date,
        "game_started": False,
        "sportsbook": book,
        "sportsbook_label": es.BOOK_LABELS.get(book, book),
        "stat_type": stat_type,
        "stat_label": stat_type,
        "pick": side,
        "pick_label": side.title(),
        "line": line,
        "odds": -110.0,
        "odds_display": "-110",
        "edge_score": score,
        "display_edge_score": score,
        "rank": rank,
    }
    if first_logged_at:
        recommendation["first_logged_at"] = first_logged_at
    return recommendation


class TrackerLedgerGateTests(unittest.TestCase):
    """The published ledger must not be re-gated on selection criteria."""

    def test_several_picks_on_one_player_all_survive_the_ledger_gate(self):
        slate = _tomorrow()
        published = [
            _recommendation(
                player_id=1630559,
                stat_type=stat_type,
                game_date=slate,
                book=book,
                line=line,
                first_logged_at=logged_at,
            )
            for stat_type, book, line, logged_at in (
                ("PTS+REB+AST", "dk", 32.5, f"{slate}T06:00:00-04:00"),
                ("FG3M", "dk", 2.5, f"{slate}T06:42:45-04:00"),
                ("PTS+REB", "fd", 26.5, f"{slate}T10:16:17-04:00"),
                ("AST", "fd", 6.5, f"{slate}T12:26:39-04:00"),
            )
        ]

        # The old selection gate collapsed these to the player's first pick.
        self.assertEqual(len(es._filter_tracker_recommendations(published)), 1)

        ledger = es._filter_tracker_ledger_recommendations(published, slate_date=slate)
        self.assertEqual(len(ledger), 4)
        self.assertEqual(
            [recommendation["stat_type"] for recommendation in ledger],
            ["PTS+REB+AST", "FG3M", "PTS+REB", "AST"],
        )

    def test_ledger_gate_drops_unsupported_books(self):
        slate = _tomorrow()
        published = [
            _recommendation(player_id=2544, stat_type="STL", game_date=slate, book="dk"),
            _recommendation(player_id=2544, stat_type="BLK", game_date=slate, book="pp"),
        ]

        ledger = es._filter_tracker_ledger_recommendations(published, slate_date=slate)
        self.assertEqual([recommendation["stat_type"] for recommendation in ledger], ["STL"])

    def test_ledger_gate_drops_other_slates(self):
        slate = _tomorrow()
        other = (es.get_et_now() + timedelta(days=2)).strftime("%Y-%m-%d")
        published = [
            _recommendation(player_id=2544, stat_type="STL", game_date=slate),
            _recommendation(player_id=1628378, stat_type="PTS+AST", game_date=other),
        ]

        ledger = es._filter_tracker_ledger_recommendations(published, slate_date=slate)
        self.assertEqual([recommendation["player_id"] for recommendation in ledger], [2544])

    def test_ledger_gate_keeps_a_pick_whose_game_has_tipped(self):
        """Grading happens after tip, so the ledger outlives the pregame gate."""
        slate = _tomorrow()
        tipped = _recommendation(player_id=2544, stat_type="STL", game_date=slate)
        tipped["game_started"] = True

        self.assertEqual(es._filter_tracker_recommendations([tipped]), [])
        self.assertEqual(
            len(es._filter_tracker_ledger_recommendations([tipped], slate_date=slate)),
            1,
        )


class TrackerRecapParityTests(unittest.TestCase):
    """Whatever the tracker renders is exactly what the recap grades."""

    def test_recap_queue_matches_the_rendered_ledger(self):
        slate = _tomorrow()
        rendered = [
            _recommendation(
                player_id=1630559,
                stat_type=stat_type,
                game_date=slate,
                first_logged_at=f"{slate}T06:0{index}:00-04:00",
            )
            for index, stat_type in enumerate(("PTS+REB+AST", "FG3M", "PTS+REB"))
        ]

        state: dict = {}
        es._queue_results_recap_payload(
            state,
            rendered,
            generated_at=f"{slate}T12:00:00-04:00",
            refresh_label="pipeline",
            alert_kind="tracker",
        )

        tracked = state["pending_result_recaps"][slate]["tracked_recommendations"]
        self.assertEqual(
            {recommendation["recommendation_key"] for recommendation in rendered},
            set(tracked),
        )

    def test_requeueing_the_ledger_is_idempotent(self):
        """The ledger is re-queued on every send, so repeats must not duplicate."""
        slate = _tomorrow()
        rendered = [
            _recommendation(
                player_id=1630559,
                stat_type="PTS+REB+AST",
                game_date=slate,
                first_logged_at=f"{slate}T06:00:00-04:00",
            )
        ]

        state: dict = {}
        for generated_at in (f"{slate}T06:00:00-04:00", f"{slate}T12:00:00-04:00"):
            es._queue_results_recap_payload(
                state,
                rendered,
                generated_at=generated_at,
                refresh_label="pipeline",
                alert_kind="tracker",
            )

        entry = state["pending_result_recaps"][slate]
        self.assertEqual(len(entry["tracked_recommendations"]), 1)
        self.assertEqual(len(entry["recommendations"]), 1)
        # The first copy wins, so a pick keeps the time it entered the tracker.
        self.assertEqual(
            entry["recommendations"][0]["first_logged_at"],
            f"{slate}T06:00:00-04:00",
        )

    def test_ghost_picks_stop_once_the_first_pick_is_stored(self):
        """The self-feeding loop: eviction kept the delta admitting new picks.

        With the player's first pick present in sent_snapshot,
        EDGE_TRACKER_PER_PLAYER_LIMIT refuses the next one — which only works
        because the ledger gate no longer evicts it before it is stored.
        """
        slate = _tomorrow()
        logged = _recommendation(
            player_id=1630559,
            stat_type="PTS+REB+AST",
            game_date=slate,
            first_logged_at=f"{slate}T06:00:00-04:00",
        )
        state = {
            "discord_tracker_state_by_date": {
                slate: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([logged]),
                    "first_sent_at": f"{slate}T06:00:00-04:00",
                }
            }
        }

        second_pick = _recommendation(player_id=1630559, stat_type="FG3M", game_date=slate, line=2.5)
        delta = es._compute_tracker_delta(
            [second_pick],
            state,
            slate_date=slate,
            generated_at=f"{slate}T06:42:45-04:00",
        )

        self.assertEqual(delta["new_recommendations"], [])
        self.assertFalse(delta["should_send"])

        running = es._filter_tracker_ledger_recommendations(
            es._tracker_running_recommendations(state, slate_date=slate, tracker_delta=delta),
            slate_date=slate,
        )
        self.assertEqual(
            [recommendation["stat_type"] for recommendation in running],
            ["PTS+REB+AST"],
        )


if __name__ == "__main__":
    unittest.main()


class PublishedPickGuardTests(unittest.TestCase):
    """State queued by the old code must not post a pick the tracker never had."""

    def _reaves_slate(self) -> tuple:
        slate = "2026-05-09"
        published = _recommendation(
            player_id=1630559,
            stat_type="PTS+REB+AST",
            game_date=slate,
            line=32.5,
            first_logged_at="2026-05-08T21:46:30-04:00",
        )
        ghosts = [
            _recommendation(player_id=1630559, stat_type="FG3M", game_date=slate, line=2.5),
            _recommendation(player_id=1630559, stat_type="AST", game_date=slate, line=6.5, book="fd"),
            _recommendation(player_id=1630559, stat_type="PTS+REB", game_date=slate, line=26.5, book="fd"),
        ]
        tracked = {
            recommendation["recommendation_key"]: {**recommendation, "first_alert_kind": "tracker"}
            for recommendation in [published, *ghosts]
        }
        recap_payload = {
            "game_date": slate,
            "tracked_recommendations": tracked,
            "recommendations": list(tracked.values()),
        }
        state = {
            "discord_tracker_state_by_date": {
                slate: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([published]),
                }
            }
        }
        return slate, published, recap_payload, state

    def test_unpublished_tracker_picks_are_pruned(self):
        slate, published, recap_payload, state = self._reaves_slate()

        filtered, pruned = es._filter_recap_payload_to_published_picks(
            recap_payload, state, game_date=slate
        )

        self.assertTrue(pruned)
        self.assertEqual(
            list(filtered["tracked_recommendations"]),
            [published["recommendation_key"]],
        )
        self.assertEqual(len(filtered["recommendations"]), 1)

    def test_preview_picks_are_exempt(self):
        slate, published, recap_payload, state = self._reaves_slate()
        for recommendation in recap_payload["tracked_recommendations"].values():
            if recommendation["recommendation_key"] != published["recommendation_key"]:
                recommendation["first_alert_kind"] = "preview"

        filtered, pruned = es._filter_recap_payload_to_published_picks(
            recap_payload, state, game_date=slate
        )

        self.assertFalse(pruned)
        self.assertEqual(len(filtered["tracked_recommendations"]), 4)

    def test_absent_tracker_state_prunes_nothing(self):
        slate, _published, recap_payload, _state = self._reaves_slate()

        filtered, pruned = es._filter_recap_payload_to_published_picks(
            recap_payload, {}, game_date=slate
        )

        self.assertFalse(pruned)
        self.assertEqual(len(filtered["tracked_recommendations"]), 4)


def _simulate_tracker_tick(state: dict, board: list, *, slate: str, generated_at: str) -> list:
    """Mirror the tracker half of one run_edge_score_refresh tick."""
    delta = es._compute_tracker_delta(
        board, state, slate_date=slate, generated_at=generated_at
    )
    running = es._filter_tracker_ledger_recommendations(
        es._tracker_running_recommendations(state, slate_date=slate, tracker_delta=delta),
        slate_date=slate,
    )
    if not delta["should_send"]:
        return running

    tracker_state = state.setdefault("discord_tracker_state_by_date", {}).setdefault(slate, {})
    tracker_state["sent_snapshot"] = es._tracker_state_snapshot_for_recommendations(running)
    tracker_state.setdefault("first_sent_at", generated_at)
    tracker_state["last_sent_at"] = generated_at
    es._queue_results_recap_payload(
        state,
        running,
        generated_at=generated_at,
        refresh_label="pipeline",
        alert_kind="tracker",
    )
    return running


class OnePropPerPlayerPerSlateTests(unittest.TestCase):
    """The cap is slate-wide, so it survives a day of 5-minute refreshes."""

    def test_a_players_best_prop_changing_all_day_logs_once(self):
        slate = _tomorrow()
        # Each tick's board holds one Reaves prop, as EDGE_TOP_PER_PLAYER_LIMIT
        # guarantees — but a different one, which is what grew the ledger to
        # four props on the real 2026-05-09 slate.
        ticks = [
            ("PTS+REB+AST", 32.5, "dk", f"{slate}T06:00:00-04:00"),
            ("FG3M", 2.5, "dk", f"{slate}T06:42:45-04:00"),
            ("PTS+REB", 26.5, "fd", f"{slate}T10:16:17-04:00"),
            ("AST", 6.5, "fd", f"{slate}T12:26:39-04:00"),
        ]

        state: dict = {}
        running: list = []
        for stat_type, line, book, generated_at in ticks:
            running = _simulate_tracker_tick(
                state,
                [
                    _recommendation(
                        player_id=1630559,
                        stat_type=stat_type,
                        game_date=slate,
                        line=line,
                        book=book,
                    )
                ],
                slate=slate,
                generated_at=generated_at,
            )

        self.assertEqual([r["stat_type"] for r in running], ["PTS+REB+AST"])
        graded = state["pending_result_recaps"][slate]["tracked_recommendations"]
        self.assertEqual(len(graded), 1)
        # Parity still holds: results grade exactly what the tracker stated.
        self.assertEqual(
            set(graded),
            set(state["discord_tracker_state_by_date"][slate]["sent_snapshot"]),
        )

    def test_other_players_still_get_logged(self):
        """The cap must thin correlated legs, not the board."""
        slate = _tomorrow()
        state: dict = {}
        for index, player_id in enumerate((1630559, 2544, 1628378)):
            running = _simulate_tracker_tick(
                state,
                [
                    _recommendation(
                        player_id=player_id,
                        stat_type="PTS",
                        game_date=slate,
                        game_id=f"004250000{index}",
                    )
                ],
                slate=slate,
                generated_at=f"{slate}T0{index + 6}:00:00-04:00",
            )

        self.assertEqual(
            sorted(r["player_id"] for r in running), [2544, 1628378, 1630559]
        )

    def test_best_of_a_correlated_cluster_wins(self):
        """Candidates arrive best-first, so the kept prop is the strongest."""
        slate = _tomorrow()
        cluster = [
            _recommendation(player_id=1630559, stat_type="PTS", game_date=slate, score=91.0),
            _recommendation(player_id=1630559, stat_type="AST", game_date=slate, score=84.0),
            _recommendation(player_id=1630559, stat_type="FG3M", game_date=slate, score=78.0),
        ]

        kept = es._drop_second_prop_per_player(cluster, published_by_player={})

        self.assertEqual([r["stat_type"] for r in kept], ["PTS"])

    def test_opposite_sides_of_one_player_are_still_one_prop(self):
        """_discord_recommendations_conflict reads these as unrelated bets."""
        slate = _tomorrow()
        over = _recommendation(player_id=1630559, stat_type="PTS", game_date=slate, side="over")
        under = _recommendation(player_id=1630559, stat_type="AST", game_date=slate, side="under")

        self.assertFalse(es._discord_recommendations_conflict(under, over))
        kept = es._drop_second_prop_per_player([over, under], published_by_player={})
        self.assertEqual(len(kept), 1)

    def test_refreshing_an_already_published_prop_is_allowed(self):
        """The cap counts distinct props, not re-sends of one."""
        slate = _tomorrow()
        logged = _recommendation(player_id=1630559, stat_type="PTS", game_date=slate)
        published = {
            es._recommendation_player_identity(logged): {logged["recommendation_key"]}
        }

        moved = {**logged, "line": 22.5, "odds": -125.0}
        kept = es._drop_second_prop_per_player([moved], published_by_player=published)

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["line"], 22.5)

    def test_cap_counts_every_channel(self):
        """A prop published as a preview still spends the player's slot."""
        slate = _tomorrow()
        previewed = _recommendation(player_id=1630559, stat_type="PTS", game_date=slate)
        state = {
            "discord_preview_state_by_date": {
                slate: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([previewed]),
                }
            }
        }

        published = es._published_player_props_for_slate(state, slate)
        # Indexed under every identity the player may be stored under.
        self.assertEqual(
            published,
            {
                identity: {previewed["recommendation_key"]}
                for identity in es._recommendation_player_identities(previewed)
            },
        )

        second = _recommendation(player_id=1630559, stat_type="AST", game_date=slate, line=6.5)
        self.assertEqual(
            es._drop_second_prop_per_player([second], published_by_player=published), []
        )

    def test_other_slates_do_not_spend_the_slot(self):
        slate = _tomorrow()
        other = (es.get_et_now() + timedelta(days=2)).strftime("%Y-%m-%d")
        logged = _recommendation(player_id=1630559, stat_type="PTS", game_date=other)
        state = {
            "discord_tracker_state_by_date": {
                other: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([logged]),
                }
            }
        }

        self.assertEqual(es._published_player_props_for_slate(state, slate), {})


class PreviewAccumulationTests(unittest.TestCase):
    def test_preview_log_holds_one_prop_per_player(self):
        slate = _tomorrow()
        state: dict = {}
        running: list = []
        for index, (stat_type, line) in enumerate((("PTS", 20.5), ("AST", 6.5), ("REB", 8.5))):
            running = es._preview_running_recommendations(
                state,
                slate_date=slate,
                preview_recommendations=[
                    _recommendation(
                        player_id=1630559, stat_type=stat_type, game_date=slate, line=line
                    )
                ],
                generated_at=f"{slate}T0{index + 6}:00:00-04:00",
            )
            state.setdefault("discord_preview_state_by_date", {})[slate] = {
                "sent_snapshot": es._tracker_state_snapshot_for_recommendations(running)
            }

        self.assertEqual([r["stat_type"] for r in running], ["PTS"])


class PlayerIdentityTests(unittest.TestCase):
    """A player gets one slot, not one slot per spelling of his name."""

    def test_name_only_history_blocks_an_id_bearing_candidate(self):
        slate = _tomorrow()
        # Older alert snapshots stored no player_id, so this player is on record
        # only under his name.
        legacy = _recommendation(player_id=1630559, stat_type="FG3M", game_date=slate, line=2.5)
        legacy.pop("player_id")
        state = {
            "discord_alert_state_by_date": {
                slate: {"last_sent_snapshot": {legacy["recommendation_key"]: legacy}}
            }
        }

        published = es._published_player_props_for_slate(state, slate)
        self.assertEqual(list(published), ["name:player 1630559"])

        candidate = _recommendation(player_id=1630559, stat_type="AST", game_date=slate, line=6.5)
        self.assertEqual(
            es._drop_second_prop_per_player([candidate], published_by_player=published), []
        )

    def test_id_history_blocks_a_name_only_candidate(self):
        slate = _tomorrow()
        logged = _recommendation(player_id=1630559, stat_type="PTS", game_date=slate)
        state = {
            "discord_tracker_state_by_date": {
                slate: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([logged])
                }
            }
        }

        candidate = _recommendation(player_id=1630559, stat_type="AST", game_date=slate, line=6.5)
        candidate.pop("player_id")
        self.assertEqual(
            es._drop_second_prop_per_player(
                [candidate],
                published_by_player=es._published_player_props_for_slate(state, slate),
            ),
            [],
        )

    def test_one_candidate_spends_both_of_its_identities(self):
        slate = _tomorrow()
        first = _recommendation(player_id=1630559, stat_type="PTS", game_date=slate)
        second = _recommendation(player_id=1630559, stat_type="AST", game_date=slate, line=6.5)
        second.pop("player_id")

        kept = es._drop_second_prop_per_player([first, second], published_by_player={})
        self.assertEqual([r["stat_type"] for r in kept], ["PTS"])
