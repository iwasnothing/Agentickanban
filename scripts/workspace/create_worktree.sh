#!/usr/bin/env bash
# Create (or refresh) the git worktree for a bot and install its sandbox files.
#   usage: create_worktree.sh <bot-id> [project-repo] [workspaces-root]
# Result: <workspaces-root>/<bot-id> is a worktree of <project-repo> on branch bots/<bot-id>,
#         with .opencode/plugins/kanban-permission-gate.js (tool.execute.before gate) installed.
#         .ticket/ and .opencode/ are excluded from commits via the repo's info/exclude.
set -euo pipefail
BOT_ID="${1:?usage: create_worktree.sh <bot-id> [project-repo] [workspaces-root]}"
[[ "$BOT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || { echo "invalid bot id: $BOT_ID" >&2; exit 2; }
CONTROL="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="${2:-${AIBOTS_PROJECT_REPO:-/workspace/aibots/project-repo}}"
ROOT="${3:-${AIBOTS_WORKSPACES_ROOT:-/workspace/aibots/workspaces}}"
PYTHON="${AIBOTS_PYTHON:-$CONTROL/.venv/bin/python}"
WS="$ROOT/$BOT_ID"
BRANCH="bots/$BOT_ID"

git -C "$PROJECT" rev-parse --git-dir >/dev/null
mkdir -p "$ROOT"
if git -C "$PROJECT" worktree list --porcelain | grep -qx "worktree $WS"; then
  echo "worktree exists: $WS"
else
  if git -C "$PROJECT" show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git -C "$PROJECT" worktree add -q "$WS" "$BRANCH"
  else
    git -C "$PROJECT" worktree add -q -b "$BRANCH" "$WS"
  fi
  echo "created worktree $WS on $BRANCH"
fi

# keep sandbox/ticket files out of commits (info/exclude is shared by all worktrees)
EXCLUDE="$(git -C "$PROJECT" rev-parse --path-format=absolute --git-common-dir)/info/exclude"
mkdir -p "$(dirname "$EXCLUDE")"; touch "$EXCLUDE"
for pat in ".ticket/" ".opencode/"; do grep -qxF "$pat" "$EXCLUDE" || echo "$pat" >> "$EXCLUDE"; done

# install the permission gate plugin (OpenCode auto-loads .opencode/plugins/*.js)
mkdir -p "$WS/.opencode/plugins" "$WS/.ticket/attachments"
rm -f "$WS/.opencode/plugins/kanban-permission-gate.js"
sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__CONTROL__#$CONTROL#g" -e "s#__BOT__#$BOT_ID#g" -e "s#__WORKSPACE__#$WS#g" \
  "$CONTROL/scripts/permission_gate/opencode-plugin/kanban-permission-gate.js.tmpl" > "$WS/.opencode/plugins/kanban-permission-gate.js"
chmod 0444 "$WS/.opencode/plugins/kanban-permission-gate.js"

# per-bot config visible to the coding agent
for f in AGENTS.md SKILL.md mcp.json; do
  [ -f "$CONTROL/bots/$BOT_ID/$f" ] && cp "$CONTROL/bots/$BOT_ID/$f" "$WS/.ticket/$f"
done
echo "$WS"
