"""What does the price we take cost us, and would shopping two books fix it?

Context. The model carries real information against the closing price: its
chosen side beats that side's own de-vigged probability by about +2 percentage
points, significant at ~3.7 sigma iid and roughly half that once rows are
clustered on player-game. But the hold on a -110/-110 market is 4.76%, so a +2%
edge over fair value still loses into the posted price, and gating alerts on
model-minus-market measured -3.8% to -4.5% ROI. The gap is in the price, not the
model.

This script asks two questions the existing harness cannot:

  1. What does the SHIPPED selection score? `backtest.py` grades one row per
     (player, date, book, stat) and averages over books. The serve path does not
     average -- `edge_score._select_market_for_side` picks one book per side,
     minimising (line, implied) for an over and (-line, implied) for an under,
     i.e. it already shops line-first then price-second. Nobody has graded that
     selection. It is the status quo, not a counterfactual, and it runs first.

  2. Would taking the better of two books' prices at the SAME line close the
     gap? That is the only lever here that does not require a better model.

Headline honestly: these are ROIs of the **ml component's** chosen side, not of
the board. The board publishes the side maximising a composite over nine
components this script cannot reproduce, and the ml component scores on
`ranking_signal`, not on the probability. Read these as "what the model's
directional opinion is worth at four different prices", nothing wider.

Expected magnitude, stated before the run so a small number is not mistaken for
a solution: the hole is ~4%, and taking -110 instead of -115 is worth ~1.1% of
ROI. With two books, improvement available on one side only, and ~79% of
two-book props quotable at a common line, the ceiling for same-line shopping is
on the order of +1%. It will probably come back negative. The lever that closes
4% is a third and fourth book, which is a scraper change.

Usage:
    python3 prop_modeling/price_shopping.py                     # closing lines
    python3 prop_modeling/price_shopping.py --all-captures       # intraday too
    python3 prop_modeling/price_shopping.py --source local
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROP_MODELING_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROP_MODELING_DIR.parent
for _p in (str(BACKEND_DIR), str(PROP_MODELING_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest as bt  # noqa: E402
from prop_modeling.market_prices import (  # noqa: E402
    american_to_implied,
    american_to_payout_decimal,
)
from prop_modeling.p_over_calibration import (  # noqa: E402
    has_usable_spread,
    ranking_signal,
)

# dk and fd only. PrizePicks rows carry no single-leg American odds, and a
# selection that maximises payout would pick a fabricated even-money price over
# every real one. build_rows now drops unpriced rows, so this is belt and
# braces -- and it is the one way this measurement could come back falsely
# positive, so it is asserted rather than assumed.
SHOPPABLE_BOOKS = ("dk", "fd")

CACHE_COLUMNS = [
    "game_date", "stat_type", "player_id", "sportsbook", "line",
    "over_odds", "under_odds", "payout_over", "payout_under",
    "market_prob_over", "actual", "result", "q25", "q50", "q75",
]


# ---------------------------------------------------------------------------
# Selection


def ml_side_at(row: Dict[str, Any], line: float) -> Optional[Tuple[str, float]]:
    """Which side the ml component prefers at a given line, and by how much.

    The component score is `+ranking_signal` for an over and `-ranking_signal`
    for an under, so at one line the preference is just the sign of the signal.
    It is reported with the magnitude because variant B compares the two sides
    at *different* lines, where the signs alone do not decide it.

    None when the model has no opinion. `ranking_signal` returns exactly 0.0
    for a crossed or zero-width interval, and `signal >= 0` would have entered
    that as a real *over* wager -- scoring a bet the serve path would never
    place, since the ml component goes dark there via `has_usable_spread`. No
    such row exists in the current data (0 crossed, 0 zero-spread, 0
    non-finite), so this is a guard, not a filter.
    """
    if not has_usable_spread(row["q25"], row["q75"]):
        return None
    signal = ranking_signal(row["q25"], row["q50"], row["q75"], line)
    if signal == 0.0:
        return None
    return ("over", signal) if signal > 0 else ("under", -signal)


def serve_path_pick(group: Sequence[Dict[str, Any]], side: str) -> Optional[Dict[str, Any]]:
    """Replicate `edge_score._select_market_for_side` for one side.

    Minimise `(line, implied)` for an over and `(-line, implied)` for an under,
    with a missing implied treated as 1.0 so a priced book always beats an
    unpriced one. Rows are visited in book-name order: production visits them in
    the feed's dict order and keeps the first on a tie, so a deterministic order
    is needed here to make the replica reproducible. Ties between two books at
    the same line AND the same price are indifferent by construction.
    """
    chosen = None
    chosen_key: Tuple[float, float] = (0.0, 0.0)
    for row in sorted(group, key=lambda r: r["sportsbook"]):
        implied = american_to_implied(row["over_odds"] if side == "over" else row["under_odds"])
        key = (row["line"], implied if implied is not None else 1.0)
        if side == "under":
            key = (-key[0], key[1])
        if chosen is None or key < chosen_key:
            chosen, chosen_key = row, key
    return chosen


def best_price_row(
    group: Sequence[Dict[str, Any]],
    side: str,
    *,
    worst: bool = False,
) -> Optional[Dict[str, Any]]:
    """The row offering the best (or worst) payout for `side`.

    Only ever called on groups sharing one line, so this is the same bet at a
    different price and the ROI delta is the price improvement. Never selects a
    row whose payout is None -- that is what would turn a missing price into a
    fabricated winner.
    """
    key = "payout_over" if side == "over" else "payout_under"
    priced = sorted((r for r in group if r[key] is not None), key=lambda r: r["sportsbook"])
    if not priced:
        return None
    # min/max return the FIRST extremal element, and the list is in book-name
    # order, so a tie resolves to the same book `serve_path_pick` would keep.
    # The payouts are equal either way, but matching tiebreaks is what lets the
    # variants be compared row for row rather than only in aggregate.
    pick = min if worst else max
    return pick(priced, key=lambda r: r[key])


def profit(row: Dict[str, Any], side: str) -> Optional[float]:
    """Net profit per unit staked. A push returns the stake, so it is not a bet."""
    if row["result"] == "push":
        return None
    payout = row["payout_over" if side == "over" else "payout_under"]
    if payout is None:
        raise AssertionError("graded an unpriced row: %r" % (row,))
    return payout if row["result"] == side else -1.0


# ---------------------------------------------------------------------------
# Statistics


def paired_diff(a: Sequence[float], b: Sequence[float], clusters: Sequence[Any],
                draws: int = 2000) -> Dict[str, Any]:
    """Mean of per-row differences, with a clustered band.

    For two variants over the SAME bets this is the only honest comparison.
    (A) and (C) are 7,170 identical wagers at two prices: paired, the gap is
    +3.00% +/- 0.13%, while each leg alone carries a +/-3.1% band. Printing the
    legs' bands beside an unbanded difference invited reading a 23-sigma result
    as noise, which is what the first version of this script did.
    """
    return roi_with_bands([x - y for x, y in zip(a, b)], clusters, draws=draws)


def roi_with_bands(pnl: Sequence[float], clusters: Sequence[Any], draws: int = 2000) -> Dict[str, Any]:
    """Mean ROI with an iid 2-sigma band and a clustered bootstrap band.

    Rows are not independent: one player's night moves PTS, PTS+REB, PTS+AST
    and PRA together, so an iid band understates by roughly a factor of two on
    this data. A +1% improvement inside a +/-0.7% iid band is +/-1.4% clustered
    and therefore nothing.
    """
    n = len(pnl)
    if n == 0:
        return {"n": 0, "roi": None, "iid_2sd": None, "cluster_2sd": None}
    mean = sum(pnl) / n
    if n > 1:
        var = sum((x - mean) ** 2 for x in pnl) / (n - 1)
        iid = 2.0 * math.sqrt(var / n)
    else:
        iid = float("nan")

    by_cluster: Dict[Any, List[float]] = defaultdict(list)
    for value, key in zip(pnl, clusters):
        by_cluster[key].append(value)
    blocks = list(by_cluster.values())
    rng = random.Random(0)
    means = []
    for _ in range(draws):
        picked: List[float] = []
        for _ in range(len(blocks)):
            picked.extend(blocks[rng.randrange(len(blocks))])
        if picked:
            means.append(sum(picked) / len(picked))
    cluster = 2.0 * math.sqrt(sum((m - mean) ** 2 for m in means) / (len(means) - 1)) if len(means) > 1 else float("nan")
    return {"n": n, "roi": mean, "iid_2sd": iid, "cluster_2sd": cluster, "clusters": len(blocks)}


# ---------------------------------------------------------------------------
# Cache


def build_cache(path: Path, *, source: str, all_captures: bool) -> None:
    lines = (bt.load_lines_local() if source == "local"
             else bt.load_lines_supabase(closing_only=not all_captures))
    rows, skips = bt.build_rows(lines, bt.load_outcomes(bt.DEFAULT_GAMELOGS))
    print("graded rows=%d skips=%s" % (len(rows), dict(skips)), flush=True)

    rows = [r for r in rows if str(r.get("sportsbook") or "").lower() in SHOPPABLE_BOOKS]
    print("after restricting to %s: %d" % ("/".join(SHOPPABLE_BOOKS), len(rows)), flush=True)

    # score_dataset is keyed (player_id, game_date, stat_type) and so is
    # book-independent: one CatBoost pass serves all four variants.
    needed = {(r["player_id"], r["game_date"], r["stat_type"]) for r in rows if r["result"] != "push"}
    scores = bt.score_dataset(
        PROP_MODELING_DIR / "generated" / "regression_training_dataset.csv",
        keys=needed,
        minutes_csv=PROP_MODELING_DIR / "generated" / "minutes_training_dataset.csv",
    )
    print("scored keys=%d" % len(scores), flush=True)

    written = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CACHE_COLUMNS)
        writer.writeheader()
        for row in rows:
            trip = scores.get((row["player_id"], row["game_date"], row["stat_type"]))
            if not trip:
                continue
            q25, q50, q75 = trip
            writer.writerow({
                "game_date": row["game_date"], "stat_type": row["stat_type"],
                "player_id": row["player_id"], "sportsbook": row["sportsbook"],
                "line": row["line"], "over_odds": row["over_odds"], "under_odds": row["under_odds"],
                "payout_over": row["payout_over"], "payout_under": row["payout_under"],
                "market_prob_over": row["market_prob_over"], "actual": row["actual"],
                "result": row["result"], "q25": q25, "q50": q50, "q75": q75,
            })
            written += 1
    print("cache=%s rows=%d" % (path, written), flush=True)


def load_cache(path: Path, *, since: Optional[str]) -> List[Dict[str, Any]]:
    out = []
    with path.open() as handle:
        for raw in csv.DictReader(handle):
            if since and raw["game_date"] <= since:
                continue
            book = str(raw["sportsbook"] or "").lower()
            if book not in SHOPPABLE_BOOKS:
                raise AssertionError("unshoppable book reached the comparison: %r" % book)
            out.append({
                "game_date": raw["game_date"], "stat_type": raw["stat_type"],
                "player_id": raw["player_id"], "sportsbook": book,
                "line": float(raw["line"]), "actual": float(raw["actual"]),
                "result": raw["result"],
                "over_odds": float(raw["over_odds"]) if raw["over_odds"] else None,
                "under_odds": float(raw["under_odds"]) if raw["under_odds"] else None,
                "payout_over": american_to_payout_decimal(raw["over_odds"]),
                "payout_under": american_to_payout_decimal(raw["under_odds"]),
                "q25": float(raw["q25"]), "q50": float(raw["q50"]), "q75": float(raw["q75"]),
            })
    return out


# ---------------------------------------------------------------------------
# Variants


def run_variants(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_key: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    by_key_line: Dict[Tuple[str, str, str, float], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key[(row["player_id"], row["game_date"], row["stat_type"])].append(row)
        by_key_line[(row["player_id"], row["game_date"], row["stat_type"], row["line"])].append(row)

    results: Dict[str, Dict[str, Any]] = {}

    def collect(name: str, pairs: Iterable[Tuple[Dict[str, Any], str]]) -> None:
        pnl, clusters, blind, keys = [], [], [], []
        for row, side in pairs:
            value = profit(row, side)
            if value is None:          # push, or an unpriced side
                continue
            under = profit(row, "under")
            pnl.append(value)
            clusters.append((row["player_id"], row["game_date"]))
            # The free null. Roughly three in four of these bets are unders,
            # because every stat's q50_bias is negative, so most of any
            # headline ROI is the slate's under bias rather than the model.
            blind.append(under if under is not None else value)
            keys.append((row["player_id"], row["game_date"], row["stat_type"], row["line"]))
        out = roi_with_bands(pnl, clusters)
        out["pnl"], out["clusters"], out["keys"] = pnl, clusters, keys
        out["vs_blind_under"] = paired_diff(pnl, blind, clusters) if pnl else None
        out["blind_under"] = roi_with_bands(blind, clusters) if blind else None
        results[name] = out

    # Baseline: what backtest.py grades -- every book row on its own merits.
    def baseline_pairs():
        for row in rows:
            opinion = ml_side_at(row, row["line"])
            if opinion is not None:
                yield row, opinion[0]
    collect("baseline (per-book pooled)", baseline_pairs())

    # (B) The shipped selection. Pick a book per side the way the serve path
    # does, then let the ml component choose between the two resulting bets.
    def serve_pairs():
        for group in by_key.values():
            choice = _serve_choice(group)
            if choice is not None:
                yield choice
    collect("(B) serve-path selection", serve_pairs())

    # (A)/(C) Same line, two books: identical bet at the better or worse price.
    two_book = {k: g for k, g in by_key_line.items() if len({r["sportsbook"] for r in g}) > 1}
    for name, worst in (("(A) same-line best price", False), ("(C) same-line worst price", True)):
        def pairs(worst=worst):
            for group in two_book.values():
                opinion = ml_side_at(group[0], group[0]["line"])
                if opinion is None:
                    continue
                pick = best_price_row(group, opinion[0], worst=worst)
                if pick is not None:
                    yield pick, opinion[0]
        collect(name, pairs())

    # Decompose (B). The line-disagreement bucket is where its margin comes
    # from, and it was asserted rather than measured in the first version.
    buckets: Dict[str, List[Tuple[Dict[str, Any], str]]] = {
        "   same-line, two books": [], "   single book": [], "   books disagree on the line": [],
    }
    for key, group in by_key.items():
        pick = _serve_choice(group)
        if pick is None:
            continue
        books = {r["sportsbook"] for r in group}
        lines = {r["line"] for r in group}
        name = ("   single book" if len(books) < 2
                else "   same-line, two books" if len(lines) == 1
                else "   books disagree on the line")
        buckets[name].append(pick)
    for name, pairs in buckets.items():
        collect(name, pairs)

    # And the paired counterfactual on the disagreement bucket: the same side,
    # at the other book's line.
    shopped, other, clusters = [], [], []
    for group in by_key.values():
        if len({r["sportsbook"] for r in group}) < 2 or len({r["line"] for r in group}) == 1:
            continue
        pick = _serve_choice(group)
        if pick is None:
            continue
        row, side = pick
        alternatives = [r for r in group if r is not row]
        if len(alternatives) != 1:
            continue
        mine, theirs = profit(row, side), profit(alternatives[0], side)
        if mine is None or theirs is None:
            continue
        shopped.append(mine)
        other.append(theirs)
        clusters.append((row["player_id"], row["game_date"]))
    results["_line_shopping"] = paired_diff(shopped, other, clusters) if shopped else None

    results["_two_book_groups"] = {
        "same_line_keys": len({(k[0], k[1], k[2]) for k in two_book}),
        "keys": len(by_key),
        "two_book_keys": len({k for k, g in by_key.items() if len({r["sportsbook"] for r in g}) > 1}),
    }
    return results


def _serve_choice(group: Sequence[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], str]]:
    """The (row, side) the ml component would take on the serve path's picks."""
    best = None
    for side in ("over", "under"):
        pick = serve_path_pick(group, side)
        if pick is None:
            continue
        signal = ranking_signal(pick["q25"], pick["q50"], pick["q75"], pick["line"])
        if not has_usable_spread(pick["q25"], pick["q75"]) or signal == 0.0:
            continue
        score = signal if side == "over" else -signal
        if best is None or score > best[2]:
            best = (pick, side, score)
    return (best[0], best[1]) if best else None


