#!/usr/bin/env bash
# Readiness checks are local-only even when optional remote permission is present.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/vault/wiki" "$TMP/vault/.raw"
cat >"$TMP/bin/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
exit 7
SH
chmod +x "$TMP/bin/curl"
export CURL_LOG="$TMP/curl.log"
export PATH="$TMP/bin:$PATH"
export OLLAMA_URL="http://10.0.0.2:11434"
: >"$CURL_LOG"
bash "$ROOT/scripts/setup-retrieve.sh" --vault "$TMP/vault" --check >"$TMP/local.out" 2>&1
bash "$ROOT/scripts/setup-retrieve.sh" --vault "$TMP/vault" --check --allow-remote-ollama >"$TMP/remote.out" 2>&1
if [ -s "$CURL_LOG" ]; then
  echo "FAIL: readiness check performed a remote request"
  exit 1
fi
grep -q -- "READY: retrieval helpers are installed" "$TMP/local.out"
grep -q -- "READY: retrieval helpers are installed" "$TMP/remote.out"
echo "All local-only setup-retrieve readiness tests passed."
