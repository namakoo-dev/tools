#!/bin/bash
# Claude Code cloud environment setup: Termux (Docker) + proot-distro Debian.
# Paste into the environment's "Setup script", or run manually: bash cloud/setup.sh
set -u

log() { echo "[setup] $*"; }

# --- host packages ---------------------------------------------------------
log "apt packages"
apt-get update -qq
apt-get install -y -qq openssh-client scrcpy >/dev/null

# --- proot (Ubuntu's 5.1.0 leaks host metadata via statx; use upstream static build)
log "proot (upstream static build)"
curl -fsSL -o /usr/local/bin/proot https://proot.gitlab.io/proot/bin/proot \
  && chmod +x /usr/local/bin/proot

# --- proot-distro + Debian on the host --------------------------------------
log "proot-distro"
if ! command -v proot-distro >/dev/null; then
  tmp=$(mktemp -d)
  git clone -q --depth 1 https://github.com/termux/proot-distro.git "$tmp/proot-distro" \
    && pip install -q --break-system-packages "$tmp/proot-distro" 2>/dev/null \
    || pip install -q "$tmp/proot-distro"
  rm -rf "$tmp"
fi
proot-distro login debian -- true >/dev/null 2>&1 || proot-distro install debian

# --- Docker + Termux container ---------------------------------------------
log "dockerd"
if ! docker info >/dev/null 2>&1; then
  (dockerd >/tmp/dockerd.log 2>&1 &)
  for _ in $(seq 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi

if ! docker ps -a --format '{{.Names}}' | grep -qx termux; then
  log "termux container"
  docker run -d --name termux --network host \
    -e HTTPS_PROXY="${HTTPS_PROXY:-}" -e HTTP_PROXY="${HTTP_PROXY:-}" \
    -e https_proxy="${HTTPS_PROXY:-}" -e http_proxy="${HTTP_PROXY:-}" \
    -v /root/.ccr/ca-bundle.crt:/ca.crt:ro \
    termux/termux-docker:x86_64 sleep infinity >/dev/null
  # trust the session proxy's CA inside Termux
  docker exec termux bash -lc '[ -f /ca.crt ] && cat /ca.crt >> $PREFIX/etc/tls/cert.pem'
  # pkg refuses to run as root; use the "system" user
  docker exec -u system termux bash -lc '
    pkg update -y >/dev/null 2>&1
    pkg install -y root-repo x11-repo >/dev/null 2>&1
    pkg update -y >/dev/null 2>&1
    pkg install -y git python nodejs micro openssh android-tools proot-distro >/dev/null 2>&1
    proot-distro install debian >/dev/null 2>&1 || true'
else
  docker start termux >/dev/null
fi

log "done"
log "  Termux:  docker exec -it -u system termux bash -l"
log "  Debian:  proot-distro login debian"
