# Sentero Architektur

Sentero ist lokal-first aufgebaut. Sensorerfassung, lokale Zustandsbildung und Verhaltensanalyse laufen auf der Sentero-Box weiter, auch wenn kein Internet verfuegbar ist.

## Dienste

- `backend/services/service.py`: Sentero-Fachlogik und Verhaltensbewertung.
- `backend/sensors/` und `backend/sensor_sources/`: Sensorquellen und normalisierte Sensordaten.
- `backend/services/sensor_manager.py`: Produktnahe Sensor-Onboarding-Fassade fuer Suche, Pairing, Zuordnung und Transport-Abstraktion.
- `backend/services/notification_service.py`: Benachrichtigungen und persistente Offline-Queue.
- `backend/services/network/`: Querschnittsdienst fuer Box-Netzwerk, Setup-WLAN, Connectivity und LTE-Fallback.

Der Sentero-Agent besitzt keine Netzwerklogik. Er konsumiert nur fachliche Sensor- und Benachrichtigungsdienste.

## Sensor-Onboarding

Sentero V1 richtet Präsenzsensoren und Türsensoren im normalen Wizard ausschliesslich ueber den einheitlichen Sensor-Suchen-Flow ein. Die normale UI zeigt keine Funktechnik, keine MQTT-/Zigbee-Begriffe und keine ESP32-/WLAN-Provisioning-Schritte.

Das Onboarding laeuft ueber:

```text
Sentero Wizard
      ↓
SensorManager / Sensor-Onboarding
      ↓
Direkte Sentero-Sensorquellen
      ↓
Mosquitto / Zigbee2MQTT / ESP32-MQTT / EcoTracker
      ↓
Sensor
```

Sentero nutzt eine einheitliche MQTT-Sensorpipeline. Zigbee-Geraete gelangen ueber Zigbee2MQTT zum Broker; ESP32- und andere MQTT-Geraete verwenden denselben Broker. Home Assistant sowie der fruehere `wifi_esphome`-Sonderweg sind entfernt.

Sentero-Fachlogik darf nicht direkt von Transportdetails abhaengen. Persistierte Sensorzuordnungen speichern technische Herkunft nur als Metadaten; Auswertung und Dashboard konsumieren normalisierte Zustaende wie `presence = true`, `door.open = false` oder `power_usage = 340`.

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

## E-Mail-Assistent

Der Sentero Mail Assistant liegt fachlich unter `backend/agents/sentero/mail/`. Er ermoeglicht Statusabfragen durch berechtigte Vertrauenspersonen, ohne dass Sentero von aussen erreichbar sein muss.

Kommunikation erfolgt ausschliesslich ueber ausgehende Verbindungen:

```text
Mail Provider
      ↑ ↓
IMAP ueber TLS / SMTP
      ↑ ↓
Sentero Mail Assistant
      ↓
Intent Parser
      ↓
Sentero Query Services
      ↓
Response Builder
      ↓
SMTP
```

Der Dienst startet, sobald der E-Mail-Kanal im Sentero-Wizard eingerichtet und erfolgreich getestet wurde. Versand- und Antwortdaten werden ueber die normale E-Mail-Konfiguration gepflegt. Das konfigurierte Kundenpostfach, z.B. `test@kunde.de`, ist Versandadresse und IMAP-Inbox zugleich. Angehoerige antworten auf Sentero-Mails oder schreiben direkt an diese normale Adresse; sie bekommen keine technische Sonderadresse. Secrets werden in API-Antworten und Logs maskiert.

Der Assistant ist strikt read-only. E-Mails duerfen keine Sensor-/Kontakt-/Benutzer-/Konfigurationsaenderungen, Shell-Aktionen oder Sicherheitsfunktionen ausloesen. Der QueryService liest nur bestehende Sentero-Daten wie Behavior Assessments, Sensorereignisse und Sensorstatus.

Autorisierung erfolgt pro Vertrauensperson ueber:

- aktive Kontakt-E-Mail-Adresse
- `email_queries_enabled`
- kontaktbezogene Leseberechtigungen: `STATUS`, `ACTIVITY`, `ROOM`, `ENVIRONMENT`, `NIGHT`, `HISTORY`, `TECHNICAL_HEALTH`

Unbekannte, deaktivierte oder nicht freigeschaltete Absender werden still ignoriert. Das vermeidet Backscatter, Mail-Loops und Antworten an Spam-Absender. Aktivierte Konversationen brauchen den konfigurierten Betreff-Marker, standardmaessig `Sentero:`, oder einen gueltigen Reply-Kontext auf eine von Sentero erzeugte Nachricht. `sentero_mail_queries` persistiert Message-ID, Kontakt, Intent, Hash der Frage, Antwortstatus und Fehlercodes; vollstaendige Mailinhalte werden nicht dauerhaft gespeichert.

LLMs duerfen fuer diese Funktion nur erlaubte Intents klassifizieren oder strukturierte Fakten sprachlich formulieren. Sie duerfen keine SQL-Abfragen erzeugen, Daten frei durchsuchen, Entity-IDs auswaehlen, Fakten erfinden oder Aktionen ausloesen. Bei Unsicherheit wird `UNKNOWN` verwendet.
