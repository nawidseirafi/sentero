# Sentero v8 – WLAN- und Fresh-Install-Fixes

## WLAN-Onboarding
- Lokale WLAN-Verbindung und Internet-Erreichbarkeit sind getrennte Zustände.
- Eine erfolgreiche Heimnetz-Verbindung wird nicht mehr abgebaut, nur weil `nmcli networking connectivity check` nicht sofort `full` liefert.
- Erfolg wird an aktivem WLAN + DHCP/IP erkannt.
- Der Setup-AP wird nur bei echtem Authentifizierungs-/DHCP-Fehler wiederhergestellt.

## Fresh Install
- Root-PATH enthält `/usr/sbin` und `/sbin`.
- Shell-/Host-Agent-Dateien erhalten ausführbare Bits beim Build und beim ersten Kopieren.
- `.env` wird weiterhin automatisch aus `.env.example` erzeugt.
- `data/sentero` wird für Container-UID 10001 schreibbar gemacht.
- Fehlender Zigbee-Adapter ist nicht mehr fatal; Zigbee2MQTT ist dann deaktiviert.
- Zigbee2MQTT läuft über das Compose-Profil `zigbee`, wenn ein Adapter erkannt wird.
- `configuration.yaml.example` bleibt im generierten Kundenpaket erhalten.

## Builder
- Docker-Build wird explizit für `linux/amd64` mit buildx gebaut und geladen.
- `SENTERO_IMAGE` enthält nur das Repository; `SENTERO_VERSION` enthält den Tag.
- Ausführbare Bits werden im generierten `build/sentero-box` gesetzt.
