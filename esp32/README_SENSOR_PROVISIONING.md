# Provisioning-Protokoll fuer Sentero WLAN-Sensoren

## Implementierungsstatus

Stand: C1001 und MR60BDA2 nutzen denselben Sentero-Provisioning- und MQTT-Vertrag.

Vorhanden:

- Setup-Hotspot `Sentero-mmWave` mit Passwort `senteroSetup`
- Captive-Portal-UI mit WLAN-Scan, QR-Flow im Wizard und manuellem SSID-Fallback
- UDP-Discovery im Heimnetz auf Port `37020`
- HTTP-Provisioning auf dem Sensor: `POST /api/provision`
- Speicherung von WLAN-, MQTT-, Device-, Raum- und Token-Metadaten im NVS
- stabile UUID im NVS-Schluessel `device_uuid`
- MQTT-Availability, MQTT-State und MQTT-Last-Will
- Runtime-Kommandos per MQTT: `configure`, `factory_reset`; beim C1001 zusaetzlich LEDs und Sensorparameter
- Sentero-Backend-Endpunkte fuer Discovery, Provisioning und Sensorrollen
- produktorientierter Wizard-Flow fuer Praesenzsensoren

Modellstatus:

| Modell | Stand |
| --- | --- |
| `C1001` | produktiv testbar, inklusive C1001-UART, Diagnose, LEDs und Parametern |
| `MR60BDA2` | Provisioning/MQTT kompatibel; UART-Rohframes vorhanden, finales Presence/Motion/Fall-Mapping noch offen |

ArduinoJson-Meldungen beim Build sind aktuell Deprecation-Warnungen, keine Compile-Fehler.

## Ziel

Ein Benutzer soll einen neuen Sensor hinzufuegen koennen, ohne MQTT, WLAN-Konfiguration oder technische Details kennen zu muessen. Wizard und Firmware uebernehmen die komplette Einrichtung.

## Gesamtablauf

```text
Sensor einschalten
        |
        v
Setup-WLAN "Sentero-mmWave" per QR-Code oder manuell verbinden
        |
        v
Captive Portal oeffnen
        |
        v
Sensor mit Heim-WLAN verbinden
        |
        v
Sensor sendet UDP-Discovery im Heimnetz
        |
        v
Sentero Backend findet Sensor
        |
        v
Wizard uebergibt Raum, Name und MQTT-Daten
        |
        v
Sentero Backend ruft /api/provision auf dem Sensor auf
        |
        v
Sensor speichert Provisioning-Konfiguration
        |
        v
Sensor startet neu
        |
        v
Sensor verbindet sich mit MQTT
        |
        v
Availability + State werden retained veroeffentlicht
        |
        v
Wizard zeigt Sensor erfolgreich eingerichtet
```

## WLAN-Setup im Captive Portal

Solange der Sensor noch nicht im Heimnetz ist, stellt die Firmware einen Setup-Hotspot bereit:

```text
SSID:     Sentero-mmWave
Passwort: senteroSetup
```

Das Captive Portal laeuft auf Port `80`.

### Captive-Portal-Endpunkte

```text
GET /                  HTML-Setup-UI
GET /config.json       Device-Metadaten und WLAN-Scan-Ergebnisse
GET /scan.json         startet neuen WLAN-Scan
GET /wifisave          speichert SSID/Passwort
GET /sentero-logo.png  Logo fuer die Setup-UI
GET /favicon.ico       204 No Content
```

`GET /config.json`:

```json
{
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "c1001-mmwave-abcdef",
  "aps": [
    {"ssid": "MeinWLAN", "rssi": -52, "lock": 1}
  ]
}
```

`GET /scan.json`:

```json
{
  "ok": true,
  "status": "scan_started"
}
```

`GET /wifisave?ssid=<ssid>&psk=<passwort>` speichert die WLAN-Daten und antwortet:

```text
Saved. Connecting...
```

WLAN-Passwoerter werden als Secret behandelt und nicht geloggt.

## UDP-Discovery

Sobald der Sensor im Heimnetz verbunden und noch nicht provisioniert ist, sendet er alle 2 Sekunden einen UDP-Broadcast.

