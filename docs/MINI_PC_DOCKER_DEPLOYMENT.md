# Sentero auf Debian Mini-PC mit Docker

Ziel: Sentero laeuft lokal auf einem Mini-PC. Die Sentero-GUI und Admin-APIs bleiben im Heimnetz. Falls Smart-Living-AAL extern angebunden wird, wird nur `/api/sentero/exchange/*` veroeffentlicht.

## 1. Debian vorbereiten

```bash
sudo apt update
sudo apt install -y ca-certificates curl git ufw
```

Docker installieren:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
```

## 2. Sentero auf den Mini-PC kopieren

```bash
git clone <repo-url> sentero
cd sentero
cp .env.example .env
```

Wichtige Werte in `.env`:

```bash
SENTERO_BIND_ADDRESS=0.0.0.0
SENTERO_MQTT_BIND_ADDRESS=127.0.0.1
SENTERO_DOCKER_SENSOR_SOURCE=mqtt
```

Wenn ESP32-Sensoren direkt MQTT vom Heimnetz erreichen muessen:

```bash
SENTERO_MQTT_BIND_ADDRESS=<MINI-PC-LAN-IP>
```

Beispiel:

```bash
SENTERO_MQTT_BIND_ADDRESS=192.168.178.20
```

## 3. Frontend bauen

```bash
cd frontend
npm install
npm run build
cd ..
```

## 4. Sentero starten

```bash
docker compose up --build -d
```

Mit Zigbee2MQTT im Stack:

```bash
docker compose --profile production up --build -d
```

Lokaler Zugriff:

```text
http://<MINI-PC-LAN-IP>:8080
```

## 5. Firewall

Grundregel: Kein Router-Portforwarding auf `8080` oder `1883`.

UFW-Beispiel fuer Heimnetz `192.168.178.0/24`:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.178.0/24 to any port 8080 proto tcp
sudo ufw allow from 192.168.178.0/24 to any port 1883 proto tcp
sudo ufw enable
```

Wenn keine LAN-MQTT-Clients vorhanden sind, Port `1883` nicht freigeben.

## 6. Optional: AAL-Exchange nach aussen

Nur wenn ein Pflegedienst oder Partner extern abrufen soll:

```bash
SENTERO_AAL_SITE=aal.example.org
```

Dann starten:

```bash
docker compose --profile external-aal up -d
```

Extern werden nur diese Pfade geroutet:

- `/api/sentero/exchange/daily-status`
- `/api/sentero/exchange/event-summary`
- `/api/sentero/exchange/system-status`

Alles andere liefert `404`.

Router-Portforwarding dann nur auf den Edge-Proxy:

- TCP `80` -> Mini-PC
- TCP `443` -> Mini-PC

Nicht weiterleiten:

- `8080`
- `1883`
- beliebige anderen Sentero-Ports

## 7. Kontrolle

LAN:

```bash
curl -i http://<MINI-PC-LAN-IP>:8080/health
```

Externe AAL-Schnittstelle:

```bash
curl -i https://aal.example.org/api/sentero/exchange/daily-status \
  -H "Authorization: Bearer <export-token>"
```

Diese externen URLs muessen blockiert sein:

```bash
curl -i https://aal.example.org/
curl -i https://aal.example.org/sentero/settings
curl -i https://aal.example.org/docs
curl -i https://aal.example.org/openapi.json
curl -i https://aal.example.org/api/sentero/auth/status
curl -i https://aal.example.org/api/sentero/transparency
```

Erwartung: `404` oder `403`.

## 8. Daten und Backup

Persistente Daten liegen unter:

- `data/sentero.db`
- `data/mosquitto/`
- `data/zigbee2mqtt/`
- `config/sentero.yaml`

Backup:

```bash
tar czf sentero-backup-$(date +%F).tar.gz data config .env
```

Die Datei `.env` enthaelt Secrets und darf nicht geteilt werden.
