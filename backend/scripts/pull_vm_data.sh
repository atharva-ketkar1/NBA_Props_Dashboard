#!/usr/bin/env bash
# Pull the VM's generated data files (backend/data/current + archive) into the
# matching local folders, so local JSON-mode dev isn't running on stale data.
# Mirrors the VM, so stale local-only files get deleted too.
# Usage: backend/scripts/pull_vm_data.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

HOST="oracle-nba"
REMOTE_DIR="NBA_Dashboard"
LOCAL_DATA="$REPO_ROOT/backend/data"

echo "Pulling data from $HOST:~/$REMOTE_DIR/backend/data -> $LOCAL_DATA"
rsync -az --delete -e ssh "$HOST:~/$REMOTE_DIR/backend/data/current/" "$LOCAL_DATA/current/"
rsync -az --delete -e ssh "$HOST:~/$REMOTE_DIR/backend/data/archive/" "$LOCAL_DATA/archive/"

echo "Done. Local data synced from the VM."
