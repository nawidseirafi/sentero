from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .config import load_agent_section
from .services.llm.factory import create_llm_client
from .services.messaging import MessagingService

from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

VALID_STATUSES = {"green", "yellow", "orange", "red"}
SYSTEM_PROMPT = """Du bist Sentero.

Du bewertest den Tagesablauf einer älteren Person.

Deine Aufgabe:
- normale Tage erkennen
- Auffälligkeiten erkennen
- Risiko einschätzen

Du stellst keine Diagnosen.
Du bewertest lediglich, ob das Verhalten vom üblichen Alltag abweicht.
Du löst keine Notrufe aus.
Du erhältst keine rohen Sensordaten, sondern nur strukturierte Kennzahlen.

Antwort nur als JSON mit:
{
  "status": "green|yellow|orange|red",
  "confidence": 0.0,
  "summary": "",
  "findings": [],
  "recommendation": "",
  "email_subject": "",
  "email_body": ""
}"""


class SenteroBehaviorAgent:
    def __init__(self, mapping: DeviceMappingService | None = None, messaging: MessagingService | None = None) -> None:
        self.mapping = mapping or DeviceMappingService()
        self.messaging = messaging or MessagingService()
        self.notifications = NotificationService(self.mapping, self.messaging)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists sentero_sensor_events (
                    id integer primary key autoincrement,
                    event_time text not null,
                    role text,
                    room text,
                    entity_id text,
                    state text,
                    device_class text,
                    source text not null default 'snapshot',
                    created_at text not null
                )"""
            )
            con.execute(
                """create table if not exists behavior_events (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    sensor_id text,
                    sensor_type text,
                    room text,
                    event_type text,
                    metadata text not null default '{}'
                )"""
            )
            con.execute(
                """create table if not exists behavior_daily_summary (
                    date text primary key,
                    wakeup_time text,
                    first_activity text,
                    last_activity text,
                    active_minutes integer not null default 0,
                    inactivity_periods text not null default '[]',
                    room_usage text not null default '{}',
                    door_events integer not null default 0,
                    occupancy_score real not null default 0,
                    anomaly_score integer not null default 0
                )"""
            )
            con.execute(
                """create table if not exists behavior_profile (
                    user_id integer primary key,
                    average_wakeup_time text,
                    average_sleep_time text,
                    average_active_minutes real not null default 0,
                    room_usage_patterns text not null default '{}',
                    normal_door_usage text not null default '{}',
                    learning_completed integer not null default 0,
                    learning_started_at text not null,
                    learning_completed_at text
                )"""
            )
            con.execute(
                """create table if not exists behavior_assessments (
                    id integer primary key autoincrement,
                    assessment_time text not null,
                    status text not null,
                    confidence real not null,
                    summary text not null,
                    findings_json text not null default '[]',
                    recommendation text not null,
                    llm_response text,
                    created_at text not null
                )"""
            )
            self._ensure_column(con, "behavior_assessments", "anomaly_score", "integer not null default 0")
            self._ensure_column(con, "behavior_assessments", "learning_completed", "integer not null default 0")
            self._ensure_column(con, "behavior_assessments", "learning_day", "integer not null default 1")
            self._ensure_column(con, "behavior_assessments", "learning_days", "integer not null default 14")
            con.commit()

    def _ensure_column(self, con: Any, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in con.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            con.execute(f"alter table {table} add column {column} {definition}")

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        self.ensure_schema()
        configured_roles = self.mapping.roles(dev=True, include_state=False)
        if not configured_roles:
            return {
                "status": "not_configured",
                "assessment": None,
                "payload": {"reason": "no_sensors_configured"},
                "dry_run": dry_run,
                "message": "Sentero wartet auf eingerichtete Sensoren. Es wurde keine KI-Auswertung gestartet und keine Benachrichtigung versendet.",
            }
        profile = self._profile()
        contacts = self._contacts()
        sensor_snapshot = self.mapping.roles(dev=True, include_state=True)
        try:
            ha_snapshot = self.mapping.snapshot()
        except Exception as exc:
            logger.info("Sentero behavior HA snapshot unavailable for presence analysis: %s", exc)
            ha_snapshot = []
        if not dry_run:
            self._record_snapshot(sensor_snapshot, ha_snapshot)
            self._cleanup_old_data()
        history = self._history(days=30)
        daily_summary = self._upsert_daily_summary(history, dry_run=dry_run)
        behavior_profile = self._update_behavior_profile(dry_run=dry_run)
        deviations = self._behavior_deviations(daily_summary, behavior_profile, sensor_snapshot)
        daily_summary["anomaly_score"] = int(deviations.get("anomaly_score") or 0)
        if not dry_run:
            self._store_daily_anomaly_score(str(daily_summary.get("date") or ""), int(daily_summary["anomaly_score"]))
        payload = self._analysis_payload(profile, contacts, sensor_snapshot, history, ha_snapshot, daily_summary, behavior_profile, deviations)
        assessment = self._assess(payload)
        assessment = self._apply_learning_policy(assessment, payload)
        stored = assessment if dry_run else self._store_assessment(assessment)
        if not dry_run:
            self._notify_if_needed(stored, contacts)
        return {
            "status": stored["status"],
            "assessment": stored,
            "payload": payload,
            "dry_run": dry_run,
        }

    def latest(self) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.mapping.connect() as con:
            row = con.execute("select * from behavior_assessments order by assessment_time desc, id desc limit 1").fetchone()
        return self._row_to_assessment(row) if row else None

    def learning_status(self) -> dict[str, Any]:
        profile = self._update_behavior_profile(dry_run=True)
        return profile.get("learning") or {"completed": False, "day": 1, "days": self._learning_days(), "remaining_days": self._learning_days() - 1}

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self.mapping.connect() as con:
            rows = con.execute("select * from behavior_assessments order by assessment_time desc, id desc limit ?", (limit,)).fetchall()
        return [self._row_to_assessment(row) for row in rows]

    def timeline_today(self) -> dict[str, Any]:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        events = [event for event in self._history(days=1) if self._parse_time(event.get("event_time")) >= start]
        return {
            "events": events,
            "assessment": self.latest(),
        }

    def _profile(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from sentero_profile where id = 1").fetchone()
        data = dict(row) if row else {}
        notes = str(data.get("notes") or "").strip()
        data["notes_list"] = [part.strip() for part in re.split(r"[\n,;]+", notes) if part.strip()]
        return data

    def _contacts(self) -> list[dict[str, Any]]:
        with self.mapping.connect() as con:
            rows = con.execute("select * from trusted_contacts where active = 1 order by id").fetchall()
        return [dict(row) for row in rows]

    def _record_snapshot(self, roles: list[dict[str, Any]], ha_snapshot: list[dict[str, Any]] | None = None) -> None:
        timestamp = now()
        extra_events = self._fp300_snapshot_events(roles, ha_snapshot or [], timestamp)
        with self.mapping.connect() as con:
            for role in [*roles, *extra_events]:
                state = role.get("state")
                if state in (None, "", "unknown", "unavailable"):
                    continue
                con.execute(
                    """insert into sentero_sensor_events
                       (event_time, role, room, entity_id, state, device_class, source, created_at)
                       values (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        role.get("last_changed") or role.get("last_updated") or timestamp,
                        role.get("role"),
                        role.get("room"),
                        role.get("entity_id"),
                        state,
                        role.get("device_class"),
                        role.get("source") or "snapshot",
                        timestamp,
                    ),
                )
                con.execute(
                    """insert into behavior_events
                       (timestamp, sensor_id, sensor_type, room, event_type, metadata)
                       values (?, ?, ?, ?, ?, ?)""",
                    (
                        role.get("last_changed") or role.get("last_updated") or timestamp,
                        role.get("entity_id") or role.get("role"),
                        role.get("device_class") or role.get("type") or role.get("domain"),
                        role.get("room"),
                        self._event_type(role),
                        json.dumps({
                            "role": role.get("role"),
                            "state": state,
                            "source": role.get("source") or "snapshot",
                        }, ensure_ascii=False),
                    ),
                )
            con.commit()

    def _history(self, days: int) -> list[dict[str, Any]]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.mapping.connect() as con:
            rows = con.execute(
                "select * from sentero_sensor_events where event_time >= ? order by event_time asc",
                (since,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _analysis_payload(
        self,
        profile: dict[str, Any],
        contacts: list[dict[str, Any]],
        sensor_snapshot: list[dict[str, Any]],
        history: list[dict[str, Any]],
        ha_snapshot: list[dict[str, Any]],
        daily_summary: dict[str, Any],
        behavior_profile: dict[str, Any],
        deviations: dict[str, Any],
    ) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        today_events = [event for event in history if self._parse_time(event.get("event_time")).date() == today]
        previous_events = [event for event in history if self._parse_time(event.get("event_time")).date() != today]
        learning = behavior_profile.get("learning") or {}
        return {
            "learning_completed": bool(learning.get("completed")),
            "learning": learning,
            "anomaly_score": deviations.get("anomaly_score", 0),
            "wakeup_deviation_minutes": deviations.get("wakeup_deviation_minutes"),
            "active_minutes_change_percent": deviations.get("active_minutes_change_percent"),
            "night_activity": deviations.get("night_activity", False),
            "door_usage_change": deviations.get("door_usage_change", False),
            "daily_summary": daily_summary,
            "behavior_profile": {
                "average_wakeup_time": behavior_profile.get("average_wakeup_time"),
                "average_sleep_time": behavior_profile.get("average_sleep_time"),
                "average_active_minutes": behavior_profile.get("average_active_minutes"),
                "room_usage_patterns": behavior_profile.get("room_usage_patterns") or {},
                "normal_door_usage": behavior_profile.get("normal_door_usage") or {},
            },
            "profile": {
                "name": profile.get("name"),
                "age": profile.get("age"),
                "living_alone": True,
                "mobility": None,
                "notes": profile.get("notes_list") or [],
            },
            "trusted_contacts": [{"name": item.get("name"), "relationship": item.get("relationship"), "email": item.get("email")} for item in contacts],
            "daily_profile": self._daily_profile(previous_events),
            "current_day": self._day_summary(today_events),
            "sensor_context": {
                "configured_sensors": len(sensor_snapshot),
                "rooms": sorted({str(item.get("room")) for item in sensor_snapshot if item.get("room")}),
            },
            "deviations": {**self._deviations(today_events, previous_events, sensor_snapshot), **deviations},
            "safety_rules": {
                "no_medical_diagnosis": True,
                "no_emergency_calls": True,
                "only_behavioral_anomaly_detection": True,
                "presence_sensor_limits": [
                    "Aqara FP300 erkennt Anwesenheit und Bewegung, aber keine Atmung.",
                    "Aqara FP300 unterscheidet Sitzen und Liegen nicht zuverlässig.",
                    "Aqara FP300 ist kein Sturzsensor und darf nicht als medizinisches Signal bewertet werden.",
                ],
            },
        }

    def _assess(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            client = create_llm_client()
            response = client.generate(
                prompt=(
                    "Du bist behavior_analysis_agent. Bewerte ausschließlich die folgenden strukturierten Kennzahlen. "
                    "Nutze das gelernte persönliche Normalverhalten stärker als feste Grenzwerte. "
                    "Während learning_completed=false nur zurückhaltende Hinweise formulieren und keine roten Alarme erzeugen. "
                    "Presence-Sensor-Auswertungen sind Näherungen: niemals Atmung, Sturz, Schlaf, Körperposition oder Krankheiten behaupten. "
                    "Formuliere für Angehörige einfach und ruhig. Erzeuge einen menschenfreundlichen E-Mail-Text nur für orange/red, sonst leer lassen.\n\n"
                    f"{json.dumps(payload, ensure_ascii=False)}"
                ),
                system=SYSTEM_PROMPT,
            )
            raw = self._extract_json(response.text)
            return self._validate_assessment(raw, response.text)
        except Exception as exc:
            logger.info("Sentero behavior LLM unavailable, using heuristic fallback: %s", exc)
            fallback = self._heuristic_assessment(payload)
            fallback["llm_response"] = json.dumps({"fallback_reason": str(exc)}, ensure_ascii=False)
            return fallback

    def _heuristic_assessment(self, payload: dict[str, Any]) -> dict[str, Any]:
        deviations = payload.get("deviations") or {}
        score = int(payload.get("anomaly_score") or deviations.get("anomaly_score") or 0)
        findings = []
        status = self._status_from_score(score)
        if deviations.get("insufficient_data"):
            return {
                "assessment_time": now(),
                "status": "green",
                "confidence": 0.4,
                "summary": "Es liegen noch nicht genug Sensordaten für eine verlässliche Tagesbewertung vor.",
                "findings": ["Sentero sammelt zunächst Sensorhistorie, um Routinen zu lernen."],
                "recommendation": "Keine Aktion erforderlich.",
                "anomaly_score": score,
                "email_subject": "",
                "email_body": "",
                "llm_response": "",
            }
        if deviations.get("wakeup_deviation_minutes", 0) >= 60:
            findings.append("Der Tag begann deutlich später als gewöhnlich.")
        if deviations.get("active_minutes_change_percent", 0) <= -35:
            findings.append("Die Aktivität lag deutlich unter dem üblichen Niveau.")
        if deviations.get("night_activity"):
            findings.append("In der Nacht wurde ungewöhnliche Aktivität erkannt.")
        if deviations.get("door_usage_change"):
            findings.append("Die Türnutzung wich vom gewohnten Muster ab.")
        if deviations.get("no_activity_today"):
            findings.append("Heute wurde bisher keine Sensoraktivität erkannt.")
        if deviations.get("inactive_hours", 0) >= 8:
            findings.append("Es gibt eine ungewöhnlich lange Phase ohne erkannte Aktivität.")
        elif deviations.get("inactive_hours", 0) >= 5:
            findings.append("Es gibt eine längere Phase ohne erkannte Aktivität.")
        summary_by_status = {
            "green": "Der heutige Tagesablauf entspricht dem gewohnten Muster.",
            "yellow": "Heute gibt es kleine Abweichungen vom gewohnten Tagesablauf.",
            "orange": "Der Tagesablauf weicht deutlich vom gewohnten Muster ab.",
            "red": "Es liegt eine deutliche Auffälligkeit vor. Bitte prüfen Sie die Situation.",
        }
        recommendation = "Keine Aktion erforderlich." if status == "green" else "Bitte kurz nachfragen, ob alles in Ordnung ist."
        return {
            "assessment_time": now(),
            "status": status,
            "confidence": 0.72 if findings else 0.62,
            "summary": summary_by_status[status],
            "findings": findings,
            "recommendation": recommendation,
            "anomaly_score": score,
            "email_subject": "Sentero Hinweis zum Tagesablauf" if status in {"orange", "red"} else "",
            "email_body": self._email_body(summary_by_status[status], findings, recommendation) if status in {"orange", "red"} else "",
            "llm_response": "",
        }

    def _store_assessment(self, assessment: dict[str, Any]) -> dict[str, Any]:
        timestamp = assessment.get("assessment_time") or now()
        with self.mapping.connect() as con:
            cur = con.execute(
                """insert into behavior_assessments
                   (assessment_time, status, confidence, summary, findings_json, recommendation, llm_response, created_at,
                    anomaly_score, learning_completed, learning_day, learning_days)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    assessment["status"],
                    float(assessment.get("confidence") or 0),
                    assessment.get("summary") or "",
                    json.dumps(assessment.get("findings") or [], ensure_ascii=False),
                    assessment.get("recommendation") or "",
                    assessment.get("llm_response") or json.dumps(assessment, ensure_ascii=False),
                    now(),
                    int(assessment.get("anomaly_score") or 0),
                    int(bool(assessment.get("learning_completed"))),
                    int(assessment.get("learning_day") or 1),
                    int(assessment.get("learning_days") or self._learning_days()),
                ),
            )
            con.commit()
            row = con.execute("select * from behavior_assessments where id = ?", (int(cur.lastrowid),)).fetchone()
        stored = self._row_to_assessment(row)
        stored["email_subject"] = assessment.get("email_subject") or ""
        stored["email_body"] = assessment.get("email_body") or ""
        return stored

    def _notify_if_needed(self, assessment: dict[str, Any], contacts: list[dict[str, Any]]) -> None:
        if not self.mapping.roles(dev=True, include_state=False):
            logger.info("Sentero notification skipped because no sensors are configured")
            return
        if not assessment.get("learning_completed", True):
            logger.info("Sentero notification skipped because behavior profile is still learning")
            return
        status = assessment.get("status")
        if status not in {"orange", "red"}:
            return
        severity = "critical" if status == "red" else "warning"
        self.messaging.create_message(
            source="sentero",
            category="behavior",
            severity=severity,
            title="Sentero Tagesablauf prüfen",
            message=assessment.get("email_body") or assessment.get("summary") or "",
            payload={
                "assessment_id": assessment.get("id"),
                "status": status,
                "contacts": [{"name": item.get("name"), "email": item.get("email")} for item in contacts],
                "email_subject": assessment.get("email_subject") or "Sentero Hinweis",
            },
        )
        self.notifications.notify_assessment(assessment, contacts)

    def _learning_days(self) -> int:
        config = load_agent_section("sentero")
        behavior = config.get("behavior") if isinstance(config.get("behavior"), dict) else {}
        raw = behavior.get("learning_days", config.get("learning_days", 14))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 14
        return max(7, min(value, 30))

    def _behavior_profile_row(self) -> dict[str, Any]:
        self.ensure_schema()
        with self.mapping.connect() as con:
            row = con.execute("select * from behavior_profile where user_id = 1").fetchone()
            if not row:
                timestamp = now()
                con.execute(
                    "insert into behavior_profile (user_id, learning_started_at) values (1, ?)",
                    (timestamp,),
                )
                con.commit()
                row = con.execute("select * from behavior_profile where user_id = 1").fetchone()
        return self._profile_row_to_dict(row)

    def _upsert_daily_summary(self, history: list[dict[str, Any]], dry_run: bool = False) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        events = [event for event in history if self._parse_time(event.get("event_time")).date() == today]
        summary = self._build_daily_summary(today, events)
        if not dry_run:
            with self.mapping.connect() as con:
                con.execute(
                    """insert into behavior_daily_summary
                       (date, wakeup_time, first_activity, last_activity, active_minutes, inactivity_periods, room_usage, door_events, occupancy_score, anomaly_score)
                       values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       on conflict(date) do update set
                         wakeup_time = excluded.wakeup_time,
                         first_activity = excluded.first_activity,
                         last_activity = excluded.last_activity,
                         active_minutes = excluded.active_minutes,
                         inactivity_periods = excluded.inactivity_periods,
                         room_usage = excluded.room_usage,
                         door_events = excluded.door_events,
                         occupancy_score = excluded.occupancy_score,
                         anomaly_score = excluded.anomaly_score""",
                    (
                        summary["date"],
                        summary.get("wakeup_time"),
                        summary.get("first_activity"),
                        summary.get("last_activity"),
                        int(summary.get("active_minutes") or 0),
                        json.dumps(summary.get("inactivity_periods") or [], ensure_ascii=False),
                        json.dumps(summary.get("room_usage") or {}, ensure_ascii=False),
                        int(summary.get("door_events") or 0),
                        float(summary.get("occupancy_score") or 0),
                        int(summary.get("anomaly_score") or 0),
                    ),
                )
                con.commit()
        return summary

    def _update_behavior_profile(self, dry_run: bool = False) -> dict[str, Any]:
        row = self._behavior_profile_row()
        learning_days = self._learning_days()
        started = self._parse_time(row.get("learning_started_at"))
        learning_day = max(1, min(learning_days, (datetime.now(timezone.utc).date() - started.date()).days + 1))
        with self.mapping.connect() as con:
            rows = con.execute("select * from behavior_daily_summary order by date asc").fetchall()
        summaries = [self._summary_row_to_dict(item) for item in rows]
        completed = bool(row.get("learning_completed")) or learning_day >= learning_days
        usable = [item for item in summaries if item.get("active_minutes") or item.get("first_activity")]
        average_wakeup = self._average_time([item.get("wakeup_time") for item in usable])
        average_sleep = self._average_time([item.get("last_activity") for item in usable])
        average_active = round(sum(float(item.get("active_minutes") or 0) for item in usable) / len(usable), 2) if usable else 0
        room_usage = self._average_room_usage(usable)
        door_usage = self._normal_door_usage(usable)
        completed_at = row.get("learning_completed_at") or (now() if completed else None)
        if not dry_run:
            with self.mapping.connect() as con:
                con.execute(
                    """update behavior_profile set
                         average_wakeup_time = ?,
                         average_sleep_time = ?,
                         average_active_minutes = ?,
                         room_usage_patterns = ?,
                         normal_door_usage = ?,
                         learning_completed = ?,
                         learning_completed_at = ?
                       where user_id = 1""",
                    (
                        average_wakeup,
                        average_sleep,
                        average_active,
                        json.dumps(room_usage, ensure_ascii=False),
                        json.dumps(door_usage, ensure_ascii=False),
                        int(completed),
                        completed_at,
                    ),
                )
                con.commit()
        return {
            "user_id": 1,
            "average_wakeup_time": average_wakeup,
            "average_sleep_time": average_sleep,
            "average_active_minutes": average_active,
            "room_usage_patterns": room_usage,
            "normal_door_usage": door_usage,
            "learning_started_at": row.get("learning_started_at"),
            "learning_completed_at": completed_at,
            "learning": {
                "completed": completed,
                "day": learning_day,
                "days": learning_days,
                "remaining_days": max(0, learning_days - learning_day),
            },
        }

    def _behavior_deviations(self, summary: dict[str, Any], profile: dict[str, Any], roles: list[dict[str, Any]]) -> dict[str, Any]:
        learning = profile.get("learning") or {}
        score = 0
        wakeup_deviation = self._minute_deviation(summary.get("wakeup_time"), profile.get("average_wakeup_time"))
        if wakeup_deviation >= 90:
            score += 10
        active_change = self._percent_change(float(summary.get("active_minutes") or 0), float(profile.get("average_active_minutes") or 0))
        if active_change <= -35:
            score += 15
        night_activity = self._has_night_activity(summary)
        if night_activity:
            score += 20
        longest_inactivity = max([int(item.get("minutes") or 0) for item in summary.get("inactivity_periods", [])], default=0)
        if longest_inactivity >= 8 * 60:
            score += 50
        elif longest_inactivity >= 5 * 60:
            score += 25
        door_usage_change = self._door_usage_change(summary, profile.get("normal_door_usage") or {})
        if door_usage_change:
            score += 30
        if int(summary.get("active_minutes") or 0) == 0 and roles:
            score += 25
        score = max(0, min(100, score))
        return {
            "anomaly_score": score,
            "severity": self._status_from_score(score),
            "wakeup_deviation_minutes": wakeup_deviation,
            "active_minutes_change_percent": active_change,
            "night_activity": night_activity,
            "door_usage_change": door_usage_change,
            "longest_inactivity_minutes": longest_inactivity,
            "learning_completed": bool(learning.get("completed")),
            "learning_day": int(learning.get("day") or 1),
            "learning_days": int(learning.get("days") or self._learning_days()),
        }

    def _apply_learning_policy(self, assessment: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        learning = payload.get("learning") or {}
        score = int(payload.get("anomaly_score") or assessment.get("anomaly_score") or 0)
        status = str(assessment.get("status") or self._status_from_score(score))
        if not learning.get("completed") and status in {"orange", "red"}:
            status = "yellow"
            assessment["summary"] = "Sentero lernt aktuell den gewohnten Tagesablauf kennen. Es gibt erste Hinweise, aber noch keine abschließende Bewertung."
            assessment["recommendation"] = "Keine dringende Aktion erforderlich. Beobachten Sie den Verlauf weiter."
            assessment["email_subject"] = ""
            assessment["email_body"] = ""
        assessment["status"] = status
        assessment["anomaly_score"] = score
        assessment["learning_completed"] = bool(learning.get("completed"))
        assessment["learning_day"] = int(learning.get("day") or 1)
        assessment["learning_days"] = int(learning.get("days") or self._learning_days())
        return assessment

    def _build_daily_summary(self, day: date, events: list[dict[str, Any]]) -> dict[str, Any]:
        parsed = sorted((self._parse_time(event.get("event_time")), event) for event in events)
        activity_times = [event_time for event_time, event in parsed if self._is_activity_event(event)]
        room_usage = Counter(str(event.get("room") or "unknown") for _, event in parsed if self._is_activity_event(event))
        first = activity_times[0] if activity_times else None
        last = activity_times[-1] if activity_times else None
        inactivity_periods = self._inactivity_periods(activity_times)
        active_minutes = self._active_minutes(activity_times)
        door_events = sum(1 for _, event in parsed if self._is_door_event(event))
        occupancy_score = min(100, round(active_minutes / 6, 1)) if active_minutes else 0
        return {
            "date": day.isoformat(),
            "wakeup_time": self._time_string(first) if first else None,
            "first_activity": first.isoformat(timespec="seconds") if first else None,
            "last_activity": last.isoformat(timespec="seconds") if last else None,
            "active_minutes": active_minutes,
            "inactivity_periods": inactivity_periods,
            "room_usage": dict(room_usage),
            "door_events": door_events,
            "occupancy_score": occupancy_score,
            "anomaly_score": 0,
        }

    def _cleanup_old_data(self) -> None:
        events_before = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
        summaries_before = (datetime.now(timezone.utc) - timedelta(days=730)).date().isoformat()
        with self.mapping.connect() as con:
            con.execute("delete from behavior_events where timestamp < ?", (events_before,))
            con.execute("delete from sentero_sensor_events where event_time < ?", (events_before,))
            con.execute("delete from behavior_daily_summary where date < ?", (summaries_before,))
            con.commit()

    def _store_daily_anomaly_score(self, day: str, score: int) -> None:
        if not day:
            return
        with self.mapping.connect() as con:
            con.execute("update behavior_daily_summary set anomaly_score = ? where date = ?", (score, day))
            con.commit()

    def _profile_row_to_dict(self, row: Any) -> dict[str, Any]:
        if not row:
            return {}
        data = dict(row)
        data["room_usage_patterns"] = self._json_dict(data.get("room_usage_patterns"))
        data["normal_door_usage"] = self._json_dict(data.get("normal_door_usage"))
        data["learning_completed"] = bool(data.get("learning_completed"))
        return data

    def _summary_row_to_dict(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["inactivity_periods"] = self._json_list(data.get("inactivity_periods"))
        data["room_usage"] = self._json_dict(data.get("room_usage"))
        return data

    def _average_time(self, values: list[Any]) -> str | None:
        minutes = []
        for value in values:
            parsed = self._minutes_of_day(value)
            if parsed is not None:
                minutes.append(parsed)
        if not minutes:
            return None
        average = round(sum(minutes) / len(minutes))
        return f"{average // 60:02d}:{average % 60:02d}"

    def _average_room_usage(self, summaries: list[dict[str, Any]]) -> dict[str, float]:
        totals: Counter[str] = Counter()
        for summary in summaries:
            totals.update({room: int(count or 0) for room, count in (summary.get("room_usage") or {}).items()})
        total = sum(totals.values())
        if not total:
            return {}
        return {room: round(count / total, 3) for room, count in totals.items()}

    def _normal_door_usage(self, summaries: list[dict[str, Any]]) -> dict[str, float]:
        if not summaries:
            return {"average_daily_events": 0}
        return {"average_daily_events": round(sum(int(item.get("door_events") or 0) for item in summaries) / len(summaries), 2)}

    def _minute_deviation(self, current: Any, expected: Any) -> int:
        current_minutes = self._minutes_of_day(current)
        expected_minutes = self._minutes_of_day(expected)
        if current_minutes is None or expected_minutes is None:
            return 0
        return abs(current_minutes - expected_minutes)

    def _percent_change(self, current: float, expected: float) -> int:
        if expected <= 0:
            return 0
        return round(((current - expected) / expected) * 100)

    def _has_night_activity(self, summary: dict[str, Any]) -> bool:
        for period in summary.get("inactivity_periods") or []:
            if str(period.get("label") or "") == "night_activity":
                return True
        first = self._parse_time(summary.get("first_activity")) if summary.get("first_activity") else None
        last = self._parse_time(summary.get("last_activity")) if summary.get("last_activity") else None
        return bool((first and first.hour < 5) or (last and last.hour >= 23))

    def _door_usage_change(self, summary: dict[str, Any], normal: dict[str, Any]) -> bool:
        average = float(normal.get("average_daily_events") or 0)
        current = int(summary.get("door_events") or 0)
        return average > 0 and current >= max(average * 2.5, average + 3)

    def _event_type(self, event: dict[str, Any]) -> str:
        if self._is_door_event(event):
            return "door"
        if self._is_presence_role(event):
            return "presence"
        if self._is_motion_entity(event):
            return "motion"
        return "sensor_state"

    def _is_activity_event(self, event: dict[str, Any]) -> bool:
        return self._is_on(event.get("state")) and (self._is_presence_role(event) or self._is_motion_entity(event) or self._is_door_event(event))

    def _is_door_event(self, event: dict[str, Any]) -> bool:
        text = self._entity_text(event)
        return "door" in text or "tuer" in text or "tür" in text or self._device_class(event) in {"door", "opening"}

    def _active_minutes(self, times: list[datetime]) -> int:
        if not times:
            return 0
        buckets = {item.replace(second=0, microsecond=0) for item in times}
        return len(buckets)

    def _inactivity_periods(self, times: list[datetime]) -> list[dict[str, Any]]:
        periods: list[dict[str, Any]] = []
        for previous, current in zip(times, times[1:]):
            minutes = int((current - previous).total_seconds() / 60)
            if minutes >= 180:
                periods.append({"from": previous.isoformat(timespec="minutes"), "to": current.isoformat(timespec="minutes"), "minutes": minutes})
        if times:
            for item in times:
                if item.hour < 5 or item.hour >= 23:
                    periods.append({"label": "night_activity", "time": item.isoformat(timespec="minutes"), "minutes": 0})
                    break
        return periods

    def _minutes_of_day(self, value: Any) -> int | None:
        if not value:
            return None
        text = str(value)
        match = re.search(r"(\d{1,2}):(\d{2})", text)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _time_string(self, value: datetime) -> str:
        local_time = value.timetz() if value.tzinfo else value.replace(tzinfo=timezone.utc).timetz()
        return time(local_time.hour, local_time.minute).strftime("%H:%M")

    def _status_from_score(self, score: int) -> str:
        if score <= 20:
            return "green"
        if score <= 40:
            return "yellow"
        if score <= 70:
            return "orange"
        return "red"

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def _daily_profile(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        by_room: dict[str, list[int]] = defaultdict(list)
        for event in events:
            room = str(event.get("room") or "unknown")
            by_room[room].append(self._parse_time(event.get("event_time")).hour)
        return {
            room: {
                "common_hours": [hour for hour, _ in Counter(hours).most_common(5)],
                "event_count": len(hours),
            }
            for room, hours in by_room.items()
        }

    def _day_summary(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        activity_times = [self._parse_time(event.get("event_time")) for event in events if self._is_activity_event(event)]
        return {
            "event_count": len(events),
            "rooms": dict(Counter(str(event.get("room") or "unknown") for event in events)),
            "first_activity": self._time_string(min(activity_times)) if activity_times else None,
            "last_activity": self._time_string(max(activity_times)) if activity_times else None,
        }

    def _deviations(self, today_events: list[dict[str, Any]], previous_events: list[dict[str, Any]], roles: list[dict[str, Any]]) -> dict[str, Any]:
        historical_days = max(1, len({self._parse_time(event.get("event_time")).date() for event in previous_events}))
        average_events = len(previous_events) / historical_days if previous_events else 0
        ratio = (len(today_events) / average_events) if average_events else 1 if today_events else 0
        latest = max((self._parse_time(role.get("last_changed") or role.get("last_updated") or role.get("updated_at")) for role in roles if role.get("last_changed") or role.get("last_updated") or role.get("updated_at")), default=None)
        inactive_hours = ((datetime.now(timezone.utc) - latest).total_seconds() / 3600) if latest else 0
        return {
            "insufficient_data": not roles and not today_events and not previous_events,
            "no_activity_today": len(today_events) == 0,
            "today_event_count": len(today_events),
            "historical_daily_average": round(average_events, 2),
            "activity_ratio": round(ratio, 2),
            "inactive_hours": round(inactive_hours, 2),
        }

    def _fp300_snapshot_events(self, roles: list[dict[str, Any]], ha_snapshot: list[dict[str, Any]], timestamp: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for role in roles:
            if not self._is_presence_role(role):
                continue
            related = self._related_presence_entities(role, ha_snapshot)
            for kind, item in related.items():
                if not item:
                    continue
                events.append({
                    "role": f"{role.get('role')}_{kind}",
                    "room": role.get("room"),
                    "entity_id": item.get("entity_id"),
                    "state": item.get("state"),
                    "device_class": item.get("device_class"),
                    "source": "fp300_snapshot",
                    "last_changed": item.get("last_changed") or timestamp,
                    "last_updated": item.get("last_updated") or timestamp,
                })
        return events

    def _fp300_analysis(
        self,
        roles: list[dict[str, Any]],
        ha_snapshot: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        devices = []
        for role in roles:
            if not self._is_presence_role(role):
                continue
            related = self._related_presence_entities(role, ha_snapshot)
            presence = related.get("presence")
            motion = related.get("motion")
            devices.append({
                "room": role.get("room"),
                "role": role.get("role"),
                "device_model": role.get("model"),
                "manufacturer": role.get("manufacturer"),
                "presence_entity": self._entity_compact(presence),
                "motion_entity": self._entity_compact(motion),
                "illuminance_entity": self._entity_compact(related.get("illuminance")),
                "temperature_entity": self._entity_compact(related.get("temperature")),
                "humidity_entity": self._entity_compact(related.get("humidity")),
                "battery_entity": self._entity_compact(related.get("battery")),
                "current": self._presence_current_metrics(presence, motion),
                "measurements": self._presence_measurements(related),
                "today": self._presence_history_metrics(history, role.get("room"), days=1),
                "history_30d": self._presence_history_metrics(history, role.get("room"), days=30),
            })
        return {
            "sensor_family": "Aqara FP300 compatible presence sensor",
            "capabilities": {
                "presence": True,
                "pir_motion": True,
                "illuminance": True,
                "temperature": True,
                "humidity": True,
                "battery": True,
                "presence_duration_calculable": True,
                "stillness_duration_calculable": True,
                "breathing_detection": False,
                "fall_detection": False,
                "sleep_detection": False,
                "posture_detection": False,
                "people_counting": False,
                "zone_tracking": False,
            },
            "interpretation_notes": [
                "presence=true und motion=false über längere Zeit bedeutet nur: Person ist im Raum und bewegt sich kaum.",
                "Stillstand kann Sitzen, Liegen oder ruhiges Verhalten bedeuten und ist keine medizinische Aussage.",
                "Atmung, Sturz, Schlaf und Körperposition dürfen aus diesen Daten nicht abgeleitet werden.",
            ],
            "devices": devices,
        }

    def _related_presence_entities(self, role: dict[str, Any], ha_snapshot: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
        role_entity = str(role.get("entity_id") or "")
        device_id = str(role.get("device_id") or "").strip()
        same_device = [item for item in ha_snapshot if device_id and str(item.get("device_id") or "") == device_id]
        if not same_device:
            prefix = role_entity.rsplit("_", 1)[0] if "_" in role_entity else role_entity.rsplit(".", 1)[-1]
            same_device = [item for item in ha_snapshot if prefix and str(item.get("entity_id") or "").startswith(prefix)]
        return {
            "presence": self._best_entity(same_device, self._is_presence_entity) or (role if self._is_presence_entity(role) else None),
            "motion": self._best_entity(same_device, self._is_motion_entity),
            "illuminance": self._best_entity(same_device, lambda item: self._device_class(item) == "illuminance" or "illuminance" in self._entity_text(item)),
            "temperature": self._best_entity(same_device, lambda item: self._device_class(item) == "temperature" or self._entity_id(item).endswith(("_temperature", "_temperatur"))),
            "humidity": self._best_entity(same_device, lambda item: self._device_class(item) == "humidity" or self._entity_id(item).endswith(("_humidity", "_luftfeuchtigkeit"))),
            "battery": self._best_entity(same_device, lambda item: self._entity_id(item).endswith(("_battery", "_batterie"))),
        }

    def _presence_measurements(self, related: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
        presence = related.get("presence")
        motion = related.get("motion")
        illuminance = related.get("illuminance")
        temperature = related.get("temperature")
        humidity = related.get("humidity")
        battery = related.get("battery")
        return {
            "presence": self._boolean_measurement(presence),
            "pir_motion": self._boolean_measurement(motion),
            "illuminance_lux": self._numeric_measurement(illuminance),
            "temperature_celsius": self._numeric_measurement(temperature),
            "humidity_percent": self._numeric_measurement(humidity),
            "battery_percent": self._numeric_measurement(battery),
        }

    def _presence_current_metrics(self, presence: dict[str, Any] | None, motion: dict[str, Any] | None) -> dict[str, Any]:
        presence_active = self._is_on(presence.get("state") if presence else None)
        motion_active = self._is_on(motion.get("state") if motion else None)
        presence_since = self._parse_time(presence.get("last_changed") or presence.get("last_updated")) if presence else None
        motion_since = self._parse_time(motion.get("last_changed") or motion.get("last_updated")) if motion else None
        current_time = datetime.now(timezone.utc)
        presence_duration = int((current_time - presence_since).total_seconds()) if presence_active and presence_since else 0
        stillness_since = max([value for value in [presence_since, motion_since] if value], default=None)
        stillness_duration = int((current_time - stillness_since).total_seconds()) if presence_active and not motion_active and stillness_since else 0
        return {
            "presence_active": presence_active,
            "motion_active": motion_active,
            "presence_duration_seconds": max(presence_duration, 0),
            "stillness_duration_seconds": max(stillness_duration, 0),
            "interpretation": "person_present_but_still" if presence_active and not motion_active else "motion_detected" if motion_active else "not_present",
        }

    def _presence_history_metrics(self, history: list[dict[str, Any]], room: Any, days: int) -> dict[str, Any]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        events = [
            event for event in history
            if event.get("room") == room
            and str(event.get("source") or "") == "fp300_snapshot"
            and self._parse_time(event.get("event_time")) >= since
        ]
        presence_events = [event for event in events if str(event.get("role") or "").endswith("_presence")]
        motion_events = [event for event in events if str(event.get("role") or "").endswith("_motion")]
        presence_active_count = sum(1 for event in presence_events if self._is_on(event.get("state")))
        motion_active_count = sum(1 for event in motion_events if self._is_on(event.get("state")))
        still_count = max(presence_active_count - motion_active_count, 0)
        return {
            "sample_count": len(events),
            "presence_samples": len(presence_events),
            "motion_samples": len(motion_events),
            "presence_active_samples": presence_active_count,
            "motion_active_samples": motion_active_count,
            "stillness_samples": still_count,
            "stillness_ratio": round(still_count / presence_active_count, 2) if presence_active_count else 0,
        }

    def _entity_compact(self, item: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item:
            return None
        return {
            "entity_id": item.get("entity_id"),
            "name": item.get("friendly_name") or item.get("label") or item.get("original_name"),
            "state": item.get("state"),
            "numeric_value": self._number(item.get("state")),
            "unit": item.get("unit") or item.get("unit_of_measurement"),
            "device_class": item.get("device_class"),
            "last_changed": item.get("last_changed"),
            "last_updated": item.get("last_updated"),
        }

    def _boolean_measurement(self, item: dict[str, Any] | None) -> dict[str, Any]:
        compact = self._entity_compact(item)
        return {
            "active": self._is_on(item.get("state") if item else None),
            "entity": compact,
        }

    def _numeric_measurement(self, item: dict[str, Any] | None) -> dict[str, Any]:
        compact = self._entity_compact(item)
        return {
            "value": self._number(item.get("state") if item else None),
            "unit": (item.get("unit") or item.get("unit_of_measurement")) if item else None,
            "entity": compact,
        }

    def _best_entity(self, items: list[dict[str, Any]], predicate: Any) -> dict[str, Any] | None:
        matches = [item for item in items if predicate(item)]
        return sorted(matches, key=lambda item: (self._entity_id(item).startswith("binary_sensor."), self._parse_time(item.get("last_updated")).timestamp()), reverse=True)[0] if matches else None

    def _is_presence_role(self, role: dict[str, Any]) -> bool:
        text = self._entity_text(role)
        return str(role.get("role") or "").endswith("presence") or self._is_presence_entity(role) or "occupy" in text

    def _is_presence_entity(self, item: dict[str, Any]) -> bool:
        dc = self._device_class(item)
        text = self._entity_text(item)
        return dc in {"occupancy", "presence"} or any(term in text for term in ["presence", "praesenz", "präsenz", "occupancy", "occupy"])

    def _is_motion_entity(self, item: dict[str, Any]) -> bool:
        dc = self._device_class(item)
        text = self._entity_text(item)
        return dc == "motion" or any(term in text for term in ["motion", "bewegung", "pir_detection", "pir detection", "pir"])

    @staticmethod
    def _device_class(item: dict[str, Any]) -> str:
        return str(item.get("device_class") or "").lower()

    @staticmethod
    def _entity_id(item: dict[str, Any]) -> str:
        return str(item.get("entity_id") or "").lower()

    @staticmethod
    def _entity_text(item: dict[str, Any]) -> str:
        return " ".join(str(item.get(key) or "").lower() for key in ["entity_id", "friendly_name", "label", "original_name", "device_name", "model"])

    @staticmethod
    def _is_on(value: Any) -> bool:
        return str(value or "").strip().lower() in {"on", "true", "detected", "occupied", "home", "present", "1"}

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(str(value).replace("%", "").replace(",", ".").strip())
        except ValueError:
            return None

    def _compact_roles(self, roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "role": role.get("role"),
                "room": role.get("room"),
                "label": role.get("label") or role.get("friendly_name"),
                "state": role.get("state"),
                "reachable": role.get("reachable"),
                "last_changed": role.get("last_changed"),
                "last_updated": role.get("last_updated"),
                "device_class": role.get("device_class"),
            }
            for role in roles
        ]

    def _validate_assessment(self, data: dict[str, Any], raw_text: str) -> dict[str, Any]:
        status = str(data.get("status") or "green").lower()
        if status not in VALID_STATUSES:
            status = "yellow"
        confidence = max(0.0, min(float(data.get("confidence") or 0.0), 1.0))
        return {
            "assessment_time": now(),
            "status": status,
            "confidence": confidence,
            "summary": str(data.get("summary") or "Sentero hat den Tagesablauf bewertet."),
            "findings": self._list(data.get("findings")),
            "recommendation": str(data.get("recommendation") or "Keine Aktion erforderlich."),
            "anomaly_score": int(data.get("anomaly_score") or 0),
            "email_subject": str(data.get("email_subject") or ""),
            "email_body": str(data.get("email_body") or ""),
            "llm_response": raw_text,
        }

    def _row_to_assessment(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["findings"] = self._list_json(data.pop("findings_json", "[]"))
        data["learning_completed"] = bool(data.get("learning_completed"))
        return data

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(0)
        return json.loads(cleaned)

    @staticmethod
    def _list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if value:
            return [str(value)]
        return []

    @staticmethod
    def _list_json(value: Any) -> list[str]:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        return SenteroBehaviorAgent._list(parsed)

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _email_body(summary: str, findings: list[str], recommendation: str) -> str:
        details = "\n".join(f"- {item}" for item in findings) if findings else "- Es wurden Abweichungen vom gewohnten Tagesablauf erkannt."
        return f"{summary}\n\n{details}\n\n{recommendation}"
