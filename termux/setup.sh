#!/data/data/com.termux/files/usr/bin/bash
# Termux (Android phone) setup: repos, tools, scrcpy + Termux:X11, proot Debian, rish.
# Usage:  bash setup.sh
set -u

log() { echo "[setup] $*"; }

log "repositories"
pkg update -y
pkg install -y root-repo x11-repo
pkg update -y

log "packages"
pkg install -y git python nodejs micro openssh android-tools \
  proot-distro termux-x11-nightly scrcpy

log "Debian (proot-distro)"
proot-distro login debian -- true >/dev/null 2>&1 || proot-distro install debian

# DISPLAY for Termux:X11 (zsh and bash)
for rc in ~/.zshrc ~/.bashrc; do
  [ -f "$rc" ] && ! grep -q 'export DISPLAY=:0' "$rc" && echo 'export DISPLAY=:0' >> "$rc"
done

# rish: needs Shizuku app -> "Use Shizuku in terminal apps" -> Export files to Download
log "rish"
src=~/storage/downloads
if [ ! -d "$src" ]; then
  log "  ~/storage missing: run 'termux-setup-storage', allow, then re-run this script"
elif [ -f "$src/rish" ] && [ -f "$src/rish_shizuku.dex" ]; then
  cp "$src/rish" "$src/rish_shizuku.dex" "$PREFIX/bin/"
  sed -i 's/PKG/com.termux/' "$PREFIX/bin/rish"
  chmod +x "$PREFIX/bin/rish"
  chmod 400 "$PREFIX/bin/rish_shizuku.dex"   # Android 14+ rejects a writable dex
  log "  installed. test: rish -c whoami  (expect: shell)"
else
  log "  rish files not found in Download: export them from the Shizuku app first"
fi

if ! pm list packages 2>/dev/null | grep -q com.termux.x11; then
  log "Termux:X11 app: install from"
  log "  https://github.com/termux/termux-x11/releases/download/nightly/termux-x11-universal-debug.apk"
fi

cat <<'EOF'
[setup] done. Mirror the screen:
  termux-x11 :0 &
  adb connect 192.168.x.x:<port from Wireless debugging>
  scrcpy --no-audio
If Termux:X11 says "managed by Shell":  adb shell pm unsuspend com.termux.x11
EOF