def price_improvement(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Model-free: how much does the better of two books move the implied price?

    No model, no out-of-sample requirement, so this is the one number on this
    sample whose error bars are tight enough to mean something.
    """
    by_key_line: Dict[Tuple[str, str, str, float], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_key_line[(row["player_id"], row["game_date"], row["stat_type"], row["line"])].append(row)

    deltas: List[float] = []
    for group in by_key_line.values():
        if len({r["sportsbook"] for r in group}) < 2:
            continue
        for side in ("over", "under"):
            key = "over_odds" if side == "over" else "under_odds"
            implieds = [american_to_implied(r[key]) for r in group]
            implieds = [i for i in implieds if i is not None]
            if len(implieds) > 1:
                deltas.append(max(implieds) - min(implieds))
    if not deltas:
        return {"n": 0}
    mean = sum(deltas) / len(deltas)
    return {
        "n": len(deltas),
        "mean_implied_spread": mean,
        "median_implied_spread": sorted(deltas)[len(deltas) // 2],
        "share_identical": sum(1 for d in deltas if d < 1e-9) / len(deltas),
    }


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", choices=("supabase", "local"), default="supabase")
    parser.add_argument("--all-captures", action="store_true",
                        help="Include intraday captures, where books disagree most.")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--since", default=bt.TRAIN_CUTOFF_DEFAULT,
                        help="Grade only dates after this, so the model is out of sample.")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_DIR / ".env")
    except Exception:
        pass

    suffix = "all_captures" if args.all_captures else "closing"
    cache = Path(args.cache) if args.cache else PROP_MODELING_DIR / "generated" / ("price_shopping_%s.csv" % suffix)
    if args.rebuild or not cache.exists():
        build_cache(cache, source=args.source, all_captures=args.all_captures)

    everything = load_cache(cache, since=None)
    out_of_sample = load_cache(cache, since=args.since)
    print()
    print("capture mode : %s" % ("intraday + closing" if args.all_captures else "closing lines only"))
    print("cache rows   : %d (%d after %s, out of sample for the models)"
          % (len(everything), len(out_of_sample), args.since))

    spread = price_improvement(everything)
    print()
    print("-- model-free price dispersion, full sample --")
    if spread["n"]:
        print("   two-book side quotes      : %d" % spread["n"])
        print("   mean best-worst implied   : %+.4f  (%.2f percentage points)"
              % (spread["mean_implied_spread"], 100 * spread["mean_implied_spread"]))
        print("   median                    : %+.4f" % spread["median_implied_spread"])
        print("   share where books agree   : %.1f%%" % (100 * spread["share_identical"]))
    else:
        print("   no two-book quotes in this sample")

    results = run_variants(out_of_sample)
    meta = results.pop("_two_book_groups")
    line_shopping = results.pop("_line_shopping")
    print()
    print("-- ROI of the ml component's chosen side, priced four ways (out of sample) --")
    print("   %d (player,date,stat) keys | %d with two books | %d of those at one line (%.1f%%)"
          % (meta["keys"], meta["two_book_keys"], meta["same_line_keys"],
             100.0 * meta["same_line_keys"] / max(1, meta["two_book_keys"])))
    print()
    print("   %-30s %7s %9s %11s %14s" % ("variant", "n", "ROI", "cluster 2sd", "vs blind under"))
    print("   " + "-" * 78)

    def row(name, label=None):
        r = results.get(name)
        if not r or not r["n"]:
            print("   %-30s %7s" % (label or name, "-"))
            return
        null = r["vs_blind_under"]
        print("   %-30s %7d %+8.2f%% %10.2f%% %+9.2f%% +/-%.2f%%"
              % (label or name, r["n"], 100 * r["roi"], 100 * r["cluster_2sd"],
                 100 * null["roi"], 100 * null["cluster_2sd"]))

    for name in ("baseline (per-book pooled)", "(B) serve-path selection",
                 "(A) same-line best price", "(C) same-line worst price"):
        row(name)
    print()
    print("   (B) decomposed:")
    for name in ("   same-line, two books", "   single book", "   books disagree on the line"):
        row(name)

    a, c = results.get("(A) same-line best price"), results.get("(C) same-line worst price")
    print()
    if a and c and a["n"] == c["n"]:
        # Paired: the same bets at two prices, so the per-leg bands do not apply.
        pair = paired_diff(a["pnl"], c["pnl"], a["clusters"])
        print("   best price minus worst, PAIRED on the same %d bets : %+.2f%% +/-%.2f%%"
              % (pair["n"], 100 * pair["roi"], 100 * pair["cluster_2sd"]))
        print("      -- already captured: argmin(implied) == argmax(payout) identically,")
        print("         so the serve path's line-tie break already takes the best price.")
    if line_shopping and line_shopping["n"]:
        print("   shopped line minus the other book's, PAIRED on %d bets : %+.2f%% +/-%.2f%%"
              % (line_shopping["n"], 100 * line_shopping["roi"], 100 * line_shopping["cluster_2sd"]))
        print("      -- also already captured, and the larger of the two effects. This is")
        print("         what the selection logic is worth; a regression in it costs this much.")
    print()
    print("   Caveats that change how these read:")
    print("   * The free null is blind unders, not zero. Roughly 3 of 4 of these bets are")
    print("     unders because every stat's q50_bias is negative, so most of any ROI here")
    print("     is the slate's under bias. The right-hand column is the paired comparison.")
    print("   * (B) is the ml component's side on the serve path's book, NOT the board: the")
    print("     board maximises a nine-component composite, then applies eligibility,")
    print("     diversification and the Discord floor. What ships is a handful of picks.")
    print("   * ~16 opponent enrichment features are one undated current-season snapshot")
    print("     stamped on every historical row, so even these out-of-sample numbers are")
    print("     optimistic (backtest.py:376-379).")
    print("   * Pooling the April-May window with 2026-27 spans an offseason and a playoff")
    print("     block. Re-run with --since 2026-10-01 for the clean season alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
