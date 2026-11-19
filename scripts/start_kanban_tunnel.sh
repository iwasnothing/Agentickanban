#!/usr/bin/env bash
# Optional public HTTPS link for the Kanban web UI via a free, no-account tunnel.
# On networks that only allow outbound TLS on 443, Cloudflare quick tunnels (7844),
# ssh tunnels (22/plain 443), tunnelmole (8083) and publiclocal may be blocked;
# localtolink (wss on 443) is what the reference deployment uses.
# Free localtolink links expire after ~2h (or can be closed server-side); a watchdog probes
# <url>/healthz every 30s and reconnects after 3 failures. The URL CHANGES on every reconnect.
# Current URL: .state/kanban-tunnel-url.txt   Log: .state/kanban-tunnel.log
# Override the control-plane root with AIBOTS_CONTROL_ROOT or KANBAN_ROOT.
set -uo pipefail
# shellcheck source=lib/control_root.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/control_root.sh"
ROOT="$AIBOTS_CONTROL_ROOT"
BIN="$ROOT/webui/tunnel/node_modules/.bin/localtolink"
PORT="${KANBAN_WEBUI_PORT:-8787}"
LOG="$ROOT/.state/kanban-tunnel.log"
URLF="$ROOT/.state/kanban-tunnel-url.txt"
mkdir -p "$ROOT/.state"
while true; do
  echo "$(date -Is) starting tunnel" >> "$LOG"
  rm -f "$URLF.pending"
  "$BIN" --port "$PORT" --json --no-qr > >(while IFS= read -r line; do
      echo "$(date -Is) $line" >> "$LOG"
      u="$(grep -oE 'https://[a-z0-9-]+\.localtolink\.com' <<<"$line" | head -1 || true)"
      if [[ -n "$u" ]]; then echo "$u" > "$URLF"; echo "$u" > "$URLF.pending"; fi
    done) 2>&1 &
  pid=$!
  fails=0
  sleep 15
  while kill -0 "$pid" 2>/dev/null; do
    u="$(cat "$URLF.pending" 2>/dev/null || true)"
    code="$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$u/healthz" 2>/dev/null || echo 000)"
    if [[ -n "$u" && "$code" == "200" ]]; then fails=0; else fails=$((fails+1)); echo "$(date -Is) watchdog: $u/healthz -> $code (fail $fails)" >> "$LOG"; fi
    if (( fails >= 3 )); then echo "$(date -Is) watchdog: restarting tunnel" >> "$LOG"; kill "$pid" 2>/dev/null; sleep 2; kill -9 "$pid" 2>/dev/null; break; fi
    sleep 30
  done
  wait "$pid" 2>/dev/null
  echo "$(date -Is) tunnel exited; retrying in 10s" >> "$LOG"
  sleep 10
done
