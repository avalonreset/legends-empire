#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAKE_BIN="$TMP/bin"
LOG="$TMP/curl.log"
mkdir -p "$FAKE_BIN"
cat >"$FAKE_BIN/curl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_LOG"
exit 7
SH
chmod +x "$FAKE_BIN/curl"

run_setup() {
  (
    cd "$ROOT"
    CURL_LOG="$LOG" PATH="$FAKE_BIN:$PATH" OLLAMA_URL="http://10.0.0.2:11434" \
      bash bin/setup-retrieve.sh "$@"
  )
}

: >"$LOG"
run_setup --check >/tmp/setup-retrieve-no-remote.out 2>&1
if [ -s "$LOG" ]; then
  echo "FAIL: remote ollama was probed without --allow-remote-ollama"
  exit 1
fi
grep -q -- "Refusing to probe remote ollama" /tmp/setup-retrieve-no-remote.out

: >"$LOG"
run_setup --check --allow-remote-ollama >/tmp/setup-retrieve-remote.out 2>&1
if ! grep -q -- "10.0.0.2:11434/api/tags" "$LOG"; then
  echo "FAIL: remote ollama was not probed with --allow-remote-ollama"
  exit 1
fi

echo "All setup-retrieve flag tests passed."
