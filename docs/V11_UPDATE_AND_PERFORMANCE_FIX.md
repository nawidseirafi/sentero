# Sentero v11 – Update-Status und UI-Performance

## Update-Fehlmeldung

Der Appliance-Updater laeuft als Host-Service weiter, waehrend der Sentero-Container beim Update ersetzt wird. Dadurch kann die HTTP-Anfrage, die das Update gestartet hat, abbrechen, obwohl der Host-Updater erfolgreich fertig wird.

v11 behebt das auf zwei Ebenen:

- Der Host-Updater kann seinen persistenten `host_update_state.json` ueber die Socket-Aktion `status` ausgeben.
- Das Backend gleicht nach dem Container-Neustart den App-Update-Status mit dem Host-Status ab.
- Das Frontend behandelt einen kurzen Verbindungsabbruch waehrend der Installation als erwarteten Neustart und pollt bis zu 120 Sekunden auf den echten Endstatus.

Damit wird `success` angezeigt, wenn der Host-Updater die Zielversion erfolgreich installiert hat, statt einen abgebrochenen Browser-Request als fehlgeschlagen zu interpretieren.

## Netzwerkstatus / zufaellige UI-Haenger

Der Host-Netzwerkagent hat bisher bei jedem Statusabruf `nmcli networking connectivity check` ausgefuehrt. Dieser aktive Internet-Test kann bei langsamer oder fehlender Internetverbindung viele Sekunden blockieren. Ausserdem wurde `iw list` bei jedem Abruf erneut gestartet.

v11:

- verwendet den schnellen gecachten NetworkManager-Connectivity-Status ohne erzwungenen `check`,
- cached die statische WLAN-AP-Faehigkeit fuer 5 Minuten,
- verwendet die bereits gelesene Device-Liste statt eines zusaetzlichen `nmcli`-Aufrufs.

Lokale Netzwerkbereitschaft bleibt weiterhin ausschliesslich von einer lokalen IPv4-Verbindung abhaengig; Internet ist nur ein separater Informationsstatus.
