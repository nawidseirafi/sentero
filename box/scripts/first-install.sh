#!/usr/bin/env bash
set -euo pipefail

# Sentero Box one-command first installer.
# Run as root from the copied build/sentero-box directory:
#   ./scripts/first-install.sh

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausfuehren: sudo ./scripts/first-install.sh" >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DIR="/opt/sentero/box"

# Make /opt/sentero/box the canonical installation directory. When this script
# is started from /root/sentero-box or a USB stick, copy the complete package
# there and continue from the installed copy.
if [ "$SOURCE_DIR" != "$TARGET_DIR" ]; then
  echo "[1/9] Sentero nach $TARGET_DIR installieren ..."
  mkdir -p /opt/sentero
  rm -rf "$TARGET_DIR"
  cp -a "$SOURCE_DIR" "$TARGET_DIR"
  chmod +x "$TARGET_DIR/scripts/first-install.sh"
  exec "$TARGET_DIR/scripts/first-install.sh"
fi

cd "$TARGET_DIR"

echo "[2/9] Docker und Systempakete pruefen ..."
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  ./scripts/install-docker-debian.sh
fi
systemctl enable --now docker

echo "[3/9] sentero.local (mDNS) einrichten ..."
# Make a freshly installed box reachable from macOS/iOS/Linux as
# http://sentero.local:8080 without requiring a fixed IP address.
if ! dpkg-query -W -f='${Status}' avahi-daemon 2>/dev/null | grep -q 'install ok installed'; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y avahi-daemon
fi
hostnamectl set-hostname sentero
systemctl enable --now avahi-daemon
systemctl restart avahi-daemon

if [ ! -f sentero-image.tar ]; then
  echo "FEHLER: $TARGET_DIR/sentero-image.tar fehlt." >&2
  echo "Baue das Kundenpaket neu mit deployment_build.py und kopiere den kompletten" >&2
  echo "Ordner build/sentero-box auf diese Box." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
fi

set_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

# Create local MQTT credentials automatically. They never leave this box.
MQTT_USER="$(grep '^SENTERO_MQTT_USERNAME=' .env | cut -d= -f2- || true)"
MQTT_PASS="$(grep '^SENTERO_MQTT_PASSWORD=' .env | cut -d= -f2- || true)"
[ -n "$MQTT_USER" ] || MQTT_USER="sentero"
if [ -z "$MQTT_PASS" ] || [ "$MQTT_PASS" = "change-me" ]; then
  MQTT_PASS="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
fi
set_env SENTERO_MQTT_USERNAME "$MQTT_USER"
set_env SENTERO_MQTT_PASSWORD "$MQTT_PASS"

# Auto-detect the Zigbee serial device where possible.
ZIGBEE_HOST="$(grep '^ZIGBEE_ADAPTER_HOST=' .env | cut -d= -f2- || true)"
if [ -z "$ZIGBEE_HOST" ] || [ ! -e "$ZIGBEE_HOST" ]; then
  ZIGBEE_HOST="$(find /dev/serial/by-id -maxdepth 1 -type l 2>/dev/null | head -n 1 || true)"
  if [ -z "$ZIGBEE_HOST" ]; then
    for candidate in /dev/ttyACM0 /dev/ttyUSB0; do
      if [ -e "$candidate" ]; then
        ZIGBEE_HOST="$candidate"
        break
      fi
    done
  fi
fi

if [ -z "$ZIGBEE_HOST" ] || [ ! -e "$ZIGBEE_HOST" ]; then
  echo "FEHLER: Kein Zigbee-Adapter gefunden." >&2
  echo "Adapter anschliessen und das Skript erneut starten." >&2
  echo "Verfuegbare serielle Geraete:" >&2
  ls -l /dev/serial/by-id/ 2>/dev/null || true
  exit 1
fi
set_env ZIGBEE_ADAPTER_HOST "$ZIGBEE_HOST"

# Keep existing adapter settings, otherwise use the Sentero defaults. The type
# can still be changed in .env before rerunning if different hardware is used.
ZIGBEE_CONTAINER="$(grep '^ZIGBEE_ADAPTER_CONTAINER=' .env | cut -d= -f2- || true)"
ZIGBEE_TYPE="$(grep '^ZIGBEE_ADAPTER_TYPE=' .env | cut -d= -f2- || true)"
ZIGBEE_TOPIC="$(grep '^ZIGBEE2MQTT_TOPIC_PREFIX=' .env | cut -d= -f2- || true)"
[ -n "$ZIGBEE_CONTAINER" ] || ZIGBEE_CONTAINER="/dev/ttyACM0"
[ -n "$ZIGBEE_TYPE" ] || ZIGBEE_TYPE="ember"
[ -n "$ZIGBEE_TOPIC" ] || ZIGBEE_TOPIC="zigbee2mqtt"
set_env ZIGBEE_ADAPTER_CONTAINER "$ZIGBEE_CONTAINER"
set_env ZIGBEE_ADAPTER_TYPE "$ZIGBEE_TYPE"
set_env ZIGBEE2MQTT_TOPIC_PREFIX "$ZIGBEE_TOPIC"

