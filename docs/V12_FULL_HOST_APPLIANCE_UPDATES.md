# v12 – Vollständige Appliance-Updates

## Ziel
Normale Sentero-Appliance-Updates aktualisieren nicht mehr nur das Docker-Image,
sondern auch die versionierten Host-Komponenten.

## Bundle-Format 2
Das Update-ZIP enthält:

- `release.json`
- `sentero-image.tar`
- `host/docker-compose.yml`
- `host/scripts/*`
- `host/sentero-network/sentero_network.py`
- `host/sentero-updater/sentero_updater.py`
- `host/systemd/*`
- statische Beispielkonfigurationen

`release.json` enthält für jede Host-Datei Pfad, SHA-256 und Dateimodus.

## Schutz persistenter Daten
Der Host-Updater besitzt eine feste Allowlist. Nicht aktualisiert werden insbesondere:

- `.env`
- `data/` und SQLite-Datenbank
- `backups/`
- `mosquitto/config/passwords`
- `mosquitto/data/` und Logs
- `zigbee2mqtt/data/configuration.yaml`
- `ollama/`

## Installation
Host-Dateien werden zunächst aus dem bereits SHA-256-geprüften Appliance-ZIP
ausgelesen, pro Datei erneut gegen `release.json` geprüft, staged und anschließend
atomar ersetzt. systemd wird bei Bedarf neu geladen. Der Netzwerk-Agent wird nach
einem erfolgreichen App-Healthcheck neu gestartet. Der Host-Updater plant seinen
eigenen Neustart erst nach dem Socket-Response ein.

## Rollback
Während eines fehlgeschlagenen Updates werden die vorherigen Host-Dateien und die
SQLite-Sicherung wiederhergestellt; die vorherige Sentero-Version wird best-effort
wieder gestartet.

## Einmaliger Übergang bei bereits installierten Boxen
Updater vor v12 kennen die `host/`-Schicht noch nicht und können sich daher nicht
selbst über ein normales Update auf v12 bringen. Für genau diesen einmaligen
Übergang liegt `bootstrap_existing_box_host_updater.py` bei. Nach diesem Bootstrap
laufen weitere Host-Updates vollständig über den normalen Sentero-Update-Button.
