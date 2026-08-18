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

Admin-/Support-Diagnose darf diese Details anzeigen.

