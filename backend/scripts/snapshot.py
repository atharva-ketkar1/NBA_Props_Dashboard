#!/usr/bin/env python3
"""
snapshot.py - build a cold archive of everything that is not in git, and push it
to the private archive repo as a GitHub Release asset.

Why: Supabase holds the only copy of the structured data, and several local
directories are gitignored (backend/data, prop_modeling/generated, logs). The VM
is disposable. This produces one verifiable zip that can rebuild all of it.

Usage:
    python backend/scripts/snapshot.py                      # tag: snapshot-YYYY-MM-DD
    python backend/scripts/snapshot.py --tag season-2026-27
    python backend/scripts/snapshot.py --no-upload          # build locally only
    python backend/scripts/snapshot.py --prune-before 2026-07-01

--prune-before deletes historical rows older than that date from Supabase *after*
a verified upload, to stay under the 500MB free-tier cap. It refuses to run
unless the upload succeeded.

Requires: gh CLI authenticated (for upload), backend/.env for Supabase creds.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
ARCHIVE_REPO = os.environ.get("ARCHIVE_REPO", "atharva-ketkar1/NBA_Dashboard_data_archive")

# Tables to export. Keep in sync with the upsert helpers in backend/utils/.
TABLES = [
    "players",
    "player_props",
    "line_movements",
    "games",
    "historical_odds",
    "historical_player_props",
    "edge_scores_current",
    "cron_state",
]

# Local trees that git does not track. (exported_regression_model IS tracked, so it
# is intentionally absent -- re-clone the main repo to recover the models.)
LOCAL_TREES = [
    ("local_data", os.path.join(BACKEND, "data")),
    ("prop_modeling_generated", os.path.join(BACKEND, "prop_modeling", "generated")),
    ("logs", os.path.join(BACKEND, "logs")),
]

PAGE = 1000


def load_env():
    env = {}
    path = os.path.join(BACKEND, ".env")
    if not os.path.exists(path):
        return env
    for raw in open(path):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def dump_tables(out_dir, env):
    import requests

    url = env.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL")
    key = env.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit("Missing SUPABASE_URL / SUPABASE_SERVICE_KEY (backend/.env)")

    os.makedirs(out_dir, exist_ok=True)
    counts = {}
    for table in TABLES:
        rows, offset = [], 0
        while True:
            headers = {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Range-Unit": "items",
                "Range": f"{offset}-{offset + PAGE - 1}",
            }
            resp = requests.get(f"{url}/rest/v1/{table}?select=*", headers=headers, timeout=120)
            if resp.status_code not in (200, 206):
                # A table that does not exist yet is not fatal -- record and move on.
                print(f"  {table:28} skipped [{resp.status_code}]")
                rows = None
                break
            batch = resp.json()
            if not batch:
                break
            rows.extend(batch)
            offset += len(batch)
            if len(batch) < PAGE:
                break
        if rows is None:
            continue
        with open(os.path.join(out_dir, f"{table}.json"), "w") as fh:
            json.dump(rows, fh)
        counts[table] = len(rows)
        print(f"  {table:28} {len(rows):>7} rows")
    with open(os.path.join(out_dir, "_row_counts.json"), "w") as fh:
        json.dump(counts, fh, indent=2)
    return counts


def copy_local(stage):
    present = {}
    for label, src in LOCAL_TREES:
        if not os.path.isdir(src):
            print(f"  {label:28} (absent, skipped)")
            continue
        dst = os.path.join(stage, label)
        shutil.copytree(
            src, dst,
            ignore=shutil.ignore_patterns(".DS_Store", "*.lock", "__pycache__"),
            dirs_exist_ok=True,
        )
        n = sum(len(f) for _, _, f in os.walk(dst))
        present[label] = n
        print(f"  {label:28} {n:>7} files")
    return present


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_metadata(stage, counts, trees, stamp):
    lines = [
        "NBA Dashboard cold archive",
        f"created: {dt.datetime.now(dt.timezone.utc).isoformat()}",
        "",
        "== supabase row counts ==",
        json.dumps(counts, indent=2),
        "",
        "== local trees (gitignored; this is their only copy) ==",
        json.dumps(trees, indent=2),
        "",
        "== inventory ==",
    ]
    for base, _, files in sorted(os.walk(stage)):
        for name in sorted(files):
            p = os.path.join(base, name)
            rel = os.path.relpath(p, stage)
            if rel in ("MANIFEST.txt", "SHA256SUMS"):
                continue
            lines.append(f"{os.path.getsize(p) // 1024:>8}KB  {rel}")
    with open(os.path.join(stage, "MANIFEST.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    sums = []
    for base, _, files in sorted(os.walk(stage)):
        for name in sorted(files):
            if name == "SHA256SUMS":
                continue
            p = os.path.join(base, name)
            sums.append(f"{sha256(p)}  {os.path.relpath(p, stage)}")
    with open(os.path.join(stage, "SHA256SUMS"), "w") as fh:
        fh.write("\n".join(sums) + "\n")


def copy_schema(stage):
    src = os.path.join(ROOT, "supabase", "migrations")
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(stage, "schema"), dirs_exist_ok=True)


def build_zip(stage, stamp):
    # Write OUTSIDE the git repo by default. The main repo is public, and an 18MB
    # archive of sportsbook data sitting in the working tree is one `git add -A`
    # away from being published. Override with SNAPSHOT_OUT_DIR.
    out_dir = os.environ.get("SNAPSHOT_OUT_DIR") or os.path.dirname(ROOT)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"nba_dashboard_backup_{stamp}.zip")
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for base, _, files in os.walk(stage):
            for name in files:
                p = os.path.join(base, name)
                zf.write(p, os.path.join("nba_bk_stage", os.path.relpath(p, stage)))
    return out


def upload(zip_path, stage, tag, counts):
    notes = [
        f"Cold archive taken {dt.date.today().isoformat()}.",
        "",
        "Supabase row counts:",
        "```",
        json.dumps(counts, indent=2),
        "```",
        "Contents: local_data/ -> backend/data/, prop_modeling_generated/ ->"
        " backend/prop_modeling/generated/, logs/ -> backend/logs/,"
        " supabase_dump/ -> Postgres, schema/ -> supabase/migrations/.",
        "",
        "Excludes backend/.env (no secrets archived) and exported_regression_model/"
        " (tracked in the main repo).",
        "",
        "Verify: `shasum -a 256 -c SHA256SUMS`",
    ]
    manifest = os.path.join(stage, "MANIFEST.txt")
    sums = os.path.join(stage, "SHA256SUMS")
    cmd = [
        "gh", "release", "create", tag,
        "--repo", ARCHIVE_REPO,
        "--title", f"Archive {tag}",
        "--notes", "\n".join(notes),
        zip_path, manifest, sums,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # Release may already exist -- upload/replace assets into it instead.
        if "already exists" in (res.stderr or ""):
            res2 = subprocess.run(
                ["gh", "release", "upload", tag, "--repo", ARCHIVE_REPO, "--clobber",
                 zip_path, manifest, sums],
                capture_output=True, text=True,
            )
            if res2.returncode != 0:
                raise SystemExit(f"gh upload failed: {res2.stderr}")
        else:
            raise SystemExit(f"gh release create failed: {res.stderr}")
    return True


def verify_remote(zip_path, tag, local_digest):
    """Download the asset back and confirm it is byte-identical before pruning."""
    with tempfile.TemporaryDirectory() as tmp:
        res = subprocess.run(
            ["gh", "release", "download", tag, "--repo", ARCHIVE_REPO,
             "-p", os.path.basename(zip_path), "-D", tmp],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            print(f"  verify: download failed: {res.stderr.strip()}")
            return False
        got = os.path.join(tmp, os.path.basename(zip_path))
        if not os.path.exists(got):
            print("  verify: asset not found after download")
            return False
        return sha256(got) == local_digest


def prune(before, env):
    import requests
    url = env.get("SUPABASE_URL"); key = env.get("SUPABASE_SERVICE_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"}
    for table, col in (("historical_player_props", "game_date"), ("historical_odds", "game_date")):
        r = requests.delete(f"{url}/rest/v1/{table}?{col}=lt.{before}", headers=headers, timeout=120)
        print(f"  pruned {table} where {col} < {before}: HTTP {r.status_code} {r.headers.get('content-range','')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=f"snapshot-{dt.date.today().isoformat()}")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--prune-before", metavar="YYYY-MM-DD",
                    help="after a VERIFIED upload, delete history older than this from Supabase")
    args = ap.parse_args()

    env = load_env()
    stamp = dt.date.today().isoformat()
    stage = tempfile.mkdtemp(prefix="nba_snapshot_")
    try:
        print("Dumping Supabase tables:")
        counts = dump_tables(os.path.join(stage, "supabase_dump"), env)
        print("Copying gitignored local trees:")
        trees = copy_local(stage)
        copy_schema(stage)
        write_metadata(stage, counts, trees, stamp)

        zip_path = build_zip(stage, stamp)
        digest = sha256(zip_path)
        size_mb = os.path.getsize(zip_path) / 1048576
        print(f"\nArchive: {zip_path}  ({size_mb:.1f} MB)")
        print(f"sha256:  {digest}")

        if args.no_upload:
            print("\n--no-upload set; stopping here.")
            return 0

        print(f"\nUploading to {ARCHIVE_REPO} as '{args.tag}' ...")
        upload(zip_path, stage, args.tag, counts)
        print("Verifying round-trip ...")
        ok = verify_remote(zip_path, args.tag, digest)
        print("  verified byte-identical" if ok else "  VERIFY FAILED")
        if not ok:
            return 1

        if args.prune_before:
            print(f"\nPruning Supabase history older than {args.prune_before} ...")
            prune(args.prune_before, env)
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
