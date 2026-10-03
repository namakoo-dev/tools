#!/bin/bash
# Session-start hook for git state recovery.
# Automatically check out the working branch and show status when Claude Code starts.
#
# Installation:
#   mkdir -p ~/.claude
#   cp cloud/session-start-hook.sh ~/.claude/session-start-hook.sh
#   chmod +x ~/.claude/session-start-hook.sh
#
# Then add this to ~/.claude/launcher-settings.json:
#   "hooks": {
#     "SessionStart": [{
#       "matcher": "",
#       "hooks": [{
#         "type": "command",
#         "command": "~/.claude/session-start-hook.sh"
#       }]
#     }]
#   }
#

set -u

log() { echo "[session-start] $*" >&2; }

BRANCH="ccr-941a3e17-fl3t1x"

# Check if we're in a repo
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  log "not in a git repository"
  exit 0
fi

# Get current branch
current=$(git branch --show-current 2>/dev/null || echo "")

# If we're on a different branch, try to check out the working branch
if [ "$current" != "$BRANCH" ]; then
  log "checking out $BRANCH"
  git fetch origin "$BRANCH" 2>/dev/null || true
  git checkout "$BRANCH" 2>/dev/null || {
    log "failed to checkout $BRANCH; current branch: $current"
    exit 0
  }
fi

# Show status
log "branch: $BRANCH"
if [ -n "$(git status -s)" ]; then
  log "uncommitted changes:"
  git status -s | sed 's/^/  /'
fi

# Show if behind/ahead of origin
behind=$(git rev-list --right-only --count origin/$BRANCH...HEAD 2>/dev/null || echo 0)
ahead=$(git rev-list --left-only --count origin/$BRANCH...HEAD 2>/dev/null || echo 0)

if [ "$behind" != "0" ] || [ "$ahead" != "0" ]; then
  log "vs origin/$BRANCH: +$ahead commits (unpushed), -$behind (to pull)"
fi

exit 0
