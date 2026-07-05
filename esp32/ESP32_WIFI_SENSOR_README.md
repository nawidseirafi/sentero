# Sentero ESP32/WLAN Sensor Contract

Diese README beschreibt, was ein WLAN-Sensor auf Basis ESP32/C1001 unterstuetzen soll, damit Sentero ihn ueber den Sensor Manager erkennen, registrieren und spaeter fuer Dashboard und Aktivitaetslogik verwenden kann.

Die Kunden-UI zeigt diese technischen Details nicht an. Begriffe wie MQTT, Topic, ESP32 oder Payload bleiben reine Entwickler- und Firmware-Details.

## Ziel

Ein WLAN-Sensor soll sich fuer Sentero wie ein normales Sentero-Geraet verhalten:

- stabil erkennbare Geraete-ID
- Status beim Start senden
- Sensordaten bei Aenderung senden
- regelmaessiges Lebenszeichen senden
- Raumzuordnung durch Sentero Wizard ermoeglichen
- keine Home-Assistant-Abhaengigkeit

Zielpfad fuer Produktion:

```text
WLAN-Sensor -> MQTT -> Sentero Sensor Manager -> Device/Event Model -> Activity Engine -> Dashboard
```

## Aktueller Implementierungsstand

Vorhanden:

- Sensor Manager API als zentrale Backend-Schicht
- gemeinsames Sentero Device/Event Model
- Normalisierung fuer Home Assistant und Zigbee2MQTT
- vorbereitete Normalisierung fuer C1001-Felder wie `presence`, `fall_detected`, `breathing_detected`, `respiration_rate`, `sleep_status`, `bed_presence`

Noch offen:

- generischer ESP32/C1001 MQTT-Adapter fuer laufende Event-Aufnahme
- automatisches Provisioning vom Sensor Manager zum ESP32
- dauerhafte MQTT-Subscription mit Event-Speicherung
- echter Firmware-Handshake mit dem Wizard

Bis diese Punkte umgesetzt sind, kann ein ESP32-Sensor manuell mit WLAN- und MQTT-Daten konfiguriert werden.

## Geraete-ID

Jeder Sensor muss eine stabile `device_id` besitzen.

Regeln:

- bleibt ueber Neustarts gleich
- ist pro physischem Sensor eindeutig
- enthaelt keine Leerzeichen
- wird nicht bei Firmware-Update geaendert

Beispiele:

```text
c1001-wohnzimmer-01
sentero-presence-a1b2c3
```

Nicht erlaubt:

```text
esp32-random-1693481234
wohnzimmer sensor
```

## MQTT Topics

Der ESP32/C1001 soll einen dieser Topic-Staemme verwenden:

```text
c1001/<device_id>/state
sentero/<device_id>/state
```

Empfohlen fuer Status/Lebenszeichen:

```text
sentero/<device_id>/availability
sentero/<device_id>/status
```

Beispiele:

```text
c1001/c1001-wohnzimmer-01/state
sentero/c1001-wohnzimmer-01/availability
```

## Availability

Der Sensor soll beim Start `online` senden und als Last-Will `offline` konfigurieren.

Topic:

```text
sentero/<device_id>/availability
```

Payload:

```json
{
  "device_id": "c1001-wohnzimmer-01",
  "status": "online",
  "firmware": "0.1.0",
  "timestamp": "2026-06-25T12:00:00Z"
}
```

Last-Will Payload:

```json
{
  "device_id": "c1001-wohnzimmer-01",
  "status": "offline"
}
```

Die Availability-Nachricht soll retained sein.

## State Payload

Der Sensor sendet seinen Zustand als JSON.

Topic:

```text
c1001/<device_id>/state
```

Beispiel fuer einen C1001/Praesenzsensor:

```json
{
  "device_id": "c1001-wohnzimmer-01",
  "name": "Wohnzimmer Praesenz",
  "type": "presence_radar",
  "room_hint": "living_room",
  "manufacturer": "Sentero",
  "model": "C1001",
  "firmware": "0.1.0",
  "capabilities": [
    "presence",
    "fall_detection",
    "breathing_detection",
    "respiration_rate",
    "battery",
    "signal_quality"
  ],
  "presence": true,
  "bed_presence": false,
  "fall_detected": false,
  "breathing_detected": true,
  "respiration_rate": 16,
  "sleep_status": "unknown",
  "battery": 92,
  "signal_quality": 78,
  "timestamp": "2026-06-25T12:00:00Z"
}
```

