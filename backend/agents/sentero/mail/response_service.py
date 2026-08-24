from __future__ import annotations

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo
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
        if result.intent == MailIntent.POWER_USAGE:
            return self._power_usage(result)
        if result.intent == MailIntent.CONTACT_STATUS:
            return self._contact_status(result)
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
        dashboard = result.facts.get("dashboard") or {}
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        person = dashboard.get("person_name") or "Person"
        location = dashboard.get("current_location")
        if location:
            lines.append(f"{person}: {location}.")
        status = str(assessment.get("status") or "normal")
        if status in {"green", "normal", "ok"}:
            lines.append("bei Sentero gibt es aktuell keine auffälligen Hinweise.")
        else:
            lines.append("Sentero hat Hinweise erkannt, die im Tagesverlauf auffällig wirken.")
        if dashboard.get("behavior_label"):
            lines.append(f"Verhaltensanalyse: {dashboard['behavior_label']}. {dashboard.get('behavior_summary') or ''}".strip())
        learning = dashboard.get("learning") or {}
        learning_text = _learning_sentence(learning)
        if learning_text:
            lines.append(learning_text)
        first_activity = dashboard.get("first_activity")
        if first_activity:
            lines.append(f"Aufgestanden: {_time_label(first_activity.get('event_time'))}.")
        if activity:
            lines.append(_activity_sentence(activity))
        elif dashboard.get("last_activity"):
            lines.append(_activity_sentence(dashboard["last_activity"]))
        if dashboard.get("activity_event_count_today") is not None:
            lines.append(f"Heute wurden {int(dashboard.get('activity_event_count_today') or 0)} Aktivitätsereignisse registriert.")
        slots = _activity_slots_sentence(dashboard.get("activity_slots") or [])
        if slots:
            lines.append(slots)
        if env.get("temperature_c") is not None:
            lines.append(_environment_line(env, "temperature_c", "Raumtemperatur", "°C"))
        sensor_health = dashboard.get("sensor_health")
        if sensor_health:
            lines.extend(_sensor_health_lines(sensor_health))
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
            lines.append(_environment_line(env, "temperature_c", "Temperatur", "°C"))
        if env.get("humidity_percent") is not None:
            lines.append(_environment_line(env, "humidity_percent", "Luftfeuchtigkeit", "%"))
        if env.get("illuminance_lux") is not None:
            lines.append(_environment_line(env, "illuminance_lux", "Helligkeit", "lx"))
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(line for line in lines if line)

    def _power_usage(self, result: QueryResult) -> str:
        readings = result.facts.get("readings") or []
        deltas = result.facts.get("today_deltas") or {}
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        if not readings:
            return self.build(QueryResult(result.intent, "no_data", data_available=False))
        for reading in readings:
            value = reading.get("value")
            if value is None:
                continue
            if reading.get("kind") == "power_usage":
                lines.append(f"Der aktuelle Stromverbrauch liegt bei {_decimal_label(value)} W.")
            elif reading.get("kind") == "energy_consumption":
                lines.append(f"Der Stromzählerstand liegt bei {_decimal_label(value)} kWh.")
            else:
                lines.append(f"{reading.get('label') or 'Messwert'}: {_decimal_label(value)}.")
            lines.append(_freshness_sentence(reading.get("freshness")))
        energy_delta = deltas.get("energy_consumption")
        if energy_delta is not None:
            lines.append(f"Heutiger Stromverbrauch seit dem ersten Tageswert: {_decimal_label(energy_delta)} kWh.")
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(line for line in lines if line)

    def _contact_status(self, result: QueryResult) -> str:
        contacts = result.facts.get("contacts") or []
        open_contacts = result.facts.get("open_contacts") or []
        unknown_contacts = result.facts.get("unknown_contacts") or []
        lines = ["Guten Tag,", ""]
        context = _context_sentence(result)
        if context:
            lines.append(context)
        if not contacts:
            return self.build(QueryResult(result.intent, "no_data", data_available=False))
        if open_contacts:
            lines.append(f"Nicht alle Türen oder Fenster sind als geschlossen bekannt. Offen gemeldet: {', '.join(_contact_label(item) for item in open_contacts[:6])}.")
        elif unknown_contacts:
            lines.append("Es wird aktuell kein offener Kontakt gemeldet, aber bei einzelnen Kontakten ist der letzte Zustand unklar.")
        else:
            lines.append("Alle bekannten Tür- und Fensterkontakte melden zuletzt geschlossen.")
        stale = [item for item in contacts if (item.get("freshness") or {}).get("bucket") in {"old", "stale", "unknown"}]
        if stale:
            lines.append(f"Hinweis: {len(stale)} Kontaktwerte sind nicht mehr ganz frisch und sollten nicht als Live-Zustand verstanden werden.")
        lines.extend(["", "Viele Grüße", "Sentero"])
        return "\n".join(lines)

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
• Wie hoch ist der Stromverbrauch?
• Sind alle Türen und Fenster zu?
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
    duration = _duration_label(age)
    if freshness.get("bucket") == "fresh":
        return f"Aktuell wurde Aktivität im {room} erkannt."
    if freshness.get("bucket") == "recent":
        return f"Die letzte erkannte Aktivität war {duration} im {room}."
    if freshness.get("bucket") == "stale":
        return f"Die letzte sichere Aktivität wurde {duration} im {room} erkannt. Eine aktuelle Raumzuordnung ist momentan nicht zuverlässig möglich."
    return f"Die letzte erkannte Aktivität war {duration} im {room}. Eine aktuelle Raumzuordnung ist nur eingeschränkt zuverlässig."


