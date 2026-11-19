# shellcheck shell=bash
# Resolve AIBOTS_CONTROL_ROOT and KANBAN_SECRETS_DIR.
# Source this from start scripts. Do not execute it.
#
# AIBOTS_CONTROL_ROOT is this control-plane checkout.
# KANBAN_ROOT is the parent of the sibling checkouts, unless the operator
# pointed it at the control repo itself (detected by mcp_servers/ + tickets/).
# Defaults stay on the reference box: /workspace/ai-bot-system and /workspace/.secrets.

if [[ -z "${AIBOTS_CONTROL_ROOT:-}" ]]; then
  _kanban="${KANBAN_ROOT:-}"
  if [[ -n "$_kanban" && -d "$_kanban/mcp_servers" && -d "$_kanban/scripts" && -d "$_kanban/tickets" ]]; then
    AIBOTS_CONTROL_ROOT="$_kanban"
  elif [[ -n "$_kanban" && -d "$_kanban/Agentickanban/mcp_servers" ]]; then
    AIBOTS_CONTROL_ROOT="$_kanban/Agentickanban"
  elif [[ -n "$_kanban" && -d "$_kanban/ai-bot-system/mcp_servers" ]]; then
    AIBOTS_CONTROL_ROOT="$_kanban/ai-bot-system"
  else
    AIBOTS_CONTROL_ROOT="/workspace/ai-bot-system"
  fi
  unset _kanban
fi
export AIBOTS_CONTROL_ROOT
: "${KANBAN_SECRETS_DIR:=/workspace/.secrets}"
export KANBAN_SECRETS_DIR
