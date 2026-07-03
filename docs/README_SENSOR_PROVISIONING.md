# Provisioning-Protokoll fuer Sentero WLAN-Sensoren

## Implementierungsstatus

Stand: **ESP32/C1001-Firmware und Sentero-Backend sind produktiv testbar
implementiert**.

Bereits vorhanden:

- WLAN-Ersteinrichtung ueber den ESP32-Setup-Hotspot
  `Sentero-mmWave` mit Passwort `senteroSetup`
- Captive-Portal-UI mit WLAN-Scan und manuellem SSID-Fallback
- UDP-Discovery im Heimnetz auf Port `37020`
- HTTP-Provisioning auf dem Sensor:
  `POST http://<sensor-ip>/api/provision`
- Speicherung von WLAN-, MQTT-, Device-, Raum- und Token-Metadaten im NVS
- MQTT-Availability, MQTT-State und MQTT-Last-Will
- Runtime-Kommandos per MQTT, inklusive Factory Reset und C1001-Einstellungen
- Sentero-Backend-Endpunkte fuer Discovery, Start und Status
- Produktorientierter Wizard-Flow fuer Praesenzsensoren

Die ArduinoJson-Meldungen beim Build sind aktuell Deprecation-Warnungen, keine
Compile-Fehler.

------------------------------------------------------------------------

## Ziel

Ein Benutzer soll einen neuen Sensor hinzufuegen koennen, ohne MQTT,
WLAN-Konfiguration oder technische Details kennen zu muessen. Der Wizard und
die Firmware uebernehmen die komplette Einrichtung.

------------------------------------------------------------------------

## Gesamtablauf

``` text
Sensor einschalten
        |
        v
Mit Setup-WLAN "Sentero-mmWave" verbinden
        |
        v
Captive Portal oeffnen
        |
        v
WLAN-SSID und WLAN-Passwort speichern
        |
        v
Sensor verbindet sich mit dem Heim-WLAN
        |
        v
Sensor sendet UDP-Broadcasts
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
Availability + State werden veroeffentlicht
        |
        v
Wizard zeigt "Sensor erfolgreich eingerichtet"
```

------------------------------------------------------------------------

## WLAN-Setup im Captive Portal

Solange der Sensor noch nicht im Heimnetz ist, stellt die ESP32-Firmware einen
Setup-Hotspot bereit:

``` text
SSID:     Sentero-mmWave
Passwort: senteroSetup
```

Das Captive Portal laeuft auf Port `80` und liefert die Sentero-Setup-UI aus.

### Captive-Portal-Endpunkte

``` text
GET /                  HTML-Setup-UI
GET /config.json       Device-Metadaten und WLAN-Scan-Ergebnisse
GET /scan.json         startet einen neuen WLAN-Scan
GET /wifisave          speichert SSID/Passwort
GET /sentero-logo.png  Logo fuer die Setup-UI
GET /favicon.ico       204 No Content
```

`GET /config.json` liefert:

``` json
{
  "mac": "aa:bb:cc:dd:ee:ff",
  "name": "c1001-mmwave-abcdef",
  "aps": [
    {
      "ssid": "MeinWLAN",
      "rssi": -52,
      "lock": 1
    }
  ]
}
```

`GET /scan.json` antwortet bei Erfolg:

``` json
{
  "ok": true,
  "status": "scan_started"
}
```

`GET /wifisave?ssid=<ssid>&psk=<passwort>` speichert die WLAN-Daten in der
ESPHome-WLAN-Konfiguration und antwortet:

``` text
Saved. Connecting...
```

Das WLAN-Passwort wird in Logs als Secret behandelt.

------------------------------------------------------------------------

## UDP-Discovery

Sobald der Sensor im Heimnetz verbunden und noch nicht provisioniert ist,
sendet er alle 2 Sekunden einen UDP-Broadcast.

Port:

``` text
37020
```

Ziel:

``` text
255.255.255.255:37020
```