## Pflichtfelder

Diese Felder soll jeder WLAN-Sensor senden:

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| `device_id` | string | stabile Sensor-ID |
| `name` | string | menschenlesbarer Name fuer interne Registrierung |
| `type` | string | Geraetetyp |
| `capabilities` | array | unterstuetzte Funktionen |
| `manufacturer` | string | Hersteller |
| `model` | string | Modell |
| `firmware` | string | Firmware-Version |
| `timestamp` | string | UTC-Zeit im ISO-Format |

Wenn der Sensor keine Uhrzeit kennt, darf `timestamp` fehlen. Sentero verwendet dann die Empfangszeit.

## Unterstuetzte Geraetetypen

```text
presence_radar
motion_sensor
button
environmental_sensor
door_contact
```

Fuer ESP32/C1001 ist normalerweise `presence_radar` korrekt.

## Unterstuetzte Capabilities

```text
presence
motion
fall_detection
breathing_detection
respiration_rate
temperature
humidity
illuminance
battery
signal_quality
button
```

## Feldmapping

Sentero erwartet diese Nutzdatenfelder:

| Payload-Feld | Sentero Capability | Erwarteter Wert |
| --- | --- | --- |
| `presence` | `presence` | `true` oder `false` |
| `bed_presence` | `presence` | `true` oder `false` |
| `fall_detected` | `fall_detection` | `true` oder `false` |
| `breathing_detected` | `breathing_detection` | `true` oder `false` |
| `respiration_rate` | `respiration_rate` | Zahl, Atemzuege pro Minute |
| `sleep_status` | Aktivitaetshinweis | `unknown`, `awake`, `resting`, `sleeping` |
| `temperature` | `temperature` | Zahl in Grad Celsius |
| `humidity` | `humidity` | Zahl in Prozent |
| `illuminance` | `illuminance` | Zahl in Lux |
| `battery` | `battery` | Zahl 0 bis 100 |
| `signal_quality` | `signal_quality` | Zahl 0 bis 100 |
| `button` | `button` | Aktion als string |

Produkttexte fuer Fall/Atem duerfen keine medizinischen Versprechen machen. In Sentero werden Begriffe wie `Sturzverdacht`, `Atemhinweis` und `Anwesenheit erkannt` verwendet.

## Sendeverhalten

Der Sensor soll senden:

- beim Start: Availability `online`
- beim Start: kompletten State
- bei jeder relevanten Aenderung: State
- alle 60 Sekunden: kurzer Status oder Availability
- alle 5 Minuten: kompletter State als Heartbeat
- bei sauberem Shutdown, falls moeglich: Availability `offline`

Der komplette State soll retained sein, damit Sentero einen Sensor auch nach Backend-Neustart wiederfindet.

## Wizard-Erkennung

Wenn der Benutzer im Wizard `Sensor hinzufuegen` waehlt, startet Sentero intern eine Discovery.

Damit der Wizard den Sensor erfolgreich findet, muss der Sensor innerhalb des Discovery-Zeitfensters:

- eine gueltige State Payload senden
- eine stabile `device_id` mitsenden
- einen passenden `type` mitsenden
- mindestens eine passende Capability mitsenden
- bei Praesenzsensoren `presence` oder `bed_presence` senden

Der Wizard gilt als erfolgreich, wenn Sentero daraus ein internes Device erzeugen kann:

```json
{
  "id": "sentero_mqtt_c1001_wohnzimmer_01",
  "name": "Wohnzimmer Praesenz",
  "type": "presence_radar",
  "capabilities": ["presence", "breathing_detection", "respiration_rate"],
  "status": "online"
}
```

Der Benutzer sieht dabei nur produktnahe Texte wie `Sensor gefunden`, `Welcher Raum?` und `Fertig`.

## Registrierung

Nach erfolgreicher Erkennung registriert Sentero den Sensor intern mit:

