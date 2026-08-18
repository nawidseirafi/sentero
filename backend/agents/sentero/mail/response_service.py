from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.agents.sentero.mail.models import MailIntent, QueryResult


class MailResponseService:
    def build(self, result: QueryResult) -> str:
        if result.permission_denied:
            return "Guten Tag,\n\ndiese Frage ist für Ihre E-Mail-Freigabe nicht freigeschaltet.\n\nViele Grüße\nSentero"
        if result.intent == MailIntent.HELP:
            return HELP_TEXT
        if result.intent == MailIntent.UNKNOWN:
            return UNKNOWN_TEXT
        if not result.data_available:
            return "Guten Tag,\n\nmomentan liegen nicht genügend aktuelle Sensordaten für eine zuverlässige Antwort vor. Sentero überwacht die Sensorverbindung weiter.\n\nViele Grüße\nSentero"
        if result.intent == MailIntent.STATUS_SUMMARY:
            return self._status(result)
        if result.intent in {MailIntent.CURRENT_ACTIVITY, MailIntent.LAST_ACTIVITY, MailIntent.LAST_ROOM}:
            return self._activity(result)
        if result.intent == MailIntent.TODAY_SUMMARY:
            return self._today(result)
        if result.intent == MailIntent.ANOMALIES:
            return self._anomalies(result)
        if result.intent == MailIntent.ENVIRONMENT:
            return self._environment(result)
        if result.intent == MailIntent.NIGHT_SUMMARY:
            return self._night(result)
        if result.intent == MailIntent.SENSOR_HEALTH:
            return self._sensor_health(result)
        return UNKNOWN_TEXT

    def read_only_action_rejected(self) -> str:
        return "Guten Tag,\n\nüber E-Mail können ausschließlich Informationen abgefragt werden. Geräte, Türen, Sensoren oder Sicherheitseinstellungen können darüber nicht verändert werden.\n\nViele Grüße\nSentero"

    def _status(self, result: QueryResult) -> str:
        assessment = result.facts.get("assessment") or {}
        activity = result.facts.get("last_activity")
        env = result.facts.get("environment") or {}
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        status = str(assessment.get("status") or "normal")
        if status in {"green", "normal", "ok"}:
            lines.append("bei Sentero gibt es aktuell keine auffälligen Hinweise.")
        else:
            lines.append("Sentero hat Hinweise erkannt, die im Tagesverlauf auffällig wirken.")
        if activity:
            lines.append(_activity_sentence(activity))
        if env.get("temperature_c") is not None:
            lines.append(f"Die zuletzt gemessene Raumtemperatur beträgt {str(env['temperature_c']).replace('.', ',')} °C.")
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(lines)

    def _activity(self, result: QueryResult) -> str:
        event = result.facts.get("activity")
        context = _context_sentence(result)
        prefix = f"{context}\n" if context else ""
        return f"Guten Tag,\n\n{prefix}{_activity_sentence(event)}\n\nViele Grüße\nSentero" if event else self.build(QueryResult(result.intent, "no_data", data_available=False))

    def _today(self, result: QueryResult) -> str:
        count = int(result.facts.get("event_count") or 0)
        last = result.facts.get("last_activity")
        lines = ["Guten Tag,", "", f"Heute wurden {count} Aktivitätsereignisse registriert."]
        context = _context_sentence(result)
        if context:
            lines.insert(2, context)
        if last:
            lines.append(_activity_sentence(last))
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(lines)

    def _anomalies(self, result: QueryResult) -> str:
        assessment = result.facts.get("assessment") or {}
        findings = result.facts.get("findings") or []
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        if str(result.facts.get("status") or "").lower() in {"green", "normal", "ok"} and not findings:
            lines.append("Sentero hat heute bisher keine auffälligen Hinweise erkannt.")
        else:
            lines.append(str(assessment.get("summary") or "Sentero hat heute Auffälligkeiten im Tagesverlauf erkannt."))
            for finding in findings[:3]:
                lines.append(f"- {finding}")
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(lines)

    def _environment(self, result: QueryResult) -> str:
        env = result.facts.get("environment") or {}
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        if env.get("temperature_c") is not None:
            lines.append(f"Die zuletzt gemessene Temperatur beträgt {str(env['temperature_c']).replace('.', ',')} °C.")
            lines.append(_freshness_sentence(env.get("temperature_c_freshness")))
        if env.get("humidity_percent") is not None:
            lines.append(f"Die zuletzt gemessene Luftfeuchtigkeit beträgt {str(env['humidity_percent']).replace('.', ',')} %.") 
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(line for line in lines if line)

    def _night(self, result: QueryResult) -> str:
        count = int(result.facts.get("night_activity_count") or 0)
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        if count == 0:
            lines.append("Für die vergangene Nacht liegen keine gesicherten Aktivitätsereignisse vor.")
        else:
            lines.append(f"In der vergangenen Nacht wurden {count} Aktivitätsereignisse erkannt.")
            last = result.facts.get("last_activity")
            if last:
                lines.append(f"Die letzte nächtliche Aktivität wurde im Raum {last.get('room_label')} registriert.")
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(lines)

    def _sensor_health(self, result: QueryResult) -> str:
        facts = result.facts
        lines = ["Guten Tag,", "", f"Sentero kennt aktuell {facts.get('sensor_count', 0)} Sensoren."]
        context = _context_sentence(result)
        if context:
            lines.insert(2, context)
        if facts.get("unreachable"):
            lines.append(f"{len(facts['unreachable'])} Sensoren sind momentan nicht erreichbar.")
        if facts.get("low_battery"):
            lines.append(f"{len(facts['low_battery'])} Sensoren melden eine schwache Batterie.")
        if not facts.get("unreachable") and not facts.get("low_battery"):
            lines.append("Es liegen aktuell keine technischen Sensorwarnungen vor.")
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(lines)