echo "[4/9] Sentero Docker-Image laden ..."
docker load -i sentero-image.tar

set -a
# shellcheck disable=SC1091
. ./.env
set +a
SENTERO_IMAGE="${SENTERO_IMAGE:-sentero/app}"
SENTERO_VERSION="${SENTERO_VERSION:-dev}"
export SENTERO_IMAGE SENTERO_VERSION

case "${SENTERO_IMAGE##*/}" in
  *:*)
    echo "FEHLER: SENTERO_IMAGE darf keinen Docker-Tag enthalten: $SENTERO_IMAGE" >&2
    exit 1
    ;;
esac

EXPECTED_IMAGE="${SENTERO_IMAGE}:${SENTERO_VERSION}"
if ! docker image inspect "$EXPECTED_IMAGE" >/dev/null 2>&1; then
  echo "FEHLER: Erwartetes Image $EXPECTED_IMAGE wurde nicht aus sentero-image.tar geladen." >&2
  docker image ls --format '{{.Repository}}:{{.Tag}}' | grep '^sentero/' || true
  exit 1
fi

PLATFORM="$(docker image inspect "$EXPECTED_IMAGE" --format '{{.Os}}/{{.Architecture}}')"
if [ "$PLATFORM" != "linux/amd64" ]; then
  echo "FEHLER: Falsche Image-Plattform: $PLATFORM (erwartet linux/amd64)." >&2
  exit 1
fi

echo "[5/9] Laufzeitverzeichnisse und Konfiguration vorbereiten ..."
mkdir -p data/sentero config backups mosquitto/config mosquitto/data mosquitto/log zigbee2mqtt/data ollama

if [ ! -f config/sentero.yaml ]; then
  echo "FEHLER: config/sentero.yaml fehlt im Kundenpaket." >&2
  exit 1
fi

if [ ! -f zigbee2mqtt/data/configuration.yaml.example ]; then
  echo "FEHLER: zigbee2mqtt/data/configuration.yaml.example fehlt." >&2
  exit 1
fi

envsubst < zigbee2mqtt/data/configuration.yaml.example > zigbee2mqtt/data/configuration.yaml

echo "[6/9] MQTT-Zugangsdaten einrichten ..."
docker run --rm \
  -v "$PWD/mosquitto/config:/mosquitto/config" \
  eclipse-mosquitto:2 \
  mosquitto_passwd -b -c /mosquitto/config/passwords "$SENTERO_MQTT_USERNAME" "$SENTERO_MQTT_PASSWORD"
chmod 600 mosquitto/config/passwords

echo "[7/9] Host-Updater und systemd-Dienste installieren ..."
install -m 0644 systemd/sentero-box.service /etc/systemd/system/sentero-box.service
install -m 0644 systemd/sentero-updater.service /etc/systemd/system/sentero-updater.service
systemctl daemon-reload
systemctl enable --now sentero-updater.service
systemctl enable sentero-box.service

# The host updater must create its runtime socket before Docker binds the
# directory into the Sentero container.
for _ in $(seq 1 20); do
  [ -S /run/sentero-updater/updater.sock ] && break
  sleep 0.25
done
if [ ! -S /run/sentero-updater/updater.sock ]; then
  echo "FEHLER: Updater-Socket wurde nicht erstellt." >&2
  systemctl status sentero-updater.service --no-pager || true
  exit 1
fi

echo "[8/9] Sentero starten ..."
docker compose up -d

if [ -n "${SENTERO_LLM_MODEL:-}" ]; then
  echo "LLM-Modell wird vorbereitet: ${SENTERO_LLM_MODEL}"
  docker compose exec -T ollama ollama pull "${SENTERO_LLM_MODEL}" || \
    echo "Hinweis: Ollama-Modell konnte noch nicht geladen werden; Sentero selbst wird weiter geprueft." >&2
fi

echo "[9/9] Healthcheck ..."
for _ in $(seq 1 60); do
  if docker inspect sentero --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null | grep -q '^healthy$'; then
    IP="$(hostname -I | awk '{print $1}')"
    echo
    echo "============================================================"
    echo " Sentero Box ist betriebsbereit"
    echo " Version:  ${SENTERO_VERSION}"
    echo " Image:    ${EXPECTED_IMAGE} (${PLATFORM})"
    echo " Zigbee:   ${ZIGBEE_HOST}"
    echo " Web:      http://sentero.local:${SENTERO_HTTP_PORT:-8080}"
    echo " Fallback: http://${IP}:${SENTERO_HTTP_PORT:-8080}"
    echo "============================================================"
    exit 0
  fi
  sleep 2
done

echo "FEHLER: Sentero wurde nicht rechtzeitig healthy." >&2
docker compose ps >&2 || true
docker logs sentero --tail 100 >&2 || true
exit 1