def _duration_label(age_seconds: Any) -> str:
    try:
        total_minutes = max(int(float(age_seconds) // 60), 0)
    except (TypeError, ValueError):
        return "vor unbekannter Zeit"
    if total_minutes < 1:
        return "gerade eben"
    if total_minutes < 60:
        return f"vor {total_minutes} {'Minute' if total_minutes == 1 else 'Minuten'}"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        if minutes == 0:
            return f"vor {hours} {'Stunde' if hours == 1 else 'Stunden'}"
        return f"vor {hours} {'Stunde' if hours == 1 else 'Stunden'} und {minutes} {'Minute' if minutes == 1 else 'Minuten'}"
    days, hours = divmod(hours, 24)
    if hours == 0:
        return f"vor {days} {'Tag' if days == 1 else 'Tagen'}"
    return f"vor {days} {'Tag' if days == 1 else 'Tagen'} und {hours} {'Stunde' if hours == 1 else 'Stunden'}"


def _learning_sentence(learning: dict[str, Any]) -> str:
    if not learning:
        return ""
    usable = learning.get("usable_days")
    required = learning.get("required_usable_days")
    if usable is not None and required is not None and not learning.get("completed"):
        remaining = learning.get("remaining_usable_days")
        suffix = f" Sentero braucht noch {remaining} verwertbare {'Tag' if remaining == 1 else 'Tage'}." if remaining is not None else ""
        return f"Lernphase: {usable} von {required} Lerntagen.{suffix}"
    day = learning.get("day")
    days = learning.get("days")
    if day and days:
        return f"Lernphase: Tag {day} von {days}."
    return ""


def _activity_slots_sentence(slots: list[dict[str, Any]]) -> str:
    active = [str(slot.get("label") or "").strip() for slot in slots if slot.get("active")]
    if not active:
        return "Tagesverlauf: bisher keine Aktivität in den Dashboard-Zeitfenstern."
    return f"Tagesverlauf: Aktivität in den Zeitfenstern {', '.join(active)} Uhr."


def _sensor_health_lines(health: dict[str, Any]) -> list[str]:
    lines = [f"Sensorstatus: {int(health.get('sensor_count') or 0)} Sensoren verbunden."]
    unreachable = health.get("unreachable") or []
    low_battery = health.get("low_battery") or []
    batteries = health.get("batteries") or []
    if unreachable:
        lines.append("Nicht erreichbar: " + "; ".join(_sensor_label(item) for item in unreachable[:5]) + ".")
    if low_battery:
        lines.append("Schwache Batterie: " + "; ".join(_sensor_label(item, include_battery=True) for item in low_battery[:5]) + ".")
    if batteries:
        lines.append("Batteriestand: " + "; ".join(_sensor_label(item, include_battery=True) for item in batteries[:6]) + ".")
    if not unreachable and not low_battery:
        lines.append("Alle überwachten Sensoren sind erreichbar; es liegen keine schwachen Batterien vor.")
    return lines


def _sensor_label(item: dict[str, Any], include_battery: bool = False) -> str:
    label = str(item.get("label") or "Sensor").strip()
    room = str(item.get("room_label") or "").strip()
    text = f"{label} ({room})" if room and room != "unbekannter Raum" else label
    battery = item.get("battery_level")
    if include_battery and isinstance(battery, (int, float)):
        text += f" {int(battery)} %"
    return text


def _environment_line(env: dict[str, Any], key: str, label: str, unit: str) -> str:
    value = _decimal_label(env.get(key))
    source = str(env.get(f"{key}_source") or "")
    freshness = _freshness_sentence(env.get(f"{key}_freshness"))
    room = str(env.get(f"{key}_room_label") or "").strip()
    room_text = f" im Raum {room}" if room and room != "unbekannter Raum" else ""
    if source == "live":
        return f"Der Sensor meldet aktuell{room_text}: {label} {value} {unit}. {freshness}".strip()
    fallback = str(env.get(f"{key}_fallback_reason") or "")
    reason = "der aktuelle Sensorzustand nicht abgerufen werden konnte"
    if fallback == "sensor_not_answering":
        reason = "der Sensor aktuell nicht antwortet"
    return f"Der letzte bekannte Wert aus der Historie{room_text}: {label} {value} {unit}. Hinweis: Dieser Wert wird aus der Historie verwendet, weil {reason}. {freshness}".strip()


def _contact_label(item: dict[str, Any]) -> str:
    label = str(item.get("role") or item.get("entity_id") or "Kontakt").replace("_", " ").strip()
    room = str(item.get("room_label") or "").strip()
    return f"{label} ({room})" if room and room != "unbekannter Raum" else label


def _decimal_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.1f}" if number % 1 else str(int(number))
    return text.replace(".", ",")


def _sentero_timezone() -> ZoneInfo:
    name = str(os.getenv("SENTERO_TIMEZONE") or os.getenv("TZ") or "Europe/Berlin").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Berlin")


def _time_label(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "noch offen"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_sentero_timezone()).strftime("%H:%M")


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
