import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils import edge_score as es


ET = ZoneInfo("America/New_York")


def _recommendation(
    *,
    player_id: int,
    game_id: str,
    score: float,
    rank: int,
    book: str = "dk",
    stat_type: str = "PTS",
    side: str = "over",
    line: float = 20.5,
    game_date: str,
) -> dict:
    return {
        "recommendation_key": f"{player_id}|{game_date}|{stat_type}|{side}",
        "player_id": player_id,
        "player_name": f"Player {player_id}",
        "team": "DET",
        "opponent": "PHI",
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


def _tomorrow() -> str:
    return (es.get_et_now() + timedelta(days=1)).strftime("%Y-%m-%d")


class NormalizedWeightedScoreTests(unittest.TestCase):
    def test_full_coverage_is_unchanged(self):
        self.assertAlmostEqual(es._normalized_weighted_score(0.44, 1.0), 0.44)

    def test_dark_components_do_not_compress_the_scale(self):
        # Opening week: ml_regression (0.25) and line_movement (0.06) dark.
        self.assertAlmostEqual(es._normalized_weighted_score(0.441, 0.69), 0.441 / 0.69, places=6)

    def test_thin_coverage_is_damped_by_the_floor(self):
        # Dividing by 0.2 would nearly quintuple two weak signals.
        self.assertAlmostEqual(
            es._normalized_weighted_score(0.1, 0.2),
            0.1 / es.MIN_RENORMALIZED_COMPONENT_WEIGHT,
            places=6,
        )

    def test_scale_survives_the_season_opening_board(self):
        # The real 2026-10-25 board: 0.441 raw over 0.69 coverage scored 68.9
        # against a 72.5 alert bar, so nothing was ever alerted.
        confidence_multiplier = 0.85 + (0.69 * 0.15)
        normalized = es._normalized_weighted_score(0.441, 0.69)
        edge_score = 50.0 + normalized * 45.0 * confidence_multiplier
        self.assertGreater(edge_score, es.EDGE_DISCORD_MIN_SIGNAL_SCORE)


class PerGameAlertSpreadTests(unittest.TestCase):
    def test_alert_selection_takes_one_pick_per_game(self):
        game_date = _tomorrow()
        recommendations = [
            _recommendation(player_id=1, game_id="G1", score=90.0, rank=1, game_date=game_date),
            _recommendation(player_id=2, game_id="G1", score=89.0, rank=2, game_date=game_date),
            _recommendation(player_id=3, game_id="G2", score=88.0, rank=3, book="fd", game_date=game_date),
        ]

        selected = es._filter_official_alert_recommendations(recommendations)

        self.assertEqual(
            [recommendation["game_id"] for recommendation in selected],
            ["G1", "G2"],
        )

    def test_preview_spreads_across_games_instead_of_stacking_one(self):
        game_date = _tomorrow()
        recommendations = [
            _recommendation(player_id=index, game_id="G1", score=70.0 - index, rank=index, game_date=game_date)
            for index in range(1, 5)
        ]
        recommendations.append(
            _recommendation(player_id=9, game_id="G2", score=60.0, rank=9, game_date=game_date)
        )

        selected = es._filter_preview_recommendations(recommendations)

        self.assertEqual(
            [recommendation["game_id"] for recommendation in selected],
            ["G1", "G2"],
        )

    def test_started_games_are_still_excluded(self):
        game_date = _tomorrow()
        started = _recommendation(player_id=1, game_id="G1", score=95.0, rank=1, game_date=game_date)
        started["game_started"] = True

        self.assertEqual(es._filter_official_alert_recommendations([started]), [])


class TrackerFloorTests(unittest.TestCase):
    def test_tracker_floor_never_exceeds_the_alert_bar(self):
        # A stricter tracker floor alerts picks that are then never logged,
        # so they are never graded.
        self.assertLessEqual(
            es.EDGE_DISCORD_TRACKER_MIN_SIGNAL_SCORE,
            es.EDGE_DISCORD_MIN_SIGNAL_SCORE,
        )


class PreviewRunningListTests(unittest.TestCase):
    def test_tipped_picks_stay_in_the_running_log(self):
        game_date = _tomorrow()
        early = _recommendation(player_id=1, game_id="G1", score=70.0, rank=1, game_date=game_date)
        state = {}

        first = es._preview_running_recommendations(
            state,
            slate_date=game_date,
            preview_recommendations=[early],
            generated_at="2026-10-25T12:00:00-04:00",
        )
        state["discord_preview_state_by_date"] = {
            game_date: {"sent_snapshot": es._tracker_state_snapshot_for_recommendations(first)}
        }

        # Later in the night G1 has tipped and only G2 is still pregame.
        late = _recommendation(player_id=2, game_id="G2", score=65.0, rank=1, game_date=game_date)
        second = es._preview_running_recommendations(
            state,
            slate_date=game_date,
            preview_recommendations=[late],
            generated_at="2026-10-25T22:00:00-04:00",
        )

        self.assertEqual(
            [recommendation["game_id"] for recommendation in second],
            ["G1", "G2"],
        )

    def test_logged_picks_keep_the_line_they_were_published_at(self):
        game_date = _tomorrow()
        logged = _recommendation(player_id=1, game_id="G1", score=70.0, rank=1, line=27.5, game_date=game_date)
        state = {
            "discord_preview_state_by_date": {
                game_date: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([
                        {**logged, "first_logged_at": "2026-10-25T12:00:00-04:00"}
                    ])
                }
            }
        }

        moved = {**logged, "line": 29.5}
        running = es._preview_running_recommendations(
            state,
            slate_date=game_date,
            preview_recommendations=[moved],
            generated_at="2026-10-25T18:00:00-04:00",
        )

        self.assertEqual(running[0]["line"], 27.5)
        self.assertEqual(running[0]["first_logged_at"], "2026-10-25T12:00:00-04:00")


