# Sentero v16 – DNS- und Update-Erkennungs-Fix

## Ursache
Docker wurde auf frisch installierten Boxen teilweise gestartet, bevor Debian über LAN/WLAN einen externen Resolver hatte. Der Docker-internen DNS-Instanz `127.0.0.11` fehlten dadurch externe Nameserver (`NO EXTERNAL NAMESERVERS DEFINED`). IP-Verbindungen funktionierten, Namensauflösung aus dem Sentero-Container jedoch nicht. Dadurch konnte `sentero.de` nicht aufgelöst werden und die Update-Prüfung blieb beim letzten gespeicherten Manifest.

## Änderungen
- Alle Compose-Dienste bekommen explizite, routerunabhängige DNS-Fallbacks (`1.1.1.1`, `9.9.9.9`), konfigurierbar über `.env`.
- `configure-docker-dns.sh` trägt dieselben Fallbacks idempotent in `/etc/docker/daemon.json` ein und erhält andere Docker-Daemon-Einstellungen.
- Der Debian-Docker-Installer konfiguriert DNS vor dem endgültigen Docker-Start.
- Update-Fehler unterscheiden DNS/Netzwerk/Timeout und werden nicht mehr als „kein Update verfügbar“ dargestellt.
- Die Update-Seite zeigt bei fehlgeschlagener Prüfung dezent „Update-Server nicht erreichbar“.

## Hinweis für bestehende Boxen
Beim Appliance-Update wird `docker-compose.yml` mit aktualisiert. Beim Recreate des Sentero-Containers gelten die DNS-Fallbacks sofort. Eine bereits manuell gesetzte `/etc/docker/daemon.json` kann bestehen bleiben.
