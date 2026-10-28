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
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=ET)
D0 = "2026-10-21"
D1 = "2026-10-22"
D2 = "2026-10-23"


def _game(game_id: str, game_date: str, home: str, away: str, hour: int = 19) -> dict:
    tip = datetime.strptime(f"{game_date} {hour:02d}:00", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_team_tricode": home,
        "away_team_tricode": away,
        "game_time_utc": tip.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "game_time_et": tip.strftime("%I:%M %p ET"),
        "closing_scrape_deadline": tip.isoformat(),
        "game_status": 1,
        "is_live": False,
        "is_final": False,
    }


def _recommendation(
    *,
    player_id: int,
    game_id: str,
    game_date: str,
    score: float = 80.0,
    rank: int = 1,
    book: str = "dk",
    stat_type: str = "PTS",
    side: str = "over",
    line: float = 20.5,
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


class SlateWindowTests(unittest.TestCase):
    """The window the scorer is allowed to look at."""

    def _dates(self, games, lookahead):
        with patch.object(es, "EDGE_SLATE_LOOKAHEAD_DAYS", lookahead), \
             patch.object(es, "get_et_now", return_value=NOW):
            return sorted(es._build_schedule_context({"games": games})["active_dates"])

    def test_lookahead_zero_keeps_a_single_date(self):
        games = [_game("1", D0, "DET", "PHI"), _game("2", D1, "CLE", "BOS")]
        self.assertEqual(self._dates(games, 0), [D0])

    def test_lookahead_one_admits_tomorrow(self):
        games = [_game("1", D0, "DET", "PHI"), _game("2", D1, "CLE", "BOS")]
        self.assertEqual(self._dates(games, 1), [D0, D1])

    def test_lookahead_stops_at_the_horizon(self):
        games = [_game("1", D0, "DET", "PHI"), _game("2", D1, "CLE", "BOS"), _game("3", D2, "NYK", "MIA")]
        self.assertEqual(self._dates(games, 1), [D0, D1])
        self.assertEqual(self._dates(games, 2), [D0, D1, D2])

    def test_window_is_anchored_on_the_next_game_not_on_today(self):
        """An off-day must still score the next slate.

        Anchoring the window on today would return nothing across a gap in the
        calendar — the All-Star break, or any dark day — and the guarantee worth
        keeping is that the next slate is always scored, whenever it is.
        """
        games = [_game("1", D2, "NYK", "MIA")]
        self.assertEqual(self._dates(games, 0), [D2])
        self.assertEqual(self._dates(games, 1), [D2])


class BackToBackTests(unittest.TestCase):
    """A team playing twice inside the window."""

    def _context(self, lookahead=1):
        games = [
            _game("g0", D0, "DET", "PHI", hour=19),
            _game("g1", D1, "CLE", "DET", hour=20),
        ]
        with patch.object(es, "EDGE_SLATE_LOOKAHEAD_DAYS", lookahead), \
             patch.object(es, "get_et_now", return_value=NOW):
            return es._build_schedule_context({"games": games})

    def test_both_games_are_kept_for_the_team(self):
        context = self._context()
        self.assertEqual(len(context["games_by_team"]["DET"]), 2)
        self.assertEqual(
            [es._normalize_game_date(g["game_date"]) for g in context["games_by_team"]["DET"]],
            [D0, D1],
        )

    def test_opponent_is_resolved_per_date(self):
        context = self._context()
        self.assertEqual(context["opponent_by_team_date"][("DET", D0)], "PHI")
        self.assertEqual(context["opponent_by_team_date"][("DET", D1)], "CLE")

    def test_single_valued_lookups_still_mean_the_nearest_game(self):
        context = self._context()
        self.assertEqual(es._normalize_game_date(context["game_by_team"]["DET"]["game_date"]), D0)
        self.assertEqual(context["opponent_by_team"]["DET"], "PHI")


class PerDateRankTests(unittest.TestCase):
    def test_rank_restarts_for_each_slate_date(self):
        """`EDGE_DISCORD_MAX_RANK` and the repeat suppressor's rank signal both
        read `rank`, and both mean "within this slate"."""
        board = [
            _recommendation(player_id=1, game_id="g0", game_date=D0, score=90.0),
            _recommendation(player_id=2, game_id="g1", game_date=D0, score=85.0),
            _recommendation(player_id=3, game_id="g2", game_date=D1, score=80.0),
            _recommendation(player_id=4, game_id="g3", game_date=D1, score=75.0),
        ]
        es._assign_board_ranks(board)
        self.assertEqual([r["rank"] for r in board], [1, 2, 1, 2])
        self.assertEqual([r["board_position"] for r in board], [1, 2, 3, 4])

    def test_a_later_date_still_gets_board_slots(self):
        """A single global cut is won outright by the nearest date, which has
        more games, more priced players and more line history behind it."""
        candidates = [
            _recommendation(player_id=i, game_id=f"a{i}", game_date=D0, score=90.0 - i)
            for i in range(6)
        ] + [
            _recommendation(player_id=100 + i, game_id=f"b{i}", game_date=D1, score=70.0 - i)
            for i in range(3)
        ]
        picked = es._diversify_candidates_by_date(candidates, 4)
        dates = [es._normalize_game_date(r["game_date"]) for r in picked]
        self.assertEqual(dates.count(D0), 4)
        self.assertEqual(dates.count(D1), 3)
        self.assertEqual(dates, sorted(dates), "nearest date must come first")


class StateRetentionTests(unittest.TestCase):
    def test_keeps_recent_and_future_drops_stale(self):
        bucket = {
            "2026-10-01": {"old": True},
            "2026-10-19": {"yesterday_ish": True},
            "2026-10-21": {"today": True},
            "2026-10-22": {"tomorrow": True},
        }
        pruned = es._prune_state_dates(bucket, retention_days=7, now_et=NOW)
        self.assertNotIn("2026-10-01", pruned)
        self.assertIn("2026-10-19", pruned)
        self.assertIn("2026-10-21", pruned)
        self.assertIn("2026-10-22", pruned)

    def test_future_dates_never_evict_yesterday(self):
        """The bug a count-based `sorted(...)[-N:]` tail would reintroduce.

        Once a future slate is in the file the lexicographic tail keeps tomorrow
        and drops yesterday, which is the bucket the next day's grading reads
        back through `_filter_recap_payload_to_published_picks`.
        """
        yesterday = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
        bucket = {yesterday: {"graded_from_here": True}}
        bucket.update({
            (NOW + timedelta(days=offset)).strftime("%Y-%m-%d"): {"future": offset}
            for offset in range(1, 13)
        })
        pruned = es._prune_state_dates(bucket, retention_days=7, now_et=NOW)
        self.assertIn(yesterday, pruned, "yesterday is still needed for grading")

    def test_housekeeping_does_not_depend_on_a_message_being_sent(self):
        """Pruning used to sit inside the `if ..._sent:` blocks.

        That coupled housekeeping to an unrelated condition: a quiet slate, or a
        run with the webhooks unset, would leave the file untrimmed for as long
        as nothing happened to publish.
        """
        stale = "2026-04-15"
        state = {
            "discord_tracker_state_by_date": {stale: {"sent_snapshot": {}}},
            "discord_alert_state_by_date": {stale: {"last_sent_snapshot": {}}},
            "discord_preview_state_by_date": {stale: {"sent_snapshot": {}}},
            "injury_context_by_date": {stale: {}},
            "injury_changes_seen_by_date": {stale: {}},
            # The recap idempotency guard is deliberately left alone.
            "sent_result_recaps": {stale: {"record": "1-0"}},
        }
        with patch.object(es, "get_et_now", return_value=NOW):
            changed = es._prune_notification_state(state)
        self.assertTrue(changed, "a trim must force the state write")
        for key in es.PRUNABLE_STATE_DATE_BUCKETS:
            self.assertEqual(state[key], {}, f"{key} should have been trimmed")
        self.assertIn(stale, state["sent_result_recaps"], "recap guard must survive")

    def test_housekeeping_reports_no_change_when_nothing_is_stale(self):
        state = {"discord_tracker_state_by_date": {D0: {"sent_snapshot": {}}}}
        with patch.object(es, "get_et_now", return_value=NOW):
            self.assertFalse(es._prune_notification_state(state))
        self.assertIn(D0, state["discord_tracker_state_by_date"])

    def test_unparseable_key_is_kept_rather_than_lost(self):
        pruned = es._prune_state_dates({"not-a-date": {"keep": True}}, retention_days=7, now_et=NOW)
        self.assertIn("not-a-date", pruned)


class PerDatePublishTests(unittest.TestCase):
    """Each slate date publishes and records its own messages."""

    def _publish(self, board, *, state=None, lookahead=1):
        state = state if state is not None else {}
        sent = []

        def _fake_send(payload, delta, *, webhook_url, channel_variant, **kwargs):
            sent.append({
                "channel": channel_variant,
                "game_dates": list(payload.get("game_dates") or []),
                "existing_message_id": kwargs.get("existing_message_id"),
                "running": [
                    es._normalize_game_date(r.get("game_date"))
                    for r in (kwargs.get("running_recommendations") or [])
                ],
            })
            return {"id": f"msg{len(sent)}", "channel_id": "c1"}

        payload = {
            "generated_at": NOW.isoformat(),
            "refresh_label": "intraday",
            "game_dates": sorted({es._normalize_game_date(r["game_date"]) for r in board}),
            "notification": {},
        }
        dates = sorted({es._normalize_game_date(r["game_date"]) for r in board})
        results = []
        with patch.object(es, "EDGE_SLATE_LOOKAHEAD_DAYS", lookahead), \
             patch.object(es, "EDGE_SCORE_DISCORD_WEBHOOK_URL", "https://discord.test/main"), \
             patch.object(es, "EDGE_SCORE_DISCORD_TRACKER_WEBHOOK_URL", "https://discord.test/tracker"), \
             patch.object(es, "EDGE_SCORE_DISCORD_PREVIEW_WEBHOOK_URL", "https://discord.test/preview"), \
             patch.object(es, "get_et_now", return_value=NOW), \
             patch.object(es, "_send_discord_webhook", side_effect=_fake_send):
            for slate_date in dates:
                delta = es._compute_notification_delta(
                    [r for r in board if es._normalize_game_date(r["game_date"]) == slate_date],
                    state,
                    "intraday",
                )
                results.append(es._publish_slate_date(
                    payload,
                    state,
                    slate_date=slate_date,
                    notification_delta=delta,
                    display_recommendations=board,
                    refresh_label="intraday",
                ))
        return state, sent, results

    def test_each_date_gets_its_own_tracker_message(self):
        board = [
            _recommendation(player_id=1, game_id="g0", game_date=D0, score=90.0),
            _recommendation(player_id=2, game_id="g1", game_date=D1, score=88.0),
        ]
        state, sent, _ = self._publish(board)
        refs = state.get("discord_messages_by_date", {})
        self.assertIn(D0, refs)
        self.assertIn(D1, refs)
        id0 = es._latest_discord_message_ref(state, slate_date=D0, channel="tracker")["message_id"]
        id1 = es._latest_discord_message_ref(state, slate_date=D1, channel="tracker")["message_id"]
        self.assertNotEqual(id0, id1, "the two dates must not share one tracker message")

    def test_each_message_names_only_its_own_slate(self):
        board = [
            _recommendation(player_id=1, game_id="g0", game_date=D0, score=90.0),
            _recommendation(player_id=2, game_id="g1", game_date=D1, score=88.0),
        ]
        _, sent, _ = self._publish(board)
        for message in sent:
            self.assertEqual(len(message["game_dates"]), 1, message)

    def test_a_tracker_ledger_never_mixes_dates(self):
        board = [
            _recommendation(player_id=1, game_id="g0", game_date=D0, score=90.0),
            _recommendation(player_id=2, game_id="g1", game_date=D1, score=88.0),
        ]
        _, sent, _ = self._publish(board)
        for message in sent:
            if message["channel"] == "tracker" and message["running"]:
                self.assertEqual(set(message["running"]), {message["game_dates"][0]}, message)

    def test_one_player_may_be_picked_on_both_nights(self):
        """The per-player cap is a per-slate budget, so the same player on two
        different nights is two separate bets, not a stacked one."""
        board = [
            _recommendation(player_id=7, game_id="g0", game_date=D0, score=90.0),
            _recommendation(player_id=7, game_id="g1", game_date=D1, score=88.0),
        ]
        state, _, results = self._publish(board)
        published = {
            slate_date: es._published_player_props_for_slate(state, slate_date)
            for slate_date in (D0, D1)
        }
        self.assertTrue(published[D0], "player should be recorded for the first night")
        self.assertTrue(published[D1], "player should be recorded for the second night")

    def test_an_alert_on_one_date_does_not_mute_the_other_dates_preview(self):
        """The preview trigger used to be a global `not official_recommendations`,
        so any alert anywhere suppressed every preview."""
        below_bar = es.EDGE_DISCORD_MIN_SIGNAL_SCORE - 20.0
        board = [
            _recommendation(player_id=1, game_id="g0", game_date=D0, score=95.0),
            _recommendation(player_id=2, game_id="g1", game_date=D1, score=below_bar),
        ]
        _, sent, results = self._publish(board)
        previews = [m for m in sent if m["channel"] == "preview"]
        self.assertTrue(previews, "the below-bar date should still preview")
        self.assertEqual({m["game_dates"][0] for m in previews}, {D1})


class ClosingLineValueTests(unittest.TestCase):
    """What the market did after we published."""

    def _pick(self, *, side, line, current):
        recommendation = _recommendation(
            player_id=1, game_id="g0", game_date=D0, side=side, line=line
        )
        recommendation["current_line"] = current
        return recommendation

    def test_over_gains_when_the_line_rises(self):
        value = es._closing_line_value(self._pick(side="over", line=25.5, current=27.5))
        self.assertEqual(value["clv"], 2.0)
        self.assertTrue(value["beat_close"])

    def test_over_loses_when_the_line_falls(self):
        value = es._closing_line_value(self._pick(side="over", line=25.5, current=24.0))
        self.assertEqual(value["clv"], -1.5)
        self.assertFalse(value["beat_close"])

    def test_under_gains_when_the_line_falls(self):
        """The sign flips with the side: an Under wants the number to come down."""
        value = es._closing_line_value(self._pick(side="under", line=25.5, current=23.5))
        self.assertEqual(value["clv"], 2.0)
        self.assertTrue(value["beat_close"])

    def test_under_loses_when_the_line_rises(self):
        value = es._closing_line_value(self._pick(side="under", line=25.5, current=27.0))
        self.assertEqual(value["clv"], -1.5)
        self.assertFalse(value["beat_close"])

    def test_a_pulled_prop_reports_itself_instead_of_a_number(self):
        recommendation = self._pick(side="over", line=25.5, current=None)
        self.assertFalse(es._closing_line_value(recommendation)["available"])
        self.assertEqual(es._closing_line_value_text(recommendation), "line pulled")

    def test_an_unmoved_line_says_so(self):
        text = es._closing_line_value_text(self._pick(side="over", line=25.5, current=25.5))
        self.assertIn("held", text)

    def test_a_queued_recap_entry_uses_its_frozen_closing_line(self):
        recommendation = _recommendation(player_id=1, game_id="g0", game_date=D0, side="over", line=25.5)
        recommendation["closing_line"] = 27.5
        self.assertEqual(es._closing_line_value(recommendation)["clv"], 2.0)

    def test_beat_the_close_summary(self):
        picks = [
            self._pick(side="over", line=20.5, current=22.5),
            self._pick(side="over", line=20.5, current=21.5),
            self._pick(side="over", line=20.5, current=19.5),
        ]
        summary = es._closing_line_value_summary(picks)
        self.assertEqual(summary["measured"], 3)
        self.assertEqual(summary["beat_close"], 2)
        self.assertAlmostEqual(summary["average_clv"], 0.67, places=2)


def _injury_report(*, timestamp, game_date, game_id, players):
    """A report payload shaped like the real artifact.

    One PDF spans more than one date: the live 2026-10-21 11:30PM report carries
    all eleven games on the 21st and both games on the 22nd, each entry tagged
    with its own game_date.
    """
    return {
        "report_timestamp_et": timestamp,
        "games": [{
            "game_id": game_id,
            "game_date": game_date,
            "teams": {
                "home": {
                    "tricode": "DET",
                    "players": [
                        {"player_name": name, "current_status": status, "reason": reason}
                        for name, status, reason in players
                    ],
                },
            },
        }],
    }


class InjuryContextTests(unittest.TestCase):
    def test_a_single_report_spans_several_dates(self):
        report = {
            "report_timestamp_et": "2026-10-21T23:30:00-04:00",
            "games": [
                {"game_id": "a", "game_date": D0,
                 "teams": {"home": {"tricode": "DET", "players": [
                     {"player_name": "Cade Cunningham", "current_status": "Questionable", "reason": "Ankle"}]}}},
                {"game_id": "b", "game_date": D1,
                 "teams": {"home": {"tricode": "CLE", "players": [
                     {"player_name": "Jarrett Allen", "current_status": "Questionable", "reason": "Patellar"}]}}},
            ],
        }
        context = es._build_injury_context(report)
        self.assertEqual(sorted(context), [D0, D1])
        self.assertEqual(context[D1]["b"]["statuses"]["jarrett allen"], "questionable")

    def test_a_player_leaving_the_report_reads_as_cleared(self):
        previous = {"statuses": {"a b": "questionable"}, "reasons": {}, "teams": {}}
        current = {"statuses": {}, "reasons": {}, "teams": {}}
        transitions = es._injury_status_transitions(previous, current)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["to_status"], "available")
        self.assertEqual(transitions[0]["direction"], "better")