```text
Port: 37020
Ziel: 255.255.255.255:37020
```

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

`model` ist je Firmware `C1001` oder `MR60BDA2`. Beide Modelle verwenden dieselben Capabilities fuer Sentero.

Die `device_id` ist eine UUID, die der Sensor beim ersten Start erzeugt und im NVS als `device_uuid` speichert. Sie bleibt ueber Neustarts, Firmware-Updates und Factory Reset stabil. Wenn im Provisioning-Request keine `device_id` enthalten ist, verwendet der Sensor seine eigene UUID.

Sentero speichert daraus intern:

- Absender-IP
- HTTP-Port, Standard `80`
- `device_id`
- `model`
- `firmware`
- `capabilities`
- Status `pending`

Nach erfolgreicher Provisionierung sendet der Sensor keine Discovery-Broadcasts mehr. Erst ein Factory Reset loescht den `provisioned`-Status.

## HTTP-Provisioning auf dem Sensor

Nach erfolgreicher UDP-Discovery ruft Sentero den Sensor direkt im Heimnetz per HTTP auf:

```text
POST http://<sensor-ip>:<http_port>/api/provision
Content-Type: application/json
```

Die Firmware liest den JSON-Body auf ESP-IDF direkt aus dem rohen `httpd_req_t`, weil ESPHomes `web_server_idf` POST-Bodies nicht ueber `handleBody()` bereitstellt. Der Body darf maximal `4096` Bytes gross sein.

### Request

```json
{
  "protocol": 2,
  "wifi": {
    "ssid": "MeinWLAN",
    "password": "********"
  },
  "mqtt": {
    "host": "192.168.178.20",
    "port": 1883,
    "username": "sentero",
    "password": "********",
    "topic_prefix": "sentero"
  },
  "device": {
    "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
    "friendly_name": "Keller Praesenz",
    "room_id": "keller",
    "token": "optional"
  }
}
```

### Kompatible Kurzfelder

```json
{
  "protocol": 2,
  "wifi_ssid": "MeinWLAN",
  "wifi_password": "********",
  "mqtt_host": "192.168.178.20",
  "mqtt_port": 1883,
  "mqtt_username": "sentero",
  "mqtt_password": "********",
  "topic_prefix": "sentero",
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "friendly_name": "Keller Praesenz",
  "room_id": "keller",
  "token": "optional"
}
```

### Pflichtfelder

Aktuell ist nur ein MQTT-Host zwingend erforderlich:

```text
mqtt.host oder mqtt_host
```

Akzeptierte Protokollversionen:

```text
1, 2
```

WLAN-Daten koennen mitgesendet werden und werden dann erneut gespeichert. Im normalen Flow wurden sie vorher im Captive Portal gespeichert.

### Erfolgreiche Response

```json
{
  "ok": true,
  "success": true,
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "model": "C1001",
  "firmware": "1.0.1"
}
```

Nach der Response:

1. Die Firmware speichert alle Provisioning-Daten im NVS-Namespace `sentero`.
2. Vorhandene MQTT-Verbindungen werden zurueckgesetzt.
3. Falls WLAN-Daten vorhanden sind, werden sie in ESP-IDF gespeichert.
4. Der Sensor startet nach ca. 1,5 Sekunden neu.

### Fehlercodes des Sensors

```text
400 body_too_large
400 request_read_failed
400 invalid_json
400 unsupported_protocol
400 missing_required_fields
409 already_provisioned
500 nvs_open_failed
```

`already_provisioned` bedeutet: Der Sensor darf nicht erneut per HTTP umprovisioniert werden. Erst ein Factory Reset loescht diesen Schutz.

## Sentero Backend API

```text
GET  /api/sentero/sensors/provisioning/status
POST /api/sentero/sensors/provisioning/esp32/discovery/start
GET  /api/sentero/sensors/provisioning/esp32/discovered
POST /api/sentero/sensors/provisioning/esp32/start
```

Praesenzsensor einrichten:

```json
{
  "room_id": "keller",
  "display_name": "Keller Praesenz",
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9"
}
```

