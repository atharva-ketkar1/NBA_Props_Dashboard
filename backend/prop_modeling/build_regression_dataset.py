"""Build a regression dataset from game logs for predicting actual stat outcomes.

v2: Adds matchup features from supplementary data files:
  - opponent_defensive_ranks.json  → opp shot-type defense ranks
  - opp_def_zones.json             → per-position zone defense pct/rank
  - play_type_analysis.json        → player scoring style breakdown
  - shot_type_analysis.json        → catch-and-shoot / pull-up / <10ft volume
  - boxscores.json                 → game margins (blowout filter)
  - season_stats.csv               → team pace, off/def rating

Note: supplementary files are current-season snapshots only. Rows from 2024-25
will have NULL matchup features — CatBoost handles these natively via its
built-in missing value support.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from feature_schema import (
    DEFAULT_GAMELOG_PATHS,
    DEFAULT_MINUTES_DATASET_PATH,
    GENERATED_DIR,
    STAT_COLUMNS,
    BACKEND_DIR,
)
from injury_feature_config import (
    ACTIVE_ROSTER_LOOKBACK_DAYS,
    INJURY_INTERACTION_COLUMNS,
    POSITION_GROUPS,
    RETURN_ABSENT_THRESHOLD,
    RETURN_LOOKBACK_TEAM_GAMES,
    SAME_POS_VACANCY_FEATURE_COLUMNS,
    TEAMMATE_ONOFF_FEATURE_COLUMNS,
    TEAMMATE_ONOFF_FULL_WEIGHT_GAMES,
    TEAMMATE_ONOFF_LOOKBACK_PLAYER_GAMES,
    TEAMMATE_ONOFF_MIN_ABSENT_GAMES,
    TEAMMATE_ONOFF_MIN_PRESENT_GAMES,
    TEAM_VACANCY_FEATURE_COLUMNS,
    TRAILING_ABSENT_PRIOR_GAMES,
    apply_injury_feature_values,
    is_high_usage,
    is_key_teammate,
    is_onball,
    is_playmaker,
    make_same_pos_vacancy_stats,
    make_teammate_onoff_stats,
    make_team_vacancy_stats,
    normalize_position_group,
    season_key_for_date,
    trailing_active_values,
)
from minutes_model_config import (
    MINUTES_MODEL_FEATURE_COLUMNS,
    MINUTES_MODEL_TARGET_COLUMN,
)


# ---------------------------------------------------------------------------
# Column schema
# ---------------------------------------------------------------------------

REGRESSION_DATASET_COLUMNS = [
    # Identity
    "game_date", "player_id", "team", "opponent", "game_id", "stat_type", "is_home",
    # Target
    "actual_value",
    # Rolling features — player form
    "prior_games", "days_rest", "is_b2b",
    "season_stat_avg", "recent3_stat_avg", "recent5_stat_avg",
    "recent10_stat_avg", "recent20_stat_avg",
    "recent5_stat_ema",
    "season_home_stat_avg", "season_away_stat_avg",
    "season_stat_std", "recent5_stat_std", "recent10_stat_std",
    # Momentum
    "momentum_5v20", "momentum_3v10",
    # Consistency
    "recent5_cv", "recent10_cv",
    # Minutes / usage
    "season_minutes_avg", "recent3_minutes_avg",
    "recent5_minutes_avg", "recent10_minutes_avg",
    "minutes_trend_5v20", "minutes_cv_recent5",
    "season_usage_pct_avg", "recent10_usage_pct_avg",
    "season_ast_pct_avg", "recent10_ast_pct_avg",
    "season_reb_pct_avg", "recent10_reb_pct_avg",
    "season_ts_pct_avg", "recent10_ts_pct_avg",
    # Opportunity rates (per minute / possession)
    "season_potential_ast_rate", "recent10_potential_ast_rate",
    "season_reb_chance_rate", "recent10_reb_chance_rate",
    "season_drive_rate", "recent10_drive_rate",
    "season_fg3a_rate", "recent10_fg3a_rate",
    "recent5_pts_per100",
    "recent10_target_per_min",
    "missing_same_pos_minutes_x_player_target_per_min",
    # Half / quarter trends
    "recent5_1h_stat_share",
    # ── NEW v2: Opponent defensive features ──────────────────────────────────
    "opp_pts_defense_rank",       # overall opp D rank (1=best, 30=worst)
    "opp_catchAndShoot_rank",     # opp C&S defense rank
    "opp_pullup_rank",            # opp pull-up defense rank
    "opp_lessThan10ft_rank",      # opp interior (<10ft) defense rank
    "opp_def_restricted_pct",    # opp % allowed in restricted area (player pos)
    "opp_def_paint_pct",         # opp % allowed in paint (player pos)
    "opp_def_3pt_pct",           # opp avg 3pt% allowed (player pos, all zones)
    "opp_def_restricted_rank",   # opp restricted area rank (player pos)
    "opp_def_3pt_rank",          # opp 3pt defense rank (player pos)
    # ── NEW v2: Player style fingerprint ────────────────────────────────────
    "player_catchAndShoot_pg",   # catch-and-shoot pts per game
    "player_pullup_pg",          # pull-up pts per game
    "player_lessThan10ft_pg",    # <10ft pts per game
    "player_transition_pg",      # transition pts per game
    "player_isolation_pg",       # isolation pts per game
    "player_pnr_pg",             # pick-and-roll pts per game
    "player_spotup_pg",          # spot-up pts per game
    # ── NEW v2: Cross-feature matchup scores ────────────────────────────────
    "matchup_score_fg3",         # C&S volume × opp C&S rank weakness
    "matchup_score_pts",         # ISO+PnR volume × opp ISO+pull-up rank weakness
    "matchup_score_interior",    # <10ft volume × opp interior rank weakness
    # ── NEW v2: Context features ─────────────────────────────────────────────
    "team_pace",                 # team pace (possessions per 48)
    "opp_def_rating",            # opponent season defensive rating
    "recent5_avg_game_margin",   # avg margin of player's last 5 games (blowout flag)
    "recent5_blowout_flag",      # 1 if avg abs margin > 15 in last 5 games
    # ── NEW v2: Injury Reports ────────────────────────────────────────────────
    *TEAM_VACANCY_FEATURE_COLUMNS,
    *SAME_POS_VACANCY_FEATURE_COLUMNS,
    *INJURY_INTERACTION_COLUMNS,
    *TEAMMATE_ONOFF_FEATURE_COLUMNS,
]

DEFAULT_REGRESSION_DATASET_PATH = GENERATED_DIR / "regression_training_dataset.csv"
CURRENT_DATA_DIR = BACKEND_DIR / "data" / "current"

MINUTES_DATASET_COLUMNS = [
    "game_date",
    "game_id",
    MINUTES_MODEL_TARGET_COLUMN,
    *MINUTES_MODEL_FEATURE_COLUMNS,
]

# Abbreviation → full team name mapping (for matching opp_def_zones keys)
ABBREV_TO_FULL = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}

# Position group mapping for opp_def_zones lookup
POSITION_GROUP = {
    "PG": "G", "SG": "G", "G": "G",
    "SF": "F", "PF": "F", "F": "F",
    "C": "C",
}


# ---------------------------------------------------------------------------
# Enrichment data loader
# ---------------------------------------------------------------------------

class EnrichmentData:
    """Loads all supplementary data files once and exposes lookup methods."""

    def __init__(self, data_dir: Path, *, verbose: bool = True) -> None:
        self.opp_def_ranks: Dict[str, Dict] = {}       # team_full → {catchAndShoot, pullups, lessThanTenFeet}
        self.opp_def_zones: Dict[str, Dict] = {}       # team_full → {G/F/C → zone → {percentage, rank}}
        self.play_type_players: Dict[str, Dict] = {}   # player_name → {points → {transition, iso, ...}}
        self.shot_type_players: Dict[str, Dict] = {}   # player_name → {catchAndShoot, pullups, lessThan10ft, matchesPlayed}
        self.game_margins: Dict[str, Dict[str, float]] = {}  # game_id → {team_abbrev → margin}
        self.season_stats: Dict[str, Dict] = {}        # player_id_str → {PACE, DEF_RATING, ...}
        self.team_season_stats: Dict[str, Dict] = {}   # team_abbrev → aggregated stats
        self.player_id_to_name: Dict[str, str] = {}    # pid_str → player name
        self.player_id_to_position: Dict[str, str] = {}  # pid_str → raw position from season stats
        self.log_position_counts: Dict[Tuple[str, str, int], Counter[str]] = defaultdict(Counter)
        self.live_player_id_to_position: Dict[str, str] = {}
        self.verbose = verbose

        self._load(data_dir)

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    def _load(self, data_dir: Path) -> None:
        # 1. Opponent defensive ranks
        p = data_dir / "opponent_defensive_ranks.json"
        if p.exists():
            raw = json.loads(p.read_text())
            for team, v in raw.items():
                self.opp_def_ranks[team] = v.get("rankings", {})
            self._log(f"  opp_def_ranks: {len(self.opp_def_ranks)} teams")

        # 2. Opponent defensive zones
        p = data_dir / "opp_def_zones.json"
        if p.exists():
            self.opp_def_zones = json.loads(p.read_text())
            self._log(f"  opp_def_zones: {len(self.opp_def_zones)} teams")

        # 3. Play type analysis
        p = data_dir / "play_type_analysis.json"
        if p.exists():
            raw = json.loads(p.read_text())
            self.play_type_players = raw.get("players", {})
            self._log(f"  play_type: {len(self.play_type_players)} players")

        # 4. Shot type analysis
        p = data_dir / "shot_type_analysis.json"
        if p.exists():
            raw = json.loads(p.read_text())
            players = raw.get("players", {})
            for name, v in players.items():
                entry = v.get("pointsByShotType", {})
                gp = entry.get("matchesPlayed", 0) or 0
                if gp > 0:
                    self.shot_type_players[name] = {
                        "catchAndShoot_pg": entry.get("catchAndShoot", 0) / gp,
                        "pullup_pg": entry.get("pullups", 0) / gp,
                        "lessThan10ft_pg": entry.get("lessThanTenFeet", 0) / gp,
                    }
            self._log(f"  shot_type: {len(self.shot_type_players)} players")

        # 5. Boxscores (game margins)
        p = data_dir / "boxscores.json"
        if p.exists():
            raw = json.loads(p.read_text())
            for game_id, v in raw.items():
                margins = v.get("margins", {})
                if margins:
                    self.game_margins[str(game_id)] = {
                        str(team): float(margin) for team, margin in margins.items()
                    }
            self._log(f"  boxscores: {len(self.game_margins)} games with margins")

        # 6. Season stats CSV
        p = data_dir / "season_stats.csv"
        if p.exists():
            with p.open(newline="") as f:
                for row in csv.DictReader(f):
                    pid = str(row.get("PLAYER_ID") or "").strip()
                    name = str(row.get("PLAYER_NAME") or "").strip()
                    team = str(row.get("TEAM_ABBREVIATION") or "").strip()
                    position = str(row.get("POSITION") or "").strip()
                    if pid:
                        self.season_stats[pid] = row
                        if name:
                            self.player_id_to_name[pid] = name
                        if position:
                            self.player_id_to_position[pid] = position
                    if team:
                        # Use last entry per team (season averages are player-level,
                        # we want team-level pace/rating — take from any player)
                        if team not in self.team_season_stats:
                            self.team_season_stats[team] = row
            self._log(
                f"  season_stats: {len(self.season_stats)} players, "
                f"{len(self.team_season_stats)} teams, {len(self.player_id_to_name)} named"
            )

    def ingest_log_positions(self, all_rows: Sequence[Tuple[date, str, Dict[str, Any]]]) -> None:
        counts: Dict[Tuple[str, str, int], Counter[str]] = defaultdict(Counter)
        for game_date, player_id, row in all_rows:
            if game_date is None:
                continue
            pid = str(player_id or "").strip()
            team = str(row.get("TEAM_ABBREVIATION") or "").strip().upper()
            start_position = str(row.get("START_POSITION") or row.get("POSITION") or "").strip().upper()
            if not pid or not team or not start_position:
                continue
            counts[(pid, team, season_key_for_date(game_date))][start_position] += 1
        self.log_position_counts = counts

    def load_live_master_feed_positions(self, master_feed_path: Path) -> None:
        positions: Dict[str, str] = {}
        if master_feed_path.exists():
            try:
                payload = json.loads(master_feed_path.read_text())
            except Exception:
                payload = []
            if isinstance(payload, list):
                for player in payload:
                    if not isinstance(player, dict):
                        continue
                    pid = str(player.get("id") or "").strip()
                    position = str(player.get("position") or "").strip().upper()
                    if pid and position:
                        positions[pid] = position
        self.live_player_id_to_position = positions

    def _get_log_position(
        self,
        player_id: Optional[str],
        *,
        team_abbrev: Optional[str],
        game_date: Optional[date],
    ) -> Optional[str]:
        pid = str(player_id or "").strip()
        team = str(team_abbrev or "").strip().upper()
        if not pid or not team or game_date is None:
            return None
        counter = self.log_position_counts.get((pid, team, season_key_for_date(game_date)))
        if not counter:
            return None
        return counter.most_common(1)[0][0]

    def get_player_position_resolution(
        self,
        player_id: Optional[str],
        *,
        team_abbrev: Optional[str] = None,
        game_date: Optional[date] = None,
        allow_live_fallback: bool = False,
    ) -> Tuple[str, str]:
        pid = str(player_id or "").strip()
        if not pid:
            return "G", "fallback"

        season_position = str(self.player_id_to_position.get(pid) or "").strip().upper()
        if season_position:
            return season_position, "season_stats"

        log_position = self._get_log_position(
            pid,
            team_abbrev=team_abbrev,
            game_date=game_date,
        )
        if log_position:
            return log_position, "log_modal"

        if allow_live_fallback:
            live_position = str(self.live_player_id_to_position.get(pid) or "").strip().upper()
            if live_position:
                return live_position, "master_feed"

        return "G", "fallback"


    def get_opp_def_ranks(self, opp_abbrev: Optional[str]) -> Dict[str, Optional[float]]:
        """Return defensive ranks for the opponent team."""
        empty = {"opp_catchAndShoot_rank": None, "opp_pullup_rank": None,
                 "opp_lessThan10ft_rank": None, "opp_pts_defense_rank": None}
        if not opp_abbrev:
            return empty
        full = ABBREV_TO_FULL.get(opp_abbrev.upper())
        if not full:
            return empty
        ranks = self.opp_def_ranks.get(full, {})
        if not ranks:
            return empty
        # Compute an overall pts defense rank as average of the three
        vals = [ranks.get("catchAndShoot"), ranks.get("pullups"), ranks.get("lessThanTenFeet")]
        valid = [v for v in vals if v is not None]
        avg_rank = sum(valid) / len(valid) if valid else None
        return {
            "opp_catchAndShoot_rank": _safe_float(ranks.get("catchAndShoot")),
            "opp_pullup_rank": _safe_float(ranks.get("pullups")),
            "opp_lessThan10ft_rank": _safe_float(ranks.get("lessThanTenFeet")),
            "opp_pts_defense_rank": _r(avg_rank),
        }

    def get_opp_def_zones(self, opp_abbrev: Optional[str], position: Optional[str]) -> Dict[str, Optional[float]]:
        """Return per-position zone defense stats for the opponent."""
        empty = {"opp_def_restricted_pct": None, "opp_def_paint_pct": None,
                 "opp_def_3pt_pct": None, "opp_def_restricted_rank": None, "opp_def_3pt_rank": None}
        if not opp_abbrev:
            return empty
        full = ABBREV_TO_FULL.get(opp_abbrev.upper())
        if not full:
            return empty
        team_zones = self.opp_def_zones.get(full, {})
        if not team_zones:
            return empty

        # Map position to group: G / F / C
        pos_group = normalize_position_group(position or "G")
        zones = team_zones.get(pos_group, {})
        if not zones:
            zones = team_zones.get("G", {})  # fallback

        def pct(z: str) -> Optional[float]:
            val = zones.get(z, {}).get("percentage", "")
            try:
                return float(str(val).replace("%", "")) / 100.0
            except (ValueError, TypeError):
                return None

        def rank(z: str) -> Optional[float]:
            val = zones.get(z, {}).get("rank")
            return _safe_float(val)

        # Average the three 3PT zones
        three_pcts = [pct("left_corner"), pct("right_corner"), pct("top_key")]
        valid_3pt = [v for v in three_pcts if v is not None]
        avg_3pt_pct = sum(valid_3pt) / len(valid_3pt) if valid_3pt else None

        three_ranks = [rank("left_corner"), rank("right_corner"), rank("top_key")]
        valid_3pt_r = [v for v in three_ranks if v is not None]
        avg_3pt_rank = sum(valid_3pt_r) / len(valid_3pt_r) if valid_3pt_r else None

        return {
            "opp_def_restricted_pct": pct("restricted_area"),
            "opp_def_paint_pct": pct("paint"),
            "opp_def_3pt_pct": _r(avg_3pt_pct),
            "opp_def_restricted_rank": rank("restricted_area"),
            "opp_def_3pt_rank": _r(avg_3pt_rank),
        }

    def get_player_style(self, player_name: Optional[str]) -> Dict[str, Optional[float]]:
        """Return shot type and play type features for a player."""
        empty = {
            "player_catchAndShoot_pg": None, "player_pullup_pg": None,
            "player_lessThan10ft_pg": None, "player_transition_pg": None,
            "player_isolation_pg": None, "player_pnr_pg": None, "player_spotup_pg": None,
        }
        if not player_name:
            return empty

        result = dict(empty)

        # Shot type (per game)
        st = self.shot_type_players.get(player_name, {})
        result["player_catchAndShoot_pg"] = st.get("catchAndShoot_pg")
        result["player_pullup_pg"] = st.get("pullup_pg")
        result["player_lessThan10ft_pg"] = st.get("lessThan10ft_pg")

        # Play type (per match)
        pt = self.play_type_players.get(player_name, {})
        pts_data = pt.get("points", {})
        result["player_transition_pg"] = _safe_float(pts_data.get("transition", {}).get("perMatch"))
        result["player_isolation_pg"] = _safe_float(pts_data.get("isolation", {}).get("perMatch"))
        result["player_pnr_pg"] = _safe_float(pts_data.get("pickAndRollBallHandler", {}).get("perMatch"))
        result["player_spotup_pg"] = _safe_float(pts_data.get("spotUp", {}).get("perMatch"))

        return result

    def get_matchup_scores(
        self,
        style: Dict[str, Optional[float]],
        opp_ranks: Dict[str, Optional[float]],
    ) -> Dict[str, Optional[float]]:
        """Cross player style with opponent defensive ranks to produce matchup scores.

        Score = player_volume × (opp_rank / 15.5 - 1)
        A positive score means the player scores a lot in an area where the opponent is weak.
        Range: roughly -player_volume to +player_volume.
        """
        def _score(vol, rank):
            if vol is None or rank is None:
                return None
            # rank 30 = worst defense → normalized weakness = (30/15.5 - 1) ≈ +0.94
            # rank 1  = best defense  → normalized weakness = (1/15.5 - 1)  ≈ -0.94
            return _r(vol * (rank / 15.5 - 1.0))

        cs_vol = style.get("player_catchAndShoot_pg")
        cs_rank = opp_ranks.get("opp_catchAndShoot_rank")

        iso_vol = style.get("player_isolation_pg") or 0.0
        pnr_vol = style.get("player_pnr_pg") or 0.0
        combo_vol = iso_vol + pnr_vol if (style.get("player_isolation_pg") is not None
                                          or style.get("player_pnr_pg") is not None) else None
        pullup_rank = opp_ranks.get("opp_pullup_rank")

        interior_vol = style.get("player_lessThan10ft_pg")
        interior_rank = opp_ranks.get("opp_lessThan10ft_rank")

        return {
            "matchup_score_fg3": _score(cs_vol, cs_rank),
            "matchup_score_pts": _score(combo_vol, pullup_rank),
            "matchup_score_interior": _score(interior_vol, interior_rank),
        }

    def get_team_context(self, team_abbrev: Optional[str], opp_abbrev: Optional[str]) -> Dict[str, Optional[float]]:
        """Return pace and defensive rating context."""
        result: Dict[str, Optional[float]] = {"team_pace": None, "opp_def_rating": None}
        if team_abbrev:
            row = self.team_season_stats.get(team_abbrev, {})
            result["team_pace"] = _safe_float(row.get("PACE"))
        if opp_abbrev:
            row = self.team_season_stats.get(opp_abbrev, {})
            result["opp_def_rating"] = _safe_float(row.get("DEF_RATING"))
        return result

    def get_game_margin(self, game_id: Optional[str], team_abbrev: Optional[str]) -> Optional[float]:
        """Return the team's point margin in a specific game.

        Boxscores use zero-padded 10-digit IDs (e.g. '0022500359').
        Gamelogs may omit the leading zeros (e.g. '22500359').
        Normalize to 10 digits before lookup.
        """
        if not game_id or not team_abbrev:
            return None
        # Normalize: strip and zero-pad to 10 digits
        normalized = str(game_id).strip().zfill(10)
        margins = self.game_margins.get(normalized, {})
        return margins.get(team_abbrev.upper())

    def get_player_position(
        self,
        player_id: Optional[str],
        *,
        team_abbrev: Optional[str] = None,
        game_date: Optional[date] = None,
        allow_live_fallback: bool = False,
    ) -> str:
        position, _ = self.get_player_position_resolution(
            player_id,
            team_abbrev=team_abbrev,
            game_date=game_date,
            allow_live_fallback=allow_live_fallback,
        )
        return position

    def get_player_position_group(
        self,
        player_id: Optional[str],
        *,
        team_abbrev: Optional[str] = None,
        game_date: Optional[date] = None,
        allow_live_fallback: bool = False,
    ) -> str:
        return normalize_position_group(
            self.get_player_position(
                player_id,
                team_abbrev=team_abbrev,
                game_date=game_date,
                allow_live_fallback=allow_live_fallback,
            )
        )


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _parse_date(v: Any) -> Optional[date]:
    raw = str(v or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _mean(vals: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return fmean(clean) if clean else None


def _std(vals: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    return pstdev(clean) if len(clean) >= 2 else None


def _ema(vals: Sequence[Optional[float]], alpha: float = 0.4) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    res = clean[0]
    for v in clean[1:]:
        res = v * alpha + res * (1 - alpha)
    return res


def _r(v: Optional[float], d: int = 4) -> Optional[float]:
    return round(v, d) if v is not None else None


def _sum_stat(row: Dict[str, Any], stat_type: str) -> Optional[float]:
    cols = STAT_COLUMNS.get(stat_type)
    if not cols:
        return None
    total = 0.0
    for c in cols:
        v = _safe_float(row.get(c))
        if v is None:
            return None
        total += v
    return total


def _metric_rate(row: Dict[str, Any], key: str) -> Optional[float]:
    num = _safe_float(row.get(key))
    mins = _safe_float(row.get("MIN"))
    if num is None or mins is None or mins <= 0:
        return None
    return num / mins


def _target_per_min(row: Dict[str, Any], stat_type: str) -> Optional[float]:
    total = _sum_stat(row, stat_type)
    mins = _safe_float(row.get("MIN"))
    if total is None or mins is None or mins <= 0:
        return None
    return total / max(mins, 1.0)


def _parse_matchup(matchup: Any, team: str) -> Tuple[Optional[str], Optional[int]]:
    raw = str(matchup or "").strip()
    if not raw or not team:
        return None, None
    if " vs. " in raw:
        l, r = raw.split(" vs. ", 1)
        opp = r if l == team else l
        return opp.strip() or None, 1
    if " @ " in raw:
        l, r = raw.split(" @ ", 1)
        opp = r if l == team else l
        return opp.strip() or None, 0
    return None, None


def _1h_stat(row: Dict[str, Any], stat_type: str) -> Optional[float]:
    mapping = {"PTS": "1H_PTS", "AST": "1H_AST", "REB": "1H_REB", "FG3M": "1H_FG3M"}
    key = mapping.get(stat_type)
    return _safe_float(row.get(key)) if key else None


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build v2 regression training dataset.")
    parser.add_argument("--gamelogs", nargs="*",
                        default=[str(p) for p in DEFAULT_GAMELOG_PATHS])
    parser.add_argument("--output-csv", default=str(DEFAULT_REGRESSION_DATASET_PATH))
    parser.add_argument("--minutes-output-csv", default=str(DEFAULT_MINUTES_DATASET_PATH))
    parser.add_argument("--min-prior-games", type=int, default=5)
    parser.add_argument("--min-minutes", type=float, default=10.0)
    parser.add_argument("--minutes-min-prior-games", type=int, default=3)
    parser.add_argument("--stat-types", nargs="*",
                        default=["PTS", "AST", "REB", "FG3M", "STL", "BLK",
                                 "PTS+REB+AST", "PTS+REB", "PTS+AST", "REB+AST", "STL+BLK"])
    parser.add_argument("--data-dir", default=str(CURRENT_DATA_DIR))
    return parser.parse_args()


def _build_history(
    gamelog_paths: Sequence[Path],
    *,
    include_zero_minutes: bool = False,
) -> List[Tuple[date, str, Dict[str, Any]]]:
    rows: List[Tuple[date, str, Dict[str, Any]]] = []
    for path in gamelog_paths:
        if not path.exists():
            continue
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                pid = str(row.get("PLAYER_ID") or "").strip()
                gd = _parse_date(row.get("GAME_DATE"))
                mins = _safe_float(row.get("MIN"))
                if not pid or gd is None or mins is None:
                    continue
                if include_zero_minutes:
                    if mins < 0:
                        continue
                elif mins <= 0:
                    continue
                rows.append((gd, pid, row))
    rows.sort(key=lambda x: (x[0], str(x[2].get("GAME_ID") or "").strip(), x[1]))
    return rows


def _build_player_index(
    all_rows: List[Tuple[date, str, Dict[str, Any]]],
) -> Dict[str, List[Tuple[date, Dict[str, Any]]]]:
    index: Dict[str, List[Tuple[date, Dict[str, Any]]]] = defaultdict(list)
    for gd, pid, row in all_rows:
        index[pid].append((gd, row))
    return dict(index)


def _compute_absent_player_priors(
    player_state: Dict[str, Any],
    current_date: date,
) -> Dict[str, Optional[float]]:
    usage_vals = trailing_active_values(
        player_state["usg_history"],
        current_date,
        lookback_days=ACTIVE_ROSTER_LOOKBACK_DAYS,
        max_games=TRAILING_ABSENT_PRIOR_GAMES,
    )
    ast_pct_vals = trailing_active_values(
        player_state["ast_pct_history"],
        current_date,
        lookback_days=ACTIVE_ROSTER_LOOKBACK_DAYS,
        max_games=TRAILING_ABSENT_PRIOR_GAMES,
    )
    potential_ast_vals = trailing_active_values(
        player_state["potential_ast_history"],
        current_date,
        lookback_days=ACTIVE_ROSTER_LOOKBACK_DAYS,
        max_games=TRAILING_ABSENT_PRIOR_GAMES,
    )
    drive_vals = trailing_active_values(
        player_state["drive_history"],
        current_date,
        lookback_days=ACTIVE_ROSTER_LOOKBACK_DAYS,
        max_games=TRAILING_ABSENT_PRIOR_GAMES,
    )
    minute_vals = trailing_active_values(
        player_state["min_history"],
        current_date,
        lookback_days=ACTIVE_ROSTER_LOOKBACK_DAYS,
        max_games=TRAILING_ABSENT_PRIOR_GAMES,
    )
    usage_pct = _mean(usage_vals)
    ast_pct = _mean(ast_pct_vals)
    potential_ast_pg = _mean(potential_ast_vals)
    drives_pg = _mean(drive_vals)
    minutes = _mean(minute_vals)
    active_games = len(minute_vals)
    return {
        "usage_pct": usage_pct,
        "ast_pct": ast_pct,
        "potential_ast_pg": potential_ast_pg,
        "drives_pg": drives_pg,
        "minutes": minutes,
        "active_games": float(active_games),
    }


def _empty_same_pos_by_group() -> Dict[str, Dict[str, float]]:
    return {group: make_same_pos_vacancy_stats() for group in POSITION_GROUPS}


def _build_team_presence_context(
    all_rows: List[Tuple[date, str, Dict[str, Any]]],
) -> Tuple[
    Dict[Tuple[str, date], set[str]],
    Dict[Tuple[str, int], List[date]],
]:
    team_presence_index: Dict[Tuple[str, date], set[str]] = defaultdict(set)
    team_game_dates_by_season: Dict[Tuple[str, int], set[date]] = defaultdict(set)

    for game_date, player_id, row in all_rows:
        team = str(row.get("TEAM_ABBREVIATION") or "").strip()
        if not team:
            continue
        team_presence_index[(team, game_date)].add(player_id)
        team_game_dates_by_season[(team, season_key_for_date(game_date))].add(game_date)

    return (
        {key: set(player_ids) for key, player_ids in team_presence_index.items()},
        {key: sorted(game_dates) for key, game_dates in team_game_dates_by_season.items()},
    )


def _teammate_priors_with_position(player_state: Dict[str, Any], current_date: date) -> Dict[str, Any]:
    priors = _compute_absent_player_priors(player_state, current_date)
    return {
        **priors,
        "pos_group": player_state.get("pos_group"),
    }


def _teammate_impact_score(priors: Dict[str, Any]) -> float:
    usage_pct = _safe_float(priors.get("usage_pct")) or 0.0
    minutes = _safe_float(priors.get("minutes")) or 0.0
    return usage_pct + 0.5 * minutes


def _metric_mean_for_rows(
    rows: Sequence[Dict[str, Any]],
    *,
    stat_type: str,
    metric_key: str,
) -> float:
    if metric_key == "stat":
        return _mean([_sum_stat(row, stat_type) for row in rows]) or 0.0
    if metric_key == "minutes":
        return _mean([_safe_float(row.get("MIN")) for row in rows]) or 0.0
    if metric_key == "usage_pct":
        return _mean([_safe_float(row.get("USG_PCT")) for row in rows]) or 0.0
    if metric_key == "potential_ast_rate":
        return _mean([_metric_rate(row, "POTENTIAL_AST") for row in rows]) or 0.0
    if metric_key == "drive_rate":
        return _mean([_metric_rate(row, "DRIVES") for row in rows]) or 0.0
    if metric_key == "target_per_min":
        return _mean([
            _target_per_min(row, stat_type)
            for row in rows
            if (_safe_float(row.get("MIN")) or 0.0) >= 4.0
        ]) or 0.0
    return 0.0


def _compute_teammate_split_summary(
    prior_player_games: Sequence[Tuple[date, Dict[str, Any]]],
    *,
    team: str,
    teammate_id: str,
    stat_type: str,
    team_presence_index: Dict[Tuple[str, date], set[str]],
) -> Optional[Dict[str, float]]:
    absent_rows: List[Dict[str, Any]] = []
    present_rows: List[Dict[str, Any]] = []

    for game_date, row in prior_player_games:
        present_player_ids = team_presence_index.get((team, game_date), set())
        if teammate_id in present_player_ids:
            present_rows.append(row)
        else:
            absent_rows.append(row)

    absent_games = len(absent_rows)
    present_games = len(present_rows)
    if (
        absent_games < TEAMMATE_ONOFF_MIN_ABSENT_GAMES
        or present_games < TEAMMATE_ONOFF_MIN_PRESENT_GAMES
    ):
        return None

    support = min(absent_games, present_games)
    shrink = min(1.0, support / float(TEAMMATE_ONOFF_FULL_WEIGHT_GAMES))
    return {
        "absent_games": float(absent_games),
        "present_games": float(present_games),
        "support": float(support),
        "shrink": float(shrink),
        "stat_absent_mean": _metric_mean_for_rows(absent_rows, stat_type=stat_type, metric_key="stat"),
        "stat_present_mean": _metric_mean_for_rows(present_rows, stat_type=stat_type, metric_key="stat"),
        "minutes_absent_mean": _metric_mean_for_rows(absent_rows, stat_type=stat_type, metric_key="minutes"),
        "minutes_present_mean": _metric_mean_for_rows(present_rows, stat_type=stat_type, metric_key="minutes"),
        "usage_pct_absent_mean": _metric_mean_for_rows(absent_rows, stat_type=stat_type, metric_key="usage_pct"),
        "usage_pct_present_mean": _metric_mean_for_rows(present_rows, stat_type=stat_type, metric_key="usage_pct"),
        "potential_ast_rate_absent_mean": _metric_mean_for_rows(
            absent_rows, stat_type=stat_type, metric_key="potential_ast_rate"
        ),
        "potential_ast_rate_present_mean": _metric_mean_for_rows(
            present_rows, stat_type=stat_type, metric_key="potential_ast_rate"
        ),
        "drive_rate_absent_mean": _metric_mean_for_rows(
            absent_rows, stat_type=stat_type, metric_key="drive_rate"
        ),
        "drive_rate_present_mean": _metric_mean_for_rows(
            present_rows, stat_type=stat_type, metric_key="drive_rate"
        ),
        "target_per_min_absent_mean": _metric_mean_for_rows(
            absent_rows, stat_type=stat_type, metric_key="target_per_min"
        ),
        "target_per_min_present_mean": _metric_mean_for_rows(
            present_rows, stat_type=stat_type, metric_key="target_per_min"
        ),
    }


def _aggregate_teammate_delta_features(
    teammate_priors: Dict[str, Dict[str, Any]],
    *,
    prefix: str,
    delta_direction: str,
    prior_player_games: Sequence[Tuple[date, Dict[str, Any]]],
    team: str,
    stat_type: str,
    team_presence_index: Dict[Tuple[str, date], set[str]],
) -> Dict[str, float]:
    aggregated = {
        f"{prefix}_player_stat_delta": 0.0,
        f"{prefix}_player_minutes_delta": 0.0,
        f"{prefix}_player_usage_pct_delta": 0.0,
        f"{prefix}_player_potential_ast_rate_delta": 0.0,
        f"{prefix}_player_drive_rate_delta": 0.0,
        f"{prefix}_player_target_per_min_delta": 0.0,
        f"{prefix}_effective_support": 0.0,
    }
    if not teammate_priors:
        return aggregated

    contributors: List[Tuple[float, Dict[str, float]]] = []
    for teammate_id, priors in teammate_priors.items():
        split_summary = _compute_teammate_split_summary(
            prior_player_games,
            team=team,
            teammate_id=teammate_id,
            stat_type=stat_type,
            team_presence_index=team_presence_index,
        )
        if split_summary is None:
            continue
        contributors.append((_teammate_impact_score(priors), split_summary))

    if not contributors:
        return aggregated

    total_impact = sum(max(impact, 0.0) for impact, _ in contributors)
    equal_weight = 1.0 / float(len(contributors))

    for impact, split_summary in contributors:
        weight = equal_weight if total_impact <= 0.0 else max(impact, 0.0) / total_impact
        shrink = split_summary["shrink"]
        if delta_direction == "missing":
            stat_delta = split_summary["stat_absent_mean"] - split_summary["stat_present_mean"]
            minutes_delta = split_summary["minutes_absent_mean"] - split_summary["minutes_present_mean"]
            usage_delta = split_summary["usage_pct_absent_mean"] - split_summary["usage_pct_present_mean"]
            ast_delta = (
                split_summary["potential_ast_rate_absent_mean"]
                - split_summary["potential_ast_rate_present_mean"]
            )
            drive_delta = split_summary["drive_rate_absent_mean"] - split_summary["drive_rate_present_mean"]
            target_per_min_delta = (
                split_summary["target_per_min_absent_mean"]
                - split_summary["target_per_min_present_mean"]
            )
        else:
            stat_delta = split_summary["stat_present_mean"] - split_summary["stat_absent_mean"]
            minutes_delta = split_summary["minutes_present_mean"] - split_summary["minutes_absent_mean"]
            usage_delta = split_summary["usage_pct_present_mean"] - split_summary["usage_pct_absent_mean"]
            ast_delta = (
                split_summary["potential_ast_rate_present_mean"]
                - split_summary["potential_ast_rate_absent_mean"]
            )
            drive_delta = split_summary["drive_rate_present_mean"] - split_summary["drive_rate_absent_mean"]
            target_per_min_delta = (
                split_summary["target_per_min_present_mean"]
                - split_summary["target_per_min_absent_mean"]
            )

        aggregated[f"{prefix}_player_stat_delta"] += weight * shrink * stat_delta
        aggregated[f"{prefix}_player_minutes_delta"] += weight * shrink * minutes_delta
        aggregated[f"{prefix}_player_usage_pct_delta"] += weight * shrink * usage_delta
        aggregated[f"{prefix}_player_potential_ast_rate_delta"] += weight * shrink * ast_delta
        aggregated[f"{prefix}_player_drive_rate_delta"] += weight * shrink * drive_delta
        aggregated[f"{prefix}_player_target_per_min_delta"] += weight * shrink * target_per_min_delta
        aggregated[f"{prefix}_effective_support"] += weight * split_summary["support"]

    return aggregated


def _build_key_teammate_onoff_features(
    player_history: List[Tuple[date, Dict[str, Any]]],
    game_idx: int,
    *,
    stat_type: str,
    target_pos_group: str,
    team_presence_index: Dict[Tuple[str, date], set[str]],
    team_game_dates_by_season: Dict[Tuple[str, int], List[date]],
    current_missing_player_priors: Optional[Dict[str, Dict[str, Any]]] = None,
    current_active_player_priors: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, float]:
    feature_values = make_teammate_onoff_stats()
    target_date, target_row = player_history[game_idx]
    team = str(target_row.get("TEAM_ABBREVIATION") or "").strip()
    target_player_id = str(target_row.get("PLAYER_ID") or "").strip()
    if not team or not target_player_id:
        return feature_values

    target_season_key = season_key_for_date(target_date)
    prior_same_team_season_games = [
        (game_date, row)
        for game_date, row in player_history[:game_idx]
        if (
            game_date is not None
            and str(row.get("TEAM_ABBREVIATION") or "").strip() == team
            and season_key_for_date(game_date) == target_season_key
        )
    ][-TEAMMATE_ONOFF_LOOKBACK_PLAYER_GAMES:]
    if not prior_same_team_season_games:
        return feature_values

    missing_teammate_priors = {
        teammate_id: priors
        for teammate_id, priors in (current_missing_player_priors or {}).items()
        if teammate_id != target_player_id and is_key_teammate(
            _safe_float(priors.get("usage_pct")),
            _safe_float(priors.get("minutes")),
            _safe_float(priors.get("ast_pct")),
            _safe_float(priors.get("potential_ast_pg")),
            _safe_float(priors.get("drives_pg")),
        )
    }
    feature_values["missing_key_teammate_count"] = float(len(missing_teammate_priors))
    feature_values["missing_same_pos_key_count"] = float(
        sum(
            1
            for priors in missing_teammate_priors.values()
            if normalize_position_group(priors.get("pos_group")) == target_pos_group
        )
    )
    feature_values["missing_guard_key_count"] = float(
        sum(
            1
            for priors in missing_teammate_priors.values()
            if normalize_position_group(priors.get("pos_group")) == "G"
        )
    )
    feature_values["missing_playmaker_key_count"] = float(
        sum(
            1
            for priors in missing_teammate_priors.values()
            if is_playmaker(
                _safe_float(priors.get("ast_pct")),
                _safe_float(priors.get("potential_ast_pg")),
            )
        )
    )

    previous_team_dates = [
        game_date
        for game_date in team_game_dates_by_season.get((team, target_season_key), [])
        if game_date < target_date
    ][-RETURN_LOOKBACK_TEAM_GAMES:]
    returning_teammate_priors: Dict[str, Dict[str, Any]] = {}
    if previous_team_dates:
        for teammate_id, priors in (current_active_player_priors or {}).items():
            if teammate_id == target_player_id:
                continue
            if not is_key_teammate(
                _safe_float(priors.get("usage_pct")),
                _safe_float(priors.get("minutes")),
                _safe_float(priors.get("ast_pct")),
                _safe_float(priors.get("potential_ast_pg")),
                _safe_float(priors.get("drives_pg")),
            ):
                continue
            absent_count = sum(
                1
                for game_date in previous_team_dates
                if teammate_id not in team_presence_index.get((team, game_date), set())
            )
            if absent_count >= RETURN_ABSENT_THRESHOLD:
                returning_teammate_priors[teammate_id] = priors

    feature_values["returning_key_teammate_count"] = float(len(returning_teammate_priors))
    feature_values["returning_same_pos_key_count"] = float(
        sum(
            1
            for priors in returning_teammate_priors.values()
            if normalize_position_group(priors.get("pos_group")) == target_pos_group
        )
    )
    feature_values["returning_guard_key_count"] = float(
        sum(
            1
            for priors in returning_teammate_priors.values()
            if normalize_position_group(priors.get("pos_group")) == "G"
        )
    )
    feature_values["returning_playmaker_key_count"] = float(
        sum(
            1
            for priors in returning_teammate_priors.values()
            if is_playmaker(
                _safe_float(priors.get("ast_pct")),
                _safe_float(priors.get("potential_ast_pg")),
            )
        )
    )

    feature_values.update(
        _aggregate_teammate_delta_features(
            missing_teammate_priors,
            prefix="missing_key_teammates",
            delta_direction="missing",
            prior_player_games=prior_same_team_season_games,
            team=team,
            stat_type=stat_type,
            team_presence_index=team_presence_index,
        )
    )
    feature_values.update(
        _aggregate_teammate_delta_features(
            returning_teammate_priors,
            prefix="returning_key_teammates",
            delta_direction="returning",
            prior_player_games=prior_same_team_season_games,
            team=team,
            stat_type=stat_type,
            team_presence_index=team_presence_index,
        )
    )
    return feature_values


def _aggregate_vacancy_stats_from_player_priors(
    missing_player_priors: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    team_stats = make_team_vacancy_stats()
    same_pos_stats = _empty_same_pos_by_group()

    for priors in missing_player_priors.values():
        usage_pct = _safe_float(priors.get("usage_pct"))
        minutes = _safe_float(priors.get("minutes"))
        ast_pct = _safe_float(priors.get("ast_pct"))
        potential_ast_pg = _safe_float(priors.get("potential_ast_pg"))
        drives_pg = _safe_float(priors.get("drives_pg"))
        pos_group = normalize_position_group(priors.get("pos_group"))

        if usage_pct is None and minutes is None:
            continue
        if not is_key_teammate(usage_pct, minutes, ast_pct, potential_ast_pg, drives_pg):
            continue

        team_stats["missing_team_usage_pct"] += usage_pct or 0.0
        team_stats["missing_team_minutes"] += minutes or 0.0

        same_pos_stats[pos_group]["missing_same_pos_usage_pct"] += usage_pct or 0.0
        same_pos_stats[pos_group]["missing_same_pos_minutes"] += minutes or 0.0

        if pos_group == "G":
            team_stats["missing_guard_usage_pct"] += usage_pct or 0.0
            team_stats["missing_guard_minutes"] += minutes or 0.0

        if is_high_usage(usage_pct):
            team_stats["missing_high_usage_usage_pct"] += usage_pct or 0.0
            team_stats["missing_high_usage_minutes"] += minutes or 0.0

        if is_playmaker(ast_pct, potential_ast_pg):
            team_stats["missing_playmaker_potential_ast_pg"] += potential_ast_pg or 0.0
            team_stats["missing_playmaker_minutes"] += minutes or 0.0

        if is_onball(drives_pg):
            team_stats["missing_onball_drives_pg"] += drives_pg or 0.0
            team_stats["missing_onball_minutes"] += minutes or 0.0

    return (
        {key: _r(value) or 0.0 for key, value in team_stats.items()},
        {
            pos_group: {key: _r(value) or 0.0 for key, value in pos_stats.items()}
            for pos_group, pos_stats in same_pos_stats.items()
        },
    )


def _build_team_game_regime_features(
    *,
    team: str,
    player_id: str,
    target_date: date,
    team_presence_index: Dict[Tuple[str, date], set[str]],
    team_game_dates_by_season: Dict[Tuple[str, int], List[date]],
) -> Dict[str, float]:
    season_key = season_key_for_date(target_date)
    prior_team_dates = [
        game_date
        for game_date in team_game_dates_by_season.get((team, season_key), [])
        if game_date < target_date
    ]
    prior_presence_flags = [
        player_id in team_presence_index.get((team, game_date), set())
        for game_date in prior_team_dates
    ]

    recent_team_games_missed_10 = sum(1 for flag in prior_presence_flags[-10:] if not flag)

    inactive_streak = 0
    for flag in reversed(prior_presence_flags):
        if flag:
            break
        inactive_streak += 1

    previous_absence_streak = 0
    games_since_return = 0

    if inactive_streak > 0:
        previous_absence_streak = inactive_streak
    else:
        current_active_streak = 0
        for flag in reversed(prior_presence_flags):
            if not flag:
                break
            current_active_streak += 1
        idx = len(prior_presence_flags) - current_active_streak - 1
        while idx >= 0 and not prior_presence_flags[idx]:
            previous_absence_streak += 1
            idx -= 1
        if previous_absence_streak > 0:
            games_since_return = current_active_streak

    return {
        "recent_team_games_missed_10": float(recent_team_games_missed_10),
        "inactive_streak_team_games": float(inactive_streak),
        "games_since_return": float(games_since_return),
        "previous_absence_streak_team_games": float(previous_absence_streak),
    }


def _build_minutes_row(
    player_history: List[Tuple[date, Dict[str, Any]]],
    game_idx: int,
    *,
    min_prior: int,
    enrichment: EnrichmentData,
    team_missing_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    team_active_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    team_presence_index: Dict[Tuple[str, date], set[str]],
    team_game_dates_by_season: Dict[Tuple[str, int], List[date]],
) -> Optional[Dict[str, Any]]:
    target_date, target_row = player_history[game_idx]
    target_player_id = str(target_row.get("PLAYER_ID") or "").strip()
    team = str(target_row.get("TEAM_ABBREVIATION") or "").strip()
    if not target_player_id or not team or target_date is None:
        return None

    target_season_key = season_key_for_date(target_date)
    prior_history = player_history[:game_idx]
    prior_rows = [row for _, row in prior_history]
    prior_same_team_season_rows = [
        row
        for game_date, row in prior_history
        if (
            str(row.get("TEAM_ABBREVIATION") or "").strip() == team
            and game_date is not None
            and season_key_for_date(game_date) == target_season_key
        )
    ]
    if len(prior_same_team_season_rows) < min_prior:
        return None

    target_mins = _safe_float(target_row.get("MIN"))
    if target_mins is None or target_mins < 0:
        return None

    mins_all = [_safe_float(r.get("MIN")) for r in prior_same_team_season_rows]
    m3 = [_safe_float(r.get("MIN")) for r in prior_same_team_season_rows[-3:]]
    m5 = [_safe_float(r.get("MIN")) for r in prior_same_team_season_rows[-5:]]
    m10 = [_safe_float(r.get("MIN")) for r in prior_same_team_season_rows[-10:]]
    m20 = [_safe_float(r.get("MIN")) for r in prior_same_team_season_rows[-20:]]
    m5_avg = _mean(m5)
    m20_avg = _mean(m20)
    m5_std = _std(m5)
    min_trend = None if m5_avg is None or m20_avg is None else m5_avg - m20_avg
    min_cv = None if m5_avg is None or m5_std is None or abs(m5_avg) < 0.01 else m5_std / abs(m5_avg)

    recent_1q = [_safe_float(r.get("1Q_MIN")) for r in prior_same_team_season_rows[-3:]]
    recent_1h = [_safe_float(r.get("1H_MIN")) for r in prior_same_team_season_rows[-5:]]
    recent_1h_shares = []
    for row in prior_same_team_season_rows[-5:]:
        full_min = _safe_float(row.get("MIN"))
        first_half_min = _safe_float(row.get("1H_MIN"))
        if full_min is None or first_half_min is None or full_min <= 0:
            continue
        recent_1h_shares.append(first_half_min / full_min)

    last_active_date = prior_history[-1][0] if prior_history else None
    days_rest = (target_date - last_active_date).days if last_active_date else None

    recent_margins = []
    for row in prior_same_team_season_rows[-10:]:
        game_id = str(row.get("GAME_ID") or "").strip()
        margin = enrichment.get_game_margin(game_id, team)
        if margin is not None:
            recent_margins.append(abs(margin))
    blowout_rate = (
        sum(1 for margin in recent_margins if margin > 15.0) / len(recent_margins)
        if recent_margins
        else 0.0
    )

    last_game_minutes = _safe_float(prior_same_team_season_rows[-1].get("MIN")) if prior_same_team_season_rows else None
    target_pos_group = enrichment.get_player_position_group(
        target_player_id,
        team_abbrev=team,
        game_date=target_date,
    )
    opponent, is_home = _parse_matchup(target_row.get("MATCHUP"), team)

    current_missing_player_priors = {
        pid: priors
        for pid, priors in team_missing_player_priors_dict.get((team, target_date), {}).items()
        if pid != target_player_id
    }
    team_vacancy_stats, same_pos_vacancy_stats_by_group = _aggregate_vacancy_stats_from_player_priors(
        current_missing_player_priors
    )
    teammate_onoff_stats = _build_key_teammate_onoff_features(
        player_history,
        game_idx,
        stat_type="PTS",
        target_pos_group=target_pos_group,
        team_presence_index=team_presence_index,
        team_game_dates_by_season=team_game_dates_by_season,
        current_missing_player_priors=current_missing_player_priors,
        current_active_player_priors={
            pid: priors
            for pid, priors in team_active_player_priors_dict.get((team, target_date), {}).items()
            if pid != target_player_id
        },
    )
    regime_features = _build_team_game_regime_features(
        team=team,
        player_id=target_player_id,
        target_date=target_date,
        team_presence_index=team_presence_index,
        team_game_dates_by_season=team_game_dates_by_season,
    )

    row_obj = {
        "game_date": target_date.isoformat(),
        "player_id": target_player_id,
        "team": team,
        "opponent": opponent,
        "game_id": str(target_row.get("GAME_ID") or "").strip() or None,
        "is_home": is_home,
        MINUTES_MODEL_TARGET_COLUMN: _r(target_mins),
        "prior_games": len(prior_rows),
        "same_team_current_season_games": len(prior_same_team_season_rows),
        "days_rest": days_rest,
        "is_b2b": 1 if days_rest == 1 else 0,
        "season_minutes_avg": _r(_mean(mins_all)),
        "recent3_minutes_avg": _r(_mean(m3)),
        "recent5_minutes_avg": _r(m5_avg),
        "recent10_minutes_avg": _r(_mean(m10)),
        "minutes_trend_5v20": _r(min_trend),
        "minutes_cv_recent5": _r(min_cv),
        "recent3_1q_minutes_avg": _r(_mean(recent_1q)),
        "recent5_1h_minutes_avg": _r(_mean(recent_1h)),
        "recent5_1h_minutes_share": _r(_mean(recent_1h_shares)),
        **regime_features,
        "minutes_last_game": _r(last_game_minutes),
        "minutes_delta_last1_vs_recent5": _r(
            None if last_game_minutes is None or m5_avg is None else last_game_minutes - m5_avg
        ),
        "recent5_minutes_max": _r(max((m for m in m5 if m is not None), default=None)),
        "recent5_minutes_min": _r(min((m for m in m5 if m is not None), default=None)),
        "recent10_blowout_rate": _r(blowout_rate),
    }

    return apply_injury_feature_values(
        row_obj,
        team_vacancy_stats=team_vacancy_stats,
        same_pos_vacancy_stats=same_pos_vacancy_stats_by_group.get(target_pos_group),
        teammate_onoff_stats=teammate_onoff_stats,
    )


def _build_regression_row(
    player_history: List[Tuple[date, Dict[str, Any]]],
    game_idx: int,
    stat_type: str,
    min_prior: int,
    min_minutes: float,
    enrichment: EnrichmentData,
    team_missing_stats_dict: Dict[Tuple[str, date], Dict[str, float]],
    same_pos_missing_stats_dict: Dict[Tuple[str, date, str], Dict[str, float]],
    team_missing_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    team_active_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    team_presence_index: Dict[Tuple[str, date], set[str]],
    team_game_dates_by_season: Dict[Tuple[str, int], List[date]],
) -> Optional[Dict[str, Any]]:
    target_date, target_row = player_history[game_idx]
    target_mins = _safe_float(target_row.get("MIN"))
    if target_mins is None or target_mins < min_minutes:
        return None

    actual = _sum_stat(target_row, stat_type)
    if actual is None:
        return None

    team = str(target_row.get("TEAM_ABBREVIATION") or "").strip()
    target_season_key = season_key_for_date(target_date)
    prior_history = player_history[:game_idx]
    prior_rows = [row for _, row in prior_history]
    prior_same_team_season_rows = [
        row
        for game_date, row in prior_history
        if (
            str(row.get("TEAM_ABBREVIATION") or "").strip() == team
            and game_date is not None
            and season_key_for_date(game_date) == target_season_key
        )
    ]
    if len(prior_same_team_season_rows) < min_prior:
        return None
    prior_feature_rows = prior_same_team_season_rows

    # ── Rolling stat values ────────────────────────────────────────────────
    vals = [_sum_stat(r, stat_type) for r in prior_feature_rows]
    v3, v5, v10, v20 = vals[-3:], vals[-5:], vals[-10:], vals[-20:]
    s_avg, r3_avg, r5_avg = _mean(vals), _mean(v3), _mean(v5)
    r10_avg, r20_avg = _mean(v10), _mean(v20)
    s_std, r5_std, r10_std = _std(vals), _std(v5), _std(v10)
    r5_ema = _ema(v5)

    home_vals = [
        _sum_stat(r, stat_type)
        for r in prior_feature_rows
        if _parse_matchup(r.get("MATCHUP"), str(r.get("TEAM_ABBREVIATION")).strip())[1] == 1
    ]
    away_vals = [
        _sum_stat(r, stat_type)
        for r in prior_feature_rows
        if _parse_matchup(r.get("MATCHUP"), str(r.get("TEAM_ABBREVIATION")).strip())[1] == 0
    ]
    home_avg = _mean(home_vals)
    away_avg = _mean(away_vals)

    mom_5v20 = None if r5_avg is None or r20_avg is None else r5_avg - r20_avg
    mom_3v10 = None if r3_avg is None or r10_avg is None else r3_avg - r10_avg
    r5_cv = None if r5_avg is None or r5_std is None or abs(r5_avg) < 0.01 else r5_std / abs(r5_avg)
    r10_cv = None if r10_avg is None or r10_std is None or abs(r10_avg) < 0.01 else r10_std / abs(r10_avg)

    # ── Minutes ───────────────────────────────────────────────────────────
    mins_all = [_safe_float(r.get("MIN")) for r in prior_feature_rows]
    m3  = [_safe_float(r.get("MIN")) for r in prior_feature_rows[-3:]]
    m5  = [_safe_float(r.get("MIN")) for r in prior_feature_rows[-5:]]
    m10 = [_safe_float(r.get("MIN")) for r in prior_feature_rows[-10:]]
    m20 = [_safe_float(r.get("MIN")) for r in prior_feature_rows[-20:]]
    m5_avg, m20_avg, m5_std = _mean(m5), _mean(m20), _std(m5)
    min_trend = None if m5_avg is None or m20_avg is None else m5_avg - m20_avg
    min_cv    = None if m5_avg is None or m5_std is None or abs(m5_avg) < 0.01 else m5_std / abs(m5_avg)

    # ── Rest ──────────────────────────────────────────────────────────────
    last_date = next((game_date for game_date, _ in reversed(prior_history) if game_date is not None), None)
    days_rest = (target_date - last_date).days if last_date else None

    # ── Opponent / home ───────────────────────────────────────────────────
    opp, is_home = _parse_matchup(target_row.get("MATCHUP"), team)

    # ── First-half share ─────────────────────────────────────────────────
    h1_shares = []
    for r in prior_feature_rows[-5:]:
        full_val = _sum_stat(r, stat_type)
        half_val = _1h_stat(r, stat_type)
        if full_val is not None and half_val is not None and full_val > 0:
            h1_shares.append(half_val / full_val)
    r5_1h_share = _mean(h1_shares)

    # ── Enrichment features ───────────────────────────────────────────────
    # Gamelogs don't carry PLAYER_NAME — resolve from season_stats by PLAYER_ID
    player_id_str = str(target_row.get("PLAYER_ID") or "").strip()
    player_name = enrichment.player_id_to_name.get(player_id_str, "")
    position = enrichment.get_player_position(
        player_id_str,
        team_abbrev=team,
        game_date=target_date,
    )
    target_pos_group = enrichment.get_player_position_group(
        player_id_str,
        team_abbrev=team,
        game_date=target_date,
    )

    opp_ranks   = enrichment.get_opp_def_ranks(opp)
    opp_zones   = enrichment.get_opp_def_zones(opp, position)
    style       = enrichment.get_player_style(player_name)
    matchup     = enrichment.get_matchup_scores(style, opp_ranks)
    team_ctx    = enrichment.get_team_context(team, opp)

    # Game margins for last 5 of this player's games (blowout context)
    recent_margins = []
    for r in prior_feature_rows[-5:]:
        gid  = str(r.get("GAME_ID") or "").strip()
        tm   = str(r.get("TEAM_ABBREVIATION") or "").strip()
        margin = enrichment.get_game_margin(gid, tm)
        if margin is not None:
            recent_margins.append(abs(margin))
    avg_margin = _mean(recent_margins)
    blowout_flag = 1 if (avg_margin is not None and avg_margin > 15) else 0
    team_missing_stats = team_missing_stats_dict.get((team, target_date))
    same_pos_missing_stats = same_pos_missing_stats_dict.get((team, target_date, target_pos_group))
    teammate_onoff_stats = _build_key_teammate_onoff_features(
        player_history,
        game_idx,
        stat_type=stat_type,
        target_pos_group=target_pos_group,
        team_presence_index=team_presence_index,
        team_game_dates_by_season=team_game_dates_by_season,
        current_missing_player_priors=team_missing_player_priors_dict.get((team, target_date)),
        current_active_player_priors=team_active_player_priors_dict.get((team, target_date)),
    )

    # ── Pace- adjusted form ──
    # Pace is roughly possessions per 48 min. We want PTS per 100 possessions.
    # pts_per_100 = (pts / mins) * 48 * 100 / pace
    # We'll use recent 5 games for this
    r5_pts_per100 = None
    if stat_type in ["PTS", "AST", "REB"] and r5_avg is not None and m5_avg is not None and m5_avg > 0:
        pace_recent = []
        for r in prior_feature_rows[-5:]:
            opp_tm, _ = _parse_matchup(r.get("MATCHUP"), team)
            tm_ctx = enrichment.get_team_context(team, opp_tm)
            if tm_ctx.get("team_pace") is not None:
                pace_recent.append(tm_ctx.get("team_pace"))
        avg_pace = _mean(pace_recent)
        if avg_pace and avg_pace > 0:
            r5_pts_per100 = (r5_avg / m5_avg) * 48.0 * 100.0 / avg_pace

    recent10_target_per_min = _mean([_target_per_min(r, stat_type) for r in prior_feature_rows[-10:]])
    same_pos_missing_minutes = (
        _safe_float((same_pos_missing_stats or {}).get("missing_same_pos_minutes")) or 0.0
    )

    row_obj = {
        # Identity
        "game_date": target_date.isoformat(),
        "player_id": str(target_row.get("PLAYER_ID") or "").strip(),
        "team": team,
        "opponent": opp,
        "game_id": str(target_row.get("GAME_ID") or "").strip() or None,
        "stat_type": stat_type,
        "is_home": is_home,
        # Target
        "actual_value": _r(actual),
        # Player form
        "prior_games": len(prior_rows),
        "days_rest": days_rest,
        "is_b2b": 1 if days_rest == 1 else 0,
        "season_stat_avg": _r(s_avg),
        "recent3_stat_avg": _r(r3_avg),
        "recent5_stat_avg": _r(r5_avg),
        "recent10_stat_avg": _r(r10_avg),
        "recent20_stat_avg": _r(r20_avg),
        "recent5_stat_ema": _r(r5_ema),
        "season_home_stat_avg": _r(home_avg),
        "season_away_stat_avg": _r(away_avg),
        "season_stat_std": _r(s_std),
        "recent5_stat_std": _r(r5_std),
        "recent10_stat_std": _r(r10_std),
        "momentum_5v20": _r(mom_5v20),
        "momentum_3v10": _r(mom_3v10),
        "recent5_cv": _r(r5_cv),
        "recent10_cv": _r(r10_cv),
        "season_minutes_avg": _r(_mean(mins_all)),
        "recent3_minutes_avg": _r(_mean(m3)),
        "recent5_minutes_avg": _r(m5_avg),
        "recent10_minutes_avg": _r(_mean(m10)),
        "minutes_trend_5v20": _r(min_trend),
        "minutes_cv_recent5": _r(min_cv),
        "season_usage_pct_avg": _r(_mean([_safe_float(r.get("USG_PCT")) for r in prior_feature_rows])),
        "recent10_usage_pct_avg": _r(_mean([_safe_float(r.get("USG_PCT")) for r in prior_feature_rows[-10:]])),
        "season_ast_pct_avg": _r(_mean([_safe_float(r.get("AST_PCT")) for r in prior_feature_rows])),
        "recent10_ast_pct_avg": _r(_mean([_safe_float(r.get("AST_PCT")) for r in prior_feature_rows[-10:]])),
        "season_reb_pct_avg": _r(_mean([_safe_float(r.get("REB_PCT")) for r in prior_feature_rows])),
        "recent10_reb_pct_avg": _r(_mean([_safe_float(r.get("REB_PCT")) for r in prior_feature_rows[-10:]])),
        "season_ts_pct_avg": _r(_mean([_safe_float(r.get("TS_PCT")) for r in prior_feature_rows])),
        "recent10_ts_pct_avg": _r(_mean([_safe_float(r.get("TS_PCT")) for r in prior_feature_rows[-10:]])),
        "season_potential_ast_rate": _r(_mean([_metric_rate(r, "POTENTIAL_AST") for r in prior_feature_rows])),
        "recent10_potential_ast_rate": _r(_mean([_metric_rate(r, "POTENTIAL_AST") for r in prior_feature_rows[-10:]])),
        "season_reb_chance_rate": _r(_mean([_metric_rate(r, "REB_CHANCES") for r in prior_feature_rows])),
        "recent10_reb_chance_rate": _r(_mean([_metric_rate(r, "REB_CHANCES") for r in prior_feature_rows[-10:]])),
        "season_drive_rate": _r(_mean([_metric_rate(r, "DRIVES") for r in prior_feature_rows])),
        "recent10_drive_rate": _r(_mean([_metric_rate(r, "DRIVES") for r in prior_feature_rows[-10:]])),
        "season_fg3a_rate": _r(_mean([_metric_rate(r, "FG3A") for r in prior_feature_rows])),
        "recent10_fg3a_rate": _r(_mean([_metric_rate(r, "FG3A") for r in prior_feature_rows[-10:]])),
        "recent5_pts_per100": _r(r5_pts_per100),
        "recent10_target_per_min": _r(recent10_target_per_min),
        "missing_same_pos_minutes_x_player_target_per_min": _r(
            same_pos_missing_minutes * (recent10_target_per_min or 0.0)
        ),
        "missing_playmaker_potential_ast_pg_x_player_target_per_min": _r(
            (_safe_float((team_missing_stats or {}).get("missing_playmaker_potential_ast_pg")) or 0.0)
            * (recent10_target_per_min or 0.0)
        ),
        "missing_onball_drives_pg_x_player_target_per_min": _r(
            (_safe_float((team_missing_stats or {}).get("missing_onball_drives_pg")) or 0.0)
            * (recent10_target_per_min or 0.0)
        ),
        "recent5_1h_stat_share": _r(r5_1h_share),
        # Opponent defense (v2)
        **opp_ranks,
        **opp_zones,
        # Player style (v2)
        **style,
        # Matchup scores (v2)
        **matchup,
        # Context (v2)
        **team_ctx,
        "recent5_avg_game_margin": _r(avg_margin),
        "recent5_blowout_flag": blowout_flag,
    }
    return apply_injury_feature_values(
        row_obj,
        team_vacancy_stats=team_missing_stats,
        same_pos_vacancy_stats=same_pos_missing_stats,
        teammate_onoff_stats=teammate_onoff_stats,
    )


def _build_missing_team_stats(
    all_rows: List[Tuple[date, str, Dict[str, Any]]],
    enrichment: EnrichmentData,
    lookback_days: int = ACTIVE_ROSTER_LOOKBACK_DAYS,
) -> Tuple[
    Dict[Tuple[str, date], Dict[str, float]],
    Dict[Tuple[str, date, str], Dict[str, float]],
    Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
]:
    games_by_date_team: Dict[Tuple[date, str], List[Tuple[str, Dict[str, Any]]]] = defaultdict(list)
    teams_by_date: Dict[date, set[str]] = defaultdict(set)
    for gd, pid, row in all_rows:
        team = str(row.get("TEAM_ABBREVIATION") or "").strip()
        if not team:
            continue
        games_by_date_team[(gd, team)].append((pid, row))
        teams_by_date[gd].add(team)

    team_active_roster: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    player_current_team: Dict[str, str] = {}
    team_missing_stats_dict: Dict[Tuple[str, date], Dict[str, float]] = {}
    same_pos_missing_stats_dict: Dict[Tuple[str, date, str], Dict[str, float]] = {}
    team_missing_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]] = {}
    team_active_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]] = {}

    for current_date in sorted(teams_by_date):
        for team in sorted(teams_by_date[current_date]):
            roster_state = team_active_roster[team]
            actual_players = {
                pid
                for pid, row in games_by_date_team[(current_date, team)]
                if (_safe_float(row.get("MIN")) or 0.0) > 0.0
            }

            team_stats = make_team_vacancy_stats()
            same_pos_stats = _empty_same_pos_by_group()
            stale_players: List[str] = []
            current_missing_player_priors: Dict[str, Dict[str, Any]] = {}
            current_active_player_priors: Dict[str, Dict[str, Any]] = {}

            for pid, state in list(roster_state.items()):
                days_since_last = (current_date - state["last_active_date"]).days
                if days_since_last > lookback_days:
                    stale_players.append(pid)
                    continue
                if pid in actual_players:
                    active_priors = _teammate_priors_with_position(state, current_date)
                    if any(
                        active_priors.get(metric_key) is not None
                        for metric_key in ("usage_pct", "minutes", "ast_pct", "potential_ast_pg", "drives_pg")
                    ):
                        current_active_player_priors[pid] = active_priors
                    continue

                priors = _compute_absent_player_priors(state, current_date)
                avg_usg = priors["usage_pct"]
                avg_minutes = priors["minutes"]
                pos_group = state["pos_group"]
                if avg_usg is None and avg_minutes is None:
                    continue
                if not is_key_teammate(
                    avg_usg,
                    avg_minutes,
                    priors["ast_pct"],
                    priors["potential_ast_pg"],
                    priors["drives_pg"],
                ):
                    continue
                current_missing_player_priors[pid] = {
                    **priors,
                    "pos_group": pos_group,
                }

                team_stats["missing_team_usage_pct"] += avg_usg or 0.0
                team_stats["missing_team_minutes"] += avg_minutes or 0.0

                same_pos_stats[pos_group]["missing_same_pos_usage_pct"] += avg_usg or 0.0
                same_pos_stats[pos_group]["missing_same_pos_minutes"] += avg_minutes or 0.0

                if pos_group == "G":
                    team_stats["missing_guard_usage_pct"] += avg_usg or 0.0
                    team_stats["missing_guard_minutes"] += avg_minutes or 0.0

                if is_high_usage(priors["usage_pct"]):
                    team_stats["missing_high_usage_usage_pct"] += priors["usage_pct"] or 0.0
                    team_stats["missing_high_usage_minutes"] += avg_minutes or 0.0

                if is_playmaker(priors["ast_pct"], priors["potential_ast_pg"]):
                    team_stats["missing_playmaker_potential_ast_pg"] += priors["potential_ast_pg"] or 0.0
                    team_stats["missing_playmaker_minutes"] += avg_minutes or 0.0

                if is_onball(priors["drives_pg"]):
                    team_stats["missing_onball_drives_pg"] += priors["drives_pg"] or 0.0
                    team_stats["missing_onball_minutes"] += avg_minutes or 0.0

            for pid in stale_players:
                roster_state.pop(pid, None)

            team_missing_stats_dict[(team, current_date)] = {
                key: _r(value) or 0.0
                for key, value in team_stats.items()
            }
            team_missing_player_priors_dict[(team, current_date)] = current_missing_player_priors
            team_active_player_priors_dict[(team, current_date)] = current_active_player_priors
            for pos_group, pos_stats in same_pos_stats.items():
                same_pos_missing_stats_dict[(team, current_date, pos_group)] = {
                    key: _r(value) or 0.0
                    for key, value in pos_stats.items()
                }

            for pid, row in games_by_date_team[(current_date, team)]:
                mins = _safe_float(row.get("MIN"))
                if mins is None or mins <= 0:
                    continue

                old_team = player_current_team.get(pid)
                if old_team and old_team != team:
                    team_active_roster.get(old_team, {}).pop(pid, None)
                player_current_team[pid] = team

                state = roster_state.setdefault(
                    pid,
                    {
                        "last_active_date": current_date,
                        "pos_group": enrichment.get_player_position_group(
                            pid,
                            team_abbrev=team,
                            game_date=current_date,
                        ),
                        "usg_history": [],
                        "ast_pct_history": [],
                        "potential_ast_history": [],
                        "drive_history": [],
                        "min_history": [],
                    },
                )
                usg = _safe_float(row.get("USG_PCT"))
                ast_pct = _safe_float(row.get("AST_PCT"))
                potential_ast = _safe_float(row.get("POTENTIAL_AST"))
                drives = _safe_float(row.get("DRIVES"))
                if usg is not None:
                    state["usg_history"].append((current_date, usg))
                if ast_pct is not None:
                    state["ast_pct_history"].append((current_date, ast_pct))
                if potential_ast is not None:
                    state["potential_ast_history"].append((current_date, potential_ast))
                if drives is not None:
                    state["drive_history"].append((current_date, drives))
                state["min_history"].append((current_date, mins))
                state["last_active_date"] = current_date
                state["pos_group"] = enrichment.get_player_position_group(
                    pid,
                    team_abbrev=team,
                    game_date=current_date,
                )

                if len(state["usg_history"]) > 25:
                    state["usg_history"] = state["usg_history"][-25:]
                if len(state["ast_pct_history"]) > 25:
                    state["ast_pct_history"] = state["ast_pct_history"][-25:]
                if len(state["potential_ast_history"]) > 25:
                    state["potential_ast_history"] = state["potential_ast_history"][-25:]
                if len(state["drive_history"]) > 25:
                    state["drive_history"] = state["drive_history"][-25:]
                if len(state["min_history"]) > 25:
                    state["min_history"] = state["min_history"][-25:]

    return (
        team_missing_stats_dict,
        same_pos_missing_stats_dict,
        team_missing_player_priors_dict,
        team_active_player_priors_dict,
    )


def _prepare_dataset_context(
    gamelog_paths: Sequence[Path],
    data_dir: Path,
) -> Dict[str, Any]:
    print("Loading enrichment data...")
    enrichment = EnrichmentData(data_dir)

    print("\nLoading game logs...")
    active_rows = _build_history(gamelog_paths)
    full_boxscore_rows = _build_history(gamelog_paths, include_zero_minutes=True)
    enrichment.ingest_log_positions(full_boxscore_rows)
    print(f"  {len(active_rows):,} active game-log entries")
    print(f"  {len(full_boxscore_rows):,} boxscore-present entries")

    active_player_index = _build_player_index(active_rows)
    full_player_index = _build_player_index(full_boxscore_rows)
    print(f"  {len(active_player_index):,} unique active players")
    print(f"  {len(full_player_index):,} unique boxscore players")

    team_presence_index, team_game_dates_by_season = _build_team_presence_context(active_rows)

    print("Pre-computing archetype-aware missing teammate vacancy features...")
    (
        team_missing_stats_dict,
        same_pos_missing_stats_dict,
        team_missing_player_priors_dict,
        team_active_player_priors_dict,
    ) = _build_missing_team_stats(active_rows, enrichment)
    populated_missing_games = sum(
        1
        for stats_map in team_missing_stats_dict.values()
        if any((stats_map.get(column) or 0.0) > 0.0 for column in TEAM_VACANCY_FEATURE_COLUMNS)
    )
    print(f"  Populated missing-player features for {populated_missing_games:,} team-games")

    return {
        "enrichment": enrichment,
        "active_rows": active_rows,
        "full_boxscore_rows": full_boxscore_rows,
        "active_player_index": active_player_index,
        "full_player_index": full_player_index,
        "team_presence_index": team_presence_index,
        "team_game_dates_by_season": team_game_dates_by_season,
        "team_missing_stats_dict": team_missing_stats_dict,
        "same_pos_missing_stats_dict": same_pos_missing_stats_dict,
        "team_missing_player_priors_dict": team_missing_player_priors_dict,
        "team_active_player_priors_dict": team_active_player_priors_dict,
        "populated_missing_games": populated_missing_games,
    }


def build_regression_dataset(
    gamelog_paths: Sequence[Path],
    stat_types: Sequence[str],
    min_prior_games: int,
    min_minutes: float,
    data_dir: Path,
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    context = context or _prepare_dataset_context(gamelog_paths, data_dir)
    enrichment = context["enrichment"]
    player_index = context["active_player_index"]
    team_presence_index = context["team_presence_index"]
    team_game_dates_by_season = context["team_game_dates_by_season"]
    team_missing_stats_dict = context["team_missing_stats_dict"]
    same_pos_missing_stats_dict = context["same_pos_missing_stats_dict"]
    team_missing_player_priors_dict = context["team_missing_player_priors_dict"]
    team_active_player_priors_dict = context["team_active_player_priors_dict"]

    output_rows: List[Dict[str, Any]] = []
    stats = {"total_candidates": 0, "written_rows": 0,
             "skipped_too_few_prior": 0, "skipped_low_minutes": 0, "skipped_missing_stat": 0}

    for pid, history in player_index.items():
        for game_idx in range(len(history)):
            for stat_type in stat_types:
                stats["total_candidates"] += 1
                row = _build_regression_row(
                    history, game_idx, stat_type,
                    min_prior=min_prior_games,
                    min_minutes=min_minutes,
                    enrichment=enrichment,
                    team_missing_stats_dict=team_missing_stats_dict,
                    same_pos_missing_stats_dict=same_pos_missing_stats_dict,
                    team_missing_player_priors_dict=team_missing_player_priors_dict,
                    team_active_player_priors_dict=team_active_player_priors_dict,
                    team_presence_index=team_presence_index,
                    team_game_dates_by_season=team_game_dates_by_season,
                )
                if row is None:
                    if game_idx < min_prior_games:
                        stats["skipped_too_few_prior"] += 1
                    else:
                        mins = _safe_float(history[game_idx][1].get("MIN"))
                        if mins is not None and mins < min_minutes:
                            stats["skipped_low_minutes"] += 1
                        else:
                            stats["skipped_missing_stat"] += 1
                    continue
                output_rows.append(row)

    stats["written_rows"] = len(output_rows)
    output_rows.sort(key=lambda r: r["game_date"])
    return output_rows, stats


def build_minutes_dataset(
    *,
    full_player_index: Dict[str, List[Tuple[date, Dict[str, Any]]]],
    active_player_index: Dict[str, List[Tuple[date, Dict[str, Any]]]],
    min_prior_games: int,
    enrichment: EnrichmentData,
    team_missing_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    team_active_player_priors_dict: Dict[Tuple[str, date], Dict[str, Dict[str, Any]]],
    team_presence_index: Dict[Tuple[str, date], set[str]],
    team_game_dates_by_season: Dict[Tuple[str, int], List[date]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    output_rows: List[Dict[str, Any]] = []
    stats = {
        "total_candidates": 0,
        "written_rows": 0,
        "skipped_too_few_prior": 0,
        "skipped_missing_target_minutes": 0,
    }

    for pid, full_history in full_player_index.items():
        active_history = active_player_index.get(pid, [])
        active_cursor = 0

        for target_date, target_row in full_history:
            stats["total_candidates"] += 1

            while active_cursor < len(active_history) and active_history[active_cursor][0] < target_date:
                active_cursor += 1

            prior_active_history = active_history[:active_cursor]
            player_history = [*prior_active_history, (target_date, target_row)]
            row = _build_minutes_row(
                player_history,
                len(player_history) - 1,
                min_prior=min_prior_games,
                enrichment=enrichment,
                team_missing_player_priors_dict=team_missing_player_priors_dict,
                team_active_player_priors_dict=team_active_player_priors_dict,
                team_presence_index=team_presence_index,
                team_game_dates_by_season=team_game_dates_by_season,
            )
            if row is None:
                target_minutes = _safe_float(target_row.get("MIN"))
                if target_minutes is None or target_minutes < 0:
                    stats["skipped_missing_target_minutes"] += 1
                else:
                    stats["skipped_too_few_prior"] += 1
                continue
            output_rows.append(row)

    stats["written_rows"] = len(output_rows)
    output_rows.sort(key=lambda r: r["game_date"])
    return output_rows, stats


def main() -> int:
    args = _parse_args()
    gamelog_paths = [Path(p) for p in args.gamelogs]
    output_csv = Path(args.output_csv)
    minutes_output_csv = Path(args.minutes_output_csv)
    data_dir = Path(args.data_dir)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    context = _prepare_dataset_context(gamelog_paths, data_dir)
    rows, stats = build_regression_dataset(
        gamelog_paths=gamelog_paths,
        stat_types=args.stat_types,
        min_prior_games=args.min_prior_games,
        min_minutes=args.min_minutes,
        data_dir=data_dir,
        context=context,
    )
    minutes_rows, minutes_stats = build_minutes_dataset(
        full_player_index=context["full_player_index"],
        active_player_index=context["active_player_index"],
        min_prior_games=args.minutes_min_prior_games,
        enrichment=context["enrichment"],
        team_missing_player_priors_dict=context["team_missing_player_priors_dict"],
        team_active_player_priors_dict=context["team_active_player_priors_dict"],
        team_presence_index=context["team_presence_index"],
        team_game_dates_by_season=context["team_game_dates_by_season"],
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REGRESSION_DATASET_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in REGRESSION_DATASET_COLUMNS})

    minutes_output_csv.parent.mkdir(parents=True, exist_ok=True)
    with minutes_output_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MINUTES_DATASET_COLUMNS)
        writer.writeheader()
        for row in minutes_rows:
            writer.writerow({k: row.get(k) for k in MINUTES_DATASET_COLUMNS})

    print(f"\nwrote_rows={stats['written_rows']:,}")
    print(f"total_candidates={stats['total_candidates']:,}")
    print(f"skipped_too_few_prior={stats['skipped_too_few_prior']:,}")
    print(f"skipped_low_minutes={stats['skipped_low_minutes']:,}")
    print(f"skipped_missing_stat={stats['skipped_missing_stat']:,}")
    print(f"output_csv={output_csv}")

    print(f"\nminutes_wrote_rows={minutes_stats['written_rows']:,}")
    print(f"minutes_total_candidates={minutes_stats['total_candidates']:,}")
    print(f"minutes_skipped_too_few_prior={minutes_stats['skipped_too_few_prior']:,}")
    print(
        "minutes_skipped_missing_target_minutes="
        f"{minutes_stats['skipped_missing_target_minutes']:,}"
    )
    print(f"minutes_output_csv={minutes_output_csv}")

    if rows:
        from collections import Counter
        st_counts = Counter(r["stat_type"] for r in rows)
        print(f"\nRows per stat type:")
        for st, count in sorted(st_counts.items(), key=lambda x: -x[1]):
            print(f"  {st}: {count:,}")
        dates = sorted(set(r["game_date"] for r in rows))
        print(f"\nDate range: {dates[0]} → {dates[-1]} ({len(dates)} dates)")

        # Report enrichment fill rates for new columns
        new_cols = ["opp_pts_defense_rank", "player_catchAndShoot_pg",
                    "matchup_score_fg3", "team_pace", "recent5_avg_game_margin"]
        print("\nEnrichment fill rates:")
        for col in new_cols:
            filled = sum(1 for r in rows if r.get(col) is not None)
            print(f"  {col}: {filled/len(rows)*100:.1f}% filled ({filled:,}/{len(rows):,})")

        injury_cols = [
            "missing_team_usage_pct",
            "missing_team_minutes",
            "missing_same_pos_minutes",
            "missing_guard_minutes",
            "missing_key_teammate_count",
            "missing_key_teammates_player_minutes_delta",
        ]
        print("\nInjury feature activation rates:")
        for col in injury_cols:
            active = sum(1 for r in rows if abs(float(r.get(col) or 0.0)) > 1e-9)
            print(f"  {col}: {active/len(rows)*100:.1f}% active ({active:,}/{len(rows):,})")

    if minutes_rows:
        dates = sorted(set(r["game_date"] for r in minutes_rows))
        print(f"\nMinutes date range: {dates[0]} → {dates[-1]} ({len(dates)} dates)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
