# Sentero Production Readiness

Diese Liste beschreibt, was noch fehlt, bevor Sentero produktiv bei echten Nutzern betrieben werden sollte.

Zielbild fuer Produktion:

- Sentero laeuft als Docker-Stack.
- Sensorik laeuft ueber Mosquitto, Zigbee2MQTT und MQTT.
- Updates kommen ueber `UPDATE_BASE_URL=https://sentero.de/sentero`.
- Runtime-Daten bleiben in `data/` und werden durch Updates nicht ueberschrieben.
- Vertrauenspersonen erhalten relevante Warnungen zu Verhalten, Sensor-Batterie und Sensor-Erreichbarkeit.
- Die Box kann ohne vorhandenen Router ueber ein temporaeres geschuetztes Setup-WLAN und optional LTE eingerichtet werden.

Tech-Debt: `notification_logs` wird derzeit aus zwei unabhaengigen Schema-Definitionen erzeugt (`device_mapping_service.py` und `audit_service.py`). Langfristig sollte die Tabelle aus einer einzigen Quelle stammen, idealerweise ueber eine Notification-Service-eigene Migration.

## Status

Bereits erledigt:

- Backend-Services werden nicht mehr global beim Import instanziiert.
- OpenAPI kann erzeugt werden, ohne Backend-Services/DB-Zugriffe zu starten.
- OpenAPI markiert geschuetzte Endpoints mit Bearer Auth.
- Erste automatisierte Backend-Tests existieren.
- Docker ist auf MQTT als Produktionsquelle ausgerichtet.
- Direkter MQTT-Service fuer Mosquitto Publish/Snapshot ist vorhanden.
- Zigbee2MQTT-Snapshots werden aus MQTT-Nachrichten erzeugt.
- Persistenter MQTT-Listener und SQLite-Cache fuer zuletzt bekannte MQTT-Zustaende sind vorhanden.
- Zigbee Permit-Join laeuft im MQTT-Modus direkt ueber Mosquitto.
- V1-Sensorwizard verwendet fuer Praesenz- und Tuersensoren einen einheitlichen Such-Flow mit Zigbee als Standardtransport.
- ESP32-Sensoren werden ueber denselben MQTT-Broker wie andere MQTT-Geraete angebunden; es gibt keinen separaten ESPHome-Provisioningpfad.
- EcoTracker kann lokal als Strom-/Leistungsquelle angebunden werden.
- Update-Manifeste werden aus `UPDATE_BASE_URL` generiert.
- Release-Manifeste enthalten keine lokalen `/Users/...` Download-Pfade mehr.
- NetworkService als Querschnittsdienst fuer Setup-WLAN, WLAN, Ethernet, LTE-Fallback und Connectivity ist vorhanden.
- Benachrichtigungen koennen bei Offline-Zeit persistent gepuffert und nach Wiederherstellung versendet werden.
- SQLite-Connections werden zentral ueber Context Manager geschlossen; Tests laufen ohne `unclosed database` ResourceWarnings.

## Muss Vor Produktivbetrieb

### 1. Echter Docker-End-to-End-Test

Offen:

- Docker-Stack mit Sentero, Mosquitto und Zigbee2MQTT starten.
- Einen echten Zigbee-Sensor anlernen.
- Pruefen, ob Praesenz- und Tuersensoren im Sentero-Wizard ueber denselben Sensor-Suchen-Flow sichtbar werden.
- Sensor bestaetigen und Dashboard-/Statusdaten pruefen.
- Container neu starten und pruefen, ob Mapping und Status erhalten bleiben.

Abnahmekriterium:

- Ein realer Sensor kann registriert, gespeichert, gelesen und nach Neustart weiter verwendet werden.

### 2. MQTT-Ereignisverarbeitung im echten Betrieb pruefen

Erledigt:

- Der MQTT-Service haelt einen persistenten Listener und spiegelt zuletzt bekannte Topic-Zustaende in SQLite.
- Zigbee2MQTT-Snapshots nutzen den Cache und bootstrappen bei leerem Cache aus retained Messages.
- Registrierte Rollen koennen ihren Zustand aus Snapshot oder Discovery-/MQTT-Cache aufloesen.