HELP_TEXT = """Guten Tag,

Sie können Sentero zum Beispiel fragen:
• Ist alles in Ordnung?
• Wann wurde zuletzt Aktivität erkannt?
• Wo wurde zuletzt Aktivität erkannt?
• Gab es heute Auffälligkeiten?
• Wie ist die Temperatur in der Wohnung?
• Wie war die vergangene Nacht?

Viele Grüße
Sentero"""

UNKNOWN_TEXT = """Guten Tag,

diese Frage konnte ich noch nicht sicher einordnen. Sie können mich zum Beispiel fragen, ob alles in Ordnung ist, wann zuletzt Aktivität erkannt wurde oder wie die Temperatur in der Wohnung ist.

Viele Grüße
Sentero"""


def _activity_sentence(event: dict[str, Any]) -> str:
    room = event.get("room_label") or event.get("room") or "einem Raum"
    freshness = event.get("freshness") or {}
    age = freshness.get("age_seconds")
    if age is None:
        return f"Die letzte erkannte Aktivität wurde im {room} registriert. Die Aktualität der Daten ist momentan nicht zuverlässig bestimmbar."
    minutes = max(int(age // 60), 0)
    if freshness.get("bucket") == "fresh":
        return f"Aktuell wurde Aktivität im {room} erkannt."
    if freshness.get("bucket") == "recent":
        return f"Die letzte erkannte Aktivität war vor {minutes} Minuten im {room}."
    if freshness.get("bucket") == "stale":
        return f"Die letzte sichere Aktivität wurde vor {minutes} Minuten im {room} erkannt. Eine aktuelle Raumzuordnung ist momentan nicht zuverlässig möglich."
    return f"Die letzte erkannte Aktivität war vor {minutes} Minuten im {room}. Eine aktuelle Raumzuordnung ist nur eingeschränkt zuverlässig."


def _context_sentence(result: QueryResult) -> str:
    context = result.facts.get("thread_context") or {}
    if not context:
        return ""
    title = str(context.get("message_title") or "eine frühere Sentero-Meldung").strip()
    created_at = str(context.get("created_at") or "").strip()
    if created_at:
        return f'Zur ursprünglichen Meldung "{title}" vom {created_at}:'
    return f'Zur ursprünglichen Meldung "{title}":'


def _freshness_sentence(freshness: dict[str, Any] | None) -> str:
    if not freshness:
        return "Die Aktualität dieses Sensorwerts ist momentan nicht zuverlässig bestimmbar."
    bucket = freshness.get("bucket")
    age = freshness.get("age_seconds")
    minutes = int(age // 60) if isinstance(age, int) else None
    if bucket == "fresh":
        return "Der Wert ist aktuell."
    if bucket == "recent" and minutes is not None:
        return f"Der Wert wurde vor {minutes} Minuten aktualisiert."
    if minutes is not None:
        return f"Der Wert ist {minutes} Minuten alt und sollte nicht als Live-Zustand verstanden werden."
    return "Der Wert sollte nicht als Live-Zustand verstanden werden."