Payload:

``` json
{
  "type": "sentero-discovery",
  "protocol": 1,
  "device_id": "c1001-b16c33e0",
  "model": "C1001",
  "firmware": "1.0.0",
  "sensor_type": "presence_radar",
  "http_port": 80,
  "capabilities": [
    "presence",
    "motion",
    "fall_detection",
    "signal_quality"
  ]
}
```

Die `device_id` ist MAC-basiert im Format `c1001-<4-mac-bytes>`. Der
Platzhalter `c1001-a1b2c3d4` wird von der Firmware nicht als echte ID
uebernommen; wenn er im Provisioning-Request auftaucht, verwendet der Sensor
seine eigene MAC-basierte ID.

Sentero speichert daraus intern:

- Absender-IP
- HTTP-Port, Standard `80`
- `device_id`
- `model`
- `firmware`
- `capabilities`
- Status `pending`

Nach erfolgreicher Provisionierung sendet der Sensor keine Discovery-Broadcasts
mehr. Erst ein Factory Reset loescht den `provisioned`-Status.

------------------------------------------------------------------------

## HTTP-Provisioning auf dem Sensor

Nach erfolgreicher UDP-Discovery ruft Sentero den Sensor direkt im Heimnetz
per HTTP auf:

``` text
POST http://<sensor-ip>:<http_port>/api/provision
Content-Type: application/json
```

Beispiel:

``` text
POST http://192.168.178.44/api/provision
```

Die Firmware liest den JSON-Body auf ESP-IDF direkt aus dem rohen
`httpd_req_t`, weil ESPHomes `web_server_idf` POST-Bodies nicht ueber
`handleBody()` bereitstellt. Der Body darf maximal `4096` Bytes gross sein.

### Request

``` json
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
    "device_id": "c1001-b16c33e0",
    "friendly_name": "Wohnzimmer Praesenzsensor",
    "room_id": "living_room",
    "token": "optional"
  }
}
```

### Kompatible Kurzfelder

Die Firmware akzeptiert zusaetzlich flache Legacy-Felder:

``` json
{
  "protocol": 2,
  "wifi_ssid": "MeinWLAN",
  "wifi_password": "********",
  "mqtt_host": "192.168.178.20",
  "mqtt_port": 1883,
  "mqtt_username": "sentero",
  "mqtt_password": "********",
  "topic_prefix": "sentero",
  "device_id": "c1001-b16c33e0",
  "friendly_name": "Wohnzimmer Praesenzsensor",
  "room_id": "living_room",
  "token": "optional"
}
```

### Pflichtfelder

Aktuell ist nur ein MQTT-Host zwingend erforderlich:

``` text
mqtt.host oder mqtt_host
```

WLAN-Daten koennen mitgesendet werden und werden dann erneut in ESP-IDF
gespeichert. Im normalen Flow wurden sie aber bereits vorher im Captive Portal
gespeichert.

Akzeptierte Protokollversionen:

``` text
1, 2
```

### Erfolgreiche Response

``` json
{
  "ok": true,
  "success": true,
  "device_id": "c1001-b16c33e0",
  "model": "C1001",
  "firmware": "1.0.0"
}
```

Nach der Response:

1. Die Firmware speichert alle Provisioning-Daten im NVS-Namespace `sentero`.
2. Vorhandene MQTT-Verbindungen werden zurueckgesetzt.
3. Falls WLAN-Daten vorhanden sind, werden sie in ESP-IDF gespeichert.
4. Der Sensor startet nach ca. 1,5 Sekunden neu.

### Fehlercodes des Sensors

``` text
400 body_too_large
400 request_read_failed
400 invalid_json
400 unsupported_protocol
400 missing_required_fields
409 already_provisioned
500 nvs_open_failed
```

`already_provisioned` bedeutet: Der Sensor darf nicht erneut per HTTP
umprovisioniert werden. Erst ein Factory Reset loescht diesen Schutz.

------------------------------------------------------------------------

