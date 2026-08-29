# v10 – LAN/WLAN/Provisioning State Machine

## Ziel
Die Sentero Box unterscheidet lokale Netzwerkverbindung und Internetzugang strikt.
Die Provisionierung wird automatisch nur aktiv, wenn weder LAN noch ein gespeichertes WLAN eine lokale IPv4-Adresse bereitstellt.

## Priorität
1. LAN mit IPv4 -> lokale Verbindung aktiv, Setup-AP aus.
2. Sonst gespeichertes WLAN mit IPv4 -> lokale Verbindung aktiv, Setup-AP aus.
3. Sonst -> Sentero-Setup-XXXX aktivieren.

Internet ist ein separater Status. Fehlendes Internet darf eine funktionierende LAN-/WLAN-Verbindung nicht als offline behandeln und darf den Setup-AP nicht automatisch starten.

## Laufzeit
Der Network-Maintenance-Loop synchronisiert den Setup-AP auch nach dem Boot:
- lokale Verbindung erscheint -> Setup-AP wird beendet;
- letzte lokale Verbindung verschwindet -> Setup-AP wird gestartet.

Ethernet wird vom Host-Agenten bevorzugt, wenn LAN und WLAN gleichzeitig eine IPv4-Adresse besitzen. Eine vorhandene WLAN-Konfiguration bleibt als Fallback erhalten.

## UI
Die Netzwerkseite zeigt getrennt:
- Verbindung: LAN / WLAN / Mobilfunk / Offline
- Lokales Netzwerk: Verbunden / Nicht verbunden
- Internet: Verbunden / Nicht erreichbar
- Setup-WLAN: Aktiv / Aus

Bei aktivem LAN bleibt die WLAN-Konfiguration in den Einstellungen optional verfügbar. Der Provisionierungsbutton wird bei bestehender lokaler Verbindung nicht angezeigt.

## Fresh-Install Zusatzfix
Die Mosquitto-Passwortdatei wird nach `mosquitto_passwd` auf die UID/GID des `mosquitto`-Benutzers im offiziellen Container gesetzt und mit Modus 0600 abgesichert.
