# Sentero Sensor Architecture

Sentero ist quellenagnostisch. Home Assistant ist nur ein Entwicklungsadapter und fuer die Produktivnutzung nicht erforderlich.

## Quellen

- MQTT Generic Adapter: produktiver Pfad fuer ESP32/WLAN-Sensoren wie C1001 und MR60BDA2.
- Zigbee2MQTT Adapter: produktiver Pfad fuer Zigbee-Sensoren ueber Mosquitto.
- Home Assistant Adapter: Entwicklungsmodus fuer bestehende HA-Entities.

Produktiver Zielpfad:

```text
Sensor -> MQTT -> Sentero Sensor Manager -> Device/Event Model -> Behavior Engine -> Dashboard
```

## ESP32/WLAN-Sensoren

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