class ResultsRecapGraceTests(unittest.TestCase):
    def test_grace_has_not_elapsed_the_morning_after(self):
        morning_after = datetime(2026, 10, 25, 7, 0, tzinfo=ET)
        self.assertFalse(es._results_recap_grace_elapsed("2026-10-24", now_et=morning_after))

    def test_grace_elapses_later_the_same_day(self):
        evening_after = datetime(2026, 10, 25, 19, 0, tzinfo=ET)
        self.assertTrue(es._results_recap_grace_elapsed("2026-10-24", now_et=evening_after))

    def test_unparseable_slate_date_never_forces_a_recap(self):
        self.assertFalse(es._results_recap_grace_elapsed("not-a-date"))


class PartialGradingTests(unittest.TestCase):
    def _recap_payload(self):
        graded_pick = _recommendation(
            player_id=100,
            game_id="G1",
            score=80.0,
            rank=1,
            line=20.5,
            game_date="2026-10-24",
        )
        missing_pick = _recommendation(
            player_id=200,
            game_id="G2",
            score=79.0,
            rank=2,
            line=15.5,
            game_date="2026-10-24",
        )
        return {
            "game_date": "2026-10-24",
            "recommendations": [graded_pick, missing_pick],
            "tracked_recommendations": {
                graded_pick["recommendation_key"]: graded_pick,
                missing_pick["recommendation_key"]: missing_pick,
            },
        }

    def _master_feed(self):
        return [
            {
                "id": 100,
                "name": "Player 100",
                "team": "DET",
                "game_log": [
                    {"GAME_DATE": "2026-10-24", "MIN": 30.0, "PTS": 25.0},
                ],
            },
        ]

    def test_one_missing_box_score_blocks_the_recap_inside_the_grace_window(self):
        with patch.object(es, "_find_boxscore_player_game_log", return_value=None):
            graded = es._grade_results_recap(self._recap_payload(), self._master_feed())

        self.assertFalse(graded["ready"])
        self.assertEqual(graded["summary"]["graded_count"], 1)
        self.assertEqual(graded["summary"]["unresolved_count"], 1)

    def test_recap_goes_out_with_gaps_once_the_grace_window_passes(self):
        with patch.object(es, "_find_boxscore_player_game_log", return_value=None):
            graded = es._grade_results_recap(
                self._recap_payload(),
                self._master_feed(),
                allow_partial=True,
            )

        self.assertTrue(graded["ready"])
        self.assertEqual(graded["summary"]["cashed"], 1)
        self.assertEqual(graded["summary"]["unresolved_count"], 1)


