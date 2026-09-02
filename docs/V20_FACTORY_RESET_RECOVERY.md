# V20 – echter Factory Reset mit LAN-first Recovery

## Ziel

Der bisherige UI-Bereich „Werkseinstellungen“ zeigte nur einen Browser-Dialog. V20 implementiert einen echten, hostseitigen Factory Reset, ohne Systemsoftware oder installierte Sentero-Version zurückzusetzen.

## Sicherheits-/Architekturprinzip

Der Sentero-Container löscht keine Host-Dateien und verändert NetworkManager nicht direkt. Die Web-API authentifiziert Owner/Admin und sendet nur eine bestätigte Reset-Anforderung über den bestehenden privilegierten Updater-Unix-Socket. Der Updater plant anschließend einen separaten root-owned systemd-Job. Dadurch kann die HTTP-Antwort noch sauber an den Browser zurückgegeben werden, bevor die Box Dienste stoppt und neu startet.

## Zurückgesetzt

- Sentero Kundendatenbank inklusive Benutzer/Sessions
- Profile, Räume, Sensor-Zuordnungen, Automationen und Historie in der Sentero-Datenbank
- E-Mail-/Telegram-/Benachrichtigungs-Konfiguration in der Sentero-Datenbank
- Zigbee2MQTT Laufzeitdaten und Zigbee-Netzwerk; statische Vorlage bleibt erhalten und `configuration.yaml` wird frisch erzeugt
- Mosquitto Laufzeitdaten und Logs
- gespeicherte NetworkManager-WLAN-Profile einschließlich eines alten Setup-AP-Profils
- lokale Sentero-Backups, da sie Kundendaten enthalten können

## Bewusst erhalten

- Debian/Systemsoftware
- aktuelle Sentero-Version und Docker-Image-Konfiguration
- `.env` mit internen Box-/MQTT-Zugangsdaten
- Ethernet-Konfiguration
- `/etc/machine-id` / Box-Identität
- physischer Setup-QR-Aufkleber und daraus abgeleitete SSID
- `config/sentero.yaml`
- Ollama-Modelle
- Host-Updater und Host-Netzwerk-Agent

## Recovery-State-Machine

Nach erfolgreichem Reset rebootet die Box. Danach entscheidet `start-box.sh` ausschließlich nach lokaler IPv4-Erreichbarkeit:

1. Ethernet oder gespeichertes WLAN mit lokaler IPv4 vorhanden: Setup-AP bleibt aus und der komplette Stack startet.
2. Keine lokale IPv4 vorhanden: nur Sentero startet zunächst, danach fordert `start-box.sh` beim Host-Netzwerk-Agent explizit `start_setup_ap` an. Der offene `Sentero-Setup-XXXX` Hotspot und das Captive Portal werden aktiv.

Da der Factory Reset alle gespeicherten WLAN-Profile entfernt, bedeutet Fall 2 nach einem Reset praktisch „kein LAN vorhanden“. Ist LAN angeschlossen, bleibt die WLAN-Provisionierung bewusst aus.

Zusätzlich startet `first-install.sh` den Setup-AP jetzt explizit im No-Network-Zweig. Damit sind Fresh-Install und Reboot nach Factory Reset konsistent.

## Fehlerbehandlung

Vor dem Löschen werden die betroffenen Laufzeitverzeichnisse in ein temporäres Verzeichnis unter `/var/tmp` verschoben. NetworkManager-Keyfiles werden temporär gesichert. Schlägt die Reset-Vorbereitung fehl, werden die Verzeichnisse und NetworkManager-Konfiguration zurückgerollt und `sentero-box.service` best-effort neu gestartet. Erst wenn alle Schritte erfolgreich vorbereitet sind, wird der temporäre Altbestand entfernt und der Reboot ausgelöst.

Der dauerhafte technische Reset-Status liegt unter `/var/lib/sentero/factory-reset-state.json` und enthält keine Kundendaten.

## Neue API

- `POST /api/sentero/system/factory-reset` mit `{ "confirm": "ZURÜCKSETZEN" }`
- `GET /api/sentero/system/factory-reset/status`

Beide Endpunkte erfordern einen angemeldeten Owner oder Administrator.

## Validierung

- Python `py_compile`: erfolgreich
- Bash `bash -n`: erfolgreich
- TypeScript `tsc --noEmit`: erfolgreich
- Factory-Reset Erfolgstest mit simuliertem Docker/NetworkManager: erfolgreich
- Factory-Reset Rollback-Test bei absichtlich fehlschlagendem WLAN-Löschen: erfolgreich
- Updater-Dispatch/Systemd-Planungstest: erfolgreich
- `start-box.sh` Test „Ethernet vorhanden“: voller Stack, kein Setup-AP
- `start-box.sh` Test „kein lokales Netz“: nur Sentero + Setup-AP-Anforderung
