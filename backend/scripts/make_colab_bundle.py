#!/usr/bin/env python3
"""Pack everything a Colab runtime needs to rebuild the dataset and retrain.

Why a bundle and not a git clone: `backend/data` is gitignored, so a clone on
Colab gets the code and none of the inputs. And why inputs rather than the
derived dataset: the game logs plus enrichment snapshots are ~17 MB while
`regression_training_dataset.csv` is 287 MB, and the builder reproduces the
big file from the small ones in a few minutes. Uploading the derived artifact
would also let a Colab run train on a dataset built by different code than the
bundle carries, which is the class of mismatch this repo keeps getting bitten
by.

Self-contained on purpose -- code and data in one archive, no git credentials
and no assumption about whether the GitHub repo is public.

A note on what Colab is and is not good for here. CatBoost's MultiQuantile
loss is CPU-only (`task_type="CPU"` is pinned in the trainer for exactly this
reason), so a GPU runtime buys nothing, and Colab's free tier gives about two
vCPUs against an M1's eight. The reason to run there is to not tie up a laptop
for an hour, not speed.

Usage:
    cd backend
    python3 scripts/make_colab_bundle.py
    # -> backend/generated/colab_input.tar.gz, then follow the printed steps
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
from pathlib import Path
from typing import Iterable, List, Tuple

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUT = BACKEND_DIR / "generated" / "colab_input.tar.gz"

# Code: the import closure of build_regression_dataset + train_regression_model.
# Whole directories rather than named files, because the closure has moved twice
# already (quantile_metrics and splits are both new) and a missing module fails
# the run after the upload.
CODE_GLOBS: Tuple[Tuple[str, str], ...] = (
    ("backend/prop_modeling", "*.py"),
    ("backend/scrapers", "season_type.py"),
    ("backend/scrapers", "__init__.py"),
    ("backend/utils", "*.py"),
)

# Data: the builder's actual inputs. Everything else under data/ is either
# derived or irrelevant to training.
DATA_FILES: Tuple[str, ...] = (
    "backend/data/current/gamelogs_2026-27.csv",
    "backend/data/current/opponent_defensive_ranks.json",
    "backend/data/current/opp_def_zones.json",
    "backend/data/current/play_type_analysis.json",
    "backend/data/current/shot_type_analysis.json",
    "backend/data/current/boxscores.json",
    "backend/data/current/season_stats.csv",
    "backend/data/current/nba_injury_report.json",
)
DATA_GLOBS: Tuple[Tuple[str, str], ...] = (
    # Prior seasons, excluding the ".backup." copies discover_gamelog_paths skips.
    ("backend/data/archive", "gamelogs_*.csv"),
)

# The shipping bundle goes along so the trainer's --feature-contract check and
# its promotion-guardrail carry-forward both work on Colab exactly as they do
# locally. Without it the run would silently write the code-default guardrail
# thresholds, which are looser than the calibrated ones edge_score reads.
CONTRACT_GLOBS: Tuple[Tuple[str, str], ...] = (
    ("backend/prop_modeling/exported_regression_model", "model_metadata.json"),
    ("backend/prop_modeling/exported_regression_model", "minutes_model_metadata.json"),
)

PIN_FILES: Tuple[str, ...] = ("requirements-vm.txt",)


def _collect(verbose: bool) -> List[Tuple[Path, str]]:
    """(absolute source, archive member name) pairs."""
    members: List[Tuple[Path, str]] = []
    seen = set()

    def add(path: Path) -> None:
        if not path.is_file():
            return
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in seen:
            return
        seen.add(rel)
        members.append((path, rel))

    for rel_dir, pattern in CODE_GLOBS + DATA_GLOBS + CONTRACT_GLOBS:
        directory = REPO_ROOT / rel_dir
        if not directory.is_dir():
            if verbose:
                print("  skip (absent dir): %s" % rel_dir)
            continue
        for path in sorted(directory.glob(pattern)):
            if ".backup." in path.name:
                continue
            add(path)

    for rel in DATA_FILES + PIN_FILES:
        path = REPO_ROOT / rel
        if path.is_file():
            add(path)
        elif verbose:
            print("  skip (absent): %s" % rel)
    return members


def _required_present(members: Iterable[Tuple[Path, str]]) -> List[str]:
    """Inputs whose absence would make the Colab run fail or train on less."""
    names = {rel for _, rel in members}
    required = [
        "backend/prop_modeling/build_regression_dataset.py",
        "backend/prop_modeling/train_regression_model.py",
        "backend/prop_modeling/splits.py",
        "backend/prop_modeling/quantile_metrics.py",
        "backend/scrapers/season_type.py",
        "backend/data/current/gamelogs_2026-27.csv",
        "backend/data/current/season_stats.csv",
    ]
    return [r for r in required if r not in names]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    verbose = not args.quiet

    members = _collect(verbose)
    missing = _required_present(members)
    if missing:
        print("Refusing to build the bundle: these inputs are absent, and a Colab run "
              "without them either fails on import or silently trains on less data:")
        for rel in missing:
            print("  - %s" % rel)
        print("\nRun `python3 scripts/sync_all_data.py` first.")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        for path, rel in members:
            tar.add(path, arcname=rel)
    tmp.replace(out)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    raw = sum(p.stat().st_size for p, _ in members)
    code = sum(1 for _, rel in members if rel.endswith(".py"))

    print("")
    print("bundle: %s" % out)
    print("  %d files (%d python), %.1f MB raw -> %.1f MB gzipped"
          % (len(members), code, raw / 1e6, out.stat().st_size / 1e6))
    print("  sha256[:16] = %s" % digest)

    gamelogs = [rel for _, rel in members if "gamelogs_" in rel]
    print("  game logs: %s" % ", ".join(Path(g).name for g in sorted(gamelogs)))
    print("")
    print("Next:")
    print("  1. Upload %s to Google Drive (anywhere; the notebook asks for the path)." % out.name)
    print("  2. Open notebooks/retrain_on_colab.ipynb and pick a Colab runtime as the")
    print("     kernel (the google.colab VS Code extension, or colab.research.google.com).")
    print("  3. Run the cells top to bottom. A CPU runtime is correct -- MultiQuantile")
    print("     is CPU-only, so a GPU runtime is slower for no benefit.")
    print("  4. The notebook writes the trained bundle back to Drive; download it into")
    print("     backend/prop_modeling/exported_regression_model/ and gate it with")
    print("     `python3 prop_modeling/backtest.py --model` before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
