#!/usr/bin/env bash
# Start the superagent DSH profile with the user's OpenRouter key (free models).
# The key is read from a root-restricted file; it is never passed on the command line.
#
# Paths default to the reference box (/workspace, /home/box/.local/bin) and can be
# overridden with AIBOTS_CONTROL_ROOT, KANBAN_ROOT, KANBAN_SECRETS_DIR,
# OPENROUTER_KANBAN_KEY_FILE, DSH_SUPERAGENT_ROOT, DSH_BIN, DSH_ARTIFACTS_DIR.
set -euo pipefail
umask 077
# shellcheck source=lib/control_root.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/control_root.sh"
CONTROL_ROOT="$AIBOTS_CONTROL_ROOT"
SECRETS_DIR="$KANBAN_SECRETS_DIR"
KEYFILE="${OPENROUTER_KANBAN_KEY_FILE:-$SECRETS_DIR/openrouter-user.key}"
if [[ ! -r "$KEYFILE" ]]; then
  echo "start_dsh_superagent: missing key file" >&2
  exit 1
fi
# Trim whitespace/newlines only; do not print the value.
OPENROUTER_KANBAN_API_KEY="$(tr -d '\n\r' < "$KEYFILE")"
if [[ -z "$OPENROUTER_KANBAN_API_KEY" ]]; then
  echo "start_dsh_superagent: key file is empty" >&2
  exit 1
fi
export OPENROUTER_KANBAN_API_KEY
export OPENROUTER_API_KEY="$OPENROUTER_KANBAN_API_KEY"
# Unattended MCP/tool use for the Kanban manager loop (default policy is ask).
export DSH_PERMISSION_MODE="${DSH_PERMISSION_MODE:-danger-full-access}"
# MCP stdio spawners may strip GIT_CONFIG_KEY_* and leave a broken COUNT/VALUE pair.
if [ -n "${GIT_CONFIG_COUNT:-}" ] && [ -z "${GIT_CONFIG_KEY_0:-}" ]; then
  unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0
fi
# Worker watchdog sidecar (does not restart DSH; inspects all configured bots every minute).
WATCHDOG_TMUX="${KANBAN_WATCHDOG_TMUX:-kanban-watchdog}"
if ! tmux has-session -t "$WATCHDOG_TMUX" 2>/dev/null; then
  tmux new-session -d -s "$WATCHDOG_TMUX" -c "$CONTROL_ROOT" "$CONTROL_ROOT/scripts/start_worker_watchdog.sh"
fi

# Tee the UI URL (token=...) into artifacts so the web UI can authenticate to DSH after a restart.
DSH_SUPERAGENT_ROOT="${DSH_SUPERAGENT_ROOT:-/workspace/dsh-superagent-prototype}"
ART="${DSH_ARTIFACTS_DIR:-$DSH_SUPERAGENT_ROOT/.artifacts}"
mkdir -p "$ART"
chmod 700 "$ART"
LOG="$ART/dsh-superagent-superagent-$(date +%Y%m%d-%H%M%S).log"
DSH_BIN="${DSH_BIN:-$(command -v dsh 2>/dev/null || true)}"
DSH_BIN="${DSH_BIN:-/home/box/.local/bin/dsh}"
if [[ ! -x "$DSH_BIN" ]]; then
  echo "start_dsh_superagent: dsh executable not found (set DSH_BIN)" >&2
  exit 1
fi
exec > >(tee -a "$LOG") 2>&1
exec "$DSH_BIN" --profile "${DSH_PROFILE:-superagent}" --no-open "$@"