## Sentero Backend API

### Status

``` text
GET /api/sentero/sensors/provisioning/status
```

### Discovery starten

``` text
POST /api/sentero/sensors/provisioning/esp32/discovery/start
```

### Entdeckte Sensoren

``` text
GET /api/sentero/sensors/provisioning/esp32/discovered
```

### Praesenzsensor einrichten

``` text
POST /api/sentero/sensors/provisioning/esp32/start
```

Request:

``` json
{
  "room_id": "living_room",
  "display_name": "Wohnzimmer Praesenzsensor",
  "device_id": "c1001-b16c33e0"
}
```

Erfolgreiche Response:

``` json
{
  "ok": true,
  "device": {
    "id": "c1001-b16c33e0",
    "name": "Wohnzimmer Praesenzsensor",
    "type": "presence_radar",
    "room_id": "living_room",
    "source": "mqtt"
  },
  "message": "Praesenzsensor erfolgreich eingerichtet."
}
```

------------------------------------------------------------------------

## Sentero Konfiguration

Nicht-sensitive Werte stehen in `config/sentero.yaml`:

``` yaml
esp32:
  topic_prefix: sentero
  discovery_port: 37020
  discovery_wait_timeout: 6
  provisioning_timeout: 10
  mqtt_wait_timeout: 30
  token: SENTERO_ESP32_DEVICE_TOKEN
```

Umgebungsvariablen koennen diese Werte ueberschreiben:

``` dotenv
SENTERO_ESP32_DISCOVERY_PORT=37020
SENTERO_ESP32_DISCOVERY_WAIT_TIMEOUT=6
SENTERO_ESP32_PROVISIONING_TIMEOUT=10
SENTERO_ESP32_MQTT_WAIT_TIMEOUT=30
SENTERO_ESP32_TOPIC_PREFIX=sentero
SENTERO_ESP32_DEVICE_TOKEN=
```

Eine feste `provisioning_url` wird nicht verwendet. Sentero baut die URL aus
der UDP-Discovery:

``` text
http://<sender-ip>:<http_port>/api/provision
```

Wenn `http_port` fehlt, verwendet Sentero Port `80`. Ein Fake-Server auf
`localhost:8088` muss deshalb im UDP-Payload `"http_port": 8088` senden.

Passwoerter und Tokens werden nicht geloggt.

`esp32.token` kann ein direkter Token oder der Name einer Umgebungsvariable
sein. Wenn `SENTERO_ESP32_DEVICE_TOKEN` in `.env` gesetzt ist, hat dieser Wert
Vorrang. Wenn in `sentero.yaml` nur der Platzhalter steht und die
Umgebungsvariable fehlt, wird kein Token an den Sensor gesendet.

------------------------------------------------------------------------

## MQTT nach der Einrichtung

Nach dem Neustart verbindet sich der Sensor mit dem konfigurierten Broker:

``` text
mqtt://<mqtt_host>:<mqtt_port>
client_id: sentero-<device_id>
```

Wenn ein MQTT-Benutzer gesetzt ist, wird auch das MQTT-Passwort verwendet.
Das Topic-Prefix wird aus der Provisioning-Konfiguration gelesen und
normalisiert. Leere oder nur aus Slashes bestehende Prefixes fallen auf
`sentero` zurueck.

### Topics

``` text
<topic_prefix>/<device_id>/availability
<topic_prefix>/<device_id>/state
<topic_prefix>/<device_id>/status
<topic_prefix>/<device_id>/command
```

Standard:

``` text
sentero/<device_id>/availability
sentero/<device_id>/state
sentero/<device_id>/status
sentero/<device_id>/command
```

`availability` und `state` werden retained veroeffentlicht. Das Status-Topic
fuer Kommandobestaetigungen wird nicht retained.

### Availability

Beim MQTT-Connect veroeffentlicht der Sensor `online`. Danach wiederholt er
Availability alle 60 Sekunden. Als Last Will ist `offline` retained
konfiguriert.

