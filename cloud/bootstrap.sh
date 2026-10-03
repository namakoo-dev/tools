#!/bin/bash
# Claude Code cloud environment bootstrap: Complete session handoff automation.
# Restore environment, settings, and git state on each new session start.
#
# Setup instructions:
# 1. Paste this entire script into the cloud environment's "Setup script" field
# 2. Or: add these lines to your existing setup script
#
set -u

log() { echo "[bootstrap] $*"; }

# --- environment setup (cloud/setup.sh) ---
log "Environment setup"
bash "$(dirname "$0")/setup.sh" 2>&1 | grep -v '^$' | sed 's/^/  /'

# --- settings restoration ---
log "Claude Code settings"
SETTINGS_FILE="$HOME/.claude/settings.json"
if [ ! -f "$SETTINGS_FILE" ]; then
  mkdir -p "$(dirname "$SETTINGS_FILE")"
  cat > "$SETTINGS_FILE" <<'SETTINGS_EOF'
{
  "permissions": {
    "allow": [
      "Bash(./rish *)",
      "Bash(proot *)",
      "Bash(proot)",
      "Bash(proot-distro *)",
      "Bash(proot-distro)",
      "Bash(rish *)",
      "Bash(rish)",
      "Bash(sh rish *)",
      "Edit(*)",
      "Edit(//**)",
      "Write"
    ],
    "deny": [
      "Bash(adb shell pm uninstall *)",
      "Bash(adb uninstall *)",
      "Bash(rish -c pm uninstall *)",
      "Bash(rish -c \"pm uninstall *)",
      "Bash(rm -rf /)",
      "Bash(rm -rf /*)",
      "Bash(rm -rf ~)",
      "Bash(rm -rf ~/*)"
    ]
  },
  "sandbox": {
    "enabled": false,
    "network": {
      "allowedDomains": [
        "github.com",
        "packages-cf.termux.dev",
        "registry.npmjs.org",
        "google.com",
        "*.google.com",
        "*.googleapis.com"
      ]
    },
    "failIfUnavailable": false,
    "allowUnsandboxedCommands": true
  }
}
SETTINGS_EOF
  log "  created $SETTINGS_FILE"
else
  log "  already present"
fi

# --- git state hint ---
log "Git state on session start:"
for repo_path in /home/user/*/; do
  repo=$(basename "$repo_path")
  if [ -d "$repo_path/.git" ]; then
    cd "$repo_path"
    branch=$(git branch --show-current 2>/dev/null || echo "detached")
    dirty=$([ -z "$(git status -s)" ] && echo "clean" || echo "dirty")
    ahead=$(git rev-list --left-only --count origin/main...HEAD 2>/dev/null || echo "?")
    log "  $repo: branch=$branch ($dirty, +$ahead commits)"
  fi
done

log "Session ready. Run: cd /home/user/tools"