class InjuryChangeDirectionTests(unittest.TestCase):
    """A roster move has to be read against the pick's side."""

    def _changes(self, *, side, teammate_from, teammate_to):
        pick = _recommendation(player_id=1, game_id="g0", game_date=D0, side=side)
        pick["player_name"] = "Cade Cunningham"
        pick["first_logged_at"] = NOW.isoformat()
        pick["current_edge_score"] = pick["display_edge_score"]
        previous = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T17:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Jalen Duren", teammate_from, "Knee")],
        ))[D0]
        current = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T20:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Jalen Duren", teammate_to, "Knee")],
        ))[D0]
        return es._injury_changes_for_picks([pick], previous_context=previous, current_context=current)

    def test_a_teammate_ruled_out_strengthens_an_over(self):
        """The usage vacuum lifts the projection."""
        changes = self._changes(side="over", teammate_from="Questionable", teammate_to="Out")
        self.assertEqual([c["effect"] for c in changes], ["stronger"])

    def test_the_same_news_weakens_an_under(self):
        changes = self._changes(side="under", teammate_from="Questionable", teammate_to="Out")
        self.assertEqual([c["effect"] for c in changes], ["weaker"])

    def test_a_teammate_returning_reverses_both(self):
        self.assertEqual(
            [c["effect"] for c in self._changes(side="over", teammate_from="Out", teammate_to="Available")],
            ["weaker"],
        )
        self.assertEqual(
            [c["effect"] for c in self._changes(side="under", teammate_from="Out", teammate_to="Available")],
            ["stronger"],
        )

    def test_the_picks_own_player_being_ruled_out_kills_it(self):
        pick = _recommendation(player_id=1, game_id="g0", game_date=D0, side="over")
        pick["player_name"] = "Cade Cunningham"
        pick["current_edge_score"] = pick["display_edge_score"]
        previous = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T17:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Cade Cunningham", "Questionable", "Ankle")],
        ))[D0]
        current = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T20:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Cade Cunningham", "Out", "Left Ankle Sprain")],
        ))[D0]
        changes = es._injury_changes_for_picks([pick], previous_context=previous, current_context=current)
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0]["subject"])
        self.assertEqual(changes[0]["effect"], "dead")
        self.assertTrue(es._injury_change_is_alertable(changes[0]))

    def test_an_unrepublished_report_is_not_diffed(self):
        """`report_timestamp_et` makes the no-op case free."""
        pick = _recommendation(player_id=1, game_id="g0", game_date=D0, side="over")
        pick["player_name"] = "Cade Cunningham"
        same = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T17:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Jalen Duren", "Out", "Knee")],
        ))[D0]
        other = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T17:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Jalen Duren", "Available", "")],
        ))[D0]
        self.assertEqual(
            es._injury_changes_for_picks([pick], previous_context=same, current_context=other),
            [],
        )


