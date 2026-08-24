#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo ausfuehren: sudo ./scripts/install-systemd-services.sh" >&2
  exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$SRC_DIR" != "/opt/sentero/box" ]; then
  mkdir -p /opt/sentero
  rm -rf /opt/sentero/box
  cp -a "$SRC_DIR" /opt/sentero/box
fi

install -m 0644 /opt/sentero/box/systemd/sentero-box.service /etc/systemd/system/sentero-box.service
install -m 0644 /opt/sentero/box/systemd/sentero-updater.service /etc/systemd/system/sentero-updater.service

systemctl daemon-reload
systemctl enable --now sentero-updater.service
systemctl enable sentero-box.service

echo "systemd-Dienste installiert. Stack starten: sudo systemctl start sentero-box.service"
