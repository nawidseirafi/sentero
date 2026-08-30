# Sentero Box -- Production Security / Lockdown

## Stand

Aktuell befindet sich Sentero noch in der Entwicklungs- und Testphase.

Die derzeit verwendete Box:

-   Seriennummer: `STB-00000001`
-   ist unsere Development-/Testbox
-   SSH bleibt aktiviert
-   HDMI / lokale Konsole bleibt aktiviert
-   Debugging und Entwicklerzugriff bleiben möglich
-   diese Box soll NICHT als Kundenbox abgesichert werden

Die endgültige Production-Hardware ist noch nicht vorhanden.

## Ziel für spätere Kundenboxen

Vor Auslieferung einer Sentero Box soll ein definierter **Production
Lockdown** durchgeführt werden.

Zielzustand:

``` text
KUNDENBOX

SSH                  AUS
Port 22              geschlossen
Root Remote Login    AUS
Passwort-SSH         AUS
Development Keys     ENTFERNT
HDMI-Konsole         kein administrativer Login
TTY-Konsole          gesperrt
Firewall             restriktiv
unnötige Ports       geschlossen
unnötige Services    nicht von außen erreichbar
Sentero Web-App      AN
Updates              AN
Factory Reset        AN
Netzwerk-Setup       AN
Network-Agent        AN
Host-Updater         AN
```

## SSH

Bei einer Kundenbox soll SSH standardmäßig vollständig deaktiviert sein.
Nicht nur `PermitRootLogin no`, sondern der SSH-Dienst soll im normalen
Produktionsbetrieb gar nicht laufen.

Später beispielsweise:

``` bash
systemctl disable --now ssh
systemctl mask ssh
```

Vorher müssen Root-SSH-Freigaben, Development Public Keys, temporäre
SSH-Konfigurationen und Test-Accounts geprüft und entfernt werden.

**Die aktuelle Testbox NICHT entsprechend verändern.**

## HDMI / lokale Konsole

Die finale Hardware besitzt HDMI. Vor Auslieferung muss geprüft werden,
ob über HDMI, Tastatur, Linux TTY, `getty`, lokale Login-Konsole, GRUB,
Recovery-/Single-User-Modus, USB-Boot oder UEFI/BIOS administrativer
Zugriff möglich ist.

Ziel: Ein Kunde soll durch Anschließen von Monitor und Tastatur keinen
normalen Debian-/Root-Zugang erhalten.

Ob HDMI vollständig deaktiviert oder nur der Login darüber gesperrt
wird, wird mit der finalen Production-Hardware entschieden.

## UEFI / physischer Zugriff

Bei der finalen Hardware prüfen:

-   USB-/externen Boot deaktivieren
-   interne SSD als Bootziel
-   UEFI-/BIOS-Admin-Passwort
-   GRUB absichern
-   Recovery-Boot absichern
-   Secure Boot prüfen

Physischer Zugriff ist eine andere Sicherheitsklasse als SSH. Der
Sourcecode-/Datenträgerschutz wird separat behandelt.

## Firewall / Netzwerk

Vor Auslieferung prüfen, welche Ports tatsächlich im LAN erreichbar
sind, insbesondere SSH/22, Sentero Web-App, MQTT, Zigbee2MQTT, Ollama,
Docker-Dienste und sonstige Host-Services.

Interne Dienste sollen nicht unnötig im Kundennetz erreichbar sein.
Network-Agent und Host-Updater sollen intern bleiben.

Nach dem Lockdown von einem zweiten Rechner einen Portscan durchführen.

## Production-Lockdown-Werkzeug

Langfristig soll ein reproduzierbares Host-Werkzeug entstehen:

``` bash
sudo /opt/sentero/box/scripts/production-lockdown.py --audit
```

`--audit` prüft nur und verändert nichts.

Für echte Kundenboxen:

``` bash
sudo /opt/sentero/box/scripts/production-lockdown.py --apply
```

`--apply` darf erst nach expliziter Bestätigung Änderungen durchführen.

Prüfpunkte: Geräteidentität, Seriennummer, UUID, Installation, SSH,
Development Keys, Firewall, lokale Konsole und Boot-Konfiguration.

## Updates und Factory Reset

-   Factory Reset darf den Production Lockdown NICHT aufheben.
-   Updates dürfen SSH NICHT wieder aktivieren.
-   Updates dürfen keine Development Keys installieren.
-   Updates dürfen gesperrte Login-Zugänge nicht wieder öffnen.
-   Seriennummer und Geräte-ID/UUID müssen erhalten bleiben.
-   Security-Konfiguration und Production-Status müssen erhalten
    bleiben.

## Späterer Supportzugriff

KEIN universelles Support-Passwort auf allen Boxen.

Langfristig bevorzugt:

``` text
Normalbetrieb → SSH AUS
Besitzer/Admin aktiviert Wartungszugriff
→ temporär 30–60 Minuten
→ nur Public-Key-Authentifizierung
→ kein Passwort
→ Timeout
→ SSH automatisch wieder AUS
```

Später können individuelle, zeitlich begrenzte Support-Credentials pro
Gerät/Session ergänzt werden.

## Sourcecode-Schutz

Separates späteres Thema. Zuerst wird der Zugriff auf Kundenboxen
abgesichert.

Später prüfen: Docker-/Python-Source, Frontend-Bundles, `.git`,
Build-/Debug-Artefakte, Secrets, Container-Hardening,
Datenträgerverschlüsselung und Secure Boot.

SSH-Härtung allein ist kein vollständiger Schutz bei physischem Besitz
der Hardware.

## Vorgehen mit finaler Hardware

Zuerst Security Audit:

1.  Benutzerkonten
2.  SSH-Konfiguration und Keys
3.  offene Ports und laufende Services
4.  Docker-Port-Mappings
5.  lokale TTYs / HDMI
6.  GRUB
7.  UEFI/BIOS und USB-Boot
8.  Recovery-Modi
9.  Firewall
10. Dateisystem
11. Update-Verhalten
12. Factory-Reset-Verhalten

Danach Production Lockdown implementieren und auf echter Hardware
testen.

## Aktuelle Testbox

`STB-00000001` ist die Entwicklungs-/Testbox.

Auf dieser Box aktuell NICHT:

-   SSH deaktivieren oder maskieren
-   HDMI deaktivieren
-   TTY sperren
-   Entwicklerzugänge entfernen
-   Production Lockdown anwenden

Sie bleibt für Entwicklung und Hardwaretests offen.

Später dient sie als Gegenprobe:

-   Development Box → offen
-   Production Box → gelockt

Sentero-Updates müssen auf beiden Zuständen funktionieren.

## Nächster Schritt

Wenn die finale Kundenhardware vorhanden ist, nicht sofort locken.
Zuerst vollständigen Debian-/Hardware-Security-Audit durchführen.

Erst nach erfolgreichem Audit, Lockdown, Portscan, Reboot-Test,
Update-Test und Factory-Reset-Test gilt eine Sentero Box als
auslieferungsbereit.
