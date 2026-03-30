import os
import json
import hashlib
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

def get_et_now():
    return datetime.now(ZoneInfo("America/New_York"))


def get_intraday_interval_seconds():
    raw_value = os.getenv("INTRADAY_INTERVAL_SECONDS", "900")
    try:
        parsed = int(raw_value)
    except ValueError:
        parsed = 900
    return max(300, parsed)

class SnapshotManager:
    def __init__(self, data_dir=None, logs_dir=None):
        if data_dir is None:
            # Assume NBA_Dashboard/backend/data
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")
        
        if logs_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            logs_dir = os.path.join(base_dir, "logs")

        self.current_dir = os.path.join(data_dir, "current")
        self.archive_dir = os.path.join(data_dir, "archive")
        
        os.makedirs(self.current_dir, exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)
        os.makedirs(logs_dir, exist_ok=True)

        self.line_movements_path = os.path.join(self.current_dir, "line_movements_today.json")
        self.historical_odds_path = os.path.join(self.archive_dir, "historical_odds.json")
        self.skips_log_path = os.path.join(logs_dir, "closing_line_skips.log")
        self.schedule_path = os.path.join(self.current_dir, "today_schedule.json")

        # Setup logging for skips
        self.logger = logging.getLogger("ClosingLineSkips")
        self.logger.setLevel(logging.INFO)
        # Avoid duplicate handlers if instantiated multiple times
        if not self.logger.handlers:
            fh = logging.FileHandler(self.skips_log_path)
            fh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
            self.logger.addHandler(fh)

    def _read_json(self, filepath, default=None):
        if not os.path.exists(filepath):
            return default if default is not None else {}
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default if default is not None else {}

    def _write_json(self, filepath, data):
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def _load_schedule(self):
        return self._read_json(self.schedule_path, default={})

    def _get_active_schedule_context(self):
        schedule = self._load_schedule()
        games = schedule.get("games", []) if isinstance(schedule.get("games", []), list) else []
        now = get_et_now()
        active_games = []
        for game in games:
            if game.get("is_final", False):
                continue

            deadline_str = game.get("closing_scrape_deadline")
            if not deadline_str:
                active_games.append(game)
                continue

            try:
                deadline_dt = datetime.fromisoformat(deadline_str)
                if deadline_dt >= (now - timedelta(minutes=15)):
                    active_games.append(game)
            except ValueError:
                active_games.append(game)
        active_teams = set()
        active_dates = set()

        for game in active_games:
            home = game.get("home_team_tricode")
            away = game.get("away_team_tricode")
            game_date = game.get("game_date")
            if home:
                active_teams.add(home)
            if away:
                active_teams.add(away)
            if game_date:
                active_dates.add(game_date)

        return {
            "schedule": schedule,
            "games": games,
            "active_games": active_games,
            "active_teams": active_teams,
            "active_dates": active_dates,
        }

    def _filter_players_to_active_schedule(self, players_data, active_teams):
        if not isinstance(players_data, dict):
            return {}
        if not active_teams:
            return players_data

        schedule_ctx = self._get_active_schedule_context()
        active_dates = schedule_ctx["active_dates"]
        filtered = {}
        for player_id, pdata in players_data.items():
            team = pdata.get("team", "")
            game_date = pdata.get("game_date")
            date_ok = not active_dates or (bool(game_date) and game_date in active_dates)
            if team in active_teams and date_ok:
                filtered[player_id] = pdata
        return filtered

    def _normalize_players_data(self, players_data):
        if not isinstance(players_data, dict):
            return {}
        return players_data

    def _players_fingerprint(self, players_data):
        serialized = json.dumps(players_data or {}, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha1(serialized.encode("utf-8")).hexdigest()

    def _carry_forward_snapshots(self, existing_data, active_teams):
        carried = []
        for snapshot in existing_data.get("snapshots", []):
            filtered_players = self._filter_players_to_active_schedule(snapshot.get("players", {}), active_teams)
            if filtered_players:
                carried.append({
                    "timestamp": snapshot.get("timestamp"),
                    "label": snapshot.get("label"),
                    "players": filtered_players,
                })
        return carried

    def write_snapshot(self, label, players_data, bypass_dedupe=False, filter_to_active_schedule=False):
        """Append an intraday snapshot with optional deduplication and schedule filtering."""
        now = get_et_now()
        timestamp_str = now.isoformat()

        # Key snapshots by the schedule's game date, not the calendar date.
        # This prevents a reset at midnight when opening lines for tomorrow's
        # games are scraped tonight — the snapshot is filed under tomorrow's
        # game date and survives until the 6 AM pipeline flips the schedule.
        schedule_ctx = self._get_active_schedule_context()
        today_date = now.strftime("%Y-%m-%d")
        active_teams = schedule_ctx["active_teams"]

        data = self._read_json(self.line_movements_path, default={"date": today_date, "snapshots": []})

        if filter_to_active_schedule and data.get("snapshots"):
            data["snapshots"] = self._carry_forward_snapshots(data, active_teams)

        if data.get("date") != today_date:
            carried_snapshots = data.get("snapshots", [])
            data = {
                "date": today_date,
                "snapshots": carried_snapshots,
            }

        # Deduplication Guardrail - skipped if bypass_dedupe is True
        if not bypass_dedupe and data.get("snapshots"):
            last_snapshot = data["snapshots"][-1]
            last_timestamp_str = last_snapshot.get("timestamp")
            if last_timestamp_str:
                try:
                    last_dt = datetime.fromisoformat(last_timestamp_str)
                    if (now - last_dt).total_seconds() < get_intraday_interval_seconds():
                        return False 
                except ValueError:
                    pass

        filtered_players = (
            self._filter_players_to_active_schedule(players_data, active_teams)
            if filter_to_active_schedule
            else self._normalize_players_data(players_data)
        )
        if not filtered_players:
            return False

        if not bypass_dedupe and data.get("snapshots"):
            last_players = data["snapshots"][-1].get("players", {})
            if self._players_fingerprint(last_players) == self._players_fingerprint(filtered_players):
                return False

        new_snapshot = {
            "timestamp": timestamp_str,
            "label": label,
            "players": filtered_players
        }
        data["snapshots"].append(new_snapshot)
        self._write_json(self.line_movements_path, data)
        return True

    def _get_fallback_snapshot_data(self, player_id, game_id=None, game_date=None):
        """Find the most recent valid line for a player from today's snapshots"""
        now = get_et_now()
        today_date = now.strftime("%Y-%m-%d")
        data = self._read_json(self.line_movements_path, default={"date": today_date, "snapshots": []})

        if not data.get("snapshots"):
            return None
            
        # Iterate backwards through snapshots
        for snapshot in reversed(data["snapshots"]):
            players = snapshot.get("players", {})
            if player_id in players:
                candidate = players[player_id]
                if game_id and candidate.get("game_id") and candidate.get("game_id") != game_id:
                    continue
                if game_date and candidate.get("game_date") and candidate.get("game_date") != game_date:
                    continue
                return candidate
        return None

    def _merge_props_tree(self, primary_props, fallback_props):
        primary = primary_props if isinstance(primary_props, dict) else {}
        fallback = fallback_props if isinstance(fallback_props, dict) else {}
        merged = dict(primary)

        for stat_key, fallback_books in fallback.items():
            if not isinstance(fallback_books, dict):
                continue

            existing_books = merged.get(stat_key)
            if not isinstance(existing_books, dict):
                merged[stat_key] = fallback_books
                continue

            merged_books = dict(existing_books)
            for book_key, fallback_line in fallback_books.items():
                if not isinstance(fallback_line, dict):
                    continue

                existing_line = merged_books.get(book_key)
                if not isinstance(existing_line, dict):
                    merged_books[book_key] = fallback_line
                    continue

                merged_books[book_key] = {
                    **fallback_line,
                    **existing_line,
                }

            merged[stat_key] = merged_books

        return merged

    def _merge_historical_record(self, existing_record, incoming_record):
        existing = existing_record if isinstance(existing_record, dict) else {}
        incoming = incoming_record if isinstance(incoming_record, dict) else {}

        existing_props = existing.get("props", {})
        incoming_props = incoming.get("props", {})
        merged_props = self._merge_props_tree(incoming_props, existing_props)

        source_priority = {
            "closing_line": 3,
            "pre_game_snapshot_fallback": 2,
            "last_snapshot_fallback": 1,
            "line_movements_fallback": 1,
            None: 0,
        }
        existing_source = existing.get("source")
        incoming_source = incoming.get("source")
        preferred_source = incoming_source
        if source_priority.get(existing_source, 0) > source_priority.get(incoming_source, 0):
            preferred_source = existing_source

        return {
            "name": incoming.get("name") or existing.get("name"),
            "team": incoming.get("team") or existing.get("team"),
            "props": merged_props,
            "source": preferred_source,
            "captured_at": incoming.get("captured_at") or existing.get("captured_at"),
        }

    def process_closing_lines(self, players_data):
        """
        Process a batch of odds for closing lines.
        players_data expected schema:
        {
          "player_id_1": {
            "name": "jayson tatum",
            "team": "BOS",
            "game_id": "0022500800", # to lookup schedule
            "props": { ... },
            "fanduel_inPlay": False, # gate 2
            "draftkings_available": True # gate 2
          }
        }
        """
        now = get_et_now()
        today_date = now.strftime("%Y-%m-%d")
        
        schedule = self._load_schedule()
        schedule_games = {g["game_id"]: g for g in schedule.get("games", [])}
        
        historical_odds = self._read_json(self.historical_odds_path, default={})
            
        updated = False
        materialized_records = []

        for player_id, pdata in players_data.items():
            archive_date = pdata.get("game_date") or today_date
            if archive_date not in historical_odds:
                historical_odds[archive_date] = {}
                
            game_id = pdata.get("game_id")
            game_info = schedule_games.get(game_id)
            
            gate_1_passed = False
            gate_2_passed = False
            skip_reason = None
            
            # Gate 1: Deadline Check
            if not game_info:
                skip_reason = "game_not_found_in_schedule"
            else:
                deadline_str = game_info.get("closing_scrape_deadline")
                if not deadline_str:
                    skip_reason = "no_closing_scrape_deadline"
                else:
                    try:
                        deadline_dt = datetime.fromisoformat(deadline_str)
                        if now <= deadline_dt:
                            gate_1_passed = True
                        else:
                            skip_reason = "gate_1_deadline_passed"
                    except ValueError:
                        skip_reason = "invalid_deadline_format"
            
            # Gate 2: In-Play / Missing
            if gate_1_passed:
                fd_in_play = pdata.get("fanduel_inPlay", False)
                dk_avail = pdata.get("draftkings_available", False)
                
                if fd_in_play:
                    gate_2_passed = False
                    skip_reason = "gate_2_inplay_detected_fd"
                elif not dk_avail and not pdata.get("fanduel_available", True): # Assume fd available if we got here but dk might not be
                    gate_2_passed = False
                    skip_reason = "no_odds_returned"
                else:
                    gate_2_passed = True
                    
            if gate_1_passed and gate_2_passed:
                fallback_snapshot = self._get_fallback_snapshot_data(
                    player_id,
                    game_id=game_id,
                    game_date=archive_date,
                )
                merged_props = self._merge_props_tree(
                    pdata.get("props", {}),
                    (fallback_snapshot or {}).get("props", {}),
                )

                incoming_record = {
                    "name": pdata.get("name"),
                    "team": pdata.get("team"),
                    "props": merged_props,
                    "source": "closing_line",
                    "captured_at": now.isoformat()
                }
                existing_record = historical_odds[archive_date].get(player_id)
                next_record = self._merge_historical_record(existing_record, incoming_record)
                materialized_records.append({
                    "player_id": player_id,
                    "game_date": archive_date,
                    "game_id": game_id,
                    "record": next_record,
                })
                if next_record != existing_record:
                    historical_odds[archive_date][player_id] = next_record
                    updated = True
            else:
                # Fallback capture
                self.logger.info(f"Skipped {player_id} ({pdata.get('name')}): {skip_reason}")
                
                fallback_props = self._get_fallback_snapshot_data(
                    player_id,
                    game_id=game_id,
                    game_date=archive_date,
                )
                if fallback_props:
                    incoming_record = {
                        "name": pdata.get("name"),
                        "team": pdata.get("team"),
                        "props": fallback_props.get("props", {}),
                        "source": "last_snapshot_fallback",
                        "captured_at": now.isoformat()
                    }
                    existing_record = historical_odds[archive_date].get(player_id)
                    next_record = self._merge_historical_record(existing_record, incoming_record)
                    materialized_records.append({
                        "player_id": player_id,
                        "game_date": archive_date,
                        "game_id": game_id,
                        "record": next_record,
                    })
                    if next_record != existing_record:
                        historical_odds[archive_date][player_id] = next_record
                        updated = True
                else:
                    self.logger.info(f"  -> No fallback data found for {player_id}")

        if updated:
            self._write_json(self.historical_odds_path, historical_odds)

        return {
            "ok": True,
            "updated": updated,
            "records": materialized_records,
        }
