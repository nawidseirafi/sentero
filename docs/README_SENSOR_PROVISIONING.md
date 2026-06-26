# Provisioning-Protokoll für Sentero WLAN-Sensoren

## Implementierungsstatus in Sentero

Stand aktuell: **Sentero-seitig produktiv testbar implementiert**.

Bereits vorhanden:

- Netzwerkeinstellungen in Sentero speichern:
  - WLAN-SSID
  - WLAN-Passwort
- MQTT-Zugangsdaten werden aus `.env`/`config/sentero.yaml` gelesen.
- Status-Endpunkt:
  - `GET /api/sentero/sensors/provisioning/status`
- UDP-Discovery:
  - Sentero lauscht auf UDP-Port `37020`.
  - Sensor sendet alle 2 Sekunden Broadcasts an `255.255.255.255:37020`.
- Discovery-Endpunkte:
  - `POST /api/sentero/sensors/provisioning/esp32/discovery/start`
  - `GET /api/sentero/sensors/provisioning/esp32/discovered`
- Start-Endpunkt:
  - `POST /api/sentero/sensors/provisioning/esp32/start`
- Direkte HTTP-Übergabe an den Sensor:
  - `POST http://<sensor-ip>/api/provision`
- Warten auf MQTT-Availability und ersten MQTT-State:
  - `sentero/<device_id>/availability`
  - `sentero/<device_id>/state`
  - alternativ `c1001/<device_id>/state`
- Automatische Registrierung als Sentero-Präsenzsensor.
- Produktorientierter Wizard-Flow für Präsenzsensoren.

Noch offen:

- Hardwareseitiger Sensor muss das unten beschriebene HTTP-Protokoll
  implementieren.
- Der Sensor muss nach erfolgreicher Provisionierung Availability und State
  per MQTT veröffentlichen.
- Optionaler sicherer Geräte-Token-Austausch ist vorbereitet, aber noch kein
  verpflichtender Produktionsstandard.



## Ziel

Ein Benutzer soll einen neuen Sensor hinzufügen können, **ohne** MQTT,
WLAN-Konfiguration oder technische Details kennen zu müssen.

Der Wizard übernimmt die komplette Einrichtung.

------------------------------------------------------------------------

# Ablauf

``` text
Sensor einschalten
        │
        ▼
Sensor-Hotspot-Seite öffnen
        │
        ▼
WLAN-SSID und WLAN-Passwort eingeben
        │
        ▼
Sensor verbindet sich mit Heim-WLAN
        │
        ▼
Sensor sendet UDP-Broadcasts
        │
        ▼
Sentero Backend findet Sensor
        │
        ▼
Sentero Wizard startet Einrichtung
        │
        ▼
Sentero Backend ruft den Sensor per HTTP im Heimnetz auf
        │
        ▼
Übertragung der Konfiguration
        │
        ▼
Sensor speichert Konfiguration
        │
        ▼
Sensor speichert MQTT- und Device-Konfiguration
        │
        ▼
Verbindung mit MQTT
        │
        ▼
Availability + State senden
        │
        ▼
Wizard zeigt:
„Sensor erfolgreich eingerichtet“
```

------------------------------------------------------------------------

# UDP-Discovery

Solange der Sensor im Heimnetz noch nicht provisioniert ist, sendet er alle
2 Sekunden einen UDP-Broadcast.

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
  "device_id": "c1001-a1b2c3d4",
  "model": "C1001",
  "firmware": "1.0.0",
  "sensor_type": "presence_radar",
  "http_port": 80,
  "capabilities": [
    "presence",
    "fall_detection",
    "breathing_detection",
    "respiration_rate",
    "signal_quality"
  ]
}
```

Sentero speichert daraus intern:

- Absender-IP
- HTTP-Port, Standard `80`
- `device_id`
- `model`
- `firmware`
- `capabilities`
- Status `pending`

------------------------------------------------------------------------

# HTTP-Provisioning-Schnittstelle

Nach erfolgreicher UDP-Discovery ruft Sentero den Sensor direkt im Heimnetz
per HTTP auf.

Beispiel:

    http://192.168.178.44/api/provision

## Endpunkt

    POST /api/provision

## Request

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
    "device_id": "c1001-a1b2c3d4",
    "friendly_name": "Wohnzimmer Präsenzsensor",
    "room_id": "living_room",
    "timezone": "Europe/Berlin",
    "token": "optional"
  }
}
```

