#!/usr/bin/env bash
# Box-side poller for the Vercel Agentic Kanban UI (optional leftover; not required
# for the local FastAPI UI). Posts health heartbeats and picks up GitHub issues
# labelled awaiting-triage.
# Secrets are read from env/files and never printed.
#
#   tmux new-session -d -s kanban-bridge -c "$AIBOTS_CONTROL_ROOT" \
#     "$AIBOTS_CONTROL_ROOT/scripts/start_kanban_bridge.sh 2>&1 | tee -a $AIBOTS_CONTROL_ROOT/.state/kanban-bridge.log"
set -euo pipefail
umask 077
# shellcheck source=lib/control_root.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/control_root.sh"
ROOT="$AIBOTS_CONTROL_ROOT"
export GITHUB_REPO="${GITHUB_REPO:-iwasnothing/Agentickanban}"
export GITHUB_PROJECT="${GITHUB_PROJECT:-iwasnothing/2}"
SECRETS_DIR="$KANBAN_SECRETS_DIR"

# Optional 600-mode env files. Values already in the environment win.
load_env() {
  local f="$1"
  [[ -r "$f" ]] || return 0
  local _pre_bridge="${BRIDGE_SECRET:-}"
  local _pre_url="${KANBAN_VERCEL_URL:-}"
  local _pre_gh="${GITHUB_TOKEN:-}"
  set -a
  # shellcheck disable=SC1090
  source "$f"
  set +a
  [[ -n "$_pre_bridge" ]] && export BRIDGE_SECRET="$_pre_bridge"
  [[ -n "$_pre_url" ]] && export KANBAN_VERCEL_URL="$_pre_url"
  [[ -n "$_pre_gh" ]] && export GITHUB_TOKEN="$_pre_gh"
  unset _pre_bridge _pre_url _pre_gh
}
load_env "${KANBAN_VERCEL_ENV_FILE:-$SECRETS_DIR/kanban-vercel.env}"
load_env "${KANBAN_WEBUI_ENV_FILE:-$SECRETS_DIR/kanban-webui.env}"

TOKENFILE="${GITHUB_TOKEN_FILE:-$SECRETS_DIR/github-token}"
if [[ -z "${GITHUB_TOKEN:-}" && -r "$TOKENFILE" ]]; then
  GITHUB_TOKEN="$(tr -d '[:space:]' < "$TOKENFILE")"
  export GITHUB_TOKEN
fi

if [[ -z "${KANBAN_VERCEL_URL:-}" && -r "$ROOT/.state/kanban-vercel-url.txt" ]]; then
  # first non-empty line only
  KANBAN_VERCEL_URL="$(grep -m1 -E '^https://' "$ROOT/.state/kanban-vercel-url.txt" || true)"
  export KANBAN_VERCEL_URL
fi

if [[ -z "${BRIDGE_SECRET:-}" ]]; then
  echo "start_kanban_bridge: BRIDGE_SECRET is not set (expected in $SECRETS_DIR/kanban-vercel.env)" >&2
  exit 1
fi
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "start_kanban_bridge: GITHUB_TOKEN is not set" >&2
  exit 1
fi

# Broken GIT_CONFIG_COUNT without KEY_0 breaks ticket auto-commits.
if [ -n "${GIT_CONFIG_COUNT:-}" ] && [ -z "${GIT_CONFIG_KEY_0:-}" ]; then
  unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0
fi

cd "$ROOT"
# Prefer the webui venv (zstandard + mcp already installed); fall back to the control venv.
PY="$ROOT/webui/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/.venv/bin/python"
fi
exec "$PY" "$ROOT/scripts/kanban_bridge.py"
