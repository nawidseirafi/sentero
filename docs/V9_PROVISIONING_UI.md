# Sentero v9 – Provisionierungs-UI

## Ziel

Das bisherige Box-WLAN-Setup wurde optisch durch das bereitgestellte "Sentero esp Setup"-Template ersetzt, ohne dessen ESP-spezifische HTTP-Endpunkte zu übernehmen.

## Beibehaltene Sentero-Funktion

- WLAN-Scan: `GET /api/setup/network/wifi/networks`
- Box-Netzwerkstatus: `GET /api/setup/box-network/status`
- WLAN speichern/verbinden: `POST /api/setup/box-network/wifi`
- SSID und WLAN-Passwort werden im JSON-Request-Body übertragen, nicht in der URL.
- Der Host-Netzwerk-Agent und die v8-NetworkManager-/Installer-Fixes bleiben unverändert enthalten.

## UI

- dunkles, kompaktes Karten-Layout nach dem gelieferten Template
- für Smartphone/Tablet optimiert (`max-width: 400px`)
- sichtbare WLAN-Liste mit Signalstärke und Schloss-Symbol
- manuelle SSID-Eingabe bleibt möglich
- Passwort anzeigen/verbergen
- Scan-Status und Ladezustand beim Verbinden
- klare Hinweise beim Wechsel vom Setup-AP ins Heim-WLAN

## Wichtige Erfolgslogik

Das Frontend zeigt "Mit dem Heimnetz verbunden" nur, wenn die Sentero-API `ok=true` und `status.network_ready=true` zurückliefert.

Bricht die HTTP-Verbindung beim Single-Radio-Wechsel ab, behauptet die UI keinen Erfolg. Sie erklärt stattdessen, dass das Gerät ins Heim-WLAN wechseln soll und dass der Setup-Hotspot bei Fehlschlag wieder erscheint.

## Build-Fix: initiales Docker-Image

`deployment_build.py` erzeugt fuer ein Initial-/Kundenpaket jetzt immer auch
`build/sentero-box/sentero-image.tar`, sofern `--skip-docker-save` nicht gesetzt
ist. Das gilt auch bei `--no-update`. Das Image wird bei Bedarf via
`docker buildx build --platform linux/amd64 --load` gebaut und anschliessend mit
`docker save` unter dem vom Installer erwarteten kanonischen Dateinamen exportiert.
