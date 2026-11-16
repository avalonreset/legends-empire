#!/usr/bin/env bash
# Read-only compatibility diagnostic for the legacy address allocator.
#
# Address reservation now belongs exclusively to a reviewed
# claude-empire.transaction.v1 operation with address_requests. This helper
# only prints the next observed counter value and never creates locks, counters,
# directories, or files.
#
# Usage:
#   scripts/allocate-address.sh [--vault PATH] [--peek]
#
# Legacy `allocate` and `--rebuild` modes fail closed with migration guidance.

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPLICIT_VAULT=""
MODE="peek"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --vault)
      [ "$#" -ge 2 ] || { echo "ERR: --vault requires a path" >&2; exit 2; }
      EXPLICIT_VAULT="$2"
      shift
      ;;
    --vault=*) EXPLICIT_VAULT="${1#--vault=}" ;;
    --peek) MODE="peek" ;;
    allocate|--rebuild) MODE="disabled" ;;
    -h|--help)
      sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "ERR: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$MODE" = "disabled" ]; then
  echo "ERR LEGACY_ALLOCATOR_READ_ONLY: reserve addresses through one reviewed transaction address_requests bundle; direct allocation/rebuild is disabled" >&2
  exit 2
fi

PYTHONPATH="$PLUGIN_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - "$EXPLICIT_VAULT" "$PLUGIN_ROOT" <<'PY'
import os
import re
import stat
import sys
from pathlib import Path

from claude_empire.paths import VaultSelectionError, is_name_surrogate, resolve_vault_root
from claude_empire.transaction import TransactionError, read_vault_regular

explicit = sys.argv[1] or None
plugin_root = Path(sys.argv[2])
try:
    root = resolve_vault_root(
        explicit, start=Path.cwd(), plugin_root=plugin_root
    ).root
    raw = read_vault_regular(
        root, ".vault-meta/address-counter.txt", limit=64 * 1024
    )
except (VaultSelectionError, TransactionError) as exc:
    print(f"ERR {exc.code}: {exc}", file=sys.stderr)
    raise SystemExit(2)

if raw is not None:
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        value = ""
    if not value.isdigit() or not 1 <= int(value) <= 999999:
        print("ERR INVALID_ADDRESS_COUNTER: counter must contain 1..999999", file=sys.stderr)
        raise SystemExit(3)
    print(int(value))
    raise SystemExit(0)


def _is_unaliased_dir(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return not is_name_surrogate(st) and stat.S_ISDIR(st.st_mode)


def _is_unaliased_regular_file(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return not is_name_surrogate(st) and stat.S_ISREG(st.st_mode)


highest = 0
wiki = root / "wiki"
if _is_unaliased_dir(wiki):
    pages: list[Path] = []
    for current, dirnames, filenames in os.walk(wiki, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(
            d for d in dirnames if _is_unaliased_dir(current_path / d)
        )
        for fname in sorted(filenames):
            if not fname.lower().endswith(".md"):
                continue
            page = current_path / fname
            if _is_unaliased_regular_file(page):
                pages.append(page)
    for page in sorted(pages):
        try:
            page.resolve(strict=True).relative_to(wiki.resolve(strict=True))
            text = page.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            continue
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = re.fullmatch(r"address:\s*c-([0-9]{6})\s*", line)
            if match:
                highest = max(highest, int(match.group(1)))
print(highest + 1)
PY
