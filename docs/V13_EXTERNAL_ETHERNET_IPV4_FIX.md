# V13 – Extern verwaltetes Ethernet korrekt erkennen

## Problem

Debian 13 / NetworkManager kann ein funktionierendes Ethernet-Interface als `connected (externally)` melden. Die bisherige Sentero-Logik akzeptierte nur exakt `connected` und übersah dadurch z. B. `enp1s0` trotz gültiger lokaler IPv4-Adresse. Ergebnis: `network_ready=false` und ein unnötig aktives Setup-WLAN.

## Korrektur

- `sentero_network.py` verwendet die tatsächlich am Kernel-Interface vorhandene globale IPv4-Adresse (`ip -o -4 addr ...`) als maßgebliche Quelle.
- NetworkManager-Statusstrings entscheiden nicht mehr darüber, ob Ethernet/WLAN lokal nutzbar ist.
- Link-local-Adressen `169.254.0.0/16` zählen nicht als nutzbares lokales Netzwerk.
- `start-box.sh` und `first-install.sh` verwenden dieselbe Erkennung, damit Fresh Install, Boot und Laufzeit konsistent sind.
- Der Setup-AP bleibt nur aktiv, wenn weder Ethernet noch Client-WLAN eine nutzbare globale IPv4-Adresse besitzen.

## Erwarteter Zustand bei LAN

Für z. B. `enp1s0` mit `192.168.178.189/24` gilt:

- `active_connection=ethernet`
- `network_ready=true`
- `ethernet_active=true`
- `ethernet_device=enp1s0`
- `ethernet_ip_address=192.168.178.189`
- Setup-AP wird beendet.
