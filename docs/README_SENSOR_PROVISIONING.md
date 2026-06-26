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
- Start-Endpunkt:
  - `POST /api/sentero/sensors/provisioning/esp32/start`
- Direkte HTTP-Übergabe an den Sensor:
  - `POST /api/provision`
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
Provisioning-Modus
        │
        ▼
Temporärer WLAN-Access-Point
(z.B. Sentero-Setup-XXXX)
        │
        ▼
Sentero Wizard startet Einrichtung
        │
        ▼
Sentero Backend verbindet sich mit dem Sensor
        │
        ▼
Übertragung der Konfiguration
        │
        ▼
Sensor speichert Konfiguration
        │
        ▼
Neustart
        │
        ▼
Verbindung mit Heim-WLAN
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

# Provisioning-Schnittstelle

Während der Ersteinrichtung stellt der Sensor einen kleinen HTTP-Server
bereit.

Beispiel:

    http://192.168.4.1

## Endpunkt

    POST /api/provision

## Request

``` json
{
  "protocol": 1,
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
    "timezone": "Europe/Berlin",
    "room_id": "living_room",
    "display_name": "Wohnzimmer Präsenzsensor",
    "token": "optional"
  }
}
```

`room_id` und `display_name` werden vom Wizard an Sentero übergeben und beim
Provisioning an den Sensor weitergereicht. Der Sensor soll diese Werte fuer
eigene MQTT-Metadaten verwenden, Sentero bleibt aber weiterhin die fuehrende
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

### Präsenzsensor einrichten

    POST /api/sentero/sensors/provisioning/esp32/start

Request:

``` json
{
  "room_id": "living_room",
  "display_name": "Wohnzimmer Präsenzsensor"
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
  provisioning_url: http://192.168.4.1/api/provision
  provisioning_timeout: 10
  mqtt_wait_timeout: 30
```

Umgebungsvariablen können diese Werte überschreiben:

``` dotenv
SENTERO_ESP32_PROVISIONING_URL=http://192.168.4.1/api/provision
SENTERO_ESP32_PROVISIONING_TIMEOUT=10
SENTERO_ESP32_MQTT_WAIT_TIMEOUT=30
SENTERO_ESP32_TOPIC_PREFIX=sentero
SENTERO_ESP32_DEVICE_TOKEN=
```

Passwörter und Tokens werden nicht geloggt.

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