``` json
{
  "device_id": "c1001-b16c33e0",
  "status": "online",
  "firmware": "1.0.0"
}
```

``` json
{
  "device_id": "c1001-b16c33e0",
  "status": "offline",
  "firmware": "1.0.0"
}
```

Lifecycle-Zustaende wie `factory_resetting`, `command_accepted` oder
`command_rejected` gehoeren nicht in `availability`, sondern in
`status`.

### State

Der Sensor veroeffentlicht State:

- direkt nach MQTT-Connect
- bei relevanten Sensor-Aenderungen, maximal einmal pro Sekunde
- als Heartbeat spaetestens alle 5 Minuten

Beispiel:

``` json
{
  "device_id": "c1001-b16c33e0",
  "name": "Wohnzimmer Praesenzsensor",
  "type": "presence_radar",
  "manufacturer": "Sentero",
  "model": "C1001",
  "firmware": "1.0.0",
  "status": "online",
  "capabilities": [
    "presence",
    "motion",
    "fall_detection",
    "signal_quality"
  ],
  "presence": true,
  "fall_detected": false,
  "motion": "moving",
  "moving_range": 182,
  "work_mode": 1,
  "sensor_ready": true,
  "sensor_status": "ok",
  "setup_attempts": 1,
  "last_sensor_update_ms": 123456,
  "power_source": "usb",
  "signal_quality": 82,
  "command_topic": "sentero/c1001-b16c33e0/command",
  "writable_settings": [
    "hp_led",
    "fall_led",
    "install_height",
    "fall_time",
    "unmanned_time",
    "residence_time",
    "fall_sensitivity"
  ],
  "friendly_name": "Wohnzimmer Praesenzsensor",
  "room_id": "living_room",
  "room_hint": "living_room"
}
```

Fuer dauerhaft per USB/Netzteil versorgte C1001-Sensoren wird kein `battery`
gesendet. Stattdessen sendet die Firmware:

``` json
{
  "power_source": "usb"
}
```

------------------------------------------------------------------------

## MQTT-Kommandos

Runtime-Kommandos laufen ausschliesslich ueber MQTT:

``` text
sentero/<device_id>/command
```

Die Firmware akzeptiert Command-Namen mit Bindestrich oder Unterstrich und
normalisiert sie auf Kleinbuchstaben mit Unterstrich.

### Allgemeine Bestaetigung

Erfolgreiche Kommandos:

``` json
{
  "device_id": "c1001-b16c33e0",
  "status": "command_accepted",
  "command": "set_hp_led",
  "ok": true,
  "message": "enabled"
}
```

Abgelehnte Kommandos:

``` json
{
  "device_id": "c1001-b16c33e0",
  "status": "command_rejected",
  "command": "set_hp_led",
  "ok": false,
  "message": "missing_or_invalid_boolean"
}
```

Moegliche Ablehnungsgruende sind unter anderem:

- `invalid_json`
- `missing_command`
- `unsupported_command`
- `missing_or_invalid_boolean`
- `missing_or_invalid_number`
- `value_out_of_range`
- `no_known_settings`

### Sensor-Neustart

``` json
{
  "command": "reset_sensor"
}
```

Alias:

``` text
sensor_restart
restart_sensor
```

### LEDs

``` json
{
  "command": "set_hp_led",
  "enabled": true
}
```

``` json
{
  "command": "set_fall_led",
  "enabled": false
}
```

Alias:

``` text
hp_led
fall_led
```

Boolesche Werte koennen als Boolean, Zahl oder Text gesendet werden:
`true`, `false`, `1`, `0`, `on`, `off`, `yes`, `no`.

### Einzelne Zahlenwerte

Montagehoehe:

``` json
{
  "command": "set_install_height",
  "centimeters": 270
}
```

Range:

``` text
100..400
```

Sturz-Verzoegerung:

``` json
{
  "command": "set_fall_time",
  "seconds": 5
}
```

