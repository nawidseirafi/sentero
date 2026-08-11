# Sentero AAL External Interface

Sentero bleibt lokal. Die Weboberflaeche, Login-, Setup-, Sensor-, Transparenz- und Admin-APIs duerfen nicht aus dem Internet erreichbar sein.

Fuer Smart-Living-AAL wird nur eine schmale Partnerschnittstelle nach aussen freigegeben:

- `/api/sentero/exchange/daily-status`
- `/api/sentero/exchange/event-summary`
- `/api/sentero/exchange/system-status`

Diese Endpunkte akzeptieren ausschliesslich Export-Bearer-Tokens. Alle anderen Sentero-Pfade bleiben nur im lokalen Netz oder ueber einen privaten Admin-Zugang erreichbar.

Docker-Betrieb auf Debian Mini-PC: `docs/MINI_PC_DOCKER_DEPLOYMENT.md`.

## Zielbild

- Lokale Nutzung: komplette Sentero-GUI im Heimnetz.
- Externe Nutzung: nur `/api/sentero/exchange/*`.
- Kein externer Zugriff auf `/`, `/sentero/*`, `/docs`, `/openapi.json`, `/api/sentero/auth/*`, `/api/sentero/setup/*`, `/api/sentero/sensors/*`, `/api/sentero/transparency/*`.
- Keine Cloud-Pflicht. Falls Fernzugriff noetig ist, bevorzugt selbst kontrollierte Infrastruktur oder direkter VPN-Zugang.
- Partner erhalten keinen Sentero-Login, sondern nur einen Export-Token fuer ihre Rolle und Datenklassen.

## Empfohlene Umsetzung

Die Trennung sollte am Rand des Systems passieren, nicht nur in der App:

1. Sentero-Backend lokal laufen lassen, z. B. `127.0.0.1:8080` oder nur im Heimnetz.
2. Reverse-Proxy oder Router veroeffentlicht extern nur `/api/sentero/exchange/*`.
3. Alle anderen Pfade extern mit `404` oder `403` blockieren.
4. TLS aktivieren.
5. Wenn moeglich IP-Allowlist fuer bekannte Partnernetze setzen.
6. Export-Tokens zeitlich begrenzen und bei Bedarf widerrufen.

## Nginx-Beispiel

```nginx
server {
    listen 443 ssl;
    server_name aal.example.org;

    ssl_certificate     /etc/letsencrypt/live/aal.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aal.example.org/privkey.pem;

    location /api/sentero/exchange/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    location / {
        return 404;
    }
}
```

## Caddy-Beispiel

```caddyfile
aal.example.org {
    handle /api/sentero/exchange/* {
        reverse_proxy 127.0.0.1:8080
    }

    handle {
        respond 404
    }
}
```

## Kontrolle

Extern duerfen nur diese Tests erfolgreich sein:

```bash
curl -i https://aal.example.org/api/sentero/exchange/daily-status \
  -H "Authorization: Bearer <export-token>"
```

Diese Pfade muessen extern blockiert sein:

```bash
curl -i https://aal.example.org/
curl -i https://aal.example.org/sentero/settings
curl -i https://aal.example.org/docs
curl -i https://aal.example.org/openapi.json
curl -i https://aal.example.org/api/sentero/auth/status
curl -i https://aal.example.org/api/sentero/transparency
```

Erwartung: `404` oder `403`.

## Warum nicht die ganze Sentero-App veroeffentlichen?

Die AAL-Schnittstelle ist nur fuer zweckgebundene, aggregierte Partnerdaten gedacht. Die Sentero-App enthaelt lokale Administration, Setup, Sensorstatus, Freigaben, Transparenzdaten und Konto-/Systemfunktionen. Diese Flaechen bleiben lokal, auch wenn die Export-Schnittstelle nach aussen freigegeben wird.
