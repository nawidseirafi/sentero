# V21 Device Identity and Serial Number

## Architektur

Jede offiziell provisionierte Sentero Box besitzt zwei getrennte Identifikatoren:

- `serial_number`: menschenlesbare, vom Hersteller vergebene Produktions- und Support-ID.
- `device_id`: technische, automatisch erzeugte UUID v4 fuer die stabile physische Geräteidentität.

Die Seriennummer wird niemals von der Box geraten, hochgezaehlt oder aus Hardware abgeleitet. Die `device_id` wird niemals manuell eingegeben und nicht aus Seriennummer, MAC-Adresse, `/etc/machine-id`, CPU-ID, Netzwerkadresse oder Setup-SSID abgeleitet.

Die zentrale Host-Implementierung liegt in:

`/opt/sentero/box/scripts/sentero_device_identity.py`

Sie ist die autoritative Quelle fuer Validierung, Erzeugung, Lesen, Legacy-Fallback und Setup-SSID.

## Persistenz

Die persistente Identity liegt auf dem Host unter:

`/opt/sentero/box/device/identity.json`

Schema:

```json
{
  "schema_version": 1,
  "serial_number": "STB-00001234",
  "device_id": "550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-08-30T12:00:00Z"
}
```

`device/` ist persistenter Gerätebestand, kein Kundenzustand, kein Runtime-Verzeichnis und kein Update-Payload. Die Datei enthaelt keine Secrets. Das Verzeichnis wird mit `0755`, die JSON-Datei mit `0644` angelegt. Geschrieben wird atomar ueber temporaere Datei, `flush`, `fsync` und `os.replace()`.

## Seriennummer

Offizielles Format:

```text
^STB-[0-9]{8}$
```

Gueltige Beispiele:

- `STB-00000001`
- `STB-00001234`
- `STB-12345678`

Ungueltige Werte wie `STB-1234`, `STB12345678`, `12345678` oder `STB-ABCDEFGH` werden mit einer verstaendlichen Fehlermeldung abgelehnt.

## UUID

Die `device_id` wird per `uuid.uuid4()` erzeugt. Sie ist kein Passwort, kein Authentifizierungstoken, kein Besitznachweis und kein Autorisierungsschluessel. Fuer eine spaetere Cloud- oder Device-Registry-Anbindung sind separate Credentials, Zertifikate oder Tokens erforderlich.

Die UUID wird genau einmal bei erfolgreicher Provisionierung erzeugt. Factory Reset, Softwareupdate, Host-Update, Kundenwechsel, Netzwerkwechsel, MAC-Aenderung und erneutes Ausfuehren des Installers aendern sie nicht.

## First Install

Neue Box provisionieren:

```bash
sudo ./scripts/first-install.sh --serial STB-00001234
```

Wenn noch keine Identity existiert, validiert der Installer die Seriennummer, erzeugt automatisch eine UUID v4, schreibt `identity.json` atomar und erzeugt das Setup-Label neu.

Wenn `--serial` fehlt und ein interaktives TTY vorhanden ist, fragt der Installer die Seriennummer ab. Wenn `--serial` fehlt und keine Identity existiert und kein TTY vorhanden ist, bricht er mit Hinweis auf `--serial STB-XXXXXXXX` ab.

Wenn bereits eine gueltige Identity existiert:

- ohne `--serial`: vorhandene Identity wird verwendet.
- mit derselben Seriennummer: Installation laeuft weiter, UUID bleibt identisch.
- mit anderer Seriennummer: Installation bricht ab; die Identity wird nicht ueberschrieben.

Beim Start von einem externen Installationspaket bleibt ein vorhandenes `/opt/sentero/box/device/` erhalten, auch wenn `/opt/sentero/box` aktualisiert wird.

## Legacy-Verhalten

Existiert keine `identity.json`, bleibt die Box im Legacy-Modus funktionsfaehig. Der bisherige Setup-SSID-Mechanismus bleibt aktiv:

```text
Sentero-Setup-<sha256(/etc/machine-id oder hostname)[:4].upper()>
```

Legacy-Boxen erhalten keine erfundene Seriennummer und keine Fake-UUID. Der Systemstatus meldet:

```json
{
  "device": {
    "identity_provisioned": false,
    "serial_number": null,
    "device_id": null
  }
}
```

Manuelle Provisionierung einer Legacy-Box:

```bash
sudo /opt/sentero/box/scripts/set-device-identity.py --serial STB-00001234
```

Identity anzeigen:

```bash
sudo /opt/sentero/box/scripts/set-device-identity.py --show
```

Maschinenlesbar:

```bash
sudo /opt/sentero/box/scripts/set-device-identity.py --show --json
```

## Setup-SSID und QR-Label

Bei provisionierter Box wird die Setup-SSID aus den letzten vier Ziffern der Seriennummer abgeleitet:

```text
STB-00001234 -> Sentero-Setup-1234
```