Offen:

- Mit realem Mosquitto/Zigbee2MQTT pruefen, ob nicht-retained Bewegungsereignisse dauerhaft genug fuer Dashboard, Mail und Verhaltenserkennung ankommen.
- Neustart- und Broker-Ausfall-Szenarien mit realen Sensoren testen.

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

- Appliance-Bundle `sentero-box-<version>.zip` auf `https://sentero.de/sentero/stable/releases/` hochladen.
- `latest.json` auf `https://sentero.de/sentero/stable/latest.json` hochladen.
- Update-Check im laufenden Docker-System testen.
- Update-Install im Appliance-Modus mit Host-Updater testen.
- Backup-Verhalten pruefen.
- Rollback nach absichtlich fehlerhaftem Update pruefen.

Abnahmekriterium:

- Eine laufende Installation kann von Version A auf Version B aktualisieren, ohne `.env`, `data/` oder `backups/` zu verlieren.

### 5. Update-Integritaet: Hashpruefung und Signaturen

Erledigt:

- Der ZIP-Installer entpackt Archive sicher gegen Pfadmanipulationen.
- Der Deployment-Build erzeugt fuer jedes Release-ZIP `sha256` und `size_bytes`.
- `latest.json` und `deployment-manifest.json` enthalten die Checksumme.
- Der Installer hasht das heruntergeladene ZIP vor dem Entpacken.
- Die Installation bricht ab, wenn `sha256` fehlt, ungueltig ist oder nicht zum ZIP passt.
- Die Installation bricht ab, wenn `size_bytes` gesetzt ist und nicht zur heruntergeladenen Datei passt.

Noch optional fuer unbeaufsichtigte Updates:

- Manifeste und/oder ZIP-Artefakte kryptografisch signieren.
- Signatur vor Installation pruefen, sobald Updates ohne Nutzerfreigabe installiert werden sollen.

Abnahmekriterium:

- Sentero installiert kein Update, dessen SHA-256 nicht zum Manifest passt.
- Fuer unbeaufsichtigte Produktion wird zusaetzlich eine Signaturpruefung eingefuehrt.

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
- Test fuer Docker-Default: Sentero muss im Container ausschliesslich die konfigurierte MQTT-Pipeline verwenden und darf keine alternative Sensorquelle aktivieren.
- Fehlerfalltests fuer Update-Install, kaputtes ZIP, fehlende Manifestfelder und Rollback.
- Testlauf mit `-W default::ResourceWarning` beibehalten, damit offene SQLite-Verbindungen auffallen.

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
- Build-Befehl dokumentieren: `python3 deployment_build.py --version <version> --base-url <url>`.
- Upload-Ziel dokumentieren.
- Nach jedem Build Manifestwerte pruefen.
- Vollstaendige `box/`-Deployment-Schicht versionieren, falls initiale Appliance-Installationspakete aus diesem Repo gebaut werden sollen.

Abnahmekriterium:

- Ein Release kann reproduzierbar gebaut, veroeffentlicht und installiert werden.

## Kann Nachgelagert Werden

### 15. Sensorquellen-Cleanup

Status: umgesetzt.

- Die aktive Sensorarchitektur verwendet eine einheitliche MQTT-/Zigbee2MQTT-Pipeline.
- Produktion und Entwicklung verwenden dieselbe MQTT-/Zigbee2MQTT-Pipeline.
- Normale UI-Texte zeigen keine transportinternen Spezialbegriffe.


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
3. Persistenten MQTT-Event-State im echten Betrieb mit nicht-retained Events validieren.
4. Systemwarnungs-Scheduler einbauen.
5. Update-Flow mit echter Veroeffentlichung end-to-end testen.
6. Mosquitto Auth aktivieren.
7. API-/Integrationstests erweitern.
8. Signierte Updates implementieren.

## Produktiv-Freigabe

Sentero ist produktionsbereit, wenn alle Punkte unter "Muss Vor Produktivbetrieb" erfuellt und in einem echten Docker/MQTT/Zigbee2MQTT-Setup getestet sind.
