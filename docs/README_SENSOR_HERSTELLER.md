# README_SENSOR_HERSTELLER.md

# Sentero -- Sensor-Integrationsvertrag für Hersteller

**Version:** 1.0 (Entwurf)\
**Gültig ab:** Sentero 1.x

------------------------------------------------------------------------

## Zweck

Dieses Dokument beschreibt die technische Schnittstelle zwischen einem
Sensor (Firmware) und der Sentero-Plattform.

Ziel ist, dass jeder kompatible Sensor von Sentero automatisch erkannt,
registriert und verwendet werden kann.

Dieses Dokument richtet sich ausschließlich an Hersteller und
Firmware-Entwickler.

------------------------------------------------------------------------

# 1. Unterstützte Sensortypen

## Türkontakt

**Technologie**

-   Zigbee

**Typ**

``` text
door_contact
```

------------------------------------------------------------------------

## Präsenzsensor

**Technologie**

-   ESP32
-   WLAN
-   MQTT
-   C1001

**Typ**

``` text
presence_radar
```

Weitere Sensortypen können zukünftig ergänzt werden.

------------------------------------------------------------------------

# 2. Geräte-ID

Jeder Sensor muss eine eindeutige und dauerhafte Geräte-ID besitzen.

Anforderungen:

-   weltweit eindeutig
-   bleibt nach Neustarts erhalten
-   bleibt nach Firmware-Updates erhalten
-   keine Leerzeichen

Beispiel:

``` text
c1001-wohnzimmer-01
```

------------------------------------------------------------------------

# 3. MQTT-Kommunikation

## Topics

Empfohlen:

``` text
sentero/<device_id>/availability
sentero/<device_id>/state
sentero/<device_id>/status
```

Alternativ:

``` text
c1001/<device_id>/state
```

------------------------------------------------------------------------

# 4. Availability

Beim erfolgreichen Start muss der Sensor senden:

``` text
online
```

Als MQTT Last Will:

``` text
offline
```

Die Availability-Nachricht soll retained sein.

------------------------------------------------------------------------

# 5. Status-Payload

Der Sensor sendet JSON.

## Pflichtfelder

  Feld           Beschreibung
  -------------- --------------------------
  device_id      Eindeutige Geräte-ID
  type           Sensortyp
  manufacturer   Hersteller
  model          Modell
  firmware       Firmware-Version
  capabilities   Unterstützte Fähigkeiten

## Optionale Felder

-   presence
-   motion
-   contact
-   fall_detected
-   breathing_detected
-   respiration_rate
-   temperature
-   humidity
-   illuminance
-   battery
-   signal_quality

------------------------------------------------------------------------

# 6. Unterstützte Capabilities

``` text
presence
motion
contact
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

Nicht unterstützte Felder werden von Sentero ignoriert.

------------------------------------------------------------------------

# 7. Discovery

Während einer Discovery muss der Sensor innerhalb von **180 Sekunden**
mindestens senden:

-   Availability
-   State

Ein Sensor gilt als erkannt, wenn:

-   device_id gültig
-   type gültig
-   mindestens eine Capability vorhanden

------------------------------------------------------------------------

# 8. Registrierung

Nach erfolgreicher Erkennung speichert Sentero:

-   Geräte-ID
-   Name
-   Typ
-   Hersteller
-   Modell
-   Firmware-Version
-   Capabilities
-   Raumzuordnung

Die Raumzuordnung wird ausschließlich von Sentero verwaltet.

------------------------------------------------------------------------

# 9. Provisioning

Die Konfiguration erfolgt automatisch durch Sentero.

Der Benutzer konfiguriert **keine** MQTT- oder WLAN-Parameter am Sensor.

Sentero überträgt bei der Ersteinrichtung automatisch:

-   WLAN-SSID
-   WLAN-Passwort
-   MQTT-Host
-   MQTT-Port
-   MQTT-Benutzer
-   MQTT-Passwort

Die technische Übertragung (z. B. über temporären WLAN-Access-Point oder
BLE) ist implementierungsabhängig und nicht Bestandteil dieses Vertrags.

------------------------------------------------------------------------

# 10. Heartbeat

Empfehlung:

-   alle 60 Sekunden Availability
-   alle 5 Minuten vollständigen Status senden

Der vollständige Status sollte retained veröffentlicht werden.

------------------------------------------------------------------------

# 11. Fehlerstatus

Topic:

``` text
sentero/<device_id>/status
```

Empfohlene Statuswerte:

``` text
online
offline
provisioning
battery_low
wifi_error
mqtt_error
sensor_error
firmware_error
```

------------------------------------------------------------------------

# 12. Sicherheit

Der Sensor muss folgende Anforderungen erfüllen:

-   WLAN- und MQTT-Zugangsdaten niemals protokollieren.
-   Kommunikation ausschließlich innerhalb des lokalen Netzes.
-   Unterstützung individueller Zugangsdaten oder Tokens empfohlen.
-   TLS-Unterstützung für zukünftige Versionen empfohlen.

------------------------------------------------------------------------

# 13. Akzeptanzkriterien

Ein Sensor ist Sentero-kompatibel, wenn er:

-   eine stabile Geräte-ID besitzt,
-   sich mit WLAN verbinden kann,
-   sich mit MQTT verbinden kann,
-   Availability veröffentlicht,
-   gültige JSON-Statusmeldungen sendet,
-   retained Status unterstützt,
-   automatisch erkannt werden kann,
-   durch Sentero registriert werden kann.

------------------------------------------------------------------------

# Grundprinzip

Der Benutzer interagiert ausschließlich mit **Sentero**.

Ob der Sensor intern über Zigbee, WLAN, ESP32 oder MQTT kommuniziert,
ist ein technisches Implementierungsdetail und darf für den Benutzer
nicht sichtbar sein.
