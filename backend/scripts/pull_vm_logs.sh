#!/usr/bin/env bash
# Pull the VM's logs (including backend/logs/master_cron) into the matching
# local backend/logs folder, which is gitignored. Mirrors the VM, so stale
# local-only files get deleted too.
# Usage: backend/scripts/pull_vm_logs.sh [diagnostics_dir]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

HOST="oracle-nba"
REMOTE_DIR="NBA_Dashboard"
LOCAL_LOGS="$REPO_ROOT/backend/logs"
DEST="${1:-$REPO_ROOT/vm_logs/$(date +%Y%m%d_%H%M%S)}"

echo "Pulling logs from $HOST:~/$REMOTE_DIR/backend/logs -> $LOCAL_LOGS"
rsync -az --delete -e ssh "$HOST:~/$REMOTE_DIR/backend/logs/" "$LOCAL_LOGS/"

mkdir -p "$DEST"

echo "Capturing live diagnostics"
ssh -o ConnectTimeout=10 "$HOST" '
  echo "== crontab ==" && crontab -l
  echo
  echo "== disk ==" && df -h /
  echo
  echo "== memory ==" && free -h
  echo
  echo "== git status ==" && cd ~/'"$REMOTE_DIR"'/backend 2>/dev/null; cd ..; git -C ~/'"$REMOTE_DIR"' status --short --branch; git -C ~/'"$REMOTE_DIR"' log -1 --oneline
  echo
  echo "== recent cron processes ==" && ps aux | grep -i master_cron | grep -v grep
' > "$DEST/vm_diagnostics.txt" 2>&1 || true

echo "Done. Logs synced to $LOCAL_LOGS, diagnostics saved to $DEST"