class InjuryAlertGateTests(unittest.TestCase):
    """What earns a channel ping rather than a quiet tracker edit."""

    def _change(self, **overrides):
        change = {
            "recommendation_key": "1|2026-10-21|PTS|over",
            "changed_player": "jalen duren",
            "from_status": "questionable",
            "to_status": "out",
            "report_timestamp_et": "2026-10-21T20:00:00-04:00",
            "effect": "stronger",
            "subject": False,
            "direction": "worse",
            "score_delta": 1.0,
        }
        change.update(overrides)
        return change

    def test_a_small_score_move_stays_off_the_alert_channel(self):
        self.assertFalse(es._injury_change_is_alertable(self._change(score_delta=2.0)))

    def test_a_large_score_move_is_alertable_in_either_direction(self):
        self.assertTrue(es._injury_change_is_alertable(self._change(score_delta=12.0)))
        self.assertTrue(es._injury_change_is_alertable(self._change(score_delta=-12.0)))

    def test_a_dead_pick_always_alerts(self):
        self.assertTrue(es._injury_change_is_alertable(self._change(effect="dead", score_delta=0.0)))

    def test_the_same_change_is_only_reported_once(self):
        first = self._change()
        duplicate = self._change()
        self.assertEqual(es._injury_change_key(first), es._injury_change_key(duplicate))

    def test_a_later_report_is_a_distinct_change(self):
        self.assertNotEqual(
            es._injury_change_key(self._change()),
            es._injury_change_key(self._change(report_timestamp_et="2026-10-21T22:00:00-04:00")),
        )


