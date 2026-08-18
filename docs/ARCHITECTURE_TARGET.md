# Sentero Zielarchitektur

Sentero soll in Wohnungen ohne Router, WLAN oder Ethernet eingerichtet werden koennen.

## Erststart

```text
Sentero einschalten
keine funktionierende Netzwerkverbindung
Setup-WLAN startet
Benutzer verbindet Smartphone oder Tablet
http://sentero.local oder http://192.168.50.1 oeffnen
Wizard: Internetverbindung
WLAN, Ethernet oder Mobilfunk auswaehlen
Verbindung testen
Setup-WLAN abschalten
Onboarding fortsetzen
```

## Betriebsmodell

`NetworkService` ist der einzige Netzwerk-Koordinator. Zielplattformen verwenden NetworkManager und ModemManager:

- WLAN-Client und WLAN-Hotspot ueber NetworkManager
- LTE-Modems ueber ModemManager/NetworkManager
- keine herstellerspezifische Modemlogik im Sentero-Code
- keine parallele Hostapd-/Dnsmasq-Konfiguration, solange NetworkManager den AP-Modus kann

Fallback auf `hostapd` und `dnsmasq` ist nur zulaessig, wenn die Zielhardware NetworkManager-Hotspot nicht unterstuetzt und die alternative Konfiguration exklusiv verwaltet wird.

## Status

Zentrale Statuswerte:

- `OFFLINE`
- `LOCAL_ONLY`
- `ONLINE_ETHERNET`
- `ONLINE_WIFI`
- `ONLINE_CELLULAR`
- `DEGRADED`

Eine Verbindung gilt erst als erfolgreich, wenn Default Route, DNS, Internet und Sentero-Mailserver erreichbar sind. `interface up` reicht nicht.

## Failover

Default:

```yaml
network:
  failover:
    enabled: true
    failure_threshold: 3
    recovery_threshold: 3
    check_interval_seconds: 30
    switch_cooldown_seconds: 120
```

Nach drei fehlgeschlagenen Checks auf Ethernet/WLAN darf LTE aktiviert werden. Nach drei erfolgreichen Checks auf der hoeher priorisierten Verbindung wechselt Sentero kontrolliert zurueck und trennt LTE. Der Cooldown verhindert Interface-Flapping.

## Sicherheit

Setup-AP-Clients duerfen nur Setup-Funktionen erreichen. Nicht erreichbar sind Sensordaten, Historie, Kontakte, Verhaltensanalyse, Admin-APIs, Logs, Shell, Home Assistant und lokale RoboterSteve-Dienste.

Credentials werden getrennt von Statusdaten verwaltet:

- WLAN-Passwoerter und SIM-PINs nicht in normalen API-Responses
- keine Secrets in Logs
- SQLite enthaelt nur nicht-sensitive Netzwerk-Metadaten und Historie
- produktiv vorzugsweise NetworkManager Connection Store oder OS Secret Store