Response:

```json
{
  "ok": true,
  "device": {
    "id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
    "name": "Keller Praesenz",
    "type": "presence_radar",
    "room_id": "keller",
    "source": "mqtt"
  },
  "message": "Praesenzsensor erfolgreich eingerichtet."
}
```

## Sentero Konfiguration

Nicht-sensitive Werte stehen in `config/sentero.yaml`:

```yaml
esp32:
  topic_prefix: sentero
  discovery_port: 37020
  discovery_wait_timeout: 6
  provisioning_timeout: 10
  mqtt_wait_timeout: 30
  token: SENTERO_ESP32_DEVICE_TOKEN
```

Umgebungsvariablen koennen diese Werte ueberschreiben:

```dotenv
SENTERO_ESP32_DISCOVERY_PORT=37020
SENTERO_ESP32_DISCOVERY_WAIT_TIMEOUT=6
SENTERO_ESP32_PROVISIONING_TIMEOUT=10
SENTERO_ESP32_MQTT_WAIT_TIMEOUT=30
SENTERO_ESP32_TOPIC_PREFIX=sentero
SENTERO_ESP32_DEVICE_TOKEN=
```

Eine feste `provisioning_url` wird nicht verwendet. Sentero baut die URL aus der UDP-Discovery:

```text
http://<sender-ip>:<http_port>/api/provision
```

Passwoerter und Tokens werden nicht geloggt.

## MQTT nach der Einrichtung

Nach dem Neustart verbindet sich der Sensor mit dem konfigurierten Broker:

```text
mqtt://<mqtt_host>:<mqtt_port>
client_id: sentero-<device_id>
```

Topic-Prefix wird aus dem Provisioning gelesen und normalisiert. Leere oder nur aus Slashes bestehende Prefixes fallen auf `sentero` zurueck.

### Topics

```text
<topic_prefix>/<device_id>/availability
<topic_prefix>/<device_id>/state
<topic_prefix>/<device_id>/status
<topic_prefix>/<device_id>/command
```

Standard:

```text
sentero/<device_id>/availability
sentero/<device_id>/state
sentero/<device_id>/status
sentero/<device_id>/command
```

### Availability

Beim MQTT-Connect veroeffentlicht der Sensor `online`. Danach wiederholt er Availability alle 60 Sekunden. Als Last Will ist `offline` retained konfiguriert.

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "online",
  "firmware": "1.0.1"
}
```

### State

Der Sensor veroeffentlicht State:

- direkt nach MQTT-Connect
- bei relevanten Sensor-Aenderungen, maximal einmal pro Sekunde
- als Heartbeat spaetestens alle 5 Minuten

Basisbeispiel fuer C1001 und MR60BDA2:

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
  "setup_attempts": 1,
  "last_sensor_update_ms": 649149,
  "last_value_change_ms": 649149,
  "power_source": "usb",
  "signal_quality": 90,
  "command_topic": "sentero/3be1ddd5-ddd6-45a2-a445-274be35449a9/command",
  "writable_settings": [],
  "friendly_name": "Keller Praesenz",
  "room_id": "keller",
  "room_hint": "keller"
}
```

C1001 sendet zusaetzlich Diagnose- und LED-Felder, z. B.:

```json
{
  "presence_raw": 1,
  "motion_raw": 1,
  "fall_raw": 0,
  "moving_range": 1,
  "work_mode": 1,
  "poll_count": 313,
  "poll_ok_count": 313,
  "poll_error_count": 0,
  "read_errors": 0,
  "stuck_active_resets": 0,
  "stuck_presence_resets": 0,
  "stuck_inactive_resets": 1,
  "last_poll_ms": 649144,
  "last_poll_ok_ms": 649149,
  "last_frame_ms": 649149,
  "led_status": {
    "hp_led": false,
    "fall_led": false,
    "all_on": false,
    "any_on": false
  },
  "writable_settings": [
    "hp_led",
    "fall_led",
    "install_height",
    "fall_time",
    "unmanned_time",
    "residence_time",
    "fall_sensitivity"
  ]
}
```

