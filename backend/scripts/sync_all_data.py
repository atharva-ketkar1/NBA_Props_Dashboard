#!/usr/bin/env python3
"""Pull every artifact this repo can hold, from the VM and from Supabase.

Why this exists: `backend/data` is gitignored, so a local checkout starts empty
and drifts from the moment the VM runs its next cron tick. That drift is quiet
and it has already cost real work -- the prop model was diagnosed against a
dataset whose newest game was 2026-11-02 while the VM had games through
2026-11-11, and the local tree had no `data/archive/line_movements/` at all
even though the VM held 42 MB of it. "We don't have the data" should be one
command, not an investigation.

Two sources, because the durable data is split across them:

  * the VM (`oracle-nba`) holds the generated artifacts -- game logs, odds
    snapshots, injury reports, the line-movement archive that closing-line
    value is computed from, and the prior-season caches.
  * Supabase holds the tables the VM treats as the source of truth, because
    the VM is disposable and one has already been lost: the props history that
    `backtest.py` grades against, the season archives, the splits tables.

Additive by default. `pull_vm_data.sh` rsyncs with `--delete`, which mirrors
the VM and removes local-only files -- fine for "stop dev running on stale
data", wrong for "collect everything we have", since a local-only archive a
rebuild produced would be deleted. `--mirror` opts into the old behaviour.

Usage:
    python3 scripts/sync_all_data.py                  # everything, additive
    python3 scripts/sync_all_data.py --inventory      # report only, no transfer
    python3 scripts/sync_all_data.py --dry-run        # show what would move
    python3 scripts/sync_all_data.py --vm-only
    python3 scripts/sync_all_data.py --supabase-only
    python3 scripts/sync_all_data.py --mirror         # allow rsync --delete

Exit codes: 0 all requested sources synced, 1 one or more failed. A failure in
one source never stops the other -- a Supabase outage should still get you the
VM's game logs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DATA_DIR = BACKEND_DIR / "data"
SUPABASE_DIR = DATA_DIR / "supabase"

VM_HOST = os.environ.get("NBA_VM_HOST", "oracle-nba")
VM_REMOTE_DIR = os.environ.get("NBA_VM_REMOTE_DIR", "NBA_Dashboard")

# Subdirectories of backend/data to mirror down. `prior/` matters as much as
# the rest and pull_vm_data.sh never synced it, which is how a local run could
# rebuild a prior-season cache the VM already had.
VM_DATA_SUBDIRS = ("current", "archive", "prior", "preserved_pre_2026-10-20_run")

SUPABASE_PAGE = 1000
SUPABASE_TIMEOUT = 120

# Tables worth keeping a local copy of, with the column to order by so paging
# is stable. Operational tables are deliberately absent: `cron_state` is
# scheduler state, and `edge_scores_current` is overwritten every refresh and
# keeps no history, so neither is data we could lose.
SUPABASE_TABLES: Tuple[Dict[str, Any], ...] = (
    {
        "table": "historical_player_props",
        "order": "game_date",
        "note": "closing + intraday prop lines; what backtest.py grades against",
    },
    {
        "table": "archive_gamelogs",
        "order": "player_id",
        # One row holds an entire season of game logs as JSON, so a page is
        # measured in tens of MB: at limit=1000 the API answers HTTP 500, at
        # 200 a page is ~42 MB and succeeds. Set explicitly rather than left to
        # the adaptive halving below, so the common case costs no failed
        # request.
        "page": 200,
        "note": "completed-season game logs, per player; feeds prior_season.py",
    },
    {
        "table": "historical_odds",
        "order": "game_date",
        "note": "per player-date odds",
    },
    {
        "table": "line_movements",
        "order": "game_date",
        "note": "intraday series; EPHEMERAL, purged on a rolling window",
    },
    {
        "table": "player_season_splits",
        "order": "player_id",
        "note": "zones / shot types / play types per completed season",
    },
    {
        "table": "team_season_defense",
        "order": "team",
        "note": "team defensive aggregates and ranks per season",
    },
    {
        "table": "game_markets_closing",
        "order": "game_id",
        "note": "spread / total / moneyline at tip-off",
    },
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _log(message: str) -> None:
    print(message, flush=True)


def _human(num_bytes: Optional[int]) -> str:
    if num_bytes is None:
        return "-"
    size = float(num_bytes)
    for unit in ("B", "K", "M", "G"):
        if size < 1024 or unit == "G":
            return "%.0f%s" % (size, unit) if unit == "B" else "%.1f%s" % (size, unit)
        size /= 1024
    return "%.1fG" % size


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# VM
# ---------------------------------------------------------------------------

def vm_reachable() -> bool:
    try:
        out = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", VM_HOST, "true"],
            capture_output=True, text=True, timeout=25, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def sync_vm(*, mirror: bool, dry_run: bool) -> bool:
    """rsync the VM's data subdirectories down. Returns True on success."""
    if shutil.which("rsync") is None:
        _log("  rsync not installed; cannot sync from the VM")
        return False
    if not vm_reachable():
        _log("  %s is unreachable over ssh. Check the Host entry in ~/.ssh/config "
             "(the repo expects one named %r), or set NBA_VM_HOST."
             % (VM_HOST, VM_HOST))
        return False

    ok = True
    for subdir in VM_DATA_SUBDIRS:
        remote = "%s:~/%s/backend/data/%s/" % (VM_HOST, VM_REMOTE_DIR, subdir)
        local = DATA_DIR / subdir
        local.mkdir(parents=True, exist_ok=True)
        before = _dir_bytes(local)
        # -t preserves mtimes, which the freshness report depends on.
        cmd = ["rsync", "-az", "--partial", "--stats", "-e", "ssh"]
        if mirror:
            cmd.append("--delete")
        if dry_run:
            cmd.append("--dry-run")
        cmd += [remote, str(local) + "/"]

        _log("  %s%s" % (subdir, "  (dry run)" if dry_run else ""))
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            _log("    failed: %s" % exc)
            ok = False
            continue
        if out.returncode != 0:
            # 23/24 are partial-transfer codes: a vanished file is normal while
            # the VM's cron is mid-write, and should not fail the whole sync.
            level = "warning" if out.returncode in (23, 24) else "failed"
            _log("    %s (rsync exit %d): %s"
                 % (level, out.returncode, (out.stderr or "").strip().splitlines()[-1:] or ""))
            if out.returncode not in (23, 24):
                ok = False
                continue
        transferred = [ln for ln in (out.stdout or "").splitlines()
                       if "Number of regular files transferred" in ln
                       or "Total transferred file size" in ln]
        for line in transferred:
            _log("    %s" % line.strip())
        if not dry_run:
            _log("    local size %s -> %s" % (_human(before), _human(_dir_bytes(local))))
    return ok


# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

def _supabase_creds() -> Tuple[Optional[str], Optional[str]]:
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND_DIR / ".env")
    except Exception:
        pass
    base = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_KEY")
           or os.environ.get("SUPABASE_KEY") or "").strip()
    return (base or None), (key or None)


MIN_SUPABASE_PAGE = 25


def _fetch_page(base: str, key: str, table: str, order: str,
                limit: int, offset: int) -> Optional[List[Dict[str, Any]]]:
    url = "%s/rest/v1/%s?select=*&order=%s.asc&limit=%d&offset=%d" % (
        base, urllib.parse.quote(table), urllib.parse.quote(order), limit, offset,
    )
    req = urllib.request.Request(
        url, headers={"apikey": key, "Authorization": "Bearer " + key}
    )
    with urllib.request.urlopen(req, timeout=SUPABASE_TIMEOUT) as resp:
        page = json.load(resp)
    return page if isinstance(page, list) else None


def fetch_table_to_jsonl(
    base: str, key: str, table: str, order: str, out_path: Path, page_size: int,
) -> Optional[int]:
    """Stream a whole table to JSON Lines. None means the table does not exist.

    Streaming rather than accumulating: archive_gamelogs is ~230 MB across
    ~1,100 rows because each row holds a season of game logs, and building
    that in memory before writing is how a sync of the *largest* table becomes
    the one most likely to fail.

    The page size shrinks on a 5xx. A page too large for the API to serialize
    comes back as HTTP 500 with nothing to distinguish it from a real outage,
    so the only way to tell is to ask for less and see. JSON Lines, so a row
    can be appended without rewriting the file and a partial pull is still
    readable up to its last complete line.

    Migrations in supabase/migrations/ are applied by hand, so a table that is
    not there yet is an ordinary state and must not fail the sync -- the same
    tolerance the rest of the codebase has.
    """
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    total = 0
    offset = 0
    limit = max(MIN_SUPABASE_PAGE, int(page_size))
    try:
        with tmp.open("w") as handle:
            while True:
                try:
                    page = _fetch_page(base, key, table, order, limit, offset)
                except urllib.error.HTTPError as exc:
                    if exc.code in (400, 404):
                        tmp.unlink(missing_ok=True)
                        return None
                    if exc.code >= 500 and limit > MIN_SUPABASE_PAGE:
                        limit = max(MIN_SUPABASE_PAGE, limit // 4)
                        _log("    page too large for the API; retrying at limit=%d" % limit)
                        continue
                    raise
                if page is None:
                    tmp.unlink(missing_ok=True)
                    return None
                for row in page:
                    handle.write(json.dumps(row, separators=(",", ":")))
                    handle.write("\n")
                total += len(page)
                if len(page) < limit:
                    break
                offset += limit
        tmp.replace(out_path)
    finally:
        tmp.unlink(missing_ok=True)
    return total


def sync_supabase(*, dry_run: bool) -> bool:
    base, key = _supabase_creds()
    if not base or not key:
        _log("  SUPABASE_URL / SUPABASE_SERVICE_KEY not set (looked in backend/.env); "
             "skipping the Supabase half")
        return False

    SUPABASE_DIR.mkdir(parents=True, exist_ok=True)
    ok = True
    for spec in SUPABASE_TABLES:
        table, order = spec["table"], spec["order"]
        if dry_run:
            _log("  %-28s would page out (order by %s)" % (table, order))
            continue
        out_path = SUPABASE_DIR / ("%s.jsonl" % table)
        try:
            rows = fetch_table_to_jsonl(
                base, key, table, order, out_path, int(spec.get("page", SUPABASE_PAGE)),
            )
        except Exception as exc:
            _log("  %-28s FAILED: %s" % (table, exc))
            ok = False
            continue
        if rows is None:
            _log("  %-28s not present (migration not applied yet)" % table)
            continue

        # The sidecar carries what the old single-object format kept in a
        # wrapper. Written after the data, so a meta file always describes a
        # complete pull.
        meta_path = SUPABASE_DIR / ("%s.meta.json" % table)
        meta_path.write_text(json.dumps({
            "table": table,
            "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
            "row_count": rows,
            "order": order,
            "note": spec.get("note"),
            "format": "jsonl",
        }, indent=2))
        # An earlier run of this script wrote <table>.json in the wrapped
        # format. Leaving both would give two copies of the same table that
        # disagree the next time one of them is refreshed.
        legacy = SUPABASE_DIR / ("%s.json" % table)
        if legacy.exists():
            legacy.unlink()
            _log("    removed superseded %s" % legacy.name)
        _log("  %-28s %7d rows -> %s (%s)"
             % (table, rows, out_path.relative_to(BACKEND_DIR), _human(out_path.stat().st_size)))
    return ok


# ---------------------------------------------------------------------------
# Inventory -- the part that answers "do we have everything?"
# ---------------------------------------------------------------------------

def _csv_date_range(path: Path, column: str) -> Tuple[int, Optional[str], Optional[str]]:
    rows = 0
    lo = hi = None
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows += 1
                value = (row.get(column) or "")[:10]
                if value:
                    lo = value if lo is None or value < lo else lo
                    hi = value if hi is None or value > hi else hi
    except (OSError, csv.Error):
        return rows, None, None
    return rows, lo, hi


def inventory() -> None:
    _log("")
    _log("=== inventory ===")
    today = date.today().isoformat()

    _log("")
    _log("game logs (the model's training source)")
    found = False
    for path in sorted(DATA_DIR.glob("archive/gamelogs_*.csv")) + \
            sorted(DATA_DIR.glob("current/gamelogs_*.csv")):
        if ".backup." in path.name:
            continue
        found = True
        rows, lo, hi = _csv_date_range(path, "GAME_DATE")
        lag = ""
        if hi:
            lag = "  (%d days behind today)" % (date.fromisoformat(today) - date.fromisoformat(hi)).days
        _log("  %-38s %7d rows  %s .. %s%s"
             % (path.relative_to(BACKEND_DIR), rows, lo or "-", hi or "-", lag))
    if not found:
        _log("  none on disk")

    _log("")
    _log("data directories")
    for subdir in VM_DATA_SUBDIRS + ("supabase",):
        path = DATA_DIR / subdir
        if not path.exists():
            _log("  %-38s absent" % subdir)
            continue
        files = sum(1 for p in path.rglob("*") if p.is_file())
        _log("  %-38s %6s  %5d files" % (subdir, _human(_dir_bytes(path)), files))

    _log("")
    _log("line-movement archive (closing-line value is computed from this)")
    lm = DATA_DIR / "archive" / "line_movements"
    if lm.exists():
        days = sorted(p.stem for p in lm.glob("*.json"))
        _log("  %d dated files  %s .. %s" % (len(days), days[0] if days else "-", days[-1] if days else "-"))
    else:
        _log("  absent -- this is the durable copy; the Supabase table is purged on a "
             "rolling window, so a gap here is permanent")

    _log("")
    _log("supabase dumps")
    if SUPABASE_DIR.exists():
        metas = sorted(SUPABASE_DIR.glob("*.meta.json"))
        for path in metas:
            try:
                head = json.loads(path.read_text())
            except (OSError, ValueError):
                _log("  %-38s unreadable" % path.name)
                continue
            data_path = SUPABASE_DIR / ("%s.jsonl" % head.get("table"))
            _log("  %-30s %7d rows  %6s  pulled %s"
                 % (head.get("table"), head.get("row_count", -1),
                    _human(data_path.stat().st_size if data_path.exists() else None),
                    str(head.get("pulled_at_utc"))[:19]))
        missing = [spec["table"] for spec in SUPABASE_TABLES
                   if not (SUPABASE_DIR / ("%s.meta.json" % spec["table"])).exists()]
        if missing:
            _log("  not pulled: %s" % ", ".join(missing))
        if not metas:
            _log("  none pulled yet")
    else:
        _log("  none pulled yet")

    _log("")
    _log("derived artifacts (rebuild these after a sync)")
    for rel in ("prop_modeling/generated/regression_training_dataset.csv",
                "prop_modeling/generated/minutes_training_dataset.csv",
                "prop_modeling/generated/price_shopping_closing.csv"):
        path = BACKEND_DIR / rel
        if not path.exists():
            _log("  %-56s absent" % rel)
            continue
        stamp = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        rows, lo, hi = _csv_date_range(path, "game_date")
        _log("  %-56s built %s  %s .. %s" % (rel, stamp, lo or "-", hi or "-"))
    _log("")
    _log("  A derived artifact older than the game logs above is stale. Rebuild:")
    _log("    python3 prop_modeling/build_regression_dataset.py")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vm-only", action="store_true")
    parser.add_argument("--supabase-only", action="store_true")
    parser.add_argument("--inventory", action="store_true",
                        help="Report what is on disk and exit without transferring.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mirror", action="store_true",
                        help="Pass --delete to rsync, removing local-only files. "
                             "Off by default: this script collects data, it does not prune.")
    args = parser.parse_args()

    if args.inventory:
        inventory()
        return 0

    do_vm = not args.supabase_only
    do_supabase = not args.vm_only
    failures = []

    if do_vm:
        _log("=== VM (%s) ===" % VM_HOST)
        if not sync_vm(mirror=args.mirror, dry_run=args.dry_run):
            failures.append("vm")

    if do_supabase:
        _log("")
        _log("=== Supabase ===")
        if not sync_supabase(dry_run=args.dry_run):
            failures.append("supabase")

    if not args.dry_run:
        inventory()

    _log("")
    if failures:
        _log("INCOMPLETE: %s did not sync. Everything else above did."
             % ", ".join(failures))
        return 1
    _log("All requested sources synced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
