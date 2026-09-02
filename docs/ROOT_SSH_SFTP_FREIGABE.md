# Root-Zugriff per SSH/SFTP unter Debian freigeben

Diese Schritte erlauben vorübergehend den Login als `root` per Passwort
über SSH/SFTP.

## 1. OpenSSH-Server installieren/pruefen

``` bash
apt update
apt install -y openssh-server
systemctl enable --now ssh
```

## 2. Aktuelle Einstellung pruefen

``` bash
/usr/sbin/sshd -T | grep -E 'permitrootlogin|passwordauthentication'
```

Wenn z. B. Folgendes erscheint, ist Root-Login per Passwort noch
gesperrt:

``` text
permitrootlogin without-password
passwordauthentication yes
```

## 3. Root-Login per Passwort freigeben

``` bash
cat >/etc/ssh/sshd_config.d/99-sentero.conf <<'EOF'
PermitRootLogin yes
PasswordAuthentication yes
EOF
```

## 4. SSH-Konfiguration testen

``` bash
/usr/sbin/sshd -t
```

Keine Ausgabe bedeutet: Konfiguration ist syntaktisch in Ordnung.

## 5. SSH neu starten

``` bash
systemctl restart ssh
```

Danach kontrollieren:

``` bash
/usr/sbin/sshd -T | grep -E 'permitrootlogin|passwordauthentication'
```

Erwartet:

``` text
permitrootlogin yes
passwordauthentication yes
```

## 6. Root-Passwort setzen

``` bash
passwd root
```

## 7. IP-Adresse anzeigen

``` bash
hostname -I
```

## 8. Vom Mac per SFTP verbinden

``` bash
sftp root@IP-DER-BOX
```

Beispiel:

``` bash
sftp root@192.168.178.173
```

In einem SFTP-Programm:

-   Protokoll: SFTP
-   Port: 22
-   Benutzer: root
-   Passwort: Root-Passwort

## Sicherheit

Die Passwort-Anmeldung als `root` sollte nur fuer die
Einrichtung/Provisionierung verwendet werden. Vor Auslieferung einer
Sentero-Box sollte Root-Passwort-SSH wieder deaktiviert oder auf
SSH-Key-Authentifizierung umgestellt werden.
