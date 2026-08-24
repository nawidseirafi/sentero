# Sentero

Sentero ist ein eigenstaendiges Care-Signal-Produkt, das aus RoboterSteve herausgeloest wurde.

## Architektur

- `backend/`: FastAPI-API, Authentifizierung, Verhaltensbewertung, Setup-Flow, Benachrichtigungskanaele und Sensor-Mapping.
- `frontend/`: Vite/React-App fuer Sentero.
- `config/`: eigenstaendige Sentero-Konfiguration.
- `docker/`: Container- und Mosquitto-Konfiguration.
- `data/`: Laufzeitdaten fuer SQLite und Adapter.
- `docs/`: Produktdokumentation.

Sentero verwendet keine RoboterSteve-Editionen, keine Agent-Registry, keinen Orchestrator, keine Agent-Steuerung und keine RoboterSteve-spezifischen APIs.

## Sensorquellen

Sentero verwendet eine einheitliche MQTT-Sensorpipeline.

```text
Zigbee-Sensor -> Zigbee2MQTT --\
                               -> Mosquitto/MQTT -> Sentero
ESP32/generischer MQTT-Sensor -/
EcoTracker lokal ------------> Sentero
```

Home Assistant ist keine Sensorquelle mehr. Der alte Auswahlmechanismus
`SENTERO_SENSOR_SOURCE=homeassistant|mqtt|mixed` wurde entfernt.

Sentero verarbeitet nur Sensoren, die ueber den eigenen Onboarding-/Mapping-Flow
registriert wurden. Beliebige MQTT-Geraete, die lediglich am Broker sichtbar
sind, duerfen nicht automatisch in Verhaltensanalyse oder Benachrichtigungen
einfliessen.


## Lokale Entwicklung

```bash
deactivate
rm -rf .venv
/opt/homebrew/opt/python@3.14/bin/python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080
```

In einer zweiten Shell:

```bash
cd frontend
npm install
npm run dev
```

Frontend-Abhaengigkeiten wie MUI, Emotion und QR-Code-Rendering werden ueber `frontend/package.json` verwaltet, nicht ueber `requirements.txt`.

Oeffnen: `http://localhost:5173`.

## Docker

Das Repository enthaelt Dockerfiles sowie Mosquitto- und Caddy-Konfiguration. Ein vollstaendiger Kunden-Appliance-Stack braucht zusaetzlich eine Deployment-Schicht mit Compose/systemd-Dateien, persistenten Volumes und Host-Updater-Anbindung. Wenn der Deployment-Baum `box/` vorhanden ist, gelten dessen Installationsanweisungen. In diesem Repository-Stand wird das Applikations-Image direkt gebaut:

```bash
docker build -f docker/Dockerfile.appliance -t sentero/app:dev .
```

Das Appliance-Dockerfile baut das Frontend innerhalb von Docker. Fuer lokale Entwicklung ohne Docker die Backend-/Frontend-Kommandos oben verwenden.

Zur Box-v2-Appliance siehe `docs/README_sentero_box_v2.md`. Die externe AAL-Flaeche ist auf `/api/sentero/exchange/v1/*` begrenzt; GUI und Admin-APIs sollen im lokalen Netzwerk bleiben.

## Deployment-Build

Sentero-Box-Appliance-Update-Artefakte erzeugen:

```bash
DOCKER_DEFAULT_PLATFORM=linux/amd64 \
python3 deployment_build.py \
  --version 0.2.0 \
  --base-url https://seirafi.de/robotersteve/sentero \
  --release-note "Sentero Box Update 0.2.0"
```

`deployment_build.py` ersetzt den alten dateibasierten Update-ZIP-Build fuer Appliance v2. Das Skript erwartet `docker/Dockerfile.appliance`, baut das Frontend im Docker-Multi-Stage-Build, schreibt `version.json` vor dem Docker-Build auf die angegebene Version und erzeugt ein Appliance-Bundle mit `release.json` plus `sentero-image.tar`.

Wenn ein `box/`-Deployment-Baum existiert, bereitet der Build auch das initiale Kunden-Deployment vor. Wenn `box/` fehlt, wird dieser Teil uebersprungen.

Ausgaben:

- `build/updates/sentero/stable/latest.json`
- `build/updates/sentero/stable/releases/sentero-box-<version>.zip`
- `build/sentero-box/`, nur wenn ein `box/`-Deployment-Baum im Projekt existiert.

Metadaten-/Testlauf ohne Docker-Export:

```bash
python3 deployment_build.py \
  --version 0.2.0 \
  --base-url https://seirafi.de/robotersteve/sentero \
  --skip-docker-build \
  --skip-docker-save
```

## Aktualisierungen

