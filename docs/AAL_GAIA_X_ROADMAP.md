# Sentero AAL/GAIA-X Roadmap

Dieses Dokument beschreibt die kontrollierte Weiterentwicklung von Sentero in Richtung Smart-Living-AAL nach dem BMWE/GAIA-X-Use-Case. Ziel ist eine lokale, datensparsame Assistenzloesung, die bei Bedarf souveraenen Datenaustausch mit Angehoerigen, Pflegediensten und weiteren Akteuren ermoeglicht.

## 1. Einwilligung und Datenhoheit

Status: umgesetzt.

Ziel: Sentero muss abbilden, welche Person oder Organisation welche Daten zu welchem Zweck sehen darf.

Umgesetzt:
- Neue Consent-Tabelle `data_consents` fuer Datenfreigaben, Zweckbindung, Gueltigkeit und Widerruf.
- API-Endpunkte fuer aktive Freigaben, neue Freigaben und Widerruf.
- UI in den Kontakt-Einstellungen fuer Anzeige, erneute Freigabe und Widerruf von Verhaltensmeldungen.
- Benachrichtigungsgate vor externen personenbezogenen Verhaltensmeldungen.
- Neue Kontakte erhalten eine aktive Standardfreigabe fuer Verhaltensbenachrichtigungen, weil das Anlegen einer vertrauten Person bisher genau diesen Zweck ausdrueckt.

Kontrolle:
- Ohne aktive Einwilligung duerfen keine personenbezogenen Verhaltensdaten exportiert werden.
- Jeder Widerruf muss sofort fuer neue Zugriffe gelten.
- Tests pruefen erlaubte, abgelaufene und widerrufene Freigaben.

## 2. Akteursrollen

Status: umgesetzt.

Ziel: Sentero unterscheidet nicht nur technische Nutzer, sondern AAL-Akteure.

Umgesetzt:
- AAL-Rollenmodell eingefuehrt: `resident`, `relative`, `care_service`, `emergency_service`, `housing_provider`, `admin`.
- Technische Login-Rollen bleiben getrennt von AAL-Akteursrollen.
- `trusted_contacts` erhalten `actor_role`; bestehende Kontakte werden als `relative` migriert.
- `sentero_users` erhalten `aal_role`; bestehende Nutzer werden als `admin` migriert.
- Consent-Pruefung beruecksichtigt die Akteursrolle und blockiert Datenklassen, die fuer diese Rolle nicht erlaubt sind.
- Kontakt-UI erlaubt Auswahl der AAL-Rolle.

Kontrolle:
- Pflegedienste sehen nur freigegebene Zusammenfassungen.
- Wohnungsunternehmen sehen keine personenbezogenen Verhaltensdaten.
- Notfallrollen erhalten nur im freigegebenen oder kritischen Kontext Zugriff.
- Tests pruefen, dass Wohnungsanbieter keine personenbezogenen Verhaltensmeldungen erhalten.

## 3. Datenklassifizierung

Status: umgesetzt.

Ziel: Jedes relevante Datum bekommt eine Schutzklasse.

Umgesetzt:
- Zentrale Datenklassifizierung eingefuehrt: `technical`, `environmental`, `utility`, `personal_behavior`, `health_adjacent`, `emergency`.
- Sensorgeraete und Sensorereignisse liefern `data_class` und `aggregation_level`.
- Persistente Rohereignisse in `sentero_sensor_events` und `behavior_events` werden als `raw` klassifiziert.
- Behavior-Assessments werden als `summary` klassifiziert.
- Benachrichtigungslogs erhalten `data_class` und `aggregation_level`.
- Smart-Meter-Daten werden als `utility` klassifiziert, nicht als Bewegungsdaten.
- Rollenregeln verhindern Rohdatenzugriff fuer externe AAL-Akteure.

Kontrolle:
- API-Antworten muessen Datenklasse und Aggregationsniveau kennen.
- Tests verhindern, dass Rohdaten in falschen Rollen sichtbar werden.

## 4. Souveraene Export- und Austausch-Schnittstelle

Status: umgesetzt.

Ziel: Sentero bietet eine kontrollierte Schnittstelle fuer AAL-Partner.

