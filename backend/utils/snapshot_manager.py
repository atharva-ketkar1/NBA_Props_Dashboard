import os
import json
import logging
from datetime import datetime, timezone, timedelta

# Default to EST timezone (-5)
def get_et_now():
    return datetime.now(timezone(timedelta(hours=-5)))

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

    def write_snapshot(self, label, players_data):
        """Append an intraday snapshot to line_movements_today.json"""
        now = get_et_now()
        today_date = now.strftime("%Y-%m-%d")
        timestamp_str = now.isoformat()

        data = self._read_json(self.line_movements_path, default={"date": today_date, "snapshots": []})

        # Reset Detection Guardrail
        if data.get("date") != today_date:
            data = {"date": today_date, "snapshots": []}

        # Snapshot Deduplication Guardrail (30 minutes)
        if data.get("snapshots"):
            last_snapshot = data["snapshots"][-1]
            last_timestamp_str = last_snapshot.get("timestamp")
            if last_timestamp_str:
                try:
                    last_dt = datetime.fromisoformat(last_timestamp_str)
                    if (now - last_dt).total_seconds() < 1800:
                        return False # Skipped
                except ValueError:
                    pass

        new_snapshot = {
            "timestamp": timestamp_str,
            "label": label,
            "players": players_data
        }
        data["snapshots"].append(new_snapshot)
        self._write_json(self.line_movements_path, data)
        return True

    def _get_fallback_snapshot_data(self, player_id):
        """Find the most recent valid line for a player from today's snapshots"""
        now = get_et_now()
        today_date = now.strftime("%Y-%m-%d")
        data = self._read_json(self.line_movements_path, default={"date": today_date, "snapshots": []})
        
        if data.get("date") != today_date or not data.get("snapshots"):
            return None
            
        # Iterate backwards through snapshots
        for snapshot in reversed(data["snapshots"]):
            players = snapshot.get("players", {})
            if player_id in players:
                return players[player_id]
        return None

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
        
        schedule = self._read_json(self.schedule_path, default={})
        schedule_games = {g["game_id"]: g for g in schedule.get("games", [])}
        
        historical_odds = self._read_json(self.historical_odds_path, default={})
        if today_date not in historical_odds:
            historical_odds[today_date] = {}
            
        updated = False

        for player_id, pdata in players_data.items():
            # Immutability Guardrail
            if player_id in historical_odds[today_date]:
                continue
                
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
                        if now < deadline_dt:
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
                # Clean capture
                historical_odds[today_date][player_id] = {
                    "name": pdata.get("name"),
                    "team": pdata.get("team"),
                    "props": pdata.get("props", {}),
                    "source": "closing_line",
                    "captured_at": now.isoformat()
                }
                updated = True
            else:
                # Fallback capture
                self.logger.info(f"Skipped {player_id} ({pdata.get('name')}): {skip_reason}")
                
                fallback_props = self._get_fallback_snapshot_data(player_id)
                if fallback_props:
                    historical_odds[today_date][player_id] = {
                        "name": pdata.get("name"),
                        "team": pdata.get("team"),
                        "props": fallback_props,
                        "source": "last_snapshot_fallback",
                        "captured_at": now.isoformat()
                    }
                    updated = True
                else:
                    self.logger.info(f"  -> No fallback data found for {player_id}")

        if updated:
            self._write_json(self.historical_odds_path, historical_odds)
            
        return True
