# Sentero Easy New Box v6 – Netzwerk-Onboarding

Diese Version verschiebt WLAN/Hotspot-Steuerung aus dem Docker-Container auf den Debian-Host.

Ablauf bei einer fabrikneuen Box:

1. `first-install.sh` installiert/aktiviert NetworkManager, Avahi und den Host-Netzwerkdienst.
2. Wenn bereits Internet über Ethernet vorhanden ist, startet Sentero normal.
3. Wenn kein Internet vorhanden ist, startet zunächst nur die Sentero-App.
4. Die App fordert den Host-Netzwerkdienst auf, ein temporäres offenes WLAN `Sentero-Setup-XXXX` auf 192.168.50.1/24 zu starten.
5. Browser: `http://192.168.50.1:8080`.
6. Vor Login/Kontoanlage wird der Netzwerk-Wizard angezeigt.
7. Der Wizard zeigt gefundene WLANs; manuelle SSID-Eingabe bleibt möglich.
8. Beim Verbinden wird der Setup-AP kurz beendet. Schlägt die Verbindung fehl, wird der Setup-AP automatisch wieder gestartet.
9. Bei erfolgreichem Internetzugang bleibt der Setup-AP aus, der restliche Docker-Stack wird im Hintergrund gestartet und Sentero ist anschließend über `http://sentero.local:8080` erreichbar.

Wichtige Architekturänderungen:

- `box/sentero-network/sentero_network.py`: privilegierter Host-Agent über Unix-Socket.
- `box/systemd/sentero-network.service`: systemd-Dienst für den Host-Agent.
- `/run/sentero-network/network.sock`: Kommunikationsweg Container -> Host.
- `backend/services/network/host_client.py`: Client im Sentero-Container.
- `WifiService` und `AccessPointService` delegieren in Appliance-Mode an den Host.
- `App.tsx` blockiert Login und normalen Setup-Wizard, solange das Box-Netzwerk nicht bereit ist.
- `BoxNetworkSetup.tsx` unterstützt Scan, Auswahl und manuelle SSID.
- `start-box.sh` startet ohne Internet nur die Sentero-App, damit das WLAN-Onboarding auch vor dem Pull der übrigen Container funktioniert.

Hinweis: Die Skripte und Python-Dateien wurden syntaktisch geprüft. Ein realer AP-/WiFi-Wechsel kann in dieser Umgebung nicht mit der konkreten WLAN-Hardware der Box getestet werden. Vor einer endgültigen Factory-Auslieferung sollte einmal auf der echten Box geprüft werden, dass deren WLAN-Chipsatz AP-Modus unterstützt (`iw list`).

Für eine komplett netzlose Debian-Erstinstallation müssen Docker/NetworkManager bereits installiert sein oder die Debian-Pakete offline mitgeliefert werden. Der aktuelle `first-install.sh` kann fehlende Systempakete über APT installieren und benötigt dafür zu diesem Zeitpunkt eine Paketquelle (z. B. temporäres Ethernet). Das eigentliche Sentero-WLAN-Onboarding benötigt danach kein vorkonfiguriertes Heim-WLAN.