Sentero hat eine eigenstaendige Update-API unter `/api/sentero/system/update/*`.

Fuer lokale Entwicklung kann `dry_run` verwendet werden. Auf der Sentero Box v2 wird `appliance` verwendet; die GUI delegiert die Installation an den hostseitigen Sentero-Updater.

Der historische `zip`-Update-Modus ist nur fuer nicht-containerisierte Altinstallationen gedacht und gehoert nicht zum normalen Box-v2-Betrieb.


Fuer Sentero Box v2 werden Appliance-Bundles aus `deployment_build.py` verwendet; Details stehen in `docs/README_sentero_box_v2.md`. Diese Bundles enthalten `release.json` plus `sentero-image.tar`; die Installation wird an einen hostseitigen Updater delegiert, statt Dateien in einem laufenden Container zu ersetzen.

## Debian-Mini-PC-Deployment

Die fuehrende Installations- und Update-Dokumentation fuer die Box ist `docs/README_sentero_box_v2.md`. Dieser Abschnitt enthaelt ergaenzende Hinweise fuer Debian-Mini-PCs, Firewall-Regeln, externe AAL-Freigabe und Backups.

Debian-Basispakete:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git ufw network-manager modemmanager avahi-daemon
sudo systemctl enable --now NetworkManager ModemManager avahi-daemon
```

NetworkManager soll WLAN-Client-Modus, temporaeren Setup-Hotspot und LTE-Verbindungen exklusiv verwalten. Keine parallelen produktiven `wpa_supplicant`-, `hostapd`- oder Modem-Skripte neben Sentero betreiben.

Docker:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
newgrp docker
```

Firewall-Grundregel: kein Router-Portforwarding auf `8080` oder `1883`.

Waehrend des Setup-WLANs sollen Clients nur die Setup-Oberflaeche erreichen. Kein Routing vom Setup-WLAN zu Sensordaten, Historie, Logs, MQTT oder Admin-/Shell-Diensten freigeben.

UFW-Beispiel fuer Heimnetz `192.168.178.0/24`:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.178.0/24 to any port 8080 proto tcp
sudo ufw allow from 192.168.178.0/24 to any port 1883 proto tcp
sudo ufw enable
```

Wenn keine LAN-MQTT-Clients vorhanden sind, Port `1883` nicht oeffnen.

Die optionale externe AAL-Exchange-Freigabe haelt Sentero-GUI, Login, Setup, Sensoren, Transparenz und Admin-APIs lokal. Nur diese Endpunkte duerfen extern sichtbar sein:

- `/api/sentero/exchange/v1/daily-status`
- `/api/sentero/exchange/v1/event-summary`
- `/api/sentero/exchange/v1/system-status`

Die externe AAL-Freigabe wird ueber den jeweiligen Edge-Proxy/Deployment-Layer konfiguriert. Die frueheren `SENTERO_AAL_*`-Environment-Variablen sind kein Bestandteil der aktuellen Sentero-Konfiguration.

Ohne diese Compose-Schicht muss der Edge-Proxy separat mit `docker/caddy/Caddyfile`, Nginx oder Caddy bereitgestellt werden.

Router-Portforwarding soll dann nur auf den Edge-Proxy zeigen:

- TCP `80` -> Mini-PC
- TCP `443` -> Mini-PC

Nicht weiterleiten: `8080`, `1883` oder andere Sentero-Ports.

Kontrolle:

```bash
curl -i https://aal.example.org/api/sentero/exchange/v1/daily-status \
  -H "Authorization: Bearer <export-token>"

curl -i https://aal.example.org/
curl -i https://aal.example.org/docs
curl -i https://aal.example.org/openapi.json
curl -i https://aal.example.org/api/sentero/auth/status
curl -i https://aal.example.org/api/sentero/transparency
```

Nur der Exchange-Aufruf soll mit gueltigem Token erfolgreich sein. Die anderen Pfade muessen `404` oder `403` liefern.

Persistente Daten fuer Backups:

- `data/sentero.db`
- MQTT-Cache-Tabelle `mqtt_last_states` innerhalb von `data/sentero.db`
- Mosquitto-Daten
- Zigbee2MQTT-Daten
- `config/sentero.yaml`
- `.env`

Backup-Beispiel fuer ein Repo-/Box-Layout mit `data/` und `config/`:

```bash
tar czf sentero-backup-$(date +%F).tar.gz data config .env
```

Die Datei `.env` enthaelt Secrets und darf nicht geteilt werden. Netzwerk-Credentials gehoeren produktiv in NetworkManager oder einen OS Secret Store; Sentero speichert in SQLite nur Status und Historie, keine WLAN-Passwoerter, SIM-PINs oder Tokens.
