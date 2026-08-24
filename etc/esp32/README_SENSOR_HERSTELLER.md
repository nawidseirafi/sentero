# Sentero Sensor-Integrationsvertrag fuer Hersteller

Version: 1.1
Gueltig ab: Sentero 1.x

## Zweck

Dieses Dokument beschreibt die technische Schnittstelle zwischen kompatiblen Sensoren und der Sentero-Plattform. Ziel ist, dass Sensoren automatisch erkannt, registriert, konfiguriert und fuer Dashboard sowie Verhaltenserkennung verwendet werden koennen.

Der Benutzer interagiert ausschliesslich mit Sentero. WLAN, MQTT, ESP32, Zigbee und Payloads sind technische Implementierungsdetails.

## Unterstuetzte Sensortypen

| Sensortyp | Technologie | `type` |
| --- | --- | --- |
| Praesenzradar | ESP32, WLAN, MQTT, C1001, MR60BDA2 | `presence_radar` |
| Tuer-/Fensterkontakt | Zigbee, spaeter optional MQTT | `door_contact` |

Weitere Typen koennen spaeter ergaenzt werden. Nicht bekannte Typen werden nicht automatisch als Praesenzsensor behandelt.

## Geraete-ID

Jeder Sensor muss eine eindeutige und dauerhafte `device_id` besitzen.

Anforderungen:

- UUID-Format empfohlen
- pro physischem Sensor eindeutig
- bleibt nach Neustarts erhalten
- bleibt nach Firmware-Updates erhalten
- bleibt nach Factory Reset erhalten, wenn das Geraet weiter als dasselbe physische Produkt erkannt werden soll
- keine Leerzeichen

Beispiel:

```text
3be1ddd5-ddd6-45a2-a445-274be35449a9
```

Die aktuelle ESP32-Firmware erzeugt die UUID beim ersten Start und speichert sie im NVS-Schluessel `device_uuid`.

## Discovery

Noch nicht provisionierte ESP32/WLAN-Sensoren senden UDP-Broadcasts an Port `37020`.

Payload:

```json
{
  "type": "sentero-discovery",
  "protocol": 1,
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "model": "C1001",
  "firmware": "1.0.1",
  "sensor_type": "presence_radar",
  "http_port": 80,
  "capabilities": ["presence", "motion", "fall_detection", "signal_quality"]
}
```

Ein Sensor gilt als erkennbar, wenn `device_id`, `sensor_type` und mindestens eine Capability gueltig sind.

## Provisioning

Sentero provisioniert ESP32/WLAN-Sensoren per HTTP im lokalen Netzwerk:

```text
POST http://<sensor-ip>:<http_port>/api/provision
Content-Type: application/json
```

Die genaue Request-/Response-Struktur steht in `README_SENSOR_PROVISIONING.md`.

Der Benutzer konfiguriert keine MQTT- oder WLAN-Parameter manuell in Sentero. Der Wizard fuehrt ihn durch Setup-Hotspot, Heimnetz-Verbindung, Discovery und Raumzuordnung.

## MQTT-Kommunikation

Alle ESP32/WLAN-Sensoren muessen denselben Topic-Aufbau verwenden:

```text
<topic_prefix>/<device_id>/availability
<topic_prefix>/<device_id>/state
<topic_prefix>/<device_id>/status
<topic_prefix>/<device_id>/command
```

Standard fuer `topic_prefix` ist:

```text
sentero
```

`availability` und `state` muessen retained veroeffentlicht werden. `status` fuer Kommandoantworten darf nicht retained sein.

## Availability

Payload `online`:

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "online",
  "firmware": "1.0.1"
}
```

Last-Will `offline`:

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "offline",
  "firmware": "1.0.1"
}
```

Lifecycle- und Kommandozustaende gehoeren in `status`, nicht in `availability`.

## State-Payload