`room_id` und `friendly_name` werden vom Wizard an Sentero übergeben und beim
Provisioning an den Sensor weitergereicht. Der Sensor soll diese Werte fuer
eigene MQTT-Metadaten verwenden. Sentero bleibt aber weiterhin die fuehrende
Device Registry.

## Response

``` json
{
  "success": true,
  "device_id": "c1001-wohnzimmer-01",
  "model": "C1001",
  "firmware": "1.0.0"
}
```

## Sentero API

### Status

    GET /api/sentero/sensors/provisioning/status

### Discovery starten

    POST /api/sentero/sensors/provisioning/esp32/discovery/start

### Entdeckte Sensoren

    GET /api/sentero/sensors/provisioning/esp32/discovered

### Präsenzsensor einrichten

    POST /api/sentero/sensors/provisioning/esp32/start

Request:

``` json
{
  "room_id": "living_room",
  "display_name": "Wohnzimmer Präsenzsensor",
  "device_id": "c1001-a1b2c3d4"
}
```

Erfolgreiche Response:

``` json
{
  "ok": true,
  "device": {
    "id": "c1001-wohnzimmer-01",
    "name": "Wohnzimmer Präsenzsensor",
    "type": "presence_radar",
    "room_id": "living_room",
    "source": "mqtt"
  },
  "message": "Präsenzsensor erfolgreich eingerichtet."
}
```

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

Umgebungsvariablen können diese Werte überschreiben:

``` dotenv
SENTERO_ESP32_DISCOVERY_PORT=37020
SENTERO_ESP32_DISCOVERY_WAIT_TIMEOUT=6
SENTERO_ESP32_PROVISIONING_TIMEOUT=10
SENTERO_ESP32_MQTT_WAIT_TIMEOUT=30
SENTERO_ESP32_TOPIC_PREFIX=sentero
SENTERO_ESP32_DEVICE_TOKEN=
```

Eine feste `provisioning_url` wird nicht mehr verwendet. Sentero baut die URL
aus der UDP-Discovery:

``` text
http://<sender-ip>:<http_port>/api/provision
```

Wenn `http_port` fehlt, verwendet Sentero Port `80`. Ein Fake-Server auf
`localhost:8088` soll deshalb im UDP-Payload `"http_port": 8088` senden.

Passwörter und Tokens werden nicht geloggt.

`esp32.token` kann ein direkter Token oder ein Name einer Umgebungsvariable sein.
Wenn `SENTERO_ESP32_DEVICE_TOKEN` in `.env` gesetzt ist, hat dieser Wert Vorrang.
Wenn in `sentero.yaml` nur der Platzhalter steht und die Umgebungsvariable fehlt,
wird kein Token an den Sensor gesendet.

------------------------------------------------------------------------

# Fehlercodes

HTTP 400 - Ungültige Daten

HTTP 401 - Ungültiger Provisioning-Token (optional)

HTTP 500 - Sensor konnte Konfiguration nicht speichern

------------------------------------------------------------------------

# Verhalten nach erfolgreicher Provisionierung

Der Sensor muss:

1.  Konfiguration speichern
2.  Provisioning-Modus verlassen
3.  Neustarten
4.  Verbindung mit WLAN herstellen
5.  Verbindung mit MQTT herstellen
6.  Availability veröffentlichen
7.  Vollständigen State veröffentlichen

Akzeptierte State-Topics:

``` text
sentero/<device_id>/state
c1001/<device_id>/state
```

Akzeptiertes Availability-Topic:

``` text
sentero/<device_id>/availability
```

Availability ist ausschließlich für die Erreichbarkeit des Sensors
gedacht. Erlaubte Statuswerte:

``` json
{ "device_id": "c1001-a1b2c3d4", "status": "online" }
```