MR60BDA2 sendet aktuell `last_frame_ms` und optional `last_frame` fuer UART-Diagnose. `writable_settings` bleibt leer.

## MQTT-Kommandos

Runtime-Kommandos laufen ueber:

```text
sentero/<device_id>/command
```

Antworten laufen ueber:

```text
sentero/<device_id>/status
```

Die Firmware akzeptiert Command-Namen mit Bindestrich oder Unterstrich und normalisiert sie auf Kleinbuchstaben mit Unterstrich.

### Allgemeine Bestaetigung

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "command_accepted",
  "command": "configure",
  "ok": true,
  "message": "configured"
}
```

```json
{
  "device_id": "3be1ddd5-ddd6-45a2-a445-274be35449a9",
  "status": "command_rejected",
  "command": "configure",
  "ok": false,
  "message": "no_known_settings"
}
```

Moegliche Ablehnungsgruende:

- `invalid_json`
- `missing_command`
- `unsupported_command`
- `missing_or_invalid_boolean`
- `missing_or_invalid_number`
- `value_out_of_range`
- `no_known_settings`

### Configure fuer Name und Raum

C1001 und MR60BDA2 akzeptieren `configure` fuer Metadaten:

```json
{
  "command": "configure",
  "device": {
    "friendly_name": "Keller Praesenz",
    "room_id": "keller"
  }
}
```

Auch flach oder unter `settings` wird akzeptiert, sofern die Firmware das Feld kennt.

### Factory Reset

```json
{
  "command": "factory_reset"
}
```

Factory Reset loescht Provisioning-, WLAN- und MQTT-Daten, aber erhaelt die `device_uuid`.

### C1001 LEDs

Nur senden, wenn der State `writable_settings` mit `hp_led` oder `fall_led` meldet.

```json
{
  "command": "set_hp_led",
  "enabled": true
}
```

```json
{
  "command": "set_fall_led",
  "enabled": false
}
```

Alias:

```text
hp_led
fall_led
```

Boolesche Werte koennen als Boolean, Zahl oder Text gesendet werden: `true`, `false`, `1`, `0`, `on`, `off`, `yes`, `no`.

### C1001 Zahlenwerte

Montagehoehe:

```json
{"command": "set_install_height", "centimeters": 270}
```

Range: `100..400`

Sturz-Verzoegerung:

```json
{"command": "set_fall_time", "seconds": 5}
```

Range: `0..60`

Abwesenheitszeit:

```json
{"command": "set_unmanned_time", "seconds": 1}
```

Range: `0..60`

Verweilzeit:

```json
{"command": "set_residence_time", "seconds": 200}
```

Range: `0..3600`

Sturz-Empfindlichkeit:

```json
{"command": "set_fall_sensitivity", "sensitivity": 3}
```

Range: `0..3`

Alle Zahlenkommandos akzeptieren alternativ das Feld `value`.

### Mehrere C1001-Einstellungen

```json
{
  "command": "configure",
  "settings": {
    "hp_led": true,
    "fall_led": true,
    "install_height": 270,
    "fall_time": 5,
    "unmanned_time": 1,
    "residence_time": 200,
    "fall_sensitivity": 3
  }
}
```

Der Sensor wendet nur bekannte Einstellungen an. Wenn keine bekannte Einstellung enthalten ist, antwortet er mit `no_known_settings`.

## Factory-Reset-Taster

Beide ESP32-Firmwares nutzen einen lokalen Factory-Reset-Taster. Der Button muss ca. 5 Sekunden gehalten werden. Danach werden Provisioning-Daten geloescht und der Sensor startet neu.

## Kompatibilitaetsregeln fuer die UI

- Die UI entscheidet anhand von `type=presence_radar` und MQTT-State, nicht anhand eines festen Modells.
- `model` darf `C1001` oder `MR60BDA2` sein.
- Buttons fuer schreibbare Einstellungen werden nur aus `writable_settings` abgeleitet.
- Zusatzfelder duerfen fehlen. Der Basis-State muss aber vollstaendig bleiben.
- MR60BDA2 darf keine C1001-LED-Felder melden, solange er sie nicht wirklich steuern kann.
