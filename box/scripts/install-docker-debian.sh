#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo ausfuehren: sudo ./scripts/install-docker-debian.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl git ufw network-manager modemmanager avahi-daemon gettext-base python3
systemctl enable --now NetworkManager ModemManager avahi-daemon

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker

TARGET_USER="${SUDO_USER:-sentero}"
if id "$TARGET_USER" >/dev/null 2>&1; then
  usermod -aG docker "$TARGET_USER"
fi

echo "Docker ist installiert. Melde dich neu an oder fuehre 'newgrp docker' aus."
