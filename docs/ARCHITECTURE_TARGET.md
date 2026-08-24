# Sentero Zielarchitektur

Sentero soll in Wohnungen ohne Router, WLAN oder Ethernet eingerichtet werden koennen.

## Erststart

```text
Sentero einschalten
keine funktionierende Netzwerkverbindung
Setup-WLAN startet
Benutzer verbindet Smartphone oder Tablet
http://sentero.local oder http://192.168.50.1 oeffnen
Wizard: Internetverbindung
WLAN, Ethernet oder Mobilfunk auswaehlen
Verbindung testen
Setup-WLAN abschalten
Onboarding fortsetzen
```

## Betriebsmodell

`NetworkService` ist der einzige Netzwerk-Koordinator. Zielplattformen verwenden NetworkManager und ModemManager:

- WLAN-Client und WLAN-Hotspot ueber NetworkManager
- LTE-Modems ueber ModemManager/NetworkManager
- keine herstellerspezifische Modemlogik im Sentero-Code
- keine parallele Hostapd-/Dnsmasq-Konfiguration, solange NetworkManager den AP-Modus kann

Fallback auf `hostapd` und `dnsmasq` ist nur zulaessig, wenn die Zielhardware NetworkManager-Hotspot nicht unterstuetzt und die alternative Konfiguration exklusiv verwaltet wird.

## Status

Zentrale Statuswerte:

- `OFFLINE`
- `LOCAL_ONLY`
- `ONLINE_ETHERNET`
- `ONLINE_WIFI`
- `ONLINE_CELLULAR`
- `DEGRADED`

Eine Verbindung gilt erst als erfolgreich, wenn Default Route, DNS, Internet und Sentero-Mailserver erreichbar sind. `interface up` reicht nicht.

## Failover

Default:

```yaml
network:
  failover:
    enabled: true
    failure_threshold: 3
    recovery_threshold: 3
    check_interval_seconds: 30
    switch_cooldown_seconds: 120
```

Nach drei fehlgeschlagenen Checks auf Ethernet/WLAN darf LTE aktiviert werden. Nach drei erfolgreichen Checks auf der hoeher priorisierten Verbindung wechselt Sentero kontrolliert zurueck und trennt LTE. Der Cooldown verhindert Interface-Flapping.

## Sicherheit

Setup-AP-Clients duerfen nur Setup-Funktionen erreichen. Nicht erreichbar sind Sensordaten, Historie, Kontakte, Verhaltensanalyse, Admin-APIs, Logs oder Shell-Dienste.

Credentials werden getrennt von Statusdaten verwaltet:

- WLAN-Passwoerter und SIM-PINs nicht in normalen API-Responses
- keine Secrets in Logs
- SQLite enthaelt nur nicht-sensitive Netzwerk-Metadaten und Historie
- produktiv vorzugsweise NetworkManager Connection Store oder OS Secret Store

## Bidirektionale E-Mail ohne eingehende Ports

Sentero bleibt auch fuer aktive Statusabfragen technisch von aussen unerreichbar. Angehoerige fragen per E-Mail an oder antworten auf eine Sentero-Benachrichtigung. Die Box pollt das konfigurierte Kundenpostfach per IMAP ueber TLS und antwortet ueber dasselbe Mailkonto per SMTP. Es gibt keine technische Sonderadresse fuer Angehoerige; die normale Kundenadresse wird fuer Senden und Empfangen verwendet.

Zielzustand:

- keine Portweiterleitung
- kein Webhook aus dem Internet
- kein oeffentlich erreichbarer Sentero-Endpunkt
- periodisches IMAP-Polling, Default 60 Sekunden
- IMAP IDLE kann spaeter ergaenzt werden, ist aber nicht Voraussetzung
- SMTP-Versand ueber die bestehende Benachrichtigungsarchitektur
- Message-ID-basierte Idempotenz
- robuste Reconnects mit Backoff bei IMAP-/SMTP-Fehlern

Mail-Abfragen liefern nur vorhandene Fakten. Raumantworten unterscheiden strikt zwischen aktueller Presence, letzter Aktivitaet und unsicherer/veralteter Raumzuordnung. Sensorwerte mit alter Datenfrische werden nicht als Live-Zustand formuliert.

## Sensor-Onboarding V1

Ziel fuer Sentero V1:

- Praesenzsensoren und Tuersensoren werden im normalen Wizard ueber denselben Sensor-Suchen-Flow eingerichtet.
- Der Standardtransport ist `zigbee`.
- Zigbee-Pairing wird nur temporaer waehrend eines aktiven Onboarding- oder Admin-Flows geoeffnet und bei Erfolg, Timeout oder Abbruch wieder geschlossen.
- Default-Timeout fuer die Suche: 120 Sekunden, konfigurierbar.
- Mehrere MQTT-/Zigbee2MQTT-Entities eines physischen Geraets werden als ein Sentero-Sensor gespeichert.
- Sentero speichert eine eigene Raumzuordnung und ist nicht darauf angewiesen, externe Area-/Raum-Metadaten zu veraendern.
- ESP32-Sensoren werden nicht ueber einen separaten Provisioning-Transport behandelt. Wenn sie eingesetzt werden, publizieren sie wie andere MQTT-Geraete auf denselben Mosquitto-Broker.
- Im normalen Sensor-Wizard wird keine Funktechnik ausgewaehlt; Sentero ordnet gefundene und registrierte Sensoren ueber die bestehende SensorManager-/MQTT-Pipeline zu.

Persistente Zuordnung:

```json
{
  "sensor_type": "presence",
  "transport": "zigbee",
  "room_id": "hallway",
  "device_id": "0x...",
  "primary_entity_id": "binary_sensor.flur_occupancy",
  "entity_ids": [
    "binary_sensor.flur_occupancy",
    "sensor.flur_battery"
  ]
}
```

Die Verhaltensanalyse konsumiert daraus nur normalisierte Fachdaten, nicht den Transport.