class InjuryProcessingTests(unittest.TestCase):
    """The stateful pass: dedup, persistence and the send decision."""

    def _run(self, *, previous_statuses, current_statuses, side="over", score_delta=0.0, state=None):
        pick = _recommendation(player_id=1, game_id="g0", game_date=D0, side=side, score=80.0)
        pick["player_name"] = "Cade Cunningham"
        pick["first_logged_at"] = NOW.isoformat()
        pick["current_edge_score"] = 80.0 + score_delta
        state = state if state is not None else {}
        if previous_statuses is not None:
            state["injury_context_by_date"] = es._build_injury_context(_injury_report(
                timestamp="2026-10-21T17:00:00-04:00", game_date=D0, game_id="g0",
                players=previous_statuses,
            ))
        current = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T20:00:00-04:00", game_date=D0, game_id="g0",
            players=current_statuses,
        ))[D0]

        posted = []
        with patch.object(es, "EDGE_SCORE_DISCORD_WEBHOOK_URL", "https://discord.test/main"), \
             patch.object(es, "get_et_now", return_value=NOW), \
             patch.object(es, "_post_discord_webhook",
                          side_effect=lambda url, body: posted.append(body) or {"id": "m1"}):
            outcome = es._process_injury_changes(
                {"generated_at": NOW.isoformat()},
                state,
                slate_date=D0,
                published_recommendations=[pick],
                current_injury_context=current,
                refresh_label="intraday",
            )
        return state, outcome, posted

    def test_the_first_pass_records_context_without_alerting(self):
        """There is nothing to diff against on the first sighting."""
        state, outcome, posted = self._run(
            previous_statuses=None,
            current_statuses=[("Jalen Duren", "Out", "Knee")],
        )
        self.assertFalse(outcome["sent"])
        self.assertEqual(posted, [])
        self.assertIn(D0, state["injury_context_by_date"])

    def test_a_dead_pick_posts_once_and_not_again(self):
        state, outcome, posted = self._run(
            previous_statuses=[("Cade Cunningham", "Questionable", "Ankle")],
            current_statuses=[("Cade Cunningham", "Out", "Left Ankle Sprain")],
        )
        self.assertTrue(outcome["sent"])
        self.assertEqual(len(posted), 1)
        self.assertIn("Pick is dead", posted[0]["embeds"][0]["description"])

        # Same report again: already recorded, so nothing further is sent.
        _, repeat_outcome, repeat_posted = self._run(
            previous_statuses=[("Cade Cunningham", "Questionable", "Ankle")],
            current_statuses=[("Cade Cunningham", "Out", "Left Ankle Sprain")],
            state=state,
        )
        self.assertFalse(repeat_outcome["sent"])
        self.assertEqual(repeat_posted, [])

    def test_a_marginal_teammate_move_is_recorded_but_not_posted(self):
        state, outcome, posted = self._run(
            previous_statuses=[("Jalen Duren", "Questionable", "Knee")],
            current_statuses=[("Jalen Duren", "Out", "Knee")],
            score_delta=1.0,
        )
        self.assertFalse(outcome["sent"])
        self.assertEqual(posted, [])
        self.assertTrue(outcome["changes"], "the change is still recorded for the tracker")
        self.assertEqual(outcome["changes"][0]["effect"], "stronger")

    def test_a_big_confidence_drop_posts_and_names_the_bar(self):
        state, outcome, posted = self._run(
            previous_statuses=[("Cade Cunningham", "Available", "")],
            current_statuses=[("Cade Cunningham", "Questionable", "Left Ankle")],
            score_delta=-20.0,
        )
        self.assertTrue(outcome["sent"])
        description = posted[0]["embeds"][0]["description"]
        self.assertIn("Confidence drop", description)
        self.assertIn("below the alert bar", description)

    def test_context_is_recorded_even_when_the_send_fails(self):
        """Otherwise a failing webhook re-reports the same move forever."""
        pick = _recommendation(player_id=1, game_id="g0", game_date=D0, side="over")
        pick["player_name"] = "Cade Cunningham"
        pick["current_edge_score"] = 60.0
        state = {
            "injury_context_by_date": es._build_injury_context(_injury_report(
                timestamp="2026-10-21T17:00:00-04:00", game_date=D0, game_id="g0",
                players=[("Cade Cunningham", "Questionable", "Ankle")],
            ))
        }
        current = es._build_injury_context(_injury_report(
            timestamp="2026-10-21T20:00:00-04:00", game_date=D0, game_id="g0",
            players=[("Cade Cunningham", "Out", "Ankle")],
        ))[D0]
        with patch.object(es, "EDGE_SCORE_DISCORD_WEBHOOK_URL", "https://discord.test/main"), \
             patch.object(es, "get_et_now", return_value=NOW), \
             patch.object(es, "_post_discord_webhook", side_effect=RuntimeError("discord down")):
            outcome = es._process_injury_changes(
                {"generated_at": NOW.isoformat()},
                state,
                slate_date=D0,
                published_recommendations=[pick],
                current_injury_context=current,
                refresh_label="intraday",
            )
        self.assertFalse(outcome["sent"])
        self.assertTrue(outcome["state_changed"])
        self.assertEqual(
            state["injury_context_by_date"][D0]["g0"]["report_timestamp_et"],
            "2026-10-21T20:00:00-04:00",
        )


