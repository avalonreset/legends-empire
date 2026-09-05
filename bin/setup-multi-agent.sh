#!/usr/bin/env bash
# Legends Obsidian: Python owns the shared installer safety contract.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$REPO_ROOT/bin/setup_multi_agent.py" "$@"
elif command -v python >/dev/null 2>&1; then
  exec python "$REPO_ROOT/bin/setup_multi_agent.py" "$@"
else
  echo "ERROR: Python 3.11+ is required." >&2
  exit 2
fi