Die vollstaendige Seriennummer und die UUID werden nicht als SSID verwendet. Die letzten vier Ziffern sind nicht global eindeutig; das ist akzeptiert, weil die Setup-SSID nur der lokalen Erkennung waehrend der Provisionierung dient. Die stabile Identitaet ist die `device_id`, fachlich zusammen mit der `serial_number`.

Der QR-Payload fuer `STB-00001234` lautet:

```text
WIFI:T:nopass;S:Sentero-Setup-1234;;
```

Das physische SVG-Label bleibt lokal erzeugt, ohne Cloud-Dienst oder externe QR-API. Es zeigt `Sentero Setup`, die vollstaendige Seriennummer, den QR-Code und die Setup-SSID. Wird eine Legacy-Box spaeter provisioniert, erzeugt `set-device-identity.py` das Label automatisch neu.

## Factory Reset

Factory Reset loescht weiterhin Kundenzustand gemaess V20:

- `data/sentero`
- `zigbee2mqtt/data` mit anschliessender Wiederherstellung der statischen Vorlage
- `mosquitto/data`
- `mosquitto/log`
- `backups`
- gespeicherte NetworkManager-WLAN-Profile

Erhalten bleiben:

- `/opt/sentero/box/device/`
- `/opt/sentero/box/device/identity.json`
- Seriennummer
- `device_id`
- `created_at`
- aktuelle Softwareversion
- Host-Updater
- Host-Network-Agent
- interne MQTT-Zugangsdaten
- Ethernet-Konfiguration
- Ollama-Modelle

Nach dem Reboot entscheidet weiterhin die bestehende LAN-first-State-Machine:

1. LAN Carrier plus lokale IPv4: LAN verwenden, kein Setup-AP.
2. Kein LAN, aber gespeichertes nutzbares WLAN plus lokale IPv4: WLAN verwenden, kein Setup-AP.
3. Weder LAN noch nutzbares WLAN: Setup-AP starten.

Internetzugang ist keine Voraussetzung fuer lokale Netzwerkbereitschaft.

## Host-Updates

Normale Appliance-Updates duerfen nur allowlistete Host-Codepfade ersetzen:

- `.env.example`
- `docker-compose.yml`
- `scripts`
- `sentero-network`
- `sentero-updater`
- `systemd`
- `zigbee2mqtt/data/configuration.yaml.example`

`device/` ist explizit aus Build- und Update-Payloads ausgeschlossen. Rollback ersetzt ebenfalls nur allowlistete Host-Dateien und nicht den persistenten Gerätebestand.

## Systemstatus und UI

Der Host-Network-Agent erweitert `system_status` um:

```json
{
  "device": {
    "identity_provisioned": true,
    "serial_number": "STB-00001234",
    "device_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

Bei Legacy-Boxen stehen `serial_number` und `device_id` auf `null`. Bei beschädigter `identity.json` wird `identity_error` gemeldet und keine neue UUID erzeugt.

Im authentifizierten Systembereich zeigt die UI Seriennummer und Geräte-ID kompakt an. Die Geräte-ID kann per Button kopiert werden. Wenn die Clipboard API nicht verfuegbar ist, erscheint eine verstaendliche Fehlermeldung; die UI stuerzt nicht ab.

## Beschädigte identity.json

Eine vorhandene beschädigte `identity.json` wird niemals still ersetzt. Beispiele:

- leere Datei
- ungueltiges JSON
- fehlende oder unbekannte `schema_version`
- fehlende oder ungueltige Seriennummer
- fehlende UUID
- UUID nicht Version 4
- fehlendes oder ungueltiges `created_at`

Recovery ist ein bewusster Maintenance-Vorgang. Vor jeder Reparatur muss die bestehende Datei gesichert und die korrekte Seriennummer sowie die urspruengliche UUID aus Produktionsunterlagen oder Backup eindeutig bestimmt werden. Ohne diese Informationen darf keine neue Identity erzeugt werden, weil Identitaetsverlust kritischer ist als ein sichtbarer Fehler.

## Testbefehle

```bash
python3 -m py_compile box/scripts/sentero_device_identity.py box/scripts/set-device-identity.py box/scripts/generate-setup-label.py box/scripts/factory-reset.py box/sentero-network/sentero_network.py deployment_build.py
bash -n box/scripts/first-install.sh box/scripts/start-box.sh
python3 -m unittest tests.test_device_identity
cd frontend && npx tsc --noEmit
```

Hardwaretests:

- Neue Box mit `sudo ./scripts/first-install.sh --serial STB-00001234` installieren.
- Installer erneut mit derselben Seriennummer ausfuehren; UUID muss gleich bleiben.
- Installer mit abweichender Seriennummer ausfuehren; muss abbrechen.
- Legacy-Box per `set-device-identity.py --serial STB-00001234` provisionieren.
- Factory Reset mit LAN plus IPv4; kein Setup-AP.
- Factory Reset ohne LAN und ohne gespeichertes WLAN; Setup-AP `Sentero-Setup-1234`.
- QR-Aufkleber mit Smartphone scannen; erwarteter Payload `WIFI:T:nopass;S:Sentero-Setup-1234;;`.
- Host-Update installieren; `identity.json` vor/nach Update byteweise vergleichen.
