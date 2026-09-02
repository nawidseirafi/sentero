# V19 – Physischer Setup-WLAN-QR-Aufkleber

## Ziel
Jede Sentero Box erhält einen individuellen QR-Code, der direkt auf die Box geklebt werden kann. Der Benutzer scannt den Aufkleber mit der Kamera seines Smartphones und verbindet sich dadurch mit dem individuellen offenen Setup-WLAN `Sentero-Setup-XXXX`. Anschließend übernimmt das in V18 eingeführte Captive Portal das automatische Öffnen der Einrichtungsseite.

## Erzeugte Dateien
Beim ersten Installieren wird automatisch aus `/etc/machine-id` dieselbe deterministische Setup-SSID wie im Netzwerk-Agenten berechnet und erzeugt:

- `/opt/sentero/box/generated/sentero-setup-label.svg` – druckfertiges 50 × 30 mm Vektor-Etikett
- `/opt/sentero/box/generated/sentero-setup-wifi-qr.svg` – reiner WLAN-QR-Code
- `/opt/sentero/box/generated/sentero-setup-label.txt` – SSID, Payload und Dateipfad für Produktion/Support

Der WLAN-QR-Payload entspricht dem offenen Setup-AP:

```text
WIFI:T:nopass;S:Sentero-Setup-XXXX;;
```

## Produktion
Der Aufkleber kann jederzeit neu erzeugt werden:

```bash
python3 /opt/sentero/box/scripts/generate-setup-label.py
```

Für Produktions-/Testfälle kann eine SSID explizit vorgegeben werden:

```bash
python3 /opt/sentero/box/scripts/generate-setup-label.py --ssid Sentero-Setup-C5B9
```

Das Etikett ist absichtlich SVG/Vektor und damit ohne Auflösungsverlust für Thermo-/Etikettendrucker geeignet. Es nutzt keine Cloud und keine externe QR-Bibliothek.

## UI
Der in V18 zusätzlich in der WLAN-Webseite gezeigte QR-Block wurde wieder entfernt. Der QR-Code ist als physischer Aufkleber gedacht; das Captive Portal bleibt unverändert aktiv.

## Bestehende Boxen / normale Updates
Der Netzwerk-Agent erzeugt das Etikett bei seinem Start ebenfalls automatisch. Da der Host-Updater den Netzwerk-Agenten nach einem erfolgreichen Host-Update neu startet, wird das Label auch auf bereits installierten Boxen nach dem Update auf V19 angelegt. Ein erneuter First-Install ist nicht erforderlich.
