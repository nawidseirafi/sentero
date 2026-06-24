# Sentero Production Readiness

Diese Liste beschreibt, was noch fehlt, bevor Sentero produktiv bei echten Nutzern betrieben werden sollte.

Zielbild fuer Produktion:

- Sentero laeuft als Docker-Stack.
- Sensorik laeuft ohne Home Assistant ueber Mosquitto, Zigbee2MQTT und MQTT.
- Updates kommen ueber `UPDATE_BASE_URL=https://seirafi.de/robotersteve/sentero`.
- Runtime-Daten bleiben in `data/` und werden durch Updates nicht ueberschrieben.
- Vertrauenspersonen erhalten relevante Warnungen zu Verhalten, Sensor-Batterie und Sensor-Erreichbarkeit.

## Status

Bereits erledigt:

- Backend-Services werden nicht mehr global beim Import instanziiert.
- OpenAPI kann erzeugt werden, ohne Backend-Services/DB-Zugriffe zu starten.
- OpenAPI markiert geschuetzte Endpoints mit Bearer Auth.
- Erste automatisierte Backend-Tests existieren.
- Docker ist auf MQTT als Produktionsquelle ausgerichtet.
- Direkter MQTT-Service fuer Mosquitto Publish/Snapshot ist vorhanden.
- Zigbee2MQTT-Snapshots werden aus MQTT-Nachrichten erzeugt.
- Zigbee Permit-Join laeuft im MQTT-Modus direkt ueber Mosquitto.
- Update-Manifeste werden aus `UPDATE_BASE_URL` generiert.
- Release-Manifeste enthalten keine lokalen `/Users/...` Download-Pfade mehr.

## Muss Vor Produktivbetrieb

### 1. Echter Docker-End-to-End-Test

Offen:

- Docker-Stack mit Sentero, Mosquitto und Zigbee2MQTT starten.
- Einen echten Zigbee-Sensor anlernen.
- Pruefen, ob der Sensor im Sentero-Wizard sichtbar wird.
- Sensor bestaetigen und Dashboard-/Statusdaten pruefen.
- Container neu starten und pruefen, ob Mapping und Status erhalten bleiben.

Abnahmekriterium:

- Ein realer Sensor kann ohne Home Assistant registriert, gespeichert, gelesen und nach Neustart weiter verwendet werden.

### 2. Persistente MQTT-Ereignisverarbeitung

Offen:

- Aktuell liest der Zigbee2MQTT-Adapter Snapshots aus retained MQTT-Nachrichten.
- Fuer produktive Zuverlaessigkeit sollte ein persistenter MQTT-Subscriber Events laufend aufnehmen und in SQLite speichern.
- Batterie, Erreichbarkeit, letzte Aktivitaet und Sensorstatus sollten aus diesem lokalen Event-State kommen.

Abnahmekriterium:

- Sentero erkennt Sensoraktivitaet auch dann korrekt, wenn der HTTP/API-Aufruf nicht genau im Moment der MQTT-Nachricht stattfindet.

### 3. Mosquitto Sicherheit

Offen:

- Mosquitto laeuft aktuell entwicklungsnah.
- Benutzer/Passwort aktivieren.
- Keine anonymen Verbindungen in Produktion.
- Optional TLS vorbereiten, falls MQTT ueber Netzwerkgrenzen hinaus erreichbar ist.
- Docker-Secrets oder mindestens `.env`-basierte Zugangsdaten verwenden.

Abnahmekriterium:

- Sentero und Zigbee2MQTT verbinden sich mit Credentials; anonyme MQTT-Clients werden abgelehnt.

### 4. Update-Flow End-to-End

Offen:

- Release-ZIP auf `https://seirafi.de/robotersteve/sentero/stable/releases/` hochladen.
- `latest.json` auf `https://seirafi.de/robotersteve/sentero/stable/latest.json` hochladen.
- Update-Check im laufenden Docker-System testen.
- Update-Install im ZIP-Modus testen.
- Backup-Verhalten pruefen.
- Rollback nach absichtlich fehlerhaftem Update pruefen.

Abnahmekriterium:

- Eine laufende Installation kann von Version A auf Version B aktualisieren, ohne `.env`, `data/` oder `backups/` zu verlieren.

### 5. Signierte Updates

Offen:

- Manifeste und/oder ZIP-Artefakte signieren.
- Signatur vor Installation pruefen.
- Installation abbrechen, wenn Signatur fehlt oder ungueltig ist.

Abnahmekriterium:

- Sentero installiert kein Update, das nicht vom erwarteten Schluessel signiert wurde.

### 6. Auth und Session-Haertung

Offen:

- Cookie-Flags fuer Produktion pruefen: `Secure`, `HttpOnly`, `SameSite`.
- Token-/Session-Lifetime festlegen.
- Logout und abgelaufene Sessions testen.
- Admin-Rechte fuer Update-Install und Systemaktionen testen.
- Passwort-Reset produktionsfaehig machen oder bewusst deaktivieren, wenn kein Mailversand konfiguriert ist.

Abnahmekriterium:

- Geschuetzte Endpoints sind ohne gueltige Session nicht erreichbar; kritische Aktionen sind auf Owner/Admin begrenzt.

### 7. Systemwarnungen Automatisch Ausfuehren

Offen:

