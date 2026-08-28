# Sentero Box v2

Diese Version ist fuer eine Intel-N100-Testbox mit Debian 13 gedacht.

## Architektur

- Debian 13 amd64
- Docker Compose
- Sentero (Backend + gebautes Frontend)
- Eclipse Mosquitto
- Zigbee2MQTT + Sonoff Coordinator
- Ollama + qwen2.5:3b
- separater `sentero-updater` als systemd-Dienst auf dem Host

Der Sentero-Container bekommt **nicht** `/var/run/docker.sock`. Updates werden ueber
einen eng begrenzten Unix-Socket an den Host-Updater uebergeben.

## Warum der neue Updater?

Der alte `zip`-Modus ersetzt Dateien innerhalb der laufenden Installation. In einem
Docker-Container waeren diese Aenderungen beim naechsten Container-Neustart weg.

Im `appliance`-Modus bleibt die bestehende Update-GUI erhalten, aber:

1. Sentero prueft weiterhin das bestehende Update-Manifest.
2. Die GUI startet das Update.
3. Sentero uebergibt nur `channel` + `target_version` an `/run/sentero-updater/updater.sock`.
4. Der Host-Updater laedt das Manifest **noch einmal selbst** vom fest konfigurierten Server.
5. Er prueft Bundle-Groesse und SHA-256.
6. Er sichert die SQLite-Datenbank mit der SQLite Backup API.
7. Er laedt das neue Docker-Image (`docker load`).
8. Er startet nur den Sentero-Container neu.
9. `/health` wird geprueft.
10. Bei Fehler werden vorheriges Image und DB-Backup wiederhergestellt.

## Installation

```bash
cd box
cp .env.example .env
nano .env
sudo ./scripts/install-docker-debian.sh
ls -l /dev/serial/by-id/
# ZIGBEE_ADAPTER_HOST und MQTT_PASSWORD in .env setzen
./scripts/first-install.sh
```

Optional koennen die systemd-Dienste installiert werden:

```bash
sudo ./scripts/install-systemd-services.sh
sudo systemctl start sentero-box.service
```

## Sonoff

- ZBDongle-P: in Zigbee2MQTT normalerweise `adapter: zstack`
- ZBDongle-E: normalerweise `adapter: ember`

Unbedingt `/dev/serial/by-id/...` verwenden.

## Lokales LLM

Standard:

```text
qwen2.5:3b
```

Sentero bekommt per Environment:

```text
SENTERO_LLM_PROVIDER=ollama
SENTERO_LLM_BASE_URL=http://ollama:11434
SENTERO_LLM_MODEL=qwen2.5:3b
```

`factory.py` wurde dafuer erweitert, damit Model und Base-URL per Environment
ueberschrieben werden koennen.

## Update-Server

Das bestehende Manifest bleibt kompatibel. Fuer die Box braucht ein Release
zusaetzlich:

```json
{
  "channels": {
    "stable": {
      "latest_version": "0.2.0",
      "release_notes": ["..."],
      "appliance": {
        "bundle_url": "https://.../sentero-box-0.2.0.zip",
        "sha256": "<64 hex>",
        "size_bytes": 123456789
      }
    }
  }
}
```

Ein Bundle enthaelt:

```text
release.json
sentero-image.tar
```

Erzeugen:

```bash
python3 deployment_build.py \
  --version 0.2.0 \
  --base-url https://sentero.de/sentero \
  --release-note "Sentero Box Update 0.2.0"
```

Danach `sentero-box-0.2.0.zip` und das passende `latest.json` auf den bestehenden
Update-Server hochladen.

## Persistenz

Diese Verzeichnisse liegen ausserhalb des Sentero-Images:

```text
box/data/sentero
box/config
box/mosquitto/data
box/zigbee2mqtt/data
box/ollama
box/backups
```

Ein normales Sentero-Anwendungsupdate ersetzt nur das Sentero-Image.

## Systemkomponenten

Mosquitto, Zigbee2MQTT und Ollama werden bewusst nicht bei jedem Sentero-Patch
automatisch aktualisiert. Fuer die Testbox:

```bash
./scripts/update-system-components.sh
```

Spaeter sollte eine getestete Box-Release-Matrix feste Versionen/Digests fuer alle
Komponenten enthalten.

## Produktions-Hardening als naechster Schritt

- Docker-Image-Tags durch feste Digests ersetzen.
- Manifest kryptografisch signieren (nicht nur SHA-256 ueber HTTPS).
- Firewall/nftables-Profil fuer die Appliance.
- A/B-Update oder zweites Fallback-Image.
- DB-Migrationspolicy mit deklarierter Rollback-Kompatibilitaet.
- Automatische Backup-Rotation.
- Hardware-Watchdog und Stromausfalltests.
