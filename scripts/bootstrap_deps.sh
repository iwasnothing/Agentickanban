#!/usr/bin/env bash
# Clone the official DeepSeek Superagent prototype and OpenMausBot repositories
# as *siblings* of this control-plane repo (or under $KANBAN_ROOT) at pinned SHAs.
#
# This script only clones or reports status. It does not install Node packages,
# create DSH profiles, or start services. See SETUP.md for the rest.
#
# Environment:
#   KANBAN_ROOT            Parent directory for sibling checkouts
#                          (default: parent of this control-plane repo)
#   DSH_SUPERAGENT_ROOT    Override the DSH checkout path
#   OPENMAUSBOT_ROOT       Override the OpenMausBot checkout path
#
# Safety: if a destination already exists, it is left untouched (no reset,
# no re-clone, no checkout). That keeps a live machine from being disrupted.
set -euo pipefail

DSH_URL="https://github.com/deepseek-ai/dsh-superagent-prototype.git"
DSH_SHA="8c32a9dc5d72c033816c6a8110b37f40cf94fba4"
OMB_URL="https://github.com/milind-soni/OpenMausBot.git"
OMB_SHA="2d74ba9616270b4a1df1ebfa4a912b55bd5cb4ea"

CONTROL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KANBAN_ROOT="${KANBAN_ROOT:-$(cd "$CONTROL_ROOT/.." && pwd)}"
DSH_DIR="${DSH_SUPERAGENT_ROOT:-$KANBAN_ROOT/dsh-superagent-prototype}"
OMB_DIR="${OPENMAUSBOT_ROOT:-$KANBAN_ROOT/OpenMausBot}"

status=0

report_existing() {
  local name="$1" dest="$2" sha="$3"
  if [[ ! -d "$dest/.git" ]]; then
    echo "error: $dest exists but is not a git checkout; leaving it alone." >&2
    status=1
    return
  fi
  local head remote
  head="$(git -C "$dest" rev-parse HEAD)"
  remote="$(git -C "$dest" remote get-url origin 2>/dev/null || echo "(no origin)")"
  if [[ "$head" == "$sha" ]]; then
    echo "$name: already at pinned $sha"
    echo "         $dest"
    echo "         origin $remote"
  else
    echo "$name: exists at $dest"
    echo "         HEAD   $head"
    echo "         pin    $sha"
    echo "         origin $remote"
    echo "         Not touching an existing checkout. On a clean throwaway tree you can:"
    echo "           git -C \"$dest\" fetch origin && git -C \"$dest\" checkout $sha"
  fi
}

clone_pinned() {
  local name="$1" url="$2" dest="$3" sha="$4"
  if [[ -e "$dest" ]]; then
    report_existing "$name" "$dest" "$sha"
    return
  fi
  echo "$name: cloning $url -> $dest"
  git clone "$url" "$dest"
  echo "$name: checking out pinned $sha (detached)"
  git -C "$dest" checkout --detach "$sha"
  echo "$name: ready at $dest ($sha)"
}

echo "Control-plane repo: $CONTROL_ROOT"
echo "Sibling parent:     $KANBAN_ROOT"
echo
clone_pinned "dsh-superagent-prototype" "$DSH_URL" "$DSH_DIR" "$DSH_SHA"
echo
clone_pinned "OpenMausBot" "$OMB_URL" "$OMB_DIR" "$OMB_SHA"
echo
echo "Next steps (do not skip SETUP.md):"
echo "  1. Install Node.js 24+, pnpm, git, tmux, Python 3.10+, and (for official DSH) gh."
echo "  2. Follow SETUP.md to install DeepSeek Harness (@deepseek-ai/dsh@0.2.1-rc.3),"
echo "     build the DSH prototype, install OpenMausBot (headless), and this repo's venvs."
echo "  3. Create mode-600 secret files (keys and tokens). Never put values in git."
echo "  4. Copy deploy/dsh/cordis.patch.yml.example to ~/.dsh/profiles/superagent/cordis.patch.yml"
echo "     and edit the absolute paths."
echo "  5. Start tmux sessions as described in SETUP.md, then verify /healthz, DSH, and OpenMausBot."
echo
echo "Pinned SHAs:"
echo "  dsh-superagent-prototype  $DSH_SHA"
echo "  OpenMausBot               $OMB_SHA"
exit "$status"