class AlertVolumeTests(unittest.TestCase):
    """One slate should not produce a message per line wobble."""

    def _state_with_opening_sent(self, posted):
        game_date = _tomorrow()
        return {
            "discord_alert_state_by_date": {
                game_date: {
                    "opening_sent_at": "2026-10-25T15:00:00-04:00",
                    "last_sent_at": "2026-10-25T15:00:00-04:00",
                    "last_sent_snapshot": es._state_snapshot_for_recommendations(posted),
                }
            }
        }

    def test_a_line_move_on_a_posted_pick_does_not_alert(self):
        posted = [_recommendation(player_id=1, game_id="G1", score=80.0, rank=1, line=20.5, game_date=_tomorrow())]
        state = self._state_with_opening_sent(posted)
        moved = [{**posted[0], "line": 23.5}]

        delta = es._compute_notification_delta(moved, state, "intraday")

        self.assertFalse(delta["should_send"])
        self.assertTrue(delta["changes"], "the move is still recorded for the board")

    def test_a_new_pick_still_alerts(self):
        posted = [_recommendation(player_id=1, game_id="G1", score=80.0, rank=1, game_date=_tomorrow())]
        state = self._state_with_opening_sent(posted)
        board = posted + [
            _recommendation(player_id=2, game_id="G2", score=79.0, rank=2, book="fd", game_date=_tomorrow())
        ]

        delta = es._compute_notification_delta(board, state, "intraday")

        self.assertTrue(delta["should_send"])
        self.assertEqual(
            [recommendation["player_id"] for recommendation in delta["send_recommendations"]],
            [2],
            "only the new pick is sent, not the whole board again",
        )

    def test_update_messages_drop_the_repeated_header(self):
        board = [_recommendation(player_id=1, game_id="G1", score=80.0, rank=1, game_date=_tomorrow())]
        base_payload = {"generated_at": "2026-10-25T17:11:00-04:00", "refresh_label": "intraday", "summary": {}}

        update = es._build_discord_alert_payload(
            base_payload,
            {"send_recommendations": board, "alert_kind": "update", "changes": [], "removed": []},
            channel_variant="main",
        )
        opening = es._build_discord_alert_payload(
            base_payload,
            {"send_recommendations": board, "alert_kind": "opening", "changes": [], "removed": []},
            channel_variant="main",
        )

        self.assertEqual([embed["title"] for embed in update["embeds"]], ["DraftKings"])
        self.assertIn("Today's Best Props", opening["embeds"][0]["title"])
        self.assertTrue(update["embeds"][-1].get("timestamp"), "a compact update still shows its age")


class MessageContentTests(unittest.TestCase):
    """Every payload needs plain text, not just embeds.

    An embed-only message is blank in the channel list, in a push
    notification, and for any client not rendering embeds.
    """

    def _board(self):
        game_date = _tomorrow()
        return [
            _recommendation(player_id=1, game_id="G1", score=90.0, rank=1, game_date=game_date),
            _recommendation(player_id=2, game_id="G2", score=80.0, rank=2, book="fd", game_date=game_date),
        ]

    def test_alert_payload_carries_text(self):
        board = self._board()
        delta = {
            "send_recommendations": board,
            "alert_kind": "opening",
            "changes": [],
            "removed": [],
        }
        payload = es._build_discord_alert_payload(
            {"generated_at": "2026-10-25T17:11:00-04:00", "refresh_label": "intraday", "summary": {}},
            delta,
            channel_variant="main",
        )

        # A label, not a restatement of the card: the picks live in the embed.
        self.assertEqual(payload["content"], "**Today's Best Props**")
        self.assertLessEqual(len(payload["content"]), 2000)

    def test_tracker_payload_carries_text(self):
        board = self._board()
        payload = es._build_discord_tracker_payload(
            {"generated_at": "2026-10-25T17:11:00-04:00", "refresh_label": "intraday"},
            {"new_recommendations": board},
            running_recommendations=board,
        )

        self.assertTrue(payload["content"].strip())
        self.assertLessEqual(len(payload["content"]), 2000)

    def test_preview_payload_carries_text(self):
        payload = es._build_discord_preview_payload(
            {"generated_at": "2026-10-25T17:11:00-04:00"},
            self._board(),
        )

        self.assertTrue(payload["content"].strip())
        self.assertLessEqual(len(payload["content"]), 2000)

    def test_content_line_summarizes_and_truncates(self):
        many = [
            _recommendation(player_id=index, game_id=f"G{index}", score=80.0, rank=index, game_date=_tomorrow())
            for index in range(1, 11)
        ]

        content = es._discord_message_content("10 picks", many)

        self.assertIn("+7 more", content)
        self.assertLessEqual(len(content), 1900)

    def test_content_line_without_recommendations_is_still_text(self):
        self.assertEqual(es._discord_message_content("Results 2026-10-24 • 15-17"), "**Results 2026-10-24 • 15-17**")


class RecapPaginationTests(unittest.TestCase):
    def test_a_full_slate_of_results_is_not_truncated(self):
        lines = [f"line {index} " + "x" * 120 for index in range(40)]

        pages = es._chunk_discord_descriptions(lines)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), es.RESULTS_RECAP_CHARS_PER_EMBED)
        rejoined = "\n".join(pages)
        for line in lines:
            self.assertIn(line, rejoined)

    def test_message_batches_stay_under_the_discord_character_cap(self):
        per_message = es.RESULTS_RECAP_EMBEDS_PER_MESSAGE * es.RESULTS_RECAP_CHARS_PER_EMBED
        self.assertLessEqual(per_message, 6000)


if __name__ == "__main__":
    unittest.main()
