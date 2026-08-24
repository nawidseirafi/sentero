# Sentero Architektur-Guidelines

## Netzwerkgrenzen

Netzwerkmanagement ist Querschnittsinfrastruktur und keine Fachlogik des Sentero-Agenten.

Erlaubt:

- `NetworkService` fuer Status, Setup-AP, WLAN, LTE, Connectivity und Failover verwenden.
- NetworkManager und ModemManager als Systemintegrationspunkte nutzen.
- Kundensprache in der normalen UI verwenden.
- technische Diagnose nur in Admin-/Support-Ansichten zeigen.

Nicht erlaubt:

- Netzwerklogik in `backend/behavior_agent.py` oder Sentero-Fachservices einbauen.
- zweite WLAN-, AP- oder LTE-Implementierung neben `backend/services/network/` anlegen.
- WLAN-Passwoerter, SIM-PINs, Tokens oder AP-Passwoerter in SQLite-Statusdaten, Logs oder APIs ausgeben.
- Setup-AP dauerhaft offen halten.
- Setup-AP-Zugriff auf interne Dienste routen.

## Offline-Faehigkeit

Neue Features duerfen lokale Sensorik und lokale Verhaltensanalyse nicht von Internet abhaengig machen. Externe Dienste muessen optional bleiben oder sauber degradieren.

Ausgehende Meldungen muessen bei Offline-Zeit persistent gepuffert werden, wenn sie nicht sofort zugestellt werden koennen.

## Mail Assistant

Der Sentero Mail Assistant ist ein Sentero-Fachservice und kein globaler Messaging-Querschnitt. Inbound IMAP darf als eigene Komponente implementiert werden; SMTP-Versand muss vorhandene Notification-/Mail-Abstraktionen wiederverwenden, solange diese geeignet sind.

Erlaubt:

- Postfach per IMAP ueber TLS pollen.
- Antworten per bestehendem E-Mail-Notification-Provider versenden.
- Dasselbe konfigurierte Kundenpostfach fuer Versand und Rueckfragen verwenden.
- Message-ID, Intent, Hash der Frage und Antwortstatus auditieren.
- Kontaktberechtigungen vor jeder Datenabfrage pruefen.
- Bei LLM-Ausfall deterministische Keyword-Intents nutzen.

Nicht erlaubt:

- eingehende Ports, Webhooks oder oeffentliche Callback-Endpunkte fuer Statusabfragen oeffnen.
- zweite parallele SMTP-Infrastruktur bauen.
- Mailpasswoerter, Tokens oder vollstaendige private Mailinhalte loggen.
- Home Assistant, Tueren, Geraete, Alarmregeln, Kontakte, Benutzer, Konfiguration, Sensoren oder Shell/System per E-Mail veraendern.
- LLMs direkt SQL, Home Assistant oder Entity-Auswahl ueberlassen.
- alte Sensorwerte als aktuellen Zustand ausgeben.

LLM-Regel:

```text
E-Mail -> Intent-Klassifikation -> erlaubter Intent -> deterministischer QueryService
       -> strukturierte Fakten -> optionale Formulierung -> Antwort
```

Wenn ein Intent unsicher oder handlungsbezogen ist, wird `UNKNOWN` verwendet bzw. eine Read-only-Ablehnung formuliert. Prompt-Injection im Mailinhalt darf niemals Berechtigungen oder Read-only-Grenzen umgehen.

## UI

Normale Kundensicht:

- `Internet: Verbunden`
- `Über WLAN`
- `Über Mobilfunk, Signal: Gut`
- `Nicht verbunden. Sentero überwacht weiterhin lokal.`

Nicht in der normalen Kundensicht anzeigen:

- Interface-Namen
- IP-Adressen
- Default Routes
- ModemManager-/NetworkManager-Begriffe
- Zigbee-, Permit-Join-, MQTT-, ESPHome- oder Captive-Portal-Begriffe im Sensor-Onboarding
- QR-/Hotspot-Anleitungen fuer Praesenzsensoren im V1-Sensorwizard

Admin-/Support-Diagnose darf diese Details anzeigen.

## Sensoren

Sensor-Onboarding muss ueber `SensorManager` bzw. die bestehende Geraeteabstraktion laufen. Neue Flows duerfen keine zweite Zigbee-, MQTT-, ESP32- oder Home-Assistant-Infrastruktur neben den vorhandenen Services aufbauen.

Erlaubt:

- Transport als persistente Metadaten speichern (`zigbee`, `wifi_esphome`).
- Zigbee-Pairing fuer aktive Onboarding-/Admin-Flows temporaer oeffnen.
- Mehrere Entities eines physischen Geraets zu einer Sentero-Zuordnung zusammenfassen.
- Den persistenten MQTT-Cache/Listener als Quelle fuer zuletzt bekannte Zustaende verwenden.
- ESP32/WLAN-Code kompatibel halten und per Feature-Flag fuer spaetere Varianten vorbereiten.

Nicht erlaubt:

- Sentero-Fachlogik auf Transportnamen, MQTT-Topics oder Zigbee-spezifische Entity-Namen stuetzen.
- Pairing dauerhaft offen lassen.
- Normale Nutzer zwischen Funktechniken waehlen lassen, solange V1 nur Zigbee anbietet.
- Praesenzsensoren im V1-Wizard ueber QR-Code, Setup-Hotspot oder Captive Portal einrichten.
- Beliebige im Broker sichtbare MQTT-Geraete fuer Benachrichtigungen verwenden, bevor sie in Sentero registriert wurden.
