# Sentero AAL/GAIA-X Roadmap

Dieses Dokument beschreibt die kontrollierte Weiterentwicklung von Sentero in Richtung Smart-Living-AAL nach dem BMWE/GAIA-X-Use-Case. Ziel ist eine lokale, datensparsame Assistenzloesung, die bei Bedarf souveraenen Datenaustausch mit Angehoerigen, Pflegediensten und weiteren Akteuren ermoeglicht.

## 1. Einwilligung und Datenhoheit

Ziel: Sentero muss abbilden, welche Person oder Organisation welche Daten zu welchem Zweck sehen darf.

Umsetzung:
- Neue Consent-Tabellen fuer Datenfreigaben, Zweckbindung, Gueltigkeit und Widerruf.
- UI fuer Einwilligung, Widerruf und Anzeige aktiver Freigaben.
- API-Pruefung vor jedem Export und jeder externen Datenweitergabe.

Kontrolle:
- Ohne aktive Einwilligung duerfen keine personenbezogenen Verhaltensdaten exportiert werden.
- Jeder Widerruf muss sofort fuer neue Zugriffe gelten.
- Tests pruefen erlaubte, abgelaufene und widerrufene Freigaben.

## 2. Akteursrollen

Ziel: Sentero unterscheidet nicht nur technische Nutzer, sondern AAL-Akteure.

Umsetzung:
- Rollenmodell erweitern: `resident`, `relative`, `care_service`, `emergency_service`, `housing_provider`, `admin`.
- Berechtigungen nach Rolle und Datenklasse trennen.
- Bestehende Trusted Contacts auf konkrete Akteursrollen migrieren.

Kontrolle:
- Pflegedienste sehen nur freigegebene Zusammenfassungen.
- Wohnungsunternehmen sehen keine personenbezogenen Verhaltensdaten.
- Notfallrollen erhalten nur im freigegebenen oder kritischen Kontext Zugriff.

## 3. Datenklassifizierung

Ziel: Jedes relevante Datum bekommt eine Schutzklasse.

Umsetzung:
- Datenklassen einfuehren: `technical`, `environmental`, `utility`, `personal_behavior`, `health_adjacent`, `emergency`.
- Sensorereignisse, Assessments, Benachrichtigungen und Exporte klassifizieren.
- Standardmaessig nur aggregierte Daten nach aussen geben.

Kontrolle:
- API-Antworten muessen Datenklasse und Aggregationsniveau kennen.
- Tests verhindern, dass Rohdaten in falschen Rollen sichtbar werden.

## 4. Souveraene Export- und Austausch-Schnittstelle

Ziel: Sentero bietet eine kontrollierte Schnittstelle fuer AAL-Partner.

Umsetzung:
- Export-Endpunkte fuer Tagesstatus, Ereigniszusammenfassungen und Systemzustand.
- Zweckgebundene Zugriffstokens mit Ablaufdatum.
- Maschinenlesbares Audit-Metadatum pro Export: Empfaenger, Zweck, Datenklassen, Zeitraum.

Kontrolle:
- Exporte enthalten keine Rohdaten, sofern nicht explizit freigegeben.
- Jeder Export ist auditierbar.
- Tokens sind widerrufbar und zeitlich begrenzt.

## 5. Audit und Transparenz

Ziel: Bewohner und berechtigte Administratoren sehen, was Sentero erfasst und geteilt hat.

Umsetzung:
- Audit-Log fuer Datenzugriffe, Exporte, Benachrichtigungen und Consent-Aenderungen.
- Transparenzansicht in der App: "Welche Daten wurden wann genutzt?"
- Aufbewahrungsfristen und Loeschfunktionen fuer alte Daten.

Kontrolle:
- Jede externe Datenweitergabe erzeugt einen Audit-Eintrag.
- Audit-Logs enthalten keine geheimen Tokens oder Passwoerter.
- Loesch- und Aufbewahrungsregeln werden getestet.

## 6. Smart-Meter-Daten als Aktivitaetsindikator

Status: begonnen.

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

Naechste Ausbaustufe:
- UI-Anzeige fuer Verbrauchshinweise im Verlauf.
- Konfigurierbare Schwellwerte pro Haushalt.
- Optionale Trennung nach Strom, Wasser und Gas im Assessment-Text.