class EmbedBudgetTests(unittest.TestCase):
    """Discord caps a message at 6000 characters across its embeds."""

    DISCORD_MESSAGE_LIMIT = 6000
    DISCORD_EMBED_LIMIT = 4096

    def _tracker_payload(self, pick_count):
        ledger = []
        for index in range(pick_count):
            recommendation = _recommendation(
                player_id=index,
                game_id=f"g{index}",
                game_date=D0,
                book="dk" if index % 2 else "fd",
                score=90.0 - index * 0.1,
            )
            # A long why_summary is what actually inflates these descriptions.
            recommendation["why_summary"] = (
                "matchup edge against a bottom-five defense in this zone; "
                "usage vacuum from the injury report; line moved our way all day"
            )
            recommendation["first_logged_at"] = NOW.isoformat()
            recommendation["current_line"] = recommendation["line"] + 1.5
            ledger.append(recommendation)

        payload = {"generated_at": NOW.isoformat(), "game_dates": [D0], "notification": {}}
        with patch.object(es, "get_et_now", return_value=NOW):
            return es._build_discord_tracker_payload(
                payload,
                {"should_send": True},
                running_recommendations=ledger,
            )

    def _assert_within_discord_limits(self, message):
        embeds = message.get("embeds") or []
        self.assertLessEqual(len(embeds), 10, "Discord allows 10 embeds per message")
        total = 0
        for embed in embeds:
            description = str(embed.get("description") or "")
            self.assertLessEqual(len(description), self.DISCORD_EMBED_LIMIT)
            total += len(description) + len(str(embed.get("title") or ""))
            for field in embed.get("fields") or []:
                total += len(str(field.get("name") or "")) + len(str(field.get("value") or ""))
        self.assertLessEqual(
            total, self.DISCORD_MESSAGE_LIMIT,
            f"message embeds total {total} characters, over Discord's ceiling",
        )
        self.assertLessEqual(len(str(message.get("content") or "")), 2000)

    def test_a_small_ledger_is_unchanged(self):
        message = self._tracker_payload(4)
        self._assert_within_discord_limits(message)
        body = "\n".join(str(e.get("description") or "") for e in message["embeds"])
        self.assertNotIn("more", body.split("`+")[-1] if "`+" in body else "")

    def test_a_long_ledger_stays_within_the_message_ceiling(self):
        """Previously a header plus two 3800-char book embeds summed to 7600."""
        for pick_count in (20, 40, 80):
            with self.subTest(picks=pick_count):
                self._assert_within_discord_limits(self._tracker_payload(pick_count))

    def test_a_trimmed_ledger_says_how_many_it_dropped(self):
        message = self._tracker_payload(80)
        body = "\n".join(str(embed.get("description") or "") for embed in message["embeds"])
        self.assertRegex(body, r"\+\d+ more", "a trimmed board must mark the overflow")

    def test_entries_are_dropped_whole_not_mid_line(self):
        description, dropped = es._fit_embed_description(
            ["x" * 100, "y" * 100, "z" * 100],
            budget=260,
            overflow_label="spot(s)",
        )
        self.assertTrue(dropped)
        for entry in description.split("\n\n"):
            if entry.startswith("`+"):
                continue
            self.assertIn(len(entry), (100,), f"entry was cut mid-line: {len(entry)}")


