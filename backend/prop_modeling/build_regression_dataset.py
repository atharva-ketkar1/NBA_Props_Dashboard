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
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from feature_schema import (
    DEFAULT_GAMELOG_PATHS,
    GENERATED_DIR,
    STAT_COLUMNS,
    BACKEND_DIR,
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
]

DEFAULT_REGRESSION_DATASET_PATH = GENERATED_DIR / "regression_training_dataset.csv"
CURRENT_DATA_DIR = BACKEND_DIR / "data" / "current"

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

    def __init__(self, data_dir: Path) -> None:
        self.opp_def_ranks: Dict[str, Dict] = {}       # team_full → {catchAndShoot, pullups, lessThanTenFeet}
        self.opp_def_zones: Dict[str, Dict] = {}       # team_full → {G/F/C → zone → {percentage, rank}}
        self.play_type_players: Dict[str, Dict] = {}   # player_name → {points → {transition, iso, ...}}
        self.shot_type_players: Dict[str, Dict] = {}   # player_name → {catchAndShoot, pullups, lessThan10ft, matchesPlayed}
        self.game_margins: Dict[str, Dict[str, float]] = {}  # game_id → {team_abbrev → margin}
        self.season_stats: Dict[str, Dict] = {}        # player_id_str → {PACE, DEF_RATING, ...}
        self.team_season_stats: Dict[str, Dict] = {}   # team_abbrev → aggregated stats

        self._load(data_dir)

    def _load(self, data_dir: Path) -> None:
        # 1. Opponent defensive ranks
        p = data_dir / "opponent_defensive_ranks.json"
        if p.exists():
            raw = json.loads(p.read_text())
            for team, v in raw.items():
                self.opp_def_ranks[team] = v.get("rankings", {})
            print(f"  opp_def_ranks: {len(self.opp_def_ranks)} teams")

        # 2. Opponent defensive zones
        p = data_dir / "opp_def_zones.json"
        if p.exists():
            self.opp_def_zones = json.loads(p.read_text())
            print(f"  opp_def_zones: {len(self.opp_def_zones)} teams")

        # 3. Play type analysis
        p = data_dir / "play_type_analysis.json"
        if p.exists():
            raw = json.loads(p.read_text())
            self.play_type_players = raw.get("players", {})
            print(f"  play_type: {len(self.play_type_players)} players")

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
            print(f"  shot_type: {len(self.shot_type_players)} players")

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
            print(f"  boxscores: {len(self.game_margins)} games with margins")

        # 6. Season stats CSV
        self.player_id_to_name: Dict[str, str] = {}  # pid_str → player name
        p = data_dir / "season_stats.csv"
        if p.exists():
            with p.open(newline="") as f:
                for row in csv.DictReader(f):
                    pid = str(row.get("PLAYER_ID") or "").strip()
                    name = str(row.get("PLAYER_NAME") or "").strip()
                    team = str(row.get("TEAM_ABBREVIATION") or "").strip()
                    if pid:
                        self.season_stats[pid] = row
                        if name:
                            self.player_id_to_name[pid] = name
                    if team:
                        # Use last entry per team (season averages are player-level,
                        # we want team-level pace/rating — take from any player)
                        if team not in self.team_season_stats:
                            self.team_season_stats[team] = row
            print(f"  season_stats: {len(self.season_stats)} players, {len(self.team_season_stats)} teams, {len(self.player_id_to_name)} named")

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
        pos_group = POSITION_GROUP.get((position or "").upper(), "G")
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
    parser.add_argument("--min-prior-games", type=int, default=5)
    parser.add_argument("--min-minutes", type=float, default=10.0)
    parser.add_argument("--stat-types", nargs="*",
                        default=["PTS", "AST", "REB", "FG3M", "STL", "BLK",
                                 "PTS+REB+AST", "PTS+REB", "PTS+AST", "REB+AST", "STL+BLK"])
    parser.add_argument("--data-dir", default=str(CURRENT_DATA_DIR))
    return parser.parse_args()


def _build_history(gamelog_paths: Sequence[Path]) -> List[Tuple[date, str, Dict[str, Any]]]:
    rows: List[Tuple[date, str, Dict[str, Any]]] = []
    for path in gamelog_paths:
        if not path.exists():
            continue
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                pid = str(row.get("PLAYER_ID") or "").strip()
                gd = _parse_date(row.get("GAME_DATE"))
                mins = _safe_float(row.get("MIN"))
                if not pid or gd is None or mins is None or mins <= 0:
                    continue
                rows.append((gd, pid, row))
    rows.sort(key=lambda x: x[0])
    return rows


def _build_player_index(
    all_rows: List[Tuple[date, str, Dict[str, Any]]],
) -> Dict[str, List[Tuple[date, Dict[str, Any]]]]:
    index: Dict[str, List[Tuple[date, Dict[str, Any]]]] = defaultdict(list)
    for gd, pid, row in all_rows:
        index[pid].append((gd, row))
    return dict(index)


