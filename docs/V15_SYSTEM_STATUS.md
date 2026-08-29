# Sentero v15 – ruhiger Systemstatus

## Ziel

Die Systemseite zeigt den technischen Zustand der Box verständlich, modern und dezent, ohne Docker-, systemd- oder Prozessdetails in den Vordergrund zu stellen. Derselbe Status kann über die bereits vorhandenen E-Mail- und Telegram-Assistenten abgefragt werden.

## Oberfläche

Unter **Einstellungen → System** erscheint ein kompakter Überblick mit den Zuständen **Bereit**, **Hinweis**, **Prüfen** und **Nicht aktiv**. Angezeigt werden Sentero, Netzwerk, Nachrichten/MQTT, Zigbee, lokale KI, Updates und `sentero.local`. Zusätzlich bleiben Sensoranzahl, Erreichbarkeit und letzte Prüfung sichtbar. Die Ansicht aktualisiert sich alle 15 Sekunden.

## Zentrale Statusquelle

`GET /api/sentero/system/status` liefert denselben strukturierten Appliance-Status, den auch E-Mail und Telegram verwenden. Die Anwendung erhält dabei keinen Docker-Socket. Stattdessen liest der privilegierte Host-Agent Docker- und systemd-Zustände read-only und gibt nur abstrahierte Statusdaten über den bestehenden Unix-Socket zurück.

## E-Mail und Telegram

Die bestehende Berechtigung **TECHNICAL_HEALTH** schützt technische Statusabfragen. E-Mails mit „Systemstatus“ bzw. Fragen nach Diensten, Zigbee, MQTT oder Technik liefern die Systemübersicht. In Telegram kann derselbe Status mit `/status` abgefragt werden. Sensorwarnungen werden in derselben Antwort ergänzt.

## Zustandslogik

Eine lokale IPv4-Verbindung genügt für „Netzwerk bereit“; fehlender Internetzugang allein macht die Box nicht rot. Zigbee wird nur bewertet, wenn das `zigbee`-Profil aktiviert ist. Ist Zigbee nicht eingerichtet, wird es neutral als „Nicht aktiv“ dargestellt. Fehler werden kundenverständlich formuliert, z. B. „Adapter nicht erkannt“ statt Container- oder systemd-Fehlertexten.
