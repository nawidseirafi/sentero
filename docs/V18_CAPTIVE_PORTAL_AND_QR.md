# V18 – Captive Portal und Setup-WLAN-QR

## Ziel
Die Sentero Box soll beim offenen Setup-WLAN `Sentero-Setup-XXXX` wie ein typisches Hotel-/Gast-WLAN erkannt werden. Nach dem Verbinden soll iOS, iPadOS, macOS, Android und Windows nach Möglichkeit automatisch die Sentero-Einrichtungsseite öffnen. Zusätzlich steht ein lokal erzeugter WLAN-QR-Code zur Verfügung.

## Captive Portal

Der privilegierte Host-Netzwerkdienst übernimmt die Portal-Funktion ausschließlich solange das Setup-WLAN aktiv ist:

- NetworkManager Shared Mode stellt DHCP/DNS bereit.
- `/etc/NetworkManager/dnsmasq-shared.d/90-sentero-captive.conf` setzt für das Setup-WLAN eine Wildcard-DNS-Antwort auf `192.168.50.1`.
- Ein kleiner lokaler HTTP-Redirector beantwortet typische Captive-Portal-Aufrufe und leitet auf `http://192.168.50.1:8080/` um.
- Mit nftables werden nur HTTP-Anfragen auf dem Setup-WLAN von Port 80 zum Redirector geleitet.
- Falls `nft` auf einer bereits installierten Altbox noch fehlt, versucht der Agent als Fallback direkt auf `192.168.50.1:80` zu lauschen.
- HTTPS wird bewusst nicht abgefangen oder umgebogen. Dadurch entstehen keine Zertifikats-/HSTS-Warnungen.
- Beim Beenden des Setup-AP werden Redirect-Regel, Fallback-Listener und die Captive-DNS-Datei wieder entfernt.
- Eine Maintenance-Schleife stellt die Portal-Regel nach einem Neustart des Netzwerk-Agenten wieder her, falls der Setup-AP noch aktiv ist.

Das automatische Öffnen wird vom jeweiligen Betriebssystem gesteuert und kann daher nicht zu 100 % erzwungen werden. Die Box liefert jetzt aber die üblichen technischen Voraussetzungen für Captive-Portal-Erkennung.

## QR-Code

Der Host-Agent enthält einen kleinen dependency-freien QR-Encoder. Er erzeugt einen echten WLAN-QR-Code lokal auf der Box; es wird kein externer QR-Dienst aufgerufen.

Payload-Beispiel:

```text
WIFI:T:nopass;S:Sentero-Setup-C5B9;;
```

Während der Setup-AP aktiv ist:

```text
http://192.168.50.1/setup-wifi-qr.svg
```

Die Setup-Oberfläche zeigt den QR-Code dezent unterhalb der WLAN-Eingabe an. Er eignet sich besonders für ein zweites Gerät oder zum Drucken eines Setup-Aufklebers.

## Installer

`nftables` wurde den benötigten Debian-Systempaketen hinzugefügt. Bestehende Boxen bleiben dank Port-80-Fallback funktionsfähig, selbst wenn `nft` beim Host-Update noch nicht installiert wurde.

## Geänderte Dateien

- `box/sentero-network/sentero_network.py`
- `box/scripts/first-install.sh`
- `frontend/src/components/BoxNetworkSetup.tsx`

## Validierung

- Python `py_compile`: erfolgreich
- Bash `bash -n`: erfolgreich
- Captive HTTP-Test: HTTP 302 auf `http://192.168.50.1:8080/`
- QR-SVG lokal erzeugt
- QR mit OpenCV erfolgreich dekodiert als `WIFI:T:nopass;S:Sentero-Setup-XXXX;;`
