#!/usr/bin/env bash
# Start the superagent DSH profile with the Kanban OpenRouter key.
# The key is read from a root-restricted file; it is never passed on the command line.
set -euo pipefail
umask 077
KEYFILE="${OPENROUTER_KANBAN_KEY_FILE:-/workspace/.secrets/openrouter-kanban-bot.key}"
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
# Unattended MCP/tool use for the Kanban manager loop (default policy is ask).
export DSH_PERMISSION_MODE="${DSH_PERMISSION_MODE:-danger-full-access}"
# MCP stdio spawners may strip GIT_CONFIG_KEY_* and leave a broken COUNT/VALUE pair.
if [ -n "${GIT_CONFIG_COUNT:-}" ] && [ -z "${GIT_CONFIG_KEY_0:-}" ]; then
  unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0
fi
exec /home/box/.local/bin/dsh --profile superagent --no-open "$@"
