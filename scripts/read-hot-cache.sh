#!/usr/bin/env bash
set -euo pipefail

# Print wiki/hot.md only for a marked claude-empire vault.
# Keeps globally installed hooks from trusting an arbitrary repo that happens
# to contain a wiki/hot.md file.

HOT_CACHE="wiki/hot.md"
MAX_BYTES="${CLAUDE_EMPIRE_HOT_CACHE_MAX_BYTES:-${CODEX_EMPIRE_HOT_CACHE_MAX_BYTES:-12000}}"

case "$MAX_BYTES" in
  ''|*[!0-9]*)
    echo "CLAUDE_EMPIRE_HOT_CACHE_MAX_BYTES must be a positive integer" >&2
    exit 2
    ;;
esac

if [ "$MAX_BYTES" -le 0 ]; then
  echo "CLAUDE_EMPIRE_HOT_CACHE_MAX_BYTES must be a positive integer" >&2
  exit 2
fi

is_empire_vault() {
  [ ! -L ".claude-empire.json" ] && [ -f ".claude-empire.json" ] && return 0
  [ ! -L ".vault-meta" ] && [ ! -L ".vault-meta/claude-empire.vault" ] && [ -f ".vault-meta/claude-empire.vault" ] && return 0
  [ ! -L ".vault-meta" ] && [ ! -L ".vault-meta/codex-empire.vault" ] && [ -f ".vault-meta/codex-empire.vault" ] && return 0
  return 1
}

[ ! -L "wiki" ] && [ ! -L "$HOT_CACHE" ] && [ -f "$HOT_CACHE" ] || exit 0
is_empire_vault || exit 0

head -c "$MAX_BYTES" "$HOT_CACHE"
