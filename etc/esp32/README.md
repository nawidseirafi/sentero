# Sentero ESP32 Sensoren

Dieses Verzeichnis enthaelt die ESP32-Firmware fuer Sentero-WLAN-Sensoren.
Aktuell gepflegte Sensorlinien:

- `C1001/`: produktiv testbarer Praesenzradar mit Motion, Fallhinweis, LEDs und C1001-Parametern.
- `MR60BDA2/`: kompatibler Sentero-MQTT/Provisioning-Aufbau fuer den DA2; UART-Rohframes werden bereits gelesen, das finale Presence/Motion/Fall-Protokoll-Mapping ist noch offen.

## Dokumente

- `SENSOR_ARCHITECTURE.md`: kurze Architektur und Datenfluss.
- `README_SENSOR_HERSTELLER.md`: stabiler Integrationsvertrag fuer Firmware/Hersteller.
- `README_SENSOR_PROVISIONING.md`: konkretes Setup-, Discovery-, Provisioning- und MQTT-Protokoll.
- `ESP32_WIFI_SENSOR_README.md`: kurzer Entwickler-Contract mit Verweisen auf die beiden Hauptdokumente.

## Einheitlicher MQTT-Vertrag

C1001 und MR60BDA2 verwenden denselben Topic-Aufbau:

```text
sentero/<device_id>/availability
sentero/<device_id>/state
sentero/<device_id>/status
sentero/<device_id>/command
```

`availability` und `state` werden retained veroeffentlicht. `status` dient fuer Kommandoantworten und wird nicht retained.

Die `device_id` ist eine UUID, die beim ersten Start erzeugt und im NVS als `device_uuid` gespeichert wird. Ein Factory Reset loescht die Provisioning-Daten, erhaelt aber die UUID, damit das physische Geraet fuer Sentero stabil bleibt.

## Build

Beispiele:

```bash
cd etc/esp32/C1001
esphome compile c1001-mmwave.yaml

cd ../MR60BDA2
esphome compile mr60bda2-mmwave.yaml
```

ArduinoJson-Meldungen zu `StaticJsonDocument` und `createNestedArray` sind aktuell Deprecation-Warnungen, keine Compile-Fehler.
