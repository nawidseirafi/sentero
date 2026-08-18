# Sentero Sensor Architecture

Sentero ist quellenagnostisch. Home Assistant ist nur ein Entwicklungsadapter und fuer die Produktivnutzung nicht erforderlich.

## V1-Onboarding

Der normale Sentero-Wizard richtet in V1 Praesenzsensoren und Tuersensoren ueber denselben Sensor-Suchen-Flow ein. Fuer Nutzer werden keine Funktechniken angezeigt. Der aktive V1-Transport ist Zigbee; Pairing wird nur temporaer waehrend der Suche geoeffnet und danach wieder geschlossen.

ESP32/WLAN-Sensoren bleiben technisch kompatibel und werden als Transport `wifi_esphome` betrachtet. Der bisherige Setup-Hotspot-/QR-/HTTP-Provisioning-Pfad bleibt fuer spaetere Varianten und Admin-/Entwicklungsfluesse erhalten, ist aber im normalen V1-Wizard standardmaessig ausgeblendet (`SENTERO_ENABLE_WIFI_SENSOR_SETUP=false`).

Persistierte Sensorzuordnungen enthalten den Transport, aber Sentero-Fachlogik wertet ausschliesslich normalisierte Sensordaten aus:

```text
presence -> room.presence
door_contact -> door.open
```

Nicht erlaubt in der Fachlogik:

```text
zigbee_presence
mqtt_topic_as_behavior_signal
esp32_specific_presence
```

## Quellen

- MQTT Generic Adapter: produktiver Pfad fuer ESP32/WLAN-Sensoren wie C1001 und MR60BDA2.
- Zigbee2MQTT Adapter: produktiver Pfad fuer Zigbee-Sensoren ueber Mosquitto.
- Home Assistant Adapter: Entwicklungsmodus fuer bestehende HA-Entities.

Produktiver Zielpfad:

```text
Sensor -> MQTT -> Sentero Sensor Manager -> Device/Event Model -> Behavior Engine -> Dashboard
```

Onboarding-Zielpfad im V1-Wizard:

```text
Sentero Wizard -> SensorManager -> Home Assistant / Sensorquelle -> Zigbee2MQTT oder ZHA -> Sensor
```

## ESP32/WLAN-Sensoren

Dieser Abschnitt beschreibt den erhaltenen `wifi_esphome`-Transport. Er ist nicht Teil des normalen V1-Wizard-Flows.

C1001 und MR60BDA2 muessen fuer Sentero gleich aussehen:

- gleicher Setup-Hotspot: `Sentero-mmWave`
- gleiche UDP-Discovery auf Port `37020`
- gleiches HTTP-Provisioning: `POST /api/provision`
- gleiche MQTT-Topics unter `sentero/<device_id>/...`
- gleicher Basis-State fuer Praesenz, Motion, Fallhinweis und Signalqualitaet

Modellspezifische Unterschiede werden ueber `model`, `writable_settings` und optionale Diagnosefelder abgebildet. Die UI darf sich nicht auf ein konkretes Modell verlassen.

## Konfiguration

Set `SENTERO_SENSOR_SOURCE` to one of:

- `homeassistant`
- `mqtt`
- `mixed`

Produktive Deployments sollten `mqtt` oder `mixed` mit Mosquitto/Zigbee2MQTT verwenden.

## Doku-Aufteilung

- `README_SENSOR_HERSTELLER.md`: stabiler Vertrag fuer kompatible Sensoren.
- `README_SENSOR_PROVISIONING.md`: konkreter Provisioning- und MQTT-Ablauf der ESP32-Firmware.
- `ESP32_WIFI_SENSOR_README.md`: kurze Entwickler-Zusammenfassung und Verweise.
