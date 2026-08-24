# Sentero ESP32/WLAN Sensor Contract

Dieses Dokument ist die kurze Entwickler-Zusammenfassung. Die vollstaendige Schnittstelle steht in:

- `README_SENSOR_HERSTELLER.md` fuer den stabilen Herstellervertrag.
- `README_SENSOR_PROVISIONING.md` fuer konkrete Endpunkte, Payloads und Kommandos.

## Ziel

Ein WLAN-Sensor soll sich fuer Sentero wie ein normales Sentero-Geraet verhalten:

- stabile UUID als `device_id`
- automatisches Setup ueber Sentero-Wizard
- keine Home-Assistant-Abhaengigkeit
- MQTT-State und Availability retained
- identischer Payload-Vertrag fuer C1001 und MR60BDA2

## Unterstuetzte Modelle

| Modell | Status | Hinweise |
| --- | --- | --- |
| `C1001` | produktiv testbar | Praesenz, Motion, Fallhinweis, LED-Status und C1001-Konfiguration |
| `MR60BDA2` | strukturell kompatibel | Provisioning/MQTT kompatibel; finales UART-Mapping fuer Presence/Motion/Fall ist noch offen |

## Geraete-ID

Jeder Sensor nutzt eine UUID als `device_id`. Die Firmware erzeugt sie beim ersten Start und speichert sie im NVS-Schluessel `device_uuid`.

Eigenschaften:

- bleibt ueber Neustarts stabil
- bleibt nach Firmware-Updates stabil
- bleibt auch nach Factory Reset stabil
- ist pro physischem Sensor eindeutig

Beispiel:

```text
3be1ddd5-ddd6-45a2-a445-274be35449a9
```

## MQTT Topics

Alle ESP32-Sensoren verwenden denselben Topic-Aufbau:

```text
sentero/<device_id>/availability
sentero/<device_id>/state
sentero/<device_id>/status
sentero/<device_id>/command
```

`topic_prefix` ist provisionierbar. Standard ist `sentero`.

## Minimaler State

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "name": "Keller Praesenz",
  "type": "presence_radar",
  "manufacturer": "Sentero",
  "model": "C1001",
  "firmware": "1.0.1",
  "status": "online",
  "capabilities": ["presence", "motion", "fall_detection", "signal_quality"],
  "presence": true,
  "fall_detected": false,
  "motion": "Still",
  "sensor_ready": true,
  "sensor_status": "OK",
  "last_sensor_update_ms": 123456,
  "last_value_change_ms": 120000,
  "power_source": "usb",
  "signal_quality": 88,
  "command_topic": "sentero/3be1ddd5-ddd6-45a2-a445-274be35449a9/command",
  "writable_settings": [],
  "friendly_name": "Keller Praesenz",
  "room_id": "keller",
  "room_hint": "keller"
}
```

## Modellunterschiede

C1001 darf zusaetzlich senden:

- `presence_raw`, `motion_raw`, `fall_raw`
- `moving_range`, `work_mode`
- Poll-/Reset-Diagnosefelder
- `hp_led`, `fall_led`, `led_status`
- `writable_settings` mit C1001-Parametern

MR60BDA2 sendet denselben Basis-State, aber aktuell keine LED- oder Sensorparameter. Deshalb ist `writable_settings` leer.

## Sendeverhalten

- beim MQTT-Connect: `availability=online` retained
- direkt danach: kompletter `state` retained
- bei relevanten Aenderungen: neuer `state`
- spaetestens alle 5 Minuten: kompletter Heartbeat-State
- Last Will: `availability=offline` retained

## Produkttexte

Die Firmware liefert technische Felder. Produkttexte entstehen in Sentero. Fuer Fallhinweise keine medizinischen Versprechen machen; in der UI werden Begriffe wie `Sturzverdacht`, `Praesenz erkannt` und `letzte Bewegung` verwendet.
