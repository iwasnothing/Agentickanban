#!/usr/bin/env bash
# Launch a Kanban MCP stdio server with live GitHub sync env and a fixed git config.
# DSH MCP spawn scrubs credential-shaped vars (GITHUB_TOKEN, GIT_CONFIG_KEY_*),
# which previously forced dry-run sync and broke worktree commits.
#
# Override paths with AIBOTS_CONTROL_ROOT, KANBAN_ROOT, KANBAN_SECRETS_DIR,
# GITHUB_TOKEN_FILE.
set -euo pipefail
umask 077
# shellcheck source=lib/control_root.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/control_root.sh"
ROOT="$AIBOTS_CONTROL_ROOT"
export GITHUB_REPO="${GITHUB_REPO:-iwasnothing/Agentickanban}"
export GITHUB_PROJECT="${GITHUB_PROJECT:-iwasnothing/2}"
SECRETS_DIR="$KANBAN_SECRETS_DIR"
TOKENFILE="${GITHUB_TOKEN_FILE:-$SECRETS_DIR/github-token}"
if [[ -z "${GITHUB_TOKEN:-}" && -r "$TOKENFILE" ]]; then
  GITHUB_TOKEN="$(tr -d '\n\r' < "$TOKENFILE")"
  export GITHUB_TOKEN
fi
# MCP wrappers can drop GIT_CONFIG_KEY_* but leave COUNT/VALUE ("missing config key GIT_CONFIG_KEY_0").
if [ -n "${GIT_CONFIG_COUNT:-}" ] && [ -z "${GIT_CONFIG_KEY_0:-}" ]; then
  unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0
fi
script="${1:-}"
if [[ "$script" != "ticket_mcp.py" && "$script" != "bots_mcp.py" ]]; then
  echo "usage: $0 ticket_mcp.py|bots_mcp.py" >&2
  exit 2
fi
exec "$ROOT/.venv/bin/python" "$ROOT/mcp_servers/$script"