- Batterie- und Erreichbarkeitswarnungen existieren als Service-Funktion.
- Es fehlt ein Scheduler oder Worker, der diese Pruefung regelmaessig ausfuehrt.
- Intervall festlegen, z.B. alle 15 oder 30 Minuten.
- Deduplizierung und Recovery-Meldungen im echten Betrieb testen.

Abnahmekriterium:

- Vertrauenspersonen erhalten automatisch Warnungen bei Batterie unter 30 Prozent und bei nicht erreichbaren Sensoren.

### 8. Benachrichtigungskanaele Produktiv Testen

Offen:

- E-Mail mit echtem SMTP testen.
- Telegram oder WhatsApp, falls vorgesehen, mit echten Tokens testen.
- Fehlerfaelle testen: falsche Credentials, Rate Limit, nicht erreichbarer Provider.
- Sensible Daten in Logs vermeiden.

Abnahmekriterium:

- Mindestens ein produktiver Kanal sendet zuverlaessig Warnungen an Vertrauenspersonen.

### 9. Datenbank-Migrationen

Offen:

- Aktuell wird Schema-Migration ueber `ensure_schema()` und `alter table` geloest.
- Fuer Produktion sollte ein versionierter Migrationsmechanismus eingefuehrt werden.
- Migrationen muessen idempotent und update-sicher sein.

Abnahmekriterium:

- Eine bestehende produktive DB kann ueber mehrere Versionen aktualisiert werden, ohne Datenverlust und ohne manuelle SQL-Eingriffe.

### 10. Testabdeckung Erweitern

Offen:

- API-Tests fuer Auth, Setup, Sensor-Wizard, Notifications und Updates.
- Integrationstest fuer MQTT-Sensorquelle.
- Test fuer Docker-Default: `.env` mit Home Assistant darf den Container nicht versehentlich auf HA umstellen.
- Fehlerfalltests fuer Update-Install, kaputtes ZIP, fehlende Manifestfelder und Rollback.

Abnahmekriterium:

- Ein automatischer Testlauf deckt die wichtigsten Nutzerfluesse und kritischen Fehlerfaelle ab.

## Soll Vor Produktivbetrieb

### 11. Observability und Diagnose

Offen:

- Strukturierte Logs fuer Update, MQTT, Zigbee2MQTT, Notifications und Auth.
- Diagnoseseite oder interner Health-Endpunkt fuer DB, MQTT, Zigbee2MQTT und Update-Manifest.
- Keine Tokens, Passwoerter oder personenbezogenen Daten in Logs.

Abnahmekriterium:

- Ein Fehler im Sensor-/Update-/Notification-System ist ohne Code-Debugging nachvollziehbar.

### 12. Backup und Restore Dokumentieren

Offen:

- Backup-Umfang festlegen: `data/`, `.env`, ggf. Zigbee2MQTT-Daten.
- Restore-Anleitung schreiben und testen.
- Pruefen, ob Update-Backups ausreichen oder zusaetzliche Nutzer-Backups notwendig sind.

Abnahmekriterium:

- Eine Installation kann auf einem neuen Host aus Backup wiederhergestellt werden.

### 13. Frontend Smoke-Test

Offen:

- Login.
- Setup-Wizard.
- Zigbee-Sensorregistrierung.
- Dashboard.
- Vertrauenspersonen.
- Update-Seite.
- Mobile, Tablet und Desktop pruefen.

Abnahmekriterium:

- Die Kernfluesse funktionieren auf iPad/Tablet und Smartphone ohne Layout-Brueche.

### 14. Release-Prozess Festziehen

Offen:

- Versionierung festlegen.
- Release-Checkliste einfuehren.
- Build-Befehl dokumentieren.
- Upload-Ziel dokumentieren.
- Nach jedem Build Manifestwerte pruefen.

Abnahmekriterium:

- Ein Release kann reproduzierbar gebaut, veroeffentlicht und installiert werden.

## Kann Nachgelagert Werden

### 15. Matter/ZHA Pfade Aufraeumen

Offen:

- Wenn Produktion wirklich nur MQTT/Zigbee2MQTT nutzt, koennen Matter/ZHA/HA-Pfade klar als Development/Optional markiert werden.
- UI sollte in Produktion keine HA-spezifischen Begriffe zeigen.

### 16. LLM/Verhaltensanalyse Produktivstrategie

Offen:

- Klaeren, ob die Verhaltensanalyse rein regelbasiert bleibt oder einen externen KI-Provider nutzt.
- Datenschutz und Kosten klaeren, falls externe KI genutzt wird.

### 17. Dokumentation Fuer Installation

Offen:

- Einfache Installationsanleitung fuer produktive Docker-Installation.
- Beispiel `.env` fuer MQTT/Zigbee2MQTT.
- Anleitung fuer Zigbee2MQTT Coordinator/USB-Passthrough.

## Empfohlene Naechste Reihenfolge

1. Docker-Stack mit echtem Mosquitto/Zigbee2MQTT starten.
2. Einen echten Sensor ueber Sentero registrieren.
3. Persistenten MQTT-Event-State implementieren.
4. Systemwarnungs-Scheduler einbauen.
5. Update-Flow mit echter Veroeffentlichung end-to-end testen.
6. Mosquitto Auth aktivieren.
7. API-/Integrationstests erweitern.
8. Signierte Updates implementieren.

## Produktiv-Freigabe

Sentero ist produktionsbereit, wenn alle Punkte unter "Muss Vor Produktivbetrieb" erfuellt und in einem echten Docker/MQTT/Zigbee2MQTT-Setup getestet sind.
