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

### WLAN

Wenn kein LAN vorhanden ist:

- Box startet spaeter einen Setup-Hotspot `Sentero-Setup`.
- Benutzer verbindet sich mit diesem WLAN.
- Benutzer oeffnet `http://192.168.4.1`.
- Mini-Setup fragt nur WLAN-Name und WLAN-Passwort ab.
- Box verbindet sich mit dem Heim-WLAN.
- Setup-Hotspot wird deaktiviert.
- Benutzer oeffnet danach `http://sentero.local`.

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
- WLAN-Daten speichern
- keine OS-Aenderungen im Development
- Adapter-Grenze fuer spaetere OS-Integration
- vorbereiteter Mini-Setup-Screen
- Einstellungen -> Netzwerk zeigt Box-Verbindung getrennt von Sensor-WLAN

Noch offen:

- NetworkManager-Adapter mit `nmcli` oder DBus
- Setup-Hotspot per NetworkManager oder hostapd/dnsmasq
- Hotspot bei erfolgreicher WLAN-Verbindung deaktivieren
- Avahi/mDNS fuer `http://sentero.local`
- Produktions-Installationsskripte fuer Debian/Raspberry Pi OS