- Device-ID
- Name
- Raum
- Typ
- Capabilities
- Hersteller
- Modell
- Firmware
- letztem Status

Die Firmware muss danach weiterhin dieselbe `device_id` verwenden. Eine Raumzuordnung wird von Sentero verwaltet und muss nicht im Sensor gespeichert werden.

## Provisioning-Zielbild

Spaeter soll der Sensor Manager WLAN- und Verbindungsdaten automatisch an neue Sensoren uebertragen. Der Benutzer sieht dabei nur `Sensor hinzufuegen`.

Empfohlener Provisioning-Ablauf:

1. Neuer Sensor startet im Einrichtungsmodus.
2. Sensor oeffnet temporaer BLE oder einen lokalen Access Point.
3. Sentero Sensor Manager sendet die Konfiguration an den Sensor.
4. Sensor verbindet sich mit WLAN und MQTT.
5. Sensor sendet Availability und State.
6. Wizard zeigt `Sensor gefunden`.

Vorgeschlagene lokale Provisioning Payload:

```json
{
  "wifi_ssid": "MeinWLAN",
  "wifi_password": "secret",
  "mqtt_host": "sentero.local",
  "mqtt_port": 1883,
  "mqtt_username": "sentero_sensor",
  "mqtt_password": "secret",
  "topic_prefix": "sentero",
  "device_token": "optional-per-device-token"
}
```

Vorgeschlagene Antwort des Sensors:

```json
{
  "ok": true,
  "device_id": "c1001-wohnzimmer-01",
  "model": "C1001",
  "firmware": "0.1.0"
}
```

Diese Provisioning-Schnittstelle ist noch TODO und muss zwischen Firmware und Sensor Manager implementiert werden.

## Fehlerstatus

Ein Sensor soll Fehler knapp und maschinenlesbar melden.

Topic:

```text
sentero/<device_id>/status
```

Payload:

```json
{
  "device_id": "c1001-wohnzimmer-01",
  "status": "sensor_error",
  "error": "radar_not_ready",
  "battery": 24,
  "timestamp": "2026-06-25T12:00:00Z"
}
```

Empfohlene Statuswerte:

```text
online
offline
battery_low
sensor_error
wifi_error
mqtt_error
provisioning
```

## Sicherheit

- WLAN- und MQTT-Passwoerter duerfen nie in Logs geschrieben werden.
- Sensoren sollen keine Rohdaten an externe Dienste senden.
- MQTT soll im lokalen Netz bleiben.
- Fuer spaetere Produktion sollte pro Sensor ein Token oder eigenes MQTT-Credential vorbereitet werden.
- TLS ist optional fuer lokale Installationen, aber sinnvoll, sobald MQTT ueber Netzwerkgrenzen erreichbar ist.

## Akzeptanzkriterien

Ein ESP32/C1001-Sensor ist Sentero-kompatibel, wenn:

- er eine stabile `device_id` verwendet
- er auf einem unterstuetzten Topic JSON-State sendet
- der State retained ist
- Availability mit `online`/`offline` funktioniert
- Praesenz als `presence` oder `bed_presence` gesendet wird
- optionale C1001-Werte nach diesem Contract benannt sind
- Sentero den Sensor im Wizard innerhalb von 180 Sekunden erkennen kann
- Sentero den Sensor ohne technische Details in der UI registrieren kann

## Minimalbeispiel

Minimaler gueltiger State fuer einen Praesenzsensor:

```json
{
  "device_id": "c1001-wohnzimmer-01",
  "name": "Wohnzimmer Praesenz",
  "type": "presence_radar",
  "manufacturer": "Sentero",
  "model": "C1001",
  "firmware": "0.1.0",
  "capabilities": ["presence"],
  "presence": true
}
```

Minimaler gueltiger State fuer einen Umweltsensor:

```json
{
  "device_id": "sentero-klima-01",
  "name": "Wohnzimmer Klima",
  "type": "environmental_sensor",
  "manufacturer": "Sentero",
  "model": "ESP32",
  "firmware": "0.1.0",
  "capabilities": ["temperature", "humidity"],
  "temperature": 22.4,
  "humidity": 48
}
```
