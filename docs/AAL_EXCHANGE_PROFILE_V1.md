# Sentero AAL Exchange Profile v1

Dieses Profil beschreibt die versionierte Sentero-Schnittstelle fuer Smart-Living-AAL-Partner. Es ist kein offizieller AAL- oder GAIA-X-Standard, sondern ein stabiler Sentero-Vertrag fuer kontrollierten, lokalen Datenaustausch.

## Grundprinzip

- Sentero bleibt lokal.
- Partner erhalten keinen Sentero-Login.
- Extern sichtbar ist nur `/api/sentero/exchange/v1/*`.
- Die Schnittstelle liefert aggregierte, zweckgebundene Daten.
- Rohdaten werden nicht exportiert.
- Jeder erfolgreiche Abruf wird auditierbar protokolliert.

## Authentifizierung

Empfohlen:

```http
Authorization: Bearer <export-token>
```

Der Token wird in Sentero pro Kontakt erzeugt. Er ist an Zweck, AAL-Rolle, Datenklassen und optional ein Ablaufdatum gebunden. Sentero speichert nur den Token-Hash; der Klartext-Token ist nur direkt nach der Erstellung sichtbar.

## Endpunkte

Basis:

```text
/api/sentero/exchange/v1
```

### Tagesstatus

```text
GET /api/sentero/exchange/v1/daily-status
```

Optional:

```text
period_start=<ISO-8601>
period_end=<ISO-8601>
```

Liefert eine kompakte Einschaetzung des Tagesablaufs fuer den freigegebenen Zeitraum.

### Ereigniszusammenfassung

```text
GET /api/sentero/exchange/v1/event-summary
```

Optional:

```text
period_start=<ISO-8601>
period_end=<ISO-8601>
```

Liefert aggregierte Ereigniszaehler und Zusammenfassungen, keine einzelnen Rohereignisse.

### Systemstatus

```text
GET /api/sentero/exchange/v1/system-status
```

Liefert technische Betriebsdaten, soweit die Rolle und Freigabe diese Datenklasse erlauben.

## Antwortformat

Alle erfolgreichen Antworten nutzen dieselbe Struktur:

```json
{
  "meta": {
    "export_type": "daily-status",
    "recipient": {
      "contact_id": 1,
      "actor_role": "care_service"
    },
    "purpose": "aal_partner_export",
    "data_classes": ["personal_behavior", "health_adjacent", "emergency"],
    "period": {
      "start": "2026-08-11T00:00:00+02:00",
      "end": "2026-08-11T23:59:59+02:00"
    },
    "aggregation_level": "summary",
    "raw_data_included": false,
    "generated_at": "2026-08-11T12:00:00+02:00"
  },
  "data": {}
}
```

`data` unterscheidet sich je nach Endpunkt. `meta` ist fuer Partner die verbindliche Einordnung: Empfaenger, Zweck, Datenklassen, Zeitraum, Aggregationsniveau und Rohdatenstatus.

## Datenklassen

- `personal_behavior`: Tagesablauf und Aktivitaetsmuster.
- `health_adjacent`: AAL-Hinweise mit Gesundheitsnaehe, aber keine Diagnose.
- `emergency`: Notfallnahe Hinweise.
- `technical`: technische Betriebsdaten.
- `environmental`: Umgebungsdaten.
- `utility`: Verbrauchsdaten, z. B. Strom, Wasser oder Gas.

Welche Datenklassen ein Partner erhaelt, wird durch AAL-Rolle, aktive Freigabe und Token begrenzt.

## Fehler

- `400`: ungueltiger Zeitraum oder unbekannter Export-Typ.
- `403`: Token fehlt, ist ungueltig, abgelaufen, widerrufen oder nicht durch eine aktive Freigabe gedeckt.
- `404`: Pfad am Edge-Proxy nicht freigegeben oder unbekannt.