class GradeAtPublishedLineTests(unittest.TestCase):
    def test_the_published_line_settles_the_result_not_the_close(self):
        """Locked Over 25.5, market closes 27.5, player scores 26.

        That is a win: 25.5 is the number that was bettable when the pick was
        published. Grading it against the close would record a loss for a bet
        nobody could have placed, and would punish the model precisely for
        being early.
        """
        self.assertEqual(es._grade_pick_result(26.0, 25.5, "over"), "cashed")
        self.assertEqual(es._grade_pick_result(26.0, 27.5, "over"), "lost")

        recommendation = _recommendation(
            player_id=1, game_id="g0", game_date=D0, side="over", line=25.5
        )
        recommendation["closing_line"] = 27.5
        value = es._closing_line_value(recommendation)
        self.assertEqual(value["clv"], 2.0)
        self.assertTrue(value["beat_close"])

    def test_the_ledger_keeps_the_published_line_when_the_market_moves(self):
        """The stored snapshot deliberately wins over the live candidate, which
        is what keeps `line` equal to what the channel actually stated."""
        logged = _recommendation(player_id=1, game_id="g0", game_date=D0, side="over", line=25.5)
        state = {
            "discord_tracker_state_by_date": {
                D0: {
                    "sent_snapshot": es._tracker_state_snapshot_for_recommendations([logged]),
                    "first_sent_at": NOW.isoformat(),
                }
            }
        }
        moved = _recommendation(player_id=1, game_id="g0", game_date=D0, side="over", line=27.5)
        with patch.object(es, "get_et_now", return_value=NOW):
            ledger = es._tracker_running_recommendations(
                state,
                slate_date=D0,
                tracker_delta={"tracker_candidates": [moved], "new_recommendations": []},
            )
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["line"], 25.5, "published price must survive the move")
        self.assertEqual(ledger[0]["current_line"], 27.5, "market price must be tracked alongside")
        self.assertEqual(es._closing_line_value(ledger[0])["clv"], 2.0)


if __name__ == "__main__":
    unittest.main()
