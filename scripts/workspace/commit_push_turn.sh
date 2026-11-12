#!/usr/bin/env bash
# End-of-turn commit (and push, if the worktree has a remote) for a bot.
#   usage: commit_push_turn.sh <workspace> <bot-id> <ticket-id> <turn> [message]
# Commit message convention: "bot(<bot-id>): turn <n> for ticket <ticket-id>"
# Prints a JSON summary. Exit 0 on success (including "nothing to commit").
set -euo pipefail
WS="${1:?workspace}"; BOT_ID="${2:?bot-id}"; TICKET="${3:?ticket-id}"; TURN="${4:?turn}"
MSG="${5:-bot($BOT_ID): turn $TURN for ticket $TICKET}"
BRANCH="bots/$BOT_ID"
cd "$WS"
git add -A
if git diff --cached --quiet; then
  echo "{\"committed\": false, \"reason\": \"nothing to commit\", \"head\": \"$(git rev-parse --short HEAD)\"}"
  exit 0
fi
git -c user.name="${GIT_AUTHOR_NAME:-$BOT_ID}" -c user.email="${GIT_AUTHOR_EMAIL:-$BOT_ID@aibots.local}" commit -q -m "$MSG"
HEAD="$(git rev-parse --short HEAD)"
PUSHED=false; PUSH_NOTE="no remote configured"
REMOTE="$(git remote | head -n1 || true)"
if [ -n "$REMOTE" ]; then
  if git push -q "$REMOTE" "HEAD:refs/heads/$BRANCH" 2>/tmp/aibots-push.err; then PUSHED=true; PUSH_NOTE="pushed to $REMOTE/$BRANCH"
  else PUSH_NOTE="push failed: $(head -c 200 /tmp/aibots-push.err | tr '"\n' "' ")"; fi
fi
echo "{\"committed\": true, \"head\": \"$HEAD\", \"message\": \"$MSG\", \"pushed\": $PUSHED, \"push\": \"$PUSH_NOTE\"}"
