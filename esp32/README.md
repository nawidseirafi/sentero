# C1001 ESP32 Firmware bauen und flashen

Diese Anleitung beschreibt den manuellen Build- und Flash-Vorgang fuer den
Sentero C1001 mmWave Sensor mit ESPHome.

## Voraussetzungen

- macOS Terminal
- ESPHome installiert
- Sensor per USB mit dem Mac verbunden
- Projekt liegt unter:

```bash
/Users/nawid/Projects/sentero/esp32
```

Die Firmware-Datei ist:

```bash
c1001-mmwave.yaml
```

## 1. In den ESP32-Ordner wechseln

```bash
cd /Users/nawid/Projects/sentero/esp32
```

## 2. ESPHome pruefen

Aktuell ist ESPHome global installiert:

```bash
/opt/homebrew/bin/esphome version
```

Falls du spaeter wieder eine virtuelle Umgebung benutzt, pruefe zuerst, ob dort
ein ESPHome-Binary existiert:

```bash
ls -la /Users/nawid/Projects/sentero/.venv/bin/esphome
```

Wenn diese Datei nicht existiert, benutze weiter:

```bash
/opt/homebrew/bin/esphome
```

## 3. USB-Port finden

Sensor einstecken und Ports anzeigen:

```bash
ls /dev/cu.*
```

Typischer Port fuer den ESP32-C3:

```bash
/dev/cu.usbmodem11401
```

Wenn nur diese Ports erscheinen, wird der ESP gerade nicht erkannt:

```text
/dev/cu.Bluetooth-Incoming-Port
/dev/cu.debug-console
```

Dann:

1. USB abziehen.
2. Reset/Boot-Taster loslassen.
3. Drei Sekunden warten.
4. USB wieder einstecken.
5. `ls /dev/cu.*` erneut ausfuehren.

Wichtig: `GPIO9` ist beim ESP32-C3 der BOOT/Strapping-Pin. Wenn der Taster beim
Start gedrueckt ist oder elektrisch auf LOW haengt, startet der ESP nicht normal,
sondern im Bootloader-Modus.

## 4. Firmware bauen

```bash
/opt/homebrew/bin/esphome compile c1001-mmwave.yaml
```

Ein erfolgreicher Build endet ungefaehr so:

```text
Successfully compiled program.
```

Die erzeugte Firmware liegt danach unter:

```bash
.esphome/build/c1001-mmwave/.pioenvs/c1001-mmwave/firmware.bin
.esphome/build/c1001-mmwave/.pioenvs/c1001-mmwave/firmware.factory.bin
```

## 5. Firmware flashen

Port ggf. anpassen:

```bash
/opt/homebrew/bin/esphome run c1001-mmwave.yaml --device /dev/cu.usbmodem11401
```

Der Befehl baut bei Bedarf neu, flasht die Firmware und oeffnet danach die Logs.

Wenn du nur flashen und keine Logs offen halten willst:

```bash
/opt/homebrew/bin/esphome run c1001-mmwave.yaml --device /dev/cu.usbmodem11401 --no-logs
```

## 6. Logs manuell ansehen

```bash
/opt/homebrew/bin/esphome logs c1001-mmwave.yaml --device /dev/cu.usbmodem11401 --no-states
```

Beim normalen Start sollten ESPHome-Logs erscheinen. Nach einem Provisioning
solltest du unter anderem Zeilen wie diese sehen:

```text
MQTT Verbindung startet host=... port=1883 topic_prefix=sentero device_id=...
MQTT verbunden
MQTT publish topic=...
```

Beim Factory-Reset-Taster sollte diese Zeile erscheinen:

```text
Factory-Reset-Taster 10s gehalten, bitte loslassen
```

Danach Taster loslassen. Erst danach startet der Reset.

## 7. Wenn der Sensor nicht mehr startet

Erst pruefen, ob der USB-Port sichtbar ist:

```bash
ls /dev/cu.*
```

Wenn kein `/dev/cu.usbmodem...` sichtbar ist:

1. Taster loslassen.
2. USB abziehen.
3. Drei Sekunden warten.
4. USB wieder einstecken.
5. Port erneut pruefen.

Wenn der Port sichtbar ist, direkt neu flashen:

```bash
/opt/homebrew/bin/esphome run c1001-mmwave.yaml --device /dev/cu.usbmodem11401
```

## 8. Optional: Flash komplett loeschen

Nur benutzen, wenn der ESP wirklich sauber zurueckgesetzt werden soll. Dabei
gehen gespeicherte WLAN-, ESPHome- und Sentero-Provisioning-Daten verloren.

```bash
/Users/nawid/.platformio/penv/bin/esptool --chip esp32c3 --port /dev/cu.usbmodem11401 erase-flash
```

Danach Firmware neu flashen:

```bash
/opt/homebrew/bin/esphome run c1001-mmwave.yaml --device /dev/cu.usbmodem11401
```

## 9. Setup-Hotspot nach Reset

Nach einem Factory-Reset sollte der Setup-Hotspot erscheinen:

```text
C1001 mmWave Setup
```

Passwort:

```text
c1001setup
```

Der Provisioning-Endpunkt laeuft danach auf dem Sensor:

```text
http://<sensor-ip>/api/provision
```

## 10. MQTT-Topics pruefen

Die echte Device-ID wird aus der MAC-Adresse gebildet. Beispiel:

```text
c1001-b16c33e0
```

Nicht auf die Beispiel-ID `c1001-a1b2c3d4` hoeren, sondern auf die echte ID:

```bash
mosquitto_sub -h 192.168.178.143 -p 1883 -u sentero -P 'DEIN_PASSWORT' -t 'sentero/#' -v
```

Typische Topics:

```text
sentero/<device_id>/availability
sentero/<device_id>/state
sentero/<device_id>/status
sentero/<device_id>/command
```

Factory-Reset per MQTT:

```bash
mosquitto_pub -h 192.168.178.143 -p 1883 -u sentero -P 'DEIN_PASSWORT' \
  -t 'sentero/<device_id>/command' \
  -m '{"command":"factory_reset","reason":"manual_test"}'
```