Umgesetzt:
- Export-Endpunkte fuer Tagesstatus, Ereigniszusammenfassungen und Systemzustand.
- Zweckgebundene Zugriffstokens mit Ablaufdatum, Hash-Speicherung und Widerruf.
- Token-Verwaltung bleibt session-geschuetzt; Austausch-Endpunkte akzeptieren nur Export-Bearer-Tokens.
- Exportfreigabe, Token-Erstellung und Widerruf sind in der Kontakteinstellungen-GUI pro Partner bedienbar.
- Die GUI zeigt nach Token-Erstellung ein Dialog-Partnerpaket mit Token, Authorization-Header, Export-URLs und optionalen Direktlinks.
- Der stabile Partnervertrag ist als `Sentero AAL Exchange Profile v1` dokumentiert. Details: `docs/AAL_EXCHANGE_PROFILE_V1.md`.
- Externe Sichtbarkeit ist auf die versionierten Exchange-Pfade zu begrenzen; die lokale Sentero-GUI und Admin-APIs bleiben nicht oeffentlich. Details: `docs/AAL_EXTERNAL_INTERFACE.md`.
- Exportfreigabe setzt aktive Einwilligung fuer Zweck und Datenklassen voraus.
- Exporte enthalten maschinenlesbare Metadaten: Empfaenger, Zweck, Datenklassen, Zeitraum, Aggregationsniveau und Rohdatenstatus.
- Erfolgreiche Exporte schreiben Audit-Metadaten in `aal_export_audit`.

Kontrolle:
- Exporte enthalten keine Rohdaten, sofern nicht explizit freigegeben.
- Jeder Export ist auditierbar.
- Tokens sind widerrufbar und zeitlich begrenzt.
- Oeffentlich geroutet werden nur `/api/sentero/exchange/v1/*`; alle anderen Pfade werden am Reverse-Proxy oder Router blockiert.
- Tests pruefen aggregierte Exporte, Audit-Eintrag, Ablauf und Widerruf.

## 5. Audit und Transparenz

Status: umgesetzt.

Ziel: Bewohner und berechtigte Administratoren sehen, was Sentero erfasst und geteilt hat.

Umgesetzt:
- Zentraler Audit-Service fuehrt Consent-Aenderungen, Export-Audits, Token-Lifecycle und Benachrichtigungslogs zusammen.
- Consent-Freigaben, Consent-Widerrufe, Export-Token-Erstellung und Token-Widerruf erzeugen Audit-Eintraege.
- Transparenz-API unter `/api/sentero/transparency` mit Summary, Timeline und Retention-Status.
- Transparenzansicht in den Einstellungen zeigt: "Welche Daten wurden wann genutzt?"
- Kontrollierte Loeschfunktion fuer alte Audit-, Export- und Benachrichtigungslogs mit Retention-Audit.

Kontrolle:
- Jede externe Datenweitergabe erzeugt einen Audit-Eintrag.
- Audit-Logs enthalten keine geheimen Tokens oder Passwoerter.
- Loesch- und Aufbewahrungsregeln werden getestet.

## 6. Smart-Meter-Daten als Aktivitaetsindikator

Status: umgesetzt.

Ziel: Strom-, Leistungs-, Wasser- und Gasdaten koennen als zusaetzlicher Hinweis fuer Alltagsaktivitaet genutzt werden, ohne Bewegungsdaten zu ersetzen.

Umgesetzt:
- Neuer Device-Typ `smart_meter`.
- Neue Capabilities und Events: `energy_consumption`, `power_usage`, `water_consumption`, `gas_consumption`.
- MQTT/Zigbee2MQTT erkennt Smart-Meter-Keys aus Retained Messages.
- Dashboard zeigt `utility_usage` und `smart_meter_readings`.
- Behavior-Agent speichert Smart-Meter-Snapshots und berechnet Tagesdeltas gegen historische Tagesdurchschnitte.

Kontrolle:
- Zaehlerstaende werden nicht direkt als Bewegungsereignisse gezaehlt.
- Niedrige Verbrauchsdeltas werden nur als Zusatzhinweis genutzt.
- Tests decken Normalisierung, Dashboard und Behavior-Kontext ab.

Optionale Ausbaustufe:
- UI-Anzeige fuer Verbrauchshinweise im Verlauf.
- Konfigurierbare Schwellwerte pro Haushalt.
- Optionale Trennung nach Strom, Wasser und Gas im Assessment-Text.
