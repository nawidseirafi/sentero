# Provisioning-Protokoll für Sentero WLAN-Sensoren



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
    "token": "optional"
  }
}
```

## Response

``` json
{
  "success": true,
  "device_id": "c1001-wohnzimmer-01",
  "model": "C1001",
  "firmware": "1.0.0"
}
```

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