``` json
{ "device_id": "c1001-a1b2c3d4", "status": "offline" }
```

Lifecycle-Zustände wie `factory_resetting`, `booting`, `provisioning`
oder ähnliche Zustände gehören nicht in `availability`, sondern in das
separate Status-Topic:

``` text
sentero/<device_id>/status
```

Beispiel-State:

``` json
{
  "presence": true,
  "fall_detected": false,
  "breathing_detected": true,
  "respiration_rate": 14,
  "battery": 98,
  "signal_quality": 82
}
```

Für dauerhaft per USB/Netzteil versorgte ESP32/C1001-Sensoren ist
`battery` optional und soll nur gesendet werden, wenn tatsächlich ein
Akku vorhanden ist. Bei USB-Strom sendet der Sensor stattdessen:

``` json
{
  "presence": true,
  "power_source": "usb",
  "signal_quality": 82
}
```

Sentero zeigt diesen Sensor dann als netzbetrieben an und erzeugt keine
Akku-Warnung wegen fehlender Batterieinformation.

------------------------------------------------------------------------

# Sicherheitsregeln

-   WLAN-Passwort niemals protokollieren.
-   MQTT-Passwort niemals protokollieren.
-   Provisioning nur im Einrichtungsmodus erlauben.
-   Nach erfolgreicher Einrichtung den Access-Point deaktivieren.

------------------------------------------------------------------------

# Ziel

Nach erfolgreicher Provisionierung darf der Benutzer ausschließlich
sehen:

-   Sensor gefunden
-   Raum auswählen
-   Einrichtung abgeschlossen

Alle technischen Details bleiben ausschließlich zwischen Sensor und
Sentero Backend.

------------------------------------------------------------------------

# Kommunikation nach der Einrichtung

Die direkte Kommunikation zwischen Sentero und dem Sensor erfolgt
**ausschließlich während der Ersteinrichtung (Provisioning)**.

Nach erfolgreicher Provisionierung wird die direkte Verbindung beendet.

## Phase 1 -- Ersteinrichtung

Während der Einrichtung kommuniziert der Sentero Sensor Manager direkt
mit dem Sensor.

``` text
Wizard
    │
    ▼
Sentero Backend
    │
    ▼
Provisioning Service
    │
    ▼
ESP32 / Sensor
```

Dabei werden unter anderem folgende Informationen übertragen:

-   WLAN-SSID
-   WLAN-Passwort
-   MQTT-Host
-   MQTT-Port
-   MQTT-Benutzer
-   MQTT-Passwort
-   optionale Geräte-Tokens
-   Zeitzone

Nach erfolgreicher Übertragung bestätigt der Sensor die Konfiguration
und startet neu.

------------------------------------------------------------------------

## Phase 2 -- Normalbetrieb

Nach dem Neustart verbindet sich der Sensor selbstständig mit dem
Heimnetz und anschließend mit dem MQTT-Broker.

Ab diesem Zeitpunkt findet **keine direkte Kommunikation** zwischen
Sentero und dem Sensor mehr statt.

``` text
Sensor
   │
   ▼
WLAN
   │
   ▼
MQTT Broker
   │
   ▼
Sentero Sensor Manager
```

Sämtliche Statusmeldungen, Ereignisse und Sensordaten werden
ausschließlich über MQTT übertragen.

------------------------------------------------------------------------

## Erneute direkte Kommunikation

Eine direkte Verbindung wird nur wieder aufgebaut, wenn der Benutzer
bewusst eine Neueinrichtung startet, zum Beispiel:

-   WLAN ändern
-   Sensor auf Werkseinstellungen zurücksetzen
-   Sensor erneut einrichten
-   Sensor austauschen

Im normalen Betrieb bleibt die Provisioning-Schnittstelle deaktiviert.

------------------------------------------------------------------------

## Factory Reset

Wenn ein ESP32/C1001-Präsenzsensor aus Sentero gelöscht wird, wird er
nicht per HTTP angesprochen. HTTP ist ausschließlich für die
Ersteinrichtung reserviert. Runtime-Kommandos laufen über MQTT.

