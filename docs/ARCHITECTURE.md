# Sentero Architektur

Sentero ist lokal-first aufgebaut. Sensorerfassung, lokale Zustandsbildung und Verhaltensanalyse laufen auf der Sentero-Box weiter, auch wenn kein Internet verfuegbar ist.

## Dienste

- `backend/services/service.py`: Sentero-Fachlogik und Verhaltensbewertung.
- `backend/sensors/` und `backend/sensor_sources/`: Sensorquellen und normalisierte Sensordaten.
- `backend/services/notification_service.py`: Benachrichtigungen und persistente Offline-Queue.
- `backend/services/network/`: Querschnittsdienst fuer Box-Netzwerk, Setup-WLAN, Connectivity und LTE-Fallback.

Der Sentero-Agent besitzt keine Netzwerklogik. Er konsumiert nur fachliche Sensor- und Benachrichtigungsdienste.

## Netzwerk

`NetworkService` ist eine Infrastrukturkomponente. Er kapselt:

- Ethernet/WLAN/LTE-Status
- WLAN-Scan und Verbindungstest
- temporaeren Setup-Access-Point
- Connectivity-Pruefung ueber Link, Default Route, DNS, Internet und Sentero-Mailserver
- Failover mit Hysterese und Cooldown
- Netzwerkereignisse ohne Credential-Daten

Produktive OS-Integration erfolgt ueber NetworkManager und ModemManager. Sentero schreibt keine parallelen `wpa_supplicant`-, Modem- oder Router-Konfigurationen.

Prioritaet im Normalbetrieb:

1. Ethernet
2. WLAN
3. LTE/Mobilfunk

LTE ist nur ein Uplink fuer die Sentero-Box. Sentero wird dadurch nicht dauerhaft zum Router fuer andere Geraete.

## Setup-AP

Beim Erststart oder bei expliziter Recovery startet Sentero ein geschuetztes Setup-WLAN mit geraetespezifischer SSID, z.B. `Sentero-Setup-7F3A`. Das Passwort ist pro Geraet zufaellig und wird nicht ueber APIs ausgegeben.

Der Setup-AP ist temporaer. Er bleibt aktiv, solange eine neue Verbindung nicht erfolgreich getestet wurde, und wird nach erfolgreicher Einrichtung abgeschaltet.

## Offline-Betrieb

Ohne Internet bleiben lokale Sensorik und Verhaltensanalyse aktiv. Ausgehende Benachrichtigungen werden in `notification_outbox` gepuffert und nach Wiederherstellung der Verbindung mit dem Originalzeitpunkt versendet.

Keine eingehende Internetverbindung ist fuer Sentero erforderlich.

