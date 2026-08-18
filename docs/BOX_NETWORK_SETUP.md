# Sentero Box Netzwerk-Setup

Sentero unterscheidet zwei Setups:

1. Box-Netzwerk-Setup
   - verbindet die Sentero-Box per LAN oder WLAN mit dem Heimnetz
   - laeuft vor dem normalen Sentero-Wizard

2. Sentero-Wizard
   - Profil
   - Raeume
   - Sensoren
   - Kontakte
   - Benachrichtigungen

Diese beiden Setups duerfen nicht vermischt werden.

## Zielbild

### LAN

Wenn LAN angeschlossen ist:

- Box bekommt per DHCP eine IP.
- Sentero startet automatisch.
- Benutzer oeffnet `http://sentero.local`.
- Der normale Sentero-Wizard startet.

### WLAN / Mobilfunk ohne vorhandenen Router

Wenn kein LAN vorhanden ist:

- Box startet einen geschuetzten Setup-Hotspot mit geraetespezifischer SSID, z.B. `Sentero-Setup-7F3A`.
- Benutzer verbindet sich mit diesem WLAN.
- Benutzer oeffnet `http://sentero.local` oder `http://192.168.50.1`.
- Wizard fragt in Kundensprache nach Internetverbindung.
- Benutzer waehlt WLAN, Ethernet oder Mobilfunk.
- Box testet Internetzugang, DNS und Sentero-Mailserver.
- Setup-Hotspot wird deaktiviert.
- Wenn die Verbindung fehlschlaegt, bleibt der Setup-Hotspot aktiv.

## Konfiguration

Default fuer Development:

``` dotenv
SENTERO_BOX_SETUP_MODE=disabled
```

Nicht-sensitive Defaults in `config/sentero.yaml`:

``` yaml
box_setup:
  mode: disabled
  hostname: sentero
```

Modi:

- `disabled`: Development, keine Betriebssystemaenderungen.
- `auto`: Produktmodus, Hotspot nur wenn keine Verbindung vorhanden ist.
- `force`: Testmodus, Setup-Hotspot erzwingen.

## API

Status:

``` text
GET /api/setup/box-network/status
```

WLAN speichern/verbinden:

``` text
POST /api/setup/box-network/wifi
```

Request:

``` json
{
  "ssid": "MeinWLAN",
  "password": "secret"
}
```

Passwoerter werden nicht geloggt und nicht in API-Responses ausgegeben.

## Implementierungsstatus

Implementiert:

- sicherer Default `disabled`
- Status-API
- WLAN-Scan und WLAN-Verbindung ueber zentrale NetworkService-Grenze
- Mobilfunkstatus und LTE-Fallback ueber ModemManager/NetworkManager-Grenze
- temporaerer Setup-AP mit geraetespezifischer SSID
- Connectivity-Pruefung ueber Link, Default Route, DNS, Internet und Mailserver
- Offline-Benachrichtigungsqueue
- keine OS-Aenderungen im Development
- Adapter-Grenze fuer NetworkManager/ModemManager
- vorbereiteter Mini-Setup-Screen
- Einstellungen -> Netzwerk zeigt Box-Verbindung getrennt von Sensor-WLAN

Noch offen:

- Produktionsvalidierung auf Zielhardware fuer WLAN-AP-Mode
- Avahi/mDNS fuer `http://sentero.local`
- Firewall-Regeln fuer Setup-AP-Isolation im Installationsskript
