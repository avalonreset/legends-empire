#!/usr/bin/env bash
set -euo pipefail

# Print wiki/hot.md only for a marked codex-obsidian vault.
# Keeps globally installed hooks from trusting an arbitrary repo that happens
# to contain a wiki/hot.md file.

HOT_CACHE="wiki/hot.md"
MAX_BYTES="${CODEX_OBSIDIAN_HOT_CACHE_MAX_BYTES:-12000}"

case "$MAX_BYTES" in
  ''|*[!0-9]*)
    echo "CODEX_OBSIDIAN_HOT_CACHE_MAX_BYTES must be a positive integer" >&2
    exit 2
    ;;
esac

if [ "$MAX_BYTES" -le 0 ]; then
  echo "CODEX_OBSIDIAN_HOT_CACHE_MAX_BYTES must be a positive integer" >&2
  exit 2
fi

is_codex_obsidian_vault() {
  [ -f ".vault-meta/codex-obsidian.vault" ] && return 0
  return 1
}

[ -f "$HOT_CACHE" ] || exit 0
is_codex_obsidian_vault || exit 0

head -c "$MAX_BYTES" "$HOT_CACHE"