Pflichtfelder fuer Praesenzradar:

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| `device_id` | string | stabile Geraete-ID |
| `name` | string | Anzeigename |
| `type` | string | `presence_radar` |
| `manufacturer` | string | Hersteller, aktuell `Sentero` |
| `model` | string | z. B. `C1001` oder `MR60BDA2` |
| `firmware` | string | Firmware-Version |
| `status` | string | normalerweise `online` |
| `capabilities` | array | unterstuetzte Funktionen |
| `presence` | boolean | Praesenz erkannt |
| `fall_detected` | boolean | Fallhinweis erkannt |
| `motion` | string | `None`, `Still`, `Active` oder modellspezifische Rohbezeichnung |
| `sensor_ready` | boolean | Sensorwerte sind verwendbar |
| `last_sensor_update_ms` | number | `millis()` der letzten Sensoraktualisierung |
| `power_source` | string | z. B. `usb` |
| `signal_quality` | number | 0..100 |
| `command_topic` | string | Topic fuer MQTT-Kommandos |
| `writable_settings` | array | schreibbare Einstellungen, leer wenn keine vorhanden |

Empfohlene Felder:

| Feld | Bedeutung |
| --- | --- |
| `sensor_status` | menschenlesbarer technischer Sensorstatus |
| `last_value_change_ms` | `millis()` der letzten inhaltlichen Wertveraenderung |
| `friendly_name` | aktueller Name aus Sentero |
| `room_id` | Raum-ID aus Sentero |
| `room_hint` | identisch zu `room_id` fuer Discovery/Mapping |

Beispiel:

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
  "last_sensor_update_ms": 649149,
  "last_value_change_ms": 649149,
  "power_source": "usb",
  "signal_quality": 90,
  "command_topic": "sentero/3be1ddd5-ddd6-45a2-a445-274be35449a9/command",
  "writable_settings": ["hp_led", "fall_led"],
  "friendly_name": "Keller Praesenz",
  "room_id": "keller",
  "room_hint": "keller"
}
```

## Capabilities

Aktuell fuer Praesenzradar freigegeben:

```text
presence
motion
fall_detection
signal_quality
```

Weitere Felder werden von Sentero ignoriert, solange sie nicht vertraglich freigegeben sind. MR60BDA2 wird hier als Praesenz-/Motion-/Fall-Sensor behandelt, nicht als Vitaldaten-Sensor.

## Writable Settings

`writable_settings` steuert, welche UI-Aktionen Sentero anbietet.

C1001 kann melden:

```json
[
  "hp_led",
  "fall_led",
  "install_height",
  "fall_time",
  "unmanned_time",
  "residence_time",
  "fall_sensitivity"
]
```

MR60BDA2 meldet aktuell:

```json
[]
```

Ein Sensor darf keine Einstellung in `writable_settings` auffuehren, wenn er sie nicht per MQTT-Kommando bestaetigen und anwenden kann.

## MQTT-Kommandos

Kommandos werden als JSON an `<topic_prefix>/<device_id>/command` gesendet. Antworten gehen an `<topic_prefix>/<device_id>/status`.

Alle kompatiblen ESP32-Sensoren muessen unterstuetzen:

- `configure` fuer `friendly_name` und `room_id`
- `factory_reset`

C1001 unterstuetzt zusaetzlich LED- und Sensorparameter. Details stehen in `README_SENSOR_PROVISIONING.md`.

Erfolgreiche Antwort:

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "command_accepted",
  "command": "configure",
  "ok": true,
  "message": "configured"
}
```

Abgelehnte Antwort:

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "command_rejected",
  "command": "configure",
  "ok": false,
  "message": "no_known_settings"
}
```

## Sicherheit

- WLAN- und MQTT-Zugangsdaten duerfen nicht geloggt werden.
- Provisioning findet nur im lokalen Netzwerk statt.
- MQTT-Zugangsdaten muessen individuell provisionierbar sein.
- TLS ist fuer spaetere Versionen vorgesehen, aber fuer lokale Testsysteme nicht verpflichtend.

## Akzeptanzkriterien

Ein ESP32/WLAN-Sensor ist Sentero-kompatibel, wenn er:

- eine stabile UUID als `device_id` besitzt,
- den Setup-Hotspot bereitstellt,
- per UDP-Discovery gefunden wird,
- `/api/provision` akzeptiert,
- sich mit MQTT verbindet,
- `availability` und `state` retained veroeffentlicht,
- den Basis-State fuer `presence_radar` sendet,
- `configure` fuer Name/Raum bestaetigt,
- `factory_reset` unterstuetzt,
- nur tatsaechlich unterstuetzte Felder in `writable_settings` meldet.
