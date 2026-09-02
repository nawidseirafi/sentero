#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
tar czf "backups/sentero-backup-$(date +%F-%H%M%S).tar.gz" data config mosquitto/data zigbee2mqtt/data .env
