#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose pull mosquitto zigbee2mqtt ollama
docker compose up -d mosquitto zigbee2mqtt ollama