Der Sensorbauer muss dieses MQTT-Kommando implementieren. Sentero sendet
beim Löschen eines Sensors genau diese Nachricht:

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

Das Topic-Prefix ist standardmäßig `sentero` und kann über
`SENTERO_ESP32_TOPIC_PREFIX` oder `esp32.topic_prefix` geändert werden.
Das Kommando wird nicht per HTTP gesendet.

### Status Topic

``` text
sentero/<device_id>/status
```

Dieses Topic beschreibt Lifecycle-Zustände des Sensors. Es ersetzt nicht
das Availability-Topic. `availability` bleibt ausschließlich
`online`/`offline`.

Erwartete Bestätigung:

``` json
{
  "device_id": "c1001-a1b2c3d4",
  "status": "factory_resetting"
}
```

Sentero wartet nach dem Publish bis zu 10 Sekunden auf diese Bestätigung
auf dem Status-Topic. Die Bestätigung gilt als passend, wenn
`status` exakt `factory_resetting` ist und `device_id` entweder fehlt
oder der gelöschten Sensor-ID entspricht.

Nach Empfang von `factory_reset` soll der Sensor:

1.  `factory_resetting` auf `sentero/<device_id>/status` veröffentlichen
2.  WLAN-, MQTT- und Device-Konfiguration löschen
3.  gespeicherte Tokens und Raum-/Friendly-Name-Metadaten löschen
4.  neu starten
5.  wieder in den Einrichtungsmodus wechseln

`availability` darf dafür nicht verwendet werden. Dieses Topic bleibt
ausschließlich für `online` und `offline`.

### Ablauf

1.  Sentero prüft, ob der Sensor erreichbar ist.
    -   Dafür wird `sentero/<device_id>/availability` verwendet.
    -   Erwartet wird `status: "online"`.
2.  Sentero sendet das Factory-Reset-Kommando per MQTT.
3.  Der Sensor bestätigt auf `sentero/<device_id>/status` mit
    `status: "factory_resetting"`.
4.  Sentero wartet bis zu 10 Sekunden auf diese Bestätigung.
5.  Erst nach bestätigtem Reset löscht Sentero die lokale Registrierung.
6.  Der Sensor löscht seine Provisioning-Konfiguration und startet neu.
7.  Beim MQTT-Trennen setzt der Sensor bzw. MQTT Last Will
    `sentero/<device_id>/availability` auf `offline`.
8.  Danach befindet sich der Sensor wieder im Einrichtungszustand.

Der Sensor muss dabei löschen:

-   WLAN-Konfiguration
-   MQTT-Host, Port, Benutzer und Passwort
-   Geräte-Token
-   `device_id`
-   Anzeigename
-   Raumzuordnung

Nach dem Neustart öffnet der Sensor wieder seinen Setup-Hotspot. Nach
erneuter WLAN-Einrichtung sendet er wieder UDP-Discovery.

### Offline-Sensoren

Wenn der Sensor offline ist, sendet Sentero kein Factory-Reset-Kommando.
Die UI bietet dann nur eine bewusste lokale Entfernung an:

``` text
Nur aus Sentero entfernen
```

In diesem Fall bleibt der Sensor selbst unverändert. Wird er später
wieder eingeschaltet, muss er manuell auf Werkseinstellungen
zurückgesetzt werden, bevor er erneut sauber eingerichtet wird.

------------------------------------------------------------------------

## Architekturprinzip

Sentero verwendet zwei klar getrennte Kommunikationsprotokolle:

1.  **Provisioning-Protokoll**
    -   nur während der Ersteinrichtung aktiv
    -   direkte Kommunikation zwischen Sentero und Sensor
    -   Übertragung der Konfiguration
2.  **MQTT-Laufzeitprotokoll**
    -   für den gesamten normalen Betrieb
    -   Übertragung aller Sensordaten, Statusmeldungen und Ereignisse
    -   keine direkte Kommunikation mehr zwischen Sentero und Sensor

Diese Trennung vereinfacht die Firmware, erhöht die Sicherheit und
ermöglicht einen einheitlichen Betrieb aller Sentero-Sensoren.
