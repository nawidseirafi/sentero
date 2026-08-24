#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env wurde aus .env.example erstellt. Bitte pruefe die Werte und starte das Skript erneut." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [ "${SENTERO_MQTT_PASSWORD:-change-me}" = "change-me" ] || [ -z "${SENTERO_MQTT_PASSWORD:-}" ]; then
  echo "Bitte SENTERO_MQTT_PASSWORD in .env auf ein starkes Passwort setzen." >&2
  exit 1
fi

if [ ! -e "${ZIGBEE_ADAPTER_HOST:-}" ]; then
  echo "ZIGBEE_ADAPTER_HOST existiert nicht: ${ZIGBEE_ADAPTER_HOST:-leer}" >&2
  echo "Verfuegbare Adapter:" >&2
  ls -l /dev/serial/by-id/ 2>/dev/null || true
  exit 1
fi

mkdir -p data/sentero config backups mosquitto/config mosquitto/data mosquitto/log zigbee2mqtt/data ollama

if [ ! -f config/sentero.yaml ]; then
  if [ -f ../config/sentero.yaml ]; then
    cp ../config/sentero.yaml config/sentero.yaml
  else
    echo "config/sentero.yaml fehlt. Bitte aus dem Sentero-Repo bereitstellen." >&2
    exit 1
  fi
fi

envsubst < zigbee2mqtt/data/configuration.yaml.example > zigbee2mqtt/data/configuration.yaml

docker run --rm \
  -v "$PWD/mosquitto/config:/mosquitto/config" \
  eclipse-mosquitto:2 \
  mosquitto_passwd -b -c /mosquitto/config/passwords "${SENTERO_MQTT_USERNAME:-sentero}" "${SENTERO_MQTT_PASSWORD}"

chmod 600 mosquitto/config/passwords

if [ -f ../docker/Dockerfile.appliance ]; then
  docker build -f ../docker/Dockerfile.appliance -t "${SENTERO_IMAGE:-sentero/app}:${SENTERO_VERSION:-dev}" ..
fi

docker compose up -d

if [ -n "${SENTERO_LLM_MODEL:-}" ]; then
  docker compose exec -T ollama ollama pull "${SENTERO_LLM_MODEL}"
fi

echo "Sentero startet auf http://$(hostname -I | awk '{print $1}'):${SENTERO_HTTP_PORT:-8080}"
