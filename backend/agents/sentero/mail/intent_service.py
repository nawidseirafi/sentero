from __future__ import annotations

import re

from backend.agents.sentero.mail.models import IntentResult, MailIntent

ACTION_RE = re.compile(r"\b(öffne|oeffne|schließe|schliesse|starte|stoppe|deaktivier|aktiviere|ändere|aendere|setze|lösche|loesche)\b", re.I)


class MailIntentService:
    def classify(self, question: str) -> IntentResult:
        text = normalize_question(question)
        if not text:
            return IntentResult(MailIntent.UNKNOWN, 0.0)
        if ACTION_RE.search(text):
            return IntentResult(MailIntent.UNKNOWN, 1.0)
        if any(phrase in text for phrase in ["was kann ich fragen", "hilfe", "help", "was kannst du"]):
            return IntentResult(MailIntent.HELP, 0.95)
        if any(term in text for term in ["strom", "stromverbrauch", "verbrauch", "energie", "zaehler", "zähler", "leistung", "watt", "kwh"]):
            return IntentResult(MailIntent.POWER_USAGE, 0.9)
        if any(term in text for term in ["tuer", "türen", "tueren", "tür", "haustuer", "haustür", "fenster", "kontakt"]) and any(term in text for term in ["zu", "offen", "geschlossen", "auf"]):
            return IntentResult(MailIntent.CONTACT_STATUS, 0.9)
        if any(term in text for term in ["temperatur", "warm", "kalt", "luftfeuchtigkeit", "feuchtigkeit", "feutichkeit", "klima", "helligkeit", "hell", "dunkel", "lux", "licht"]):
            return IntentResult(MailIntent.ENVIRONMENT, 0.9)
        if any(term in text for term in ["nacht", "geschlafen", "schlaf"]):
            return IntentResult(MailIntent.NIGHT_SUMMARY, 0.88)
        if any(term in text for term in ["auffällig", "auffaellig", "auffälligkeit", "auffaelligkeit", "ungewöhnlich", "ungewoehnlich", "alarm", "warnung"]):
            return IntentResult(MailIntent.ANOMALIES, 0.9)
        if any(term in text for term in ["sensor", "batterie", "erreichbar", "gesundheit der technik", "technik"]):
            return IntentResult(MailIntent.SENSOR_HEALTH, 0.86)
        if any(term in text for term in ["heute passiert", "tagesverlauf", "heute", "zusammenfassung"]):
            return IntentResult(MailIntent.TODAY_SUMMARY, 0.84)
        if any(term in text for term in ["wo ist", "wo war", "wo befindet", "welcher raum", "in welchem raum", "wo wurde", "zuletzt erkannt", "letzter raum"]):
            return IntentResult(MailIntent.LAST_ROOM, 0.86)
        if any(term in text for term in ["wann", "letzte bewegung", "zuletzt beweg", "letzte aktivität", "letzte aktivitaet"]):
            return IntentResult(MailIntent.LAST_ACTIVITY, 0.86)
        if any(term in text for term in ["aktivität", "aktivitaet", "bewegung", "anwesenheit", "gerade aktiv"]):
            return IntentResult(MailIntent.CURRENT_ACTIVITY, 0.8)
        if any(term in text for term in ["alles gut", "alles in ordnung", "status", "geht es", "ok", "okay"]):
            return IntentResult(MailIntent.STATUS_SUMMARY, 0.9)
        return IntentResult(MailIntent.UNKNOWN, 0.0)


def normalize_question(question: str) -> str:
    text = question.lower()
    text = text.replace("ß", "ss")
    return re.sub(r"\s+", " ", text).strip()
