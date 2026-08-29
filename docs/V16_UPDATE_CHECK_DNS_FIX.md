# V16 – Update-Erkennung und DNS-Zuverlässigkeit

## Behoben

- Ein alter erfolgreicher Host-Updater-Status darf eine frisch erkannte neue Version nicht mehr überschreiben.
- Host-Updater-Reconciliation läuft nur noch bei `running`, `failed` oder `error`.
- Die Update-Seite zeigt „Update erfolgreich“ nur noch für zehn Minuten nach einem tatsächlich abgeschlossenen Installationslauf; danach wieder den normalen aktuellen Zustand.
- Der Sentero-Container erhält konfigurierbare externe DNS-Fallbacks (`SENTERO_DNS_PRIMARY`, `SENTERO_DNS_SECONDARY`), damit ein Docker-Start während des Setup-AP-Modus nicht dauerhaft ohne externen Resolver bleibt.
- Interne Docker-DNS-Namen bleiben über Dockers eingebetteten Resolver verfügbar.

## Sofort-Reparatur bestehender 0.3.2-Boxen

Nach Reparatur des Container-DNS kann der alte `host_update_state.json` einmalig archiviert und auf `{}` zurückgesetzt werden. Danach bleibt das Ergebnis einer neuen Update-Prüfung erhalten und 0.3.3 kann über die normale Oberfläche installiert werden.
