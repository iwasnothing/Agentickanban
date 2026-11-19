#!/usr/bin/env bash
# Start the Agentic Kanban web UI (FastAPI, 127.0.0.1:${KANBAN_WEBUI_PORT:-8787}).
# Secrets are read from env/files and never printed.
#   tmux new-session -d -s kanban-webui "$AIBOTS_CONTROL_ROOT/scripts/start_kanban_webui.sh"
#
# Paths default to the reference box and can be overridden with
# AIBOTS_CONTROL_ROOT, KANBAN_ROOT, KANBAN_SECRETS_DIR, KANBAN_WEBUI_ENV_FILE,
# GITHUB_TOKEN_FILE.
set -euo pipefail
umask 077
# shellcheck source=lib/control_root.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/control_root.sh"
ROOT="$AIBOTS_CONTROL_ROOT"
SECRETS_DIR="$KANBAN_SECRETS_DIR"
# Optional 600-mode env file (KANBAN_INVITE_CODE, SIMULAR_BOX_TOKEN) so a tmux server that predates the
# secrets still gets them without putting values on a command line. Values already in env win.
ENVFILE="${KANBAN_WEBUI_ENV_FILE:-$SECRETS_DIR/kanban-webui.env}"
if [[ -r "$ENVFILE" ]]; then
  _pre_code="${KANBAN_INVITE_CODE:-}"; _pre_box="${SIMULAR_BOX_TOKEN:-}"
  set -a; source "$ENVFILE"; set +a
  [[ -n "$_pre_code" ]] && export KANBAN_INVITE_CODE="$_pre_code"
  [[ -n "$_pre_box" ]] && export SIMULAR_BOX_TOKEN="$_pre_box"
  unset _pre_code _pre_box
fi
export GITHUB_REPO="${GITHUB_REPO:-iwasnothing/Agentickanban}"
export GITHUB_PROJECT="${GITHUB_PROJECT:-iwasnothing/2}"
TOKENFILE="${GITHUB_TOKEN_FILE:-$SECRETS_DIR/github-token}"
if [[ -z "${GITHUB_TOKEN:-}" && -r "$TOKENFILE" ]]; then
  GITHUB_TOKEN="$(tr -d '[:space:]' < "$TOKENFILE")"
  export GITHUB_TOKEN
fi
if [[ -z "${KANBAN_INVITE_CODE:-}" ]]; then
  echo "start_kanban_webui: KANBAN_INVITE_CODE not in env; the app may fall back to $SECRETS_DIR/kanban-invite-code (generated if missing)" >&2
fi
if [[ -z "${SIMULAR_BOX_TOKEN:-}" ]]; then
  echo "start_kanban_webui: warning: SIMULAR_BOX_TOKEN not set; Simular VM health will show red" >&2
fi
# Broken GIT_CONFIG_COUNT without KEY_0 breaks ticket auto-commits.
if [ -n "${GIT_CONFIG_COUNT:-}" ] && [ -z "${GIT_CONFIG_KEY_0:-}" ]; then
  unset GIT_CONFIG_COUNT GIT_CONFIG_VALUE_0
fi
cd "$ROOT/webui"
exec "$ROOT/webui/.venv/bin/uvicorn" app:app --host 127.0.0.1 --port "${KANBAN_WEBUI_PORT:-8787}" \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-access-log