Range:

``` text
0..60
```

Abwesenheitszeit:

``` json
{
  "command": "set_unmanned_time",
  "seconds": 1
}
```

Range:

``` text
0..60
```

Verweilzeit:

``` json
{
  "command": "set_residence_time",
  "seconds": 200
}
```

Range:

``` text
0..3600
```

Sturz-Empfindlichkeit:

``` json
{
  "command": "set_fall_sensitivity",
  "sensitivity": 3
}
```

Range:

``` text
0..3
```

Alle Zahlenkommandos akzeptieren alternativ auch das Feld `value`.

### Mehrere Einstellungen

``` json
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

Alias:

``` text
set_config
```

Die Felder koennen auch direkt auf Root-Ebene stehen, wenn kein `settings`
Objekt gesendet wird.

------------------------------------------------------------------------

## Factory Reset

Wenn ein ESP32/C1001-Praesenzsensor aus Sentero geloescht wird, wird er nicht
per HTTP angesprochen. HTTP ist ausschliesslich fuer die Ersteinrichtung
reserviert. Runtime-Kommandos laufen ueber MQTT.

### Command Topic

``` text
sentero/<device_id>/command
```

Payload:

``` json
{
  "command": "factory_reset",
  "reason": "removed_from_sentero"
}
```

Alias:

``` text
factory_resetting
```

### Status Topic

``` text
sentero/<device_id>/status
```

Erwartete Bestaetigung:

``` json
{
  "device_id": "c1001-b16c33e0",
  "status": "factory_resetting"
}
```

Nach Empfang von `factory_reset`:

1. Der Sensor veroeffentlicht `factory_resetting` auf dem Status-Topic.
2. Der Sensor veroeffentlicht `offline` auf dem Availability-Topic.
3. Der Sensor loescht den kompletten NVS-Namespace `sentero`.
4. Der Sensor ruft `esp_wifi_restore()` auf.
5. Der Sensor startet nach ca. 500 ms neu.
6. Danach befindet er sich wieder im Einrichtungszustand.

`availability` bleibt ausschliesslich fuer `online` und `offline`.

### Offline-Sensoren

Wenn der Sensor offline ist, sendet Sentero kein Factory-Reset-Kommando. Die UI
bietet dann nur eine bewusste lokale Entfernung an:

``` text
Nur aus Sentero entfernen
```

In diesem Fall bleibt der Sensor selbst unveraendert. Wird er spaeter wieder
eingeschaltet, muss er manuell auf Werkseinstellungen zurueckgesetzt werden,
bevor er erneut sauber eingerichtet wird.

------------------------------------------------------------------------

## Sicherheitsregeln

- WLAN-Passwort niemals im Klartext protokollieren.
- MQTT-Passwort niemals im Klartext protokollieren.
- Provisioning per HTTP nur einmal zulassen, solange `provisioned` gesetzt ist.
- Umprovisionierung nur nach Factory Reset erlauben.
- Direkte HTTP-Kommunikation nur waehrend der Ersteinrichtung verwenden.
- Runtime-Aktionen ausschliesslich ueber MQTT-Kommandos ausfuehren.

------------------------------------------------------------------------

## Architekturprinzip

Sentero verwendet zwei klar getrennte Kommunikationsprotokolle:

1. **Provisioning-Protokoll**
   - nur waehrend der Ersteinrichtung aktiv
   - direkte Kommunikation zwischen Sentero und Sensor
   - Uebertragung der Provisioning-Konfiguration
2. **MQTT-Laufzeitprotokoll**
   - fuer den gesamten normalen Betrieb
   - Uebertragung aller Sensordaten, Statusmeldungen und Ereignisse
   - keine direkte HTTP-Kommunikation mehr zwischen Sentero und Sensor

Diese Trennung vereinfacht die Firmware, reduziert Seiteneffekte und haelt die
Einrichtungslogik vom normalen Sensorbetrieb getrennt.
