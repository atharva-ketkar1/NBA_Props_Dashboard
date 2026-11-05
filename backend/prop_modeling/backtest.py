"""Score the prop board against what the market actually closed at.

Why this exists: the shipped model bundle has a 0.003 R-squared head, had its
hyperparameters tuned on its own holdout, and reported a headline R-squared of
0.775 that describes a *different* model than the one exported. None of that was
caught because there was no scoreboard. This module is the scoreboard.

It deliberately starts with the half that needs no model at all: join historical
sportsbook lines to actual outcomes and measure what the *market* did. That
establishes the baselines any model has to beat --

  * the de-vigged market probability (the thing to beat on log-loss),
  * the player's own recent-10 average (the thing to beat on MAE),
  * blind under (the strategy the current median/mean gap drifts toward).

Measured once already over 19,891 rows: blind unders hit 50.9% against a 53.0%
break-even, i.e. -3.8% ROI, negative on every stat after vig. So there is no
free lunch in the under lean -- which is exactly why a scoreboard has to come
before any model change.

A note on which metric to trust. At ~71.5k closing-line rows the 2-sigma band on
ROI is about +/-0.7% pooled and +/-2.1% per stat bucket, so ROI can prove a
strategy loses but cannot certify a thin edge. Log-loss against the de-vigged
market converges far faster. Gate on calibration, report ROI.

Usage:
    python3 prop_modeling/backtest.py                      # Supabase, all dates
    python3 prop_modeling/backtest.py --source local       # local JSON archive
    python3 prop_modeling/backtest.py --since 2026-10-01   # 2026-27 only
    python3 prop_modeling/backtest.py --output-csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROP_MODELING_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROP_MODELING_DIR.parent
for _p in (str(BACKEND_DIR), str(PROP_MODELING_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from feature_schema import STAT_COLUMNS  # noqa: E402
from build_prop_dataset import (  # noqa: E402
    _american_to_implied,
    _american_to_payout_decimal,
    _no_vig_probability,
)
from build_regression_dataset import _sum_stat  # noqa: E402

CURRENT_DIR = BACKEND_DIR / "data" / "current"
ARCHIVE_DIR = BACKEND_DIR / "data" / "archive"
DEFAULT_GAMELOGS = [
    ARCHIVE_DIR / "gamelogs_2024-25.csv",
    ARCHIVE_DIR / "gamelogs_2025-26.csv",
    CURRENT_DIR / "gamelogs_2026-27.csv",
]
HISTORICAL_ODDS_JSON = ARCHIVE_DIR / "historical_odds.json"
SUPABASE_TABLE = "historical_player_props"
SUPABASE_PAGE = 1000


# ---------------------------------------------------------------------------
# Metrics. Pure functions over plain sequences, so they are testable without
# pandas, sklearn, or a model. The calibration metrics live here because the
# only existing implementations are inside notebook scripts with top-level
# execution -- importing those would train models.
# ---------------------------------------------------------------------------

def log_loss(probs: Sequence[float], outcomes: Sequence[int], eps: float = 1e-15) -> Optional[float]:
    """Mean negative log likelihood. Lower is better; 0.693 is a coin flip."""
    pairs = [(p, y) for p, y in zip(probs, outcomes) if p is not None]
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        p = min(max(float(p), eps), 1.0 - eps)
        total += -(math.log(p) if y else math.log(1.0 - p))
    return total / len(pairs)


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> Optional[float]:
    pairs = [(p, y) for p, y in zip(probs, outcomes) if p is not None]
    if not pairs:
        return None
    return sum((float(p) - y) ** 2 for p, y in pairs) / len(pairs)


def reliability_table(
    probs: Sequence[float], outcomes: Sequence[int], bins: int = 10
) -> List[Dict[str, Any]]:
    """Predicted vs realised rate per probability bucket.

    A well-calibrated model has mean_pred ~= hit_rate in every row.
    """
    buckets: Dict[int, List[Tuple[float, int]]] = defaultdict(list)
    for p, y in zip(probs, outcomes):
        if p is None:
            continue
        idx = min(int(float(p) * bins), bins - 1)
        buckets[idx].append((float(p), int(y)))
    rows = []
    for idx in sorted(buckets):
        vals = buckets[idx]
        rows.append({
            "bucket": "%.1f-%.1f" % (idx / bins, (idx + 1) / bins),
            "n": len(vals),
            "mean_pred": sum(v[0] for v in vals) / len(vals),
            "hit_rate": sum(v[1] for v in vals) / len(vals),
        })
    return rows


def interval_coverage(
    actuals: Sequence[float],
    lows: Sequence[float],
    highs: Sequence[float],
    *,
    discrete: bool = True,
) -> Optional[float]:
    """Fraction of outcomes inside [low, high]; nominal 0.50 for a q25-q75 band.

    `discrete` widens the band to the enclosing integers before testing. Without
    it, BLK scores 0.132 purely as an artifact: its median IQR is 0.923, so the
    band sits strictly *between* 0 and 1 and excludes both modal outcomes, which
    are ~90% of all BLK games. The band is not mis-estimated -- it is being
    judged as if the target were continuous.
    """
    rows = [
        (a, lo, hi)
        for a, lo, hi in zip(actuals, lows, highs)
        if a is not None and lo is not None and hi is not None
    ]
    if not rows:
        return None
    hits = 0
    for a, lo, hi in rows:
        if discrete:
            lo, hi = math.floor(lo), math.ceil(hi)
        if lo <= a <= hi:
            hits += 1
    return hits / len(rows)


def roi(profits: Sequence[float]) -> Dict[str, Optional[float]]:
    """Mean profit per 1u staked, with a 2-sigma band.

    The band is the point of this function. A strategy is only distinguishable
    from noise when its ROI exceeds it, and per-stat buckets are usually too
    small for that.
    """
    vals = [float(x) for x in profits if x is not None]
    n = len(vals)
    if not n:
        return {"n": 0, "roi": None, "two_sigma": None}
    mean = sum(vals) / n
    if n < 2:
        return {"n": n, "roi": mean, "two_sigma": None}
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    return {"n": n, "roi": mean, "two_sigma": 2.0 * math.sqrt(var / n)}


# ---------------------------------------------------------------------------
# Line loading
# ---------------------------------------------------------------------------

def load_lines_supabase(since: Optional[str] = None, closing_only: bool = True) -> List[Dict[str, Any]]:
    """Page through historical_player_props.

    Preferred over the local JSON: ~75k rows versus ~27k, and it carries
    is_closing_line so the settlement reference can be filtered to genuine
    pre-tip captures.
    """
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY") or ""
    if not base or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY are required for --source supabase. "
            "Use --source local to read data/archive/historical_odds.json instead."
        )
    cols = "player_id,game_date,sportsbook,stat_type,line,over_odds,under_odds,source,is_closing_line"
    flt = ""
    if since:
        flt += "&game_date=gte." + urllib.parse.quote(since)
    if closing_only:
        flt += "&is_closing_line=is.true"
    rows: List[Dict[str, Any]] = []
    offset = 0
    while True:
        url = "%s/rest/v1/%s?select=%s%s&order=game_date.asc&limit=%d&offset=%d" % (
            base, SUPABASE_TABLE, cols, flt, SUPABASE_PAGE, offset
        )
        req = urllib.request.Request(url, headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=60) as resp:
            page = json.load(resp)
        rows.extend(page)
        if len(page) < SUPABASE_PAGE:
            break
        offset += SUPABASE_PAGE
    return rows


def load_lines_local(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the on-disk archive via the existing flattener."""
    from utils.historical_player_props import flatten_historical_date_blob

    if not HISTORICAL_ODDS_JSON.exists():
        raise RuntimeError("Missing %s" % HISTORICAL_ODDS_JSON)
    blob = json.loads(HISTORICAL_ODDS_JSON.read_text())
    rows: List[Dict[str, Any]] = []
    for game_date, date_blob in blob.items():
        if since and game_date < since:
            continue
        rows.extend(flatten_historical_date_blob(game_date, date_blob))
    return rows


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def load_outcomes(gamelog_paths: Iterable[Path]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(player_id, game_date) -> game-log row.

    Fails loudly on a missing path. The dataset builder's habit of silently
    skipping absent gamelog files is how a 2024-25-only training set can be
    produced without any warning.
    """
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    found = 0
    for path in gamelog_paths:
        if not Path(path).exists():
            print("  skip (absent): %s" % path)
            continue
        found += 1
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                gd = str(row.get("GAME_DATE") or "")[:10]
                pid = str(row.get("PLAYER_ID") or "").strip()
                if gd and pid:
                    out[(pid, gd)] = row
    if not found:
        raise RuntimeError("No gamelog files found; cannot grade anything.")
    return out


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def build_rows(
    lines: Sequence[Dict[str, Any]],
    outcomes: Dict[Tuple[str, str], Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Counter]:
    """One evaluation row per (player, date, book, stat) that can be graded."""
    rows: List[Dict[str, Any]] = []
    skips: Counter = Counter()
    for ln in lines:
        stat = str(ln.get("stat_type") or "")
        if stat not in STAT_COLUMNS:
            # canonicalize_stat_type happily emits TOV / FAN / 1Q_* which have
            # no entry in STAT_COLUMNS and cannot be graded here.
            skips["stat_not_gradeable:" + stat] += 1
            continue
        line = ln.get("line")
        if line is None:
            skips["no_line"] += 1
            continue
        pid = str(ln.get("player_id") or "").strip()
        gd = str(ln.get("game_date") or "")[:10]
        game = outcomes.get((pid, gd))
        if game is None:
            skips["no_gamelog"] += 1
            continue
        actual = _sum_stat(game, stat)
        if actual is None:
            # _sum_stat is fail-closed: a missing component column makes the row
            # ungradeable rather than silently too low. That is deliberate --
            # edge_score's variant coerces to 0.0 and would grade a short PRA.
            skips["ungradeable_stat"] += 1
            continue
        over_odds = ln.get("over_odds")
        under_odds = ln.get("under_odds")
        imp_over = _american_to_implied(over_odds)
        imp_under = _american_to_implied(under_odds)
        rows.append({
            "player_id": pid,
            "game_date": gd,
            "sportsbook": ln.get("sportsbook"),
            "stat_type": stat,
            "line": float(line),
            "actual": float(actual),
            "minutes": game.get("MIN"),
            "over_odds": over_odds,
            "under_odds": under_odds,
            "market_prob_over": _no_vig_probability(imp_over, imp_under),
            "payout_over": _american_to_payout_decimal(over_odds),
            "payout_under": _american_to_payout_decimal(under_odds),
            "source": ln.get("source"),
            "is_closing_line": ln.get("is_closing_line"),
            "result": "push" if abs(float(actual) - float(line)) < 1e-9
                      else ("over" if float(actual) > float(line) else "under"),
        })
    return rows, skips


def profit_for_side(row: Dict[str, Any], side: str) -> Optional[float]:
    """Net profit per 1u staked. A push is a stake return, not a bet."""
    if row["result"] == "push":
        return None
    won = row["result"] == side
    return row["payout_over" if side == "over" else "payout_under"] if won else -1.0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarise(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-stat market baselines: what blind under does, and how well the
    de-vigged market price predicts the outcome."""
    by_stat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_stat[r["stat_type"]].append(r)
    out = []
    for stat in sorted(by_stat, key=lambda s: -len(by_stat[s])):
        rs = by_stat[stat]
        graded = [r for r in rs if r["result"] != "push"]
        unders = [profit_for_side(r, "under") for r in rs]
        overs = [profit_for_side(r, "over") for r in rs]
        mkt = [r["market_prob_over"] for r in rs if r["result"] != "push"]
        y = [1 if r["result"] == "over" else 0 for r in rs if r["result"] != "push"]
        u_roi = roi([x for x in unders if x is not None])
        o_roi = roi([x for x in overs if x is not None])
        out.append({
            "stat_type": stat,
            "n": len(rs),
            "graded": len(graded),
            "push_pct": 100.0 * (len(rs) - len(graded)) / len(rs) if rs else 0.0,
            "under_hit_pct": 100.0 * sum(1 for r in graded if r["result"] == "under") / len(graded) if graded else None,
            "under_roi_pct": 100.0 * u_roi["roi"] if u_roi["roi"] is not None else None,
            "under_2sigma_pct": 100.0 * u_roi["two_sigma"] if u_roi["two_sigma"] is not None else None,
            "over_roi_pct": 100.0 * o_roi["roi"] if o_roi["roi"] is not None else None,
            "market_log_loss": log_loss(mkt, y),
            "market_brier": brier_score(mkt, y),
        })
    return out


# ---------------------------------------------------------------------------
# Model scoring
#
# The live predictor cannot be driven at a historical as-of date: it resolves
# the target date from live matchup maps, loads current-season enrichment, and
# is a singleton. So we score the offline dataset instead -- which is what
# build_regression_dataset.py exists to produce, with real vacancy dicts rather
# than the empty ones the serve path passes.
#
# Two honesty caveats that the report must carry, because they both inflate the
# model's apparent skill:
#
#   1. The exported bundle was trained on data ending 2026-04-09, so every row
#      at or before that date is IN-SAMPLE. Only 2026-27 rows are a real test.
#      --train-cutoff splits the report on this and it is not optional.
#   2. ~16 enrichment features (opponent ranks, play-type volumes, team pace)
#      come from a single undated current-season snapshot stamped onto every
#      historical row, so they leak. That is a Phase C fix; until then even the
#      out-of-sample numbers are optimistic.
# ---------------------------------------------------------------------------

TRAIN_CUTOFF_DEFAULT = "2026-04-09"


def _p_over_from_quantiles(q25: float, q50: float, q75: float, line: float) -> float:
    """Mirror edge_score's interpolation so the harness measures what ships.

    Note this map saturates: once the line reaches q75 it pins at 0.25 and stops
    responding, which is itself one of the things to fix. Measuring the shipped
    behaviour first means the fix has a before.
    """
    iqr = q75 - q25
    if iqr > 1e-6:
        frac = min(max((line - q25) / iqr, 0.0), 1.0)
        return min(max(0.75 - frac * 0.50, 0.10), 0.90)
    return 0.60 if q50 > line else 0.40


def score_dataset(
    dataset_csv: Path,
    *,
    stat_types: Optional[Sequence[str]] = None,
    keys: Optional[set] = None,
    batch: int = 8192,
) -> Dict[Tuple[str, str, str], Tuple[float, float, float]]:
    """(player_id, game_date, stat_type) -> sanitized (q25, q50, q75).

    `keys` restricts scoring to rows we can actually grade. The dataset is
    ~488k rows while only the ~60k with a matching closing line are useful, so
    without it nine rows in ten are scored and thrown away.
    """
    import numpy as np
    import pandas as pd
    from catboost import CatBoostRegressor, Pool

    model_dir = PROP_MODELING_DIR / "exported_regression_model"
    meta = json.loads((model_dir / "model_metadata.json").read_text())
    feat_cols: List[str] = list(meta["feature_columns"])
    cat_cols: List[str] = list(meta["cat_features"])
    alphas = list(meta.get("quantile_alphas") or [])
    if alphas and alphas != sorted(alphas):
        raise RuntimeError(
            "quantile_alphas %s are not ascending; column order would be "
            "mislabelled." % alphas
        )
    cat_idx = [feat_cols.index(c) for c in cat_cols if c in feat_cols]

    df = pd.read_csv(dataset_csv, low_memory=False)

    # Guard against the exact failure this harness exists to prevent.
    # build_regression_dataset.py does NOT emit every trained feature: the four
    # momentum/possession terms are hand-engineered in ml_inference after
    # _build_regression_row returns, and the four modeled_minutes_* columns are
    # attached by the training notebook from a separate minutes model. Without
    # them, reindex() silently fills NaN and the model collapses toward a flat
    # prior -- measured once at sd(q50)=3.04 against sd(actual)=8.46, which
    # looks exactly like "the model has no signal" and is not.
    missing_feats = [c for c in feat_cols if c not in df.columns]
    if missing_feats:
        raise RuntimeError(
            "The dataset is missing %d trained features, so scoring it would "
            "measure the gap rather than the model: %s. These have to be "
            "attached first (the minutes model's outputs, plus the engineered "
            "terms ml_inference builds after _build_regression_row)."
            % (len(missing_feats), ", ".join(sorted(missing_feats)))
        )

    df["_pid"] = df["player_id"].astype(str)
    df["_gd"] = df["game_date"].astype(str).str.slice(0, 10)
    if keys:
        wanted_pairs = {(k[0], k[1]) for k in keys}
        before = len(df)
        df = df[[(a, b) in wanted_pairs for a, b in zip(df["_pid"], df["_gd"])]]
        print("  narrowed dataset %d -> %d rows with a gradeable line" % (before, len(df)))
    out: Dict[Tuple[str, str, str], Tuple[float, float, float]] = {}
    wanted = set(stat_types) if stat_types else None

    for stat_type, filename in sorted(meta["model_files"].items()):
        if wanted and stat_type not in wanted:
            continue
        cbm = model_dir / str(filename)
        if not cbm.exists():
            print("  skip %s (missing %s)" % (stat_type, filename))
            continue
        sub = df[df["stat_type"] == stat_type]
        if sub.empty:
            continue
        model = CatBoostRegressor()
        model.load_model(str(cbm))

        X = sub.reindex(columns=feat_cols).copy()
        for c in feat_cols:
            if c in cat_cols:
                X[c] = X[c].fillna("UNKNOWN").astype(str)
            else:
                X[c] = pd.to_numeric(X[c], errors="coerce")

        preds_all = []
        for start in range(0, len(X), batch):
            chunk = X.iloc[start:start + batch]
            preds_all.append(model.predict(Pool(chunk, cat_features=cat_idx)))
        preds = np.vstack(preds_all)
        if preds.shape[1] != 3:
            raise RuntimeError(
                "%s returned %d outputs, expected 3 quantiles" % (stat_type, preds.shape[1])
            )
        # Same contract as the serve path: order first, then floor at zero.
        preds = np.clip(np.sort(preds, axis=1), 0.0, None)

        pids = sub["_pid"].to_numpy()
        dates = sub["_gd"].to_numpy()
        for i in range(len(sub)):
            out[(pids[i], dates[i], stat_type)] = (
                float(preds[i, 0]), float(preds[i, 1]), float(preds[i, 2])
            )
        print("  scored %-12s %6d rows" % (stat_type, len(sub)))
    return out


def model_report(
    rows: Sequence[Dict[str, Any]],
    scores: Dict[Tuple[str, str, str], Tuple[float, float, float]],
    *,
    train_cutoff: str = TRAIN_CUTOFF_DEFAULT,
) -> Dict[str, List[Dict[str, Any]]]:
    """Per-stat model vs market log-loss, split in-sample / out-of-sample."""
    regimes: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        "in_sample (trained on)": defaultdict(list),
        "out_of_sample": defaultdict(list),
    }
    for r in rows:
        if r["result"] == "push":
            continue
        key = (r["player_id"], r["game_date"], r["stat_type"])
        trip = scores.get(key)
        if trip is None:
            continue
        q25, q50, q75 = trip
        bucket = "in_sample (trained on)" if r["game_date"] <= train_cutoff else "out_of_sample"
        regimes[bucket][r["stat_type"]].append({
            "model_p_over": _p_over_from_quantiles(q25, q50, q75, r["line"]),
            "market_p_over": r["market_prob_over"],
            "y": 1 if r["result"] == "over" else 0,
            "q50": q50, "actual": r["actual"], "line": r["line"],
            "q25": q25, "q75": q75,
        })

    report: Dict[str, List[Dict[str, Any]]] = {}
    for regime, by_stat in regimes.items():
        out = []
        for stat in sorted(by_stat, key=lambda s: -len(by_stat[s])):
            v = by_stat[stat]
            y = [x["y"] for x in v]
            mll = log_loss([x["model_p_over"] for x in v], y)
            kll = log_loss([x["market_p_over"] for x in v], y)
            out.append({
                "stat_type": stat,
                "n": len(v),
                "model_log_loss": mll,
                "market_log_loss": kll,
                "delta": (mll - kll) if (mll is not None and kll is not None) else None,
                "model_brier": brier_score([x["model_p_over"] for x in v], y),
                "q50_minus_actual": sum(x["q50"] - x["actual"] for x in v) / len(v),
                "coverage_discrete": interval_coverage(
                    [x["actual"] for x in v], [x["q25"] for x in v], [x["q75"] for x in v],
                    discrete=True,
                ),
                "coverage_raw": interval_coverage(
                    [x["actual"] for x in v], [x["q25"] for x in v], [x["q75"] for x in v],
                    discrete=False,
                ),
            })
        report[regime] = out
    return report


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", choices=("supabase", "local"), default="supabase")
    ap.add_argument("--since", default=None, help="ISO date lower bound, e.g. 2026-10-01")
    ap.add_argument("--all-captures", action="store_true",
                    help="Supabase only: include non-closing captures too.")
    ap.add_argument("--gamelogs", nargs="*", default=None)
    ap.add_argument("--output-csv", default=None, help="Write the joined rows here.")
    ap.add_argument("--reliability", action="store_true",
                    help="Print the market's reliability table.")
    ap.add_argument("--model", action="store_true",
                    help="Also score the exported models against these lines.")
    ap.add_argument("--dataset-csv",
                    default=str(PROP_MODELING_DIR / "generated" / "regression_training_dataset.csv"),
                    help="Historical feature rows from build_regression_dataset.py.")
    ap.add_argument("--train-cutoff", default=TRAIN_CUTOFF_DEFAULT,
                    help="Rows at or before this date are in-sample for the exported bundle.")
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_DIR / ".env")
    except Exception:
        pass

    print("loading lines (source=%s since=%s)" % (args.source, args.since or "all"))
    if args.source == "supabase":
        lines = load_lines_supabase(since=args.since, closing_only=not args.all_captures)
    else:
        lines = load_lines_local(since=args.since)
    print("  lines=%d" % len(lines))

    paths = [Path(p) for p in args.gamelogs] if args.gamelogs else DEFAULT_GAMELOGS
    outcomes = load_outcomes(paths)
    print("  outcome rows=%d" % len(outcomes))

    rows, skips = build_rows(lines, outcomes)
    print("  joined=%d" % len(rows))
    for reason, n in skips.most_common(8):
        print("    skipped %-28s %d" % (reason, n))
    if not rows:
        print("nothing to report")
        return 1

    print()
    print("dates=%d  players=%d" % (
        len({r["game_date"] for r in rows}), len({r["player_id"] for r in rows})))
    print()
    hdr = ("stat", "n", "under%", "u_roi%", "+/-2sd", "o_roi%", "mkt_logloss", "mkt_brier")
    print("%-14s %6s %7s %8s %8s %8s %12s %10s" % hdr)
    for s in summarise(rows):
        print("%-14s %6d %7s %8s %8s %8s %12s %10s" % (
            s["stat_type"], s["n"],
            "%.1f" % s["under_hit_pct"] if s["under_hit_pct"] is not None else "-",
            "%+.2f" % s["under_roi_pct"] if s["under_roi_pct"] is not None else "-",
            "%.2f" % s["under_2sigma_pct"] if s["under_2sigma_pct"] is not None else "-",
            "%+.2f" % s["over_roi_pct"] if s["over_roi_pct"] is not None else "-",
            "%.4f" % s["market_log_loss"] if s["market_log_loss"] is not None else "-",
            "%.4f" % s["market_brier"] if s["market_brier"] is not None else "-",
        ))

    graded = [r for r in rows if r["result"] != "push"]
    allu = roi([profit_for_side(r, "under") for r in graded])
    y = [1 if r["result"] == "over" else 0 for r in graded]
    mkt = [r["market_prob_over"] for r in graded]
    print()
    print("ALL  n=%d  blind_under_roi=%+.2f%% (+/-%.2f)  market_log_loss=%.4f  market_brier=%.4f" % (
        allu["n"], 100.0 * allu["roi"], 100.0 * allu["two_sigma"],
        log_loss(mkt, y) or float("nan"), brier_score(mkt, y) or float("nan")))

    if args.reliability:
        print()
        print("market reliability (de-vigged P(over) vs realised)")
        print("  %-12s %7s %10s %10s" % ("bucket", "n", "mean_pred", "hit_rate"))
        for b in reliability_table(mkt, y):
            print("  %-12s %7d %10.3f %10.3f" % (b["bucket"], b["n"], b["mean_pred"], b["hit_rate"]))

    if args.model:
        ds = Path(args.dataset_csv)
        if not ds.exists():
            print()
            print("no dataset at %s -- run build_regression_dataset.py first" % ds)
        else:
            print()
            print("scoring exported models over %s" % ds.name)
            needed = {
                (r["player_id"], r["game_date"], r["stat_type"])
                for r in rows if r["result"] != "push"
            }
            scores = score_dataset(ds, keys=needed)
            rep = model_report(rows, scores, train_cutoff=args.train_cutoff)
            for regime in ("in_sample (trained on)", "out_of_sample"):
                stats = rep.get(regime) or []
                print()
                print("%s  (cutoff %s)" % (regime.upper(), args.train_cutoff))
                if not stats:
                    print("  no rows")
                    continue
                print("  %-14s %7s %10s %10s %8s %10s %9s %9s" % (
                    "stat", "n", "model_ll", "mkt_ll", "delta", "q50-act", "cov_disc", "cov_raw"))
                for st in stats:
                    print("  %-14s %7d %10s %10s %8s %10s %9s %9s" % (
                        st["stat_type"], st["n"],
                        "%.4f" % st["model_log_loss"] if st["model_log_loss"] is not None else "-",
                        "%.4f" % st["market_log_loss"] if st["market_log_loss"] is not None else "-",
                        "%+.4f" % st["delta"] if st["delta"] is not None else "-",
                        "%+.2f" % st["q50_minus_actual"],
                        "%.3f" % st["coverage_discrete"] if st["coverage_discrete"] is not None else "-",
                        "%.3f" % st["coverage_raw"] if st["coverage_raw"] is not None else "-",
                    ))
                print("  (delta < 0 means the model beat the market on log-loss)")

    if args.output_csv:
        out = Path(args.output_csv)
        with out.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print()
        print("output_csv=%s rows=%d" % (out, len(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