def _build_regression_row(
    player_history: List[Tuple[date, Dict[str, Any]]],
    game_idx: int,
    stat_type: str,
    min_prior: int,
    min_minutes: float,
    enrichment: EnrichmentData,
) -> Optional[Dict[str, Any]]:
    target_date, target_row = player_history[game_idx]
    target_mins = _safe_float(target_row.get("MIN"))
    if target_mins is None or target_mins < min_minutes:
        return None

    actual = _sum_stat(target_row, stat_type)
    if actual is None:
        return None

    prior_rows = [row for _, row in player_history[:game_idx]]
    if len(prior_rows) < min_prior:
        return None

    # ── Rolling stat values ────────────────────────────────────────────────
    vals = [_sum_stat(r, stat_type) for r in prior_rows]
    v3, v5, v10, v20 = vals[-3:], vals[-5:], vals[-10:], vals[-20:]
    s_avg, r3_avg, r5_avg = _mean(vals), _mean(v3), _mean(v5)
    r10_avg, r20_avg = _mean(v10), _mean(v20)
    s_std, r5_std, r10_std = _std(vals), _std(v5), _std(v10)
    r5_ema = _ema(v5)

    home_vals = [_sum_stat(r, stat_type) for r in prior_rows if _parse_matchup(r.get("MATCHUP"), str(r.get("TEAM_ABBREVIATION")).strip())[1] == 1]
    away_vals = [_sum_stat(r, stat_type) for r in prior_rows if _parse_matchup(r.get("MATCHUP"), str(r.get("TEAM_ABBREVIATION")).strip())[1] == 0]
    home_avg = _mean(home_vals)
    away_avg = _mean(away_vals)

    mom_5v20 = None if r5_avg is None or r20_avg is None else r5_avg - r20_avg
    mom_3v10 = None if r3_avg is None or r10_avg is None else r3_avg - r10_avg
    r5_cv = None if r5_avg is None or r5_std is None or abs(r5_avg) < 0.01 else r5_std / abs(r5_avg)
    r10_cv = None if r10_avg is None or r10_std is None or abs(r10_avg) < 0.01 else r10_std / abs(r10_avg)

    # ── Minutes ───────────────────────────────────────────────────────────
    mins_all = [_safe_float(r.get("MIN")) for r in prior_rows]
    m3  = [_safe_float(r.get("MIN")) for r in prior_rows[-3:]]
    m5  = [_safe_float(r.get("MIN")) for r in prior_rows[-5:]]
    m10 = [_safe_float(r.get("MIN")) for r in prior_rows[-10:]]
    m20 = [_safe_float(r.get("MIN")) for r in prior_rows[-20:]]
    m5_avg, m20_avg, m5_std = _mean(m5), _mean(m20), _std(m5)
    min_trend = None if m5_avg is None or m20_avg is None else m5_avg - m20_avg
    min_cv    = None if m5_avg is None or m5_std is None or abs(m5_avg) < 0.01 else m5_std / abs(m5_avg)

    # ── Rest ──────────────────────────────────────────────────────────────
    prior_dates = [_parse_date(r.get("GAME_DATE")) for r in prior_rows]
    last_date = next((d for d in reversed(prior_dates) if d is not None), None)
    days_rest = (target_date - last_date).days if last_date else None

    # ── Opponent / home ───────────────────────────────────────────────────
    team = str(target_row.get("TEAM_ABBREVIATION") or "").strip()
    opp, is_home = _parse_matchup(target_row.get("MATCHUP"), team)

    # ── First-half share ─────────────────────────────────────────────────
    h1_shares = []
    for r in prior_rows[-5:]:
        full_val = _sum_stat(r, stat_type)
        half_val = _1h_stat(r, stat_type)
        if full_val is not None and half_val is not None and full_val > 0:
            h1_shares.append(half_val / full_val)
    r5_1h_share = _mean(h1_shares)

    # ── Enrichment features ───────────────────────────────────────────────
    # Gamelogs don't carry PLAYER_NAME — resolve from season_stats by PLAYER_ID
    player_id_str = str(target_row.get("PLAYER_ID") or "").strip()
    player_name = enrichment.player_id_to_name.get(player_id_str, "")
    position    = str(target_row.get("POSITION", "")).strip() if "POSITION" in target_row else None

    opp_ranks   = enrichment.get_opp_def_ranks(opp)
    opp_zones   = enrichment.get_opp_def_zones(opp, position)
    style       = enrichment.get_player_style(player_name)
    matchup     = enrichment.get_matchup_scores(style, opp_ranks)
    team_ctx    = enrichment.get_team_context(team, opp)

    # Game margins for last 5 of this player's games (blowout context)
    recent_margins = []
    for r in prior_rows[-5:]:
        gid  = str(r.get("GAME_ID") or "").strip()
        tm   = str(r.get("TEAM_ABBREVIATION") or "").strip()
        margin = enrichment.get_game_margin(gid, tm)
        if margin is not None:
            recent_margins.append(abs(margin))
    avg_margin = _mean(recent_margins)
    blowout_flag = 1 if (avg_margin is not None and avg_margin > 15) else 0

    # ── Pace- adjusted form ──
    # Pace is roughly possessions per 48 min. We want PTS per 100 possessions.
    # pts_per_100 = (pts / mins) * 48 * 100 / pace
    # We'll use recent 5 games for this
    r5_pts_per100 = None
    if stat_type in ["PTS", "AST", "REB"] and r5_avg is not None and m5_avg is not None and m5_avg > 0:
        pace_recent = []
        for r in prior_rows[-5:]:
            opp_tm, _ = _parse_matchup(r.get("MATCHUP"), team)
            tm_ctx = enrichment.get_team_context(team, opp_tm)
            if tm_ctx.get("team_pace") is not None:
                pace_recent.append(tm_ctx.get("team_pace"))
        avg_pace = _mean(pace_recent)
        if avg_pace and avg_pace > 0:
            r5_pts_per100 = (r5_avg / m5_avg) * 48.0 * 100.0 / avg_pace

    return {
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
        "season_usage_pct_avg": _r(_mean([_safe_float(r.get("USG_PCT")) for r in prior_rows])),
        "recent10_usage_pct_avg": _r(_mean([_safe_float(r.get("USG_PCT")) for r in prior_rows[-10:]])),
        "season_ast_pct_avg": _r(_mean([_safe_float(r.get("AST_PCT")) for r in prior_rows])),
        "recent10_ast_pct_avg": _r(_mean([_safe_float(r.get("AST_PCT")) for r in prior_rows[-10:]])),
        "season_reb_pct_avg": _r(_mean([_safe_float(r.get("REB_PCT")) for r in prior_rows])),
        "recent10_reb_pct_avg": _r(_mean([_safe_float(r.get("REB_PCT")) for r in prior_rows[-10:]])),
        "season_ts_pct_avg": _r(_mean([_safe_float(r.get("TS_PCT")) for r in prior_rows])),
        "recent10_ts_pct_avg": _r(_mean([_safe_float(r.get("TS_PCT")) for r in prior_rows[-10:]])),
        "season_potential_ast_rate": _r(_mean([_metric_rate(r, "POTENTIAL_AST") for r in prior_rows])),
        "recent10_potential_ast_rate": _r(_mean([_metric_rate(r, "POTENTIAL_AST") for r in prior_rows[-10:]])),
        "season_reb_chance_rate": _r(_mean([_metric_rate(r, "REB_CHANCES") for r in prior_rows])),
        "recent10_reb_chance_rate": _r(_mean([_metric_rate(r, "REB_CHANCES") for r in prior_rows[-10:]])),
        "season_drive_rate": _r(_mean([_metric_rate(r, "DRIVES") for r in prior_rows])),
        "recent10_drive_rate": _r(_mean([_metric_rate(r, "DRIVES") for r in prior_rows[-10:]])),
        "season_fg3a_rate": _r(_mean([_metric_rate(r, "FG3A") for r in prior_rows])),
        "recent10_fg3a_rate": _r(_mean([_metric_rate(r, "FG3A") for r in prior_rows[-10:]])),
        "recent5_pts_per100": _r(r5_pts_per100),
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


def build_regression_dataset(
    gamelog_paths: Sequence[Path],
    stat_types: Sequence[str],
    min_prior_games: int,
    min_minutes: float,
    data_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    print("Loading enrichment data...")
    enrichment = EnrichmentData(data_dir)

    print("\nLoading game logs...")
    all_rows = _build_history(gamelog_paths)
    print(f"  {len(all_rows):,} total game-log entries")

    player_index = _build_player_index(all_rows)
    print(f"  {len(player_index):,} unique players")

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


def main() -> int:
    args = _parse_args()
    gamelog_paths = [Path(p) for p in args.gamelogs]
    output_csv = Path(args.output_csv)
    data_dir = Path(args.data_dir)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    rows, stats = build_regression_dataset(
        gamelog_paths=gamelog_paths,
        stat_types=args.stat_types,
        min_prior_games=args.min_prior_games,
        min_minutes=args.min_minutes,
        data_dir=data_dir,
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REGRESSION_DATASET_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in REGRESSION_DATASET_COLUMNS})

    print(f"\nwrote_rows={stats['written_rows']:,}")
    print(f"total_candidates={stats['total_candidates']:,}")
    print(f"skipped_too_few_prior={stats['skipped_too_few_prior']:,}")
    print(f"skipped_low_minutes={stats['skipped_low_minutes']:,}")
    print(f"skipped_missing_stat={stats['skipped_missing_stat']:,}")
    print(f"output_csv={output_csv}")

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
