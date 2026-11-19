#!/usr/bin/env bash
# Sidecar for the DSH Superagent profile: periodically inspect every worker and unblock stalls.
# Override the control-plane root with AIBOTS_CONTROL_ROOT or KANBAN_ROOT.
set -euo pipefail
umask 077
# shellcheck source=lib/control_root.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/control_root.sh"
ROOT="$AIBOTS_CONTROL_ROOT"
cd "$ROOT"
if [ -n "${GIT_CONFIG_COUNT:-}" ] && [ -z "${GIT_CONFIG_KEY_0:-}" ]; then
  unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0
fi
mkdir -p "$ROOT/.state"
LOG="$ROOT/.state/watchdog.log"
# Single instance: flock the pid/log lock. Do not run a second overlapping loop.
exec flock -n "$ROOT/.state/watchdog.lock" \
  "$ROOT/.venv/bin/python" -u -m scripts.bots.watchdog loop --interval "${WATCHDOG_INTERVAL:-60}" \
  >>"$LOG" 2>&1
