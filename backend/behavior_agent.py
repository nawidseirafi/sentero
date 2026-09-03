from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .config import load_agent_section
from .services.llm.factory import create_llm_client
from .services.messaging import MessagingService

from backend.services.data_classification import aggregation_for_data_class, classify_assessment, classify_sensor_event
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.logging_config import get_logger
from backend.services.notification_service import NotificationService
from backend.services.human_activity_service import HumanActivityScorer, reasons_json

logger = get_logger(__name__)

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
        self.human_activity = HumanActivityScorer()
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
            self._ensure_column(con, "sentero_sensor_events", "data_class", "text not null default 'technical'")
            self._ensure_column(con, "sentero_sensor_events", "aggregation_level", "text not null default 'raw'")
            self._ensure_column(con, "sentero_sensor_events", "human_activity_score", "integer")
            self._ensure_column(con, "sentero_sensor_events", "human_activity_confidence", "real")
            self._ensure_column(con, "sentero_sensor_events", "human_activity_classification", "text")
            self._ensure_column(con, "sentero_sensor_events", "human_activity_reasons", "text not null default '[]'")
            self._ensure_column(con, "behavior_events", "data_class", "text not null default 'technical'")
            self._ensure_column(con, "behavior_events", "aggregation_level", "text not null default 'raw'")
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
            self._ensure_column(con, "behavior_assessments", "data_class", "text not null default 'health_adjacent'")
            self._ensure_column(con, "behavior_assessments", "aggregation_level", "text not null default 'summary'")
            con.commit()

    def _ensure_column(self, con: Any, table: str, column: str, definition: str) -> None:
        columns = {str(row["name"]) for row in con.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            con.execute(f"alter table {table} add column {column} {definition}")

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        logger.debug("Behavior analysis start", extra={"component": "behavior", "dry_run": dry_run})
        self.ensure_schema()
        configured_roles = self.mapping.roles(dev=True, include_state=False)
        if not configured_roles:
            logger.info("Behavior analysis skipped because no sensors are configured", extra={"component": "behavior"})
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
        # Analysis is scoped to sensors explicitly configured in Sentero. The raw
        # MQTT snapshot can contain unrelated household devices and must not feed
        # behavior/AI context.
        source_snapshot = list(sensor_snapshot)
        if not dry_run:
            self._record_snapshot(sensor_snapshot, source_snapshot)
            self._notify_system_warnings(sensor_snapshot)
            self._cleanup_old_data()
        history = self._history(days=30)
        daily_summary = self._upsert_daily_summary(history, dry_run=dry_run)
        behavior_profile = self._update_behavior_profile(dry_run=dry_run)
        deviations = self._behavior_deviations(daily_summary, behavior_profile, sensor_snapshot)
        daily_summary["anomaly_score"] = int(deviations.get("anomaly_score") or 0)
        if not dry_run:
            self._store_daily_anomaly_score(str(daily_summary.get("date") or ""), int(daily_summary["anomaly_score"]))
        payload = self._analysis_payload(profile, contacts, sensor_snapshot, history, source_snapshot, daily_summary, behavior_profile, deviations)
        assessment = self._assess(payload)
        assessment = self._apply_learning_policy(assessment, payload)
        stored = assessment if dry_run else self._store_assessment(assessment)
        if not dry_run:
            self._notify_if_needed(stored, contacts)
        logger.debug(
            "Behavior analysis completed",
            extra={
                "component": "behavior",
                "status": stored["status"],
                "sensor_count": len(sensor_snapshot),
                "history_count": len(history),
                "dry_run": dry_run,
            },
        )
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

    def timeline_today(self, live_snapshot: bool = False, day: str | None = None) -> dict[str, Any]:
        return self.behavior_day(day=day, live_snapshot=live_snapshot)

    def behavior_day(self, day: str | None = None, live_snapshot: bool = False) -> dict[str, Any]:
        target_day = self._target_date(day)
        if live_snapshot and target_day == datetime.now(timezone.utc).date():
            self.record_current_snapshot()
        events = self._events_for_day(target_day)
        summary = self._build_daily_summary(target_day, events)
        if target_day == datetime.now(timezone.utc).date() and events:
            summary = self._upsert_daily_summary(events, dry_run=False)
        profile = self._update_behavior_profile(dry_run=True)
        deviations = self._behavior_deviations(summary, profile, self.mapping.roles(dev=True, include_state=True))
        summary["anomaly_score"] = int(deviations.get("anomaly_score") or 0)
        timeline_events = self._timeline_events_for_day(target_day, events, summary, profile, deviations)
        data_quality = self._dashboard_data_quality(profile, [summary], deviations.get("data_quality") or {}, len(events))
        logger.debug("Behavior day built", extra={"component": "behavior", "event_count": len(events), "timeline_event_count": len(timeline_events)})
        return {
            "date": target_day.isoformat(),
            "events": events,
            "timeline_events": timeline_events,
            "summary": self._ui_daily_summary(summary, profile, deviations, timeline_events),
            "profile": self._ui_profile(profile),
            "deviations": deviations,
            "data_quality": data_quality,
            "assessment": self.latest(),
        }

    def behavior_trends(self, days: int = 14) -> dict[str, Any]:
        self.ensure_schema()
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days - 1)
        profile = self._update_behavior_profile(dry_run=True)
        with self.mapping.connect() as con:
            rows = con.execute(
                "select * from behavior_daily_summary where date >= ? order by date asc",
                (start.isoformat(),),
            ).fetchall()
        summaries = [self._summary_row_to_dict(row) for row in rows]
        by_date = {str(item.get("date")): item for item in summaries}
        data_quality = self._dashboard_data_quality(profile, summaries, self._sensor_data_quality(self.mapping.roles(dev=True, include_state=True)), None)
        points = []
        for offset in range(days):
            current = start + timedelta(days=offset)
            item = by_date.get(current.isoformat()) or {"date": current.isoformat()}
            points.append({
                "date": current.isoformat(),
                "wakeup_time": item.get("wakeup_time"),
                "wakeup_minutes": self._minutes_of_day(item.get("wakeup_time")),
                "active_minutes": int(item.get("active_minutes") or 0),
                "longest_inactivity_minutes": max([int(period.get("minutes") or 0) for period in item.get("inactivity_periods", [])], default=0),
                "night_activity_count": self._night_activity_count(item),
                "away_minutes": int(item.get("door_events") or 0) * 30,
                "door_events": int(item.get("door_events") or 0),
                "anomaly_score": int(item.get("anomaly_score") or 0),
                "has_data": bool(item.get("first_activity") or item.get("active_minutes") or item.get("door_events")),
            })
        previous = summaries[:max(0, len(summaries) - 7)]
        current_week = summaries[-7:]
        return {
            "days": days,
            "profile": self._ui_profile(profile),
            "data_quality": data_quality,
            "points": points,
            "cards": [
                self._trend_card("wakeup", "Aufstehzeit", previous, current_week, profile),
                self._trend_card("activity", "Aktivität", previous, current_week, profile),
                self._trend_card("door", "Zeit außer Haus", previous, current_week, profile),
                self._trend_card("night", "Nächtliche Aktivität", previous, current_week, profile),
            ],
            "series": [
                self._trend_series("wake_time", "Aufstehzeit", "time", points, profile),
                self._trend_series("activity", "Aktivität", "minutes", points, profile),
                self._trend_series("longest_rest", "Längste Ruhephase", "minutes", points, profile),
                self._trend_series("night_activity", "Nächtliche Aktivität", "count", points, profile),
                self._trend_series("away_time", "Zeit außer Haus", "minutes", points, profile),
            ],
        }

    def behavior_hints(self, days: int = 14) -> dict[str, Any]:
        self.ensure_schema()
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=days - 1)
        hints: list[dict[str, Any]] = []
        with self.mapping.connect() as con:
            rows = con.execute(
                "select * from behavior_daily_summary where date >= ? order by date desc",
                (start.isoformat(),),
            ).fetchall()
        profile = self._update_behavior_profile(dry_run=True)
        for row in rows:
            summary = self._summary_row_to_dict(row)
            target_day = self._summary_date(summary)
            deviations = self._behavior_deviations(summary, profile, [])
            for event in self._anomaly_events_for_summary(target_day, summary, profile, deviations):
                hints.append({**event, "day": target_day.isoformat()})
        latest = self.latest()
        if latest and str(latest.get("status")) in {"yellow", "orange", "red"}:
            hints.insert(0, {
                "id": f"assessment-{latest.get('id') or latest.get('assessment_time')}",
                "day": str(latest.get("assessment_time") or "")[:10],
                "severity": self._severity_from_status(latest.get("status")),
                "title": self._status_title(latest.get("status")),
                "description": latest.get("summary") or "Sentero hat eine Auffälligkeit erkannt.",
                "start_time": latest.get("assessment_time"),
                "end_time": None,
                "time_label": self._time_label(latest.get("assessment_time")),
                "status": "observed",
                "status_label": "Beobachtet",
                "room": None,
            })
        return {
            "current": [item for item in hints if item.get("status") == "active"],
            "observed": [item for item in hints if item.get("status") == "observed"],
            "resolved": [item for item in hints if item.get("status") == "resolved"],
        }

    def record_current_snapshot(self) -> int:
        sensor_snapshot = self.mapping.roles(dev=True, include_state=True)
        source_snapshot = list(sensor_snapshot)
        written = self._record_snapshot(sensor_snapshot, source_snapshot)
        self._notify_system_warnings(sensor_snapshot)
        return written

    def handle_mqtt_message(self, topic: str, payload: Any, received_at: str) -> int:
        """Persist behavior-relevant MQTT changes at ingestion time.

        The MQTT last-state table answers "what is the current value?". This
        method answers "what happened when?" by writing presence/motion events to
        the behavior history as soon as Zigbee2MQTT publishes them.
        """
        clean_topic = str(topic or "").strip().strip("/")
        if not clean_topic or not isinstance(payload, dict):
            return 0
        # Bridge metadata and availability are technical transport messages, not
        # human behavior observations.
        if "/bridge/" in f"/{clean_topic}/" or clean_topic.endswith("/availability"):
            return 0

        matched_roles = self._roles_for_mqtt_topic(clean_topic)
        if not matched_roles:
            return 0

        written = 0
        for role in matched_roles:
            self._handle_mqtt_smoke_warning(role, payload)
            events = self._mqtt_behavior_events(role, clean_topic, payload, received_at)
            if events:
                written += self._write_sensor_events(events, created_at=now())
        if written:
            logger.debug(
                "MQTT behavior events recorded",
                extra={"component": "behavior", "topic": clean_topic, "written_events": written},
            )
        return written

    def _handle_mqtt_smoke_warning(self, role: dict[str, Any], payload: dict[str, Any]) -> None:
        if not self._is_smoke_role(role):
            return
        smoke = self._mqtt_smoke_active(payload)
        if smoke is None:
            return
        sensor = {
            **role,
            "configured": True,
            "active": True,
            "enabled": True,
            "smoke": smoke,
            "battery_level": payload.get("battery") if isinstance(payload.get("battery"), (int, float)) else role.get("battery_level"),
        }
        subject_id = self.notifications._sensor_subject_id(sensor)
        if smoke:
            self.notifications.notify_sensor_warnings([sensor])
        else:
            self.notifications.resolve_system_warning(f"smoke_alarm:{subject_id}")

    def _roles_for_mqtt_topic(self, topic: str) -> list[dict[str, Any]]:
        clean = str(topic or "").strip().strip("/")
        if not clean:
            return []
        result: list[dict[str, Any]] = []
        for role in self.mapping.roles(dev=True, include_state=False):
            candidates = {
                str(role.get("entity_id") or "").strip().strip("/"),
                str(role.get("source_ref") or "").strip().strip("/"),
                str(role.get("primary_entity_id") or "").strip().strip("/"),
            }
            entity_ids = role.get("entity_ids")
            if isinstance(entity_ids, list):
                candidates.update(str(item or "").strip().strip("/") for item in entity_ids)
            if clean in {item for item in candidates if item}:
                result.append(role)
        return result

    def _mqtt_behavior_events(
        self,
        role: dict[str, Any],
        topic: str,
        payload: dict[str, Any],
        event_time: str,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        role_name = str(role.get("role") or "").strip() or "sensor"
        if self._is_smoke_role(role):
            return events
        room = role.get("room")
        source = str(role.get("source") or "zigbee2mqtt")

        motion_raw = payload.get("motion_state") if "motion_state" in payload else payload.get("motion")
        motion = self._mqtt_motion_active(motion_raw)

        presence_value = payload.get("presence") if "presence" in payload else payload.get("occupancy")
        presence = self._mqtt_bool(presence_value)
        # Combined PIR/radar sensors can transiently report presence=false while
        # motion_state is moving/static/still. Any of those states proves that the
        # person is still in the room and must prevent a false "Nicht im Haus".
        if self._mqtt_motion_state_implies_presence(motion_raw):
            presence = True
        if presence is not None:
            events.append({
                "event_time": event_time,
                "role": role_name,
                "room": room,
                "entity_id": f"{topic}#presence",
                "state": "on" if presence else "off",
                "device_class": "presence",
                "source": source,
                "event_type": "presence",
                "motion_state": motion_raw,
                # Presence is a state. Repeated telemetry with unchanged presence
                # must not look like repeated human activity.
                "transition_only": True,
            })

        if motion is not None:
            events.append({
                "event_time": event_time,
                "role": f"{role_name}_motion",
                "room": room,
                "entity_id": f"{topic}#motion",
                "state": "on" if motion else "off",
                "device_class": "motion",
                "source": source,
                "event_type": "motion",
                "motion_state": motion_raw,
                # Every explicit moving message is useful as a fresh last-movement
                # timestamp. Repeated inactive messages are only state transitions.
                "transition_only": not motion,
            })
        return events

    @staticmethod
    def _mqtt_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"true", "on", "yes", "1", "present", "occupied", "detected"}:
            return True
        if text in {"false", "off", "no", "0", "absent", "clear", "none", "not_present", "not present"}:
            return False
        return None

    @staticmethod
    def _mqtt_motion_state_implies_presence(value: Any) -> bool:
        text = str(value or "").strip().lower().replace("-", "_")
        return text in {
            "moving", "move", "movement", "motion", "active", "detected", "moving_target", "large", "small",
            "still", "static", "stationary", "standstill", "static_target", "presence", "present",
        }

    @staticmethod
    def _mqtt_motion_active(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        text = str(value or "").strip().lower().replace("-", "_")
        if text in {"moving", "move", "movement", "motion", "active", "detected", "moving_target", "large", "small"}:
            return True
        if text in {"none", "still", "static", "stationary", "standstill", "inactive", "clear", "off", "false", "0", "no_motion", "no motion"}:
            return False
        return None

    @classmethod
    def _mqtt_smoke_active(cls, payload: dict[str, Any]) -> bool | None:
        for key in ("smoke", "smoke_alarm", "smoke_state"):
            if key in payload:
                parsed = cls._mqtt_bool(payload.get(key))
                if parsed is not None:
                    return parsed
                text = str(payload.get(key) or "").strip().lower().replace("-", "_")
                if text in {"alarm", "alert", "smoke", "smoke_detected", "detected", "fire"}:
                    return True
                if text in {"clear", "ok", "normal", "none", "no_smoke", "not_detected"}:
                    return False
        return None

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

    def _record_snapshot(self, roles: list[dict[str, Any]], source_snapshot: list[dict[str, Any]] | None = None) -> int:
        timestamp = now()
        snapshot_rows = source_snapshot or []
        extra_events = [
            *self._smart_meter_snapshot_events(snapshot_rows, timestamp),
        ]
        normalized: list[dict[str, Any]] = []
        for role in roles:
            behavior_events = self._behavior_events_from_role(role, timestamp)
            normalized.extend(behavior_events if behavior_events else [role])
        normalized.extend(extra_events)
        written = self._write_sensor_events(normalized, created_at=timestamp)
        logger.debug(
            "Behavior snapshot recorded",
            extra={"component": "behavior", "role_count": len(roles), "extra_event_count": len(extra_events), "written_events": written},
        )
        return written

    def _behavior_events_from_role(self, role: dict[str, Any], fallback_time: str) -> list[dict[str, Any]]:
        if not self._is_presence_role(role):
            return []
        event_time = str(role.get("last_changed") or role.get("last_updated") or fallback_time)
        topic = str(role.get("entity_id") or role.get("resolved_entity_id") or role.get("role") or "sensor").strip()
        payload: dict[str, Any] = {}
        if role.get("presence") is not None:
            payload["presence"] = role.get("presence")
        if role.get("motion_state") is not None:
            payload["motion_state"] = role.get("motion_state")
        elif role.get("motion") is not None:
            payload["motion"] = role.get("motion")
        if not payload:
            return []
        return self._mqtt_behavior_events(role, topic, payload, event_time)

    def _write_sensor_events(self, events: list[dict[str, Any]], created_at: str) -> int:
        written = 0
        with self.mapping.connect() as con:
            for event in events:
                state = event.get("state")
                if state in (None, "", "unknown", "unavailable"):
                    continue
                event_time = str(event.get("event_time") or event.get("last_changed") or event.get("last_updated") or created_at)
                role_name = event.get("role")
                entity_id = event.get("entity_id")
                if self._event_already_recorded(con, event_time, role_name, entity_id, state):
                    continue
                if event.get("transition_only") and self._latest_sensor_state(con, entity_id, role_name) == str(state):
                    continue
                event_type = str(event.get("event_type") or self._event_type(event))
                data_class = classify_sensor_event(event_type, event.get("device_class"), role_name)

                # Shadow-mode only: store an estimate of how human-like the event
                # looks, but do not suppress or change any existing behavior logic.
                scoring_event = {
                    **event,
                    "event_time": event_time,
                    "event_type": event_type,
                    "state": state,
                }
                human = self.human_activity.assess(con, scoring_event)

                con.execute(
                    """insert into sentero_sensor_events
                       (event_time, role, room, entity_id, state, device_class, source,
                        data_class, aggregation_level,
                        human_activity_score, human_activity_confidence,
                        human_activity_classification, human_activity_reasons,
                        created_at)
                       values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_time,
                        role_name,
                        event.get("room"),
                        entity_id,
                        state,
                        event.get("device_class"),
                        event.get("source") or "snapshot",
                        data_class,
                        "raw",
                        human.score if human else None,
                        human.confidence if human else None,
                        human.classification if human else None,
                        reasons_json(human),
                        created_at,
                    ),
                )
                con.execute(
                    """insert into behavior_events
                       (timestamp, sensor_id, sensor_type, room, event_type, metadata, data_class, aggregation_level)
                       values (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_time,
                        entity_id or role_name,
                        event.get("device_class") or event.get("type") or event.get("domain"),
                        event.get("room"),
                        event_type,
                        json.dumps({
                            "role": role_name,
                            "state": state,
                            "source": event.get("source") or "snapshot",
                            "motion_state": event.get("motion_state"),
                            "human_activity": human.as_dict() if human else None,
                            "human_activity_shadow_mode": True,
                        }, ensure_ascii=False),
                        data_class,
                        "raw",
                    ),
                )
                if human is not None:
                    logger.debug(
                        "Human activity score recorded",
                        extra={
                            "component": "behavior",
                            "room": event.get("room"),
                            "event_type": event_type,
                            "human_activity_score": human.score,
                            "human_activity_confidence": human.confidence,
                            "human_activity_classification": human.classification,
                            "human_activity_shadow_mode": True,
                        },
                    )
                written += 1
            con.commit()
        return written

    def _latest_sensor_state(self, con: Any, entity_id: Any, role: Any) -> str | None:
        row = con.execute(
            """select state from sentero_sensor_events
               where coalesce(entity_id, '') = coalesce(?, '')
                 and coalesce(role, '') = coalesce(?, '')
               order by event_time desc, id desc
               limit 1""",
            (entity_id, role),
        ).fetchone()
        return str(row["state"]) if row else None

    def _event_already_recorded(self, con: Any, event_time: Any, role: Any, entity_id: Any, state: Any) -> bool:
        row = con.execute(
            """select id from sentero_sensor_events
               where event_time = ?
                 and coalesce(role, '') = coalesce(?, '')
                 and coalesce(entity_id, '') = coalesce(?, '')
                 and state = ?
               limit 1""",
            (event_time, role, entity_id, state),
        ).fetchone()
        return row is not None

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
        source_snapshot: list[dict[str, Any]],
        daily_summary: dict[str, Any],
        behavior_profile: dict[str, Any],
        deviations: dict[str, Any],
    ) -> dict[str, Any]:
        today = datetime.now(timezone.utc).date()
        today_events = [event for event in history if self._parse_time(event.get("event_time")).date() == today]
        previous_events = [event for event in history if self._parse_time(event.get("event_time")).date() != today]
        learning = behavior_profile.get("learning") or {}
        utility_usage = self._utility_usage_summary(history)
        data_quality = self._sensor_data_quality(sensor_snapshot)
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
            "utility_usage": utility_usage,
            "sensor_context": {
                "configured_sensors": len(sensor_snapshot),
                "rooms": sorted({str(item.get("room")) for item in sensor_snapshot if item.get("room")}),
            },
            "data_quality": data_quality,
            "deviations": {**self._deviations(today_events, previous_events, sensor_snapshot), "low_utility_usage_today": utility_usage.get("low_usage_today"), **deviations},
            "safety_rules": {
                "no_medical_diagnosis": True,
                "no_emergency_calls": True,
                "only_behavioral_anomaly_detection": True,
                "data_quality_required": "Bei eingeschränkter Sensor-Datenlage keine beruhigende oder eskalierende Aussage ohne Hinweis auf die eingeschränkte Verlässlichkeit treffen.",
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
            logger.debug("Behavior LLM assessment start", extra={"component": "behavior", "provider": getattr(client, "provider", "unknown")})
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
            assessment = self._validate_assessment(raw, response.text)
            logger.debug("Behavior LLM assessment completed", extra={"component": "behavior", "status": assessment.get("status")})
            return assessment
        except Exception as exc:
            logger.exception("Behavior LLM assessment failed, using heuristic fallback", extra={"component": "behavior"})
            fallback = self._heuristic_assessment(payload)
            fallback["llm_response"] = json.dumps({"fallback_reason": str(exc)}, ensure_ascii=False)
            return fallback

    def _heuristic_assessment(self, payload: dict[str, Any]) -> dict[str, Any]:
        deviations = payload.get("deviations") or {}
        data_quality = payload.get("data_quality") or {}
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
        if data_quality.get("monitoring_reliable") is False:
            return {
                "assessment_time": now(),
                "status": "yellow",
                "confidence": 0.45,
                "summary": "Keine verlässliche Tagesbewertung möglich, weil wichtige Aktivitätssensoren keine frischen Daten liefern.",
                "findings": self._data_quality_findings(data_quality),
                "recommendation": "Bitte Sensorstatus, Stromversorgung, Batterie und Funkverbindung prüfen.",
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
        if deviations.get("low_utility_usage_today"):
            findings.append("Strom-, Wasser- oder Gasverbrauch lagen heute deutlich unter dem bisherigen Muster.")
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
        if status == "green" and data_quality.get("observation_limited"):
            status = "yellow"
            findings.extend(self._data_quality_findings(data_quality))
            summary_by_status["yellow"] = "Es sind keine Auffälligkeiten erkennbar, die Datenlage ist aber eingeschränkt."
            recommendation = "Bitte Sensorstatus im Blick behalten."
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
                    anomaly_score, learning_completed, learning_day, learning_days, data_class, aggregation_level)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    classify_assessment(assessment.get("status")),
                    aggregation_for_data_class(classify_assessment(assessment.get("status"))),
                ),
            )
            con.commit()
            row = con.execute("select * from behavior_assessments where id = ?", (int(cur.lastrowid),)).fetchone()
        stored = self._row_to_assessment(row)
        stored["email_subject"] = assessment.get("email_subject") or ""
        stored["email_body"] = assessment.get("email_body") or ""
        logger.debug("Behavior assessment stored", extra={"component": "behavior", "assessment_id": stored.get("id"), "status": stored.get("status")})
        return stored

    def _notify_system_warnings(self, sensor_snapshot: list[dict[str, Any]]) -> None:
        try:
            # Only configured Sentero roles are eligible for warnings. The raw
            # MQTT snapshot may contain unrelated devices on the same broker.
            result = self.notifications.notify_system_warnings(sensor_snapshot)
            if result.get("warnings"):
                logger.info(
                    "System warnings generated",
                    extra={"component": "behavior", "warning_count": len(result.get("warnings") or []), "sent": result.get("sent")},
                )
        except Exception as exc:
            logger.exception("System warning check failed", extra={"component": "behavior"})

    def _notify_if_needed(self, assessment: dict[str, Any], contacts: list[dict[str, Any]]) -> None:
        if not self.mapping.roles(dev=True, include_state=False):
            logger.info("Sentero notification skipped because no sensors are configured")
            return
        if not assessment.get("learning_completed", True):
            logger.info("Sentero notification skipped because behavior profile is still learning")
            return
        status = assessment.get("status")
        if status not in {"orange", "red"}:
            self.notifications.resolve_behavior_notification()
            return
        notification_result = self.notifications.notify_assessment(assessment, contacts)
        if not int(notification_result.get("sent") or 0):
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

    def _learning_days(self) -> int:
        config = load_agent_section("sentero")
        behavior = config.get("behavior") if isinstance(config.get("behavior"), dict) else {}
        raw = behavior.get("learning_days", config.get("learning_days", 14))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 14
        return max(7, min(value, 30))

    def _learning_min_usable_days(self, learning_days: int) -> int:
        config = load_agent_section("sentero")
        behavior = config.get("behavior") if isinstance(config.get("behavior"), dict) else {}
        raw = behavior.get("min_usable_days", config.get("min_usable_days", 7))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 7
        return max(1, min(value, learning_days))

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
        required_usable_days = self._learning_min_usable_days(learning_days)
        started = self._parse_time(row.get("learning_started_at"))
        learning_day = max(1, min(learning_days, (datetime.now(timezone.utc).date() - started.date()).days + 1))
        with self.mapping.connect() as con:
            rows = con.execute("select * from behavior_daily_summary order by date asc").fetchall()
        summaries = [
            item for item in (self._summary_row_to_dict(row) for row in rows)
            if self._summary_date(item) >= started.date()
        ]
        usable = [item for item in summaries if item.get("active_minutes") or item.get("first_activity")]
        usable_days = len(usable)
        calendar_complete = learning_day >= learning_days
        data_complete = usable_days >= required_usable_days
        completed = calendar_complete and data_complete
        average_wakeup = self._average_time([item.get("wakeup_time") for item in usable])
        average_sleep = self._average_time([item.get("last_activity") for item in usable])
        average_active = round(sum(float(item.get("active_minutes") or 0) for item in usable) / len(usable), 2) if usable else 0
        room_usage = self._average_room_usage(usable)
        door_usage = self._normal_door_usage(usable)
        completed_at = row.get("learning_completed_at") if completed and row.get("learning_completed_at") else (now() if completed else None)
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
                "usable_days": usable_days,
                "required_usable_days": required_usable_days,
                "calendar_complete": calendar_complete,
                "data_complete": data_complete,
                "remaining_days": max(0, learning_days - learning_day),
                "remaining_usable_days": max(0, required_usable_days - usable_days),
            },
        }

    def _summary_date(self, summary: dict[str, Any]) -> date:
        value = str(summary.get("date") or "")
        try:
            return date.fromisoformat(value)
        except ValueError:
            return date.min

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
            "data_quality": self._sensor_data_quality(roles),
        }

    def _sensor_data_quality(self, roles: list[dict[str, Any]]) -> dict[str, Any]:
        configured = [role for role in roles if role.get("configured", role.get("active", True))]
        unavailable = [role for role in configured if role.get("reachable") is False]
        stale = [role for role in configured if role.get("stale") is True]
        activity = [role for role in configured if self._is_presence_role(role) or self._is_motion_entity(role)]
        activity_limited = [role for role in activity if role.get("reachable") is False or role.get("stale") is True]
        monitoring_reliable = bool(activity) and len(activity_limited) < len(activity)
        return {
            "configured_sensors": len(configured),
            "activity_sensors": len(activity),
            "stale_sensors": len(stale),
            "unreachable_sensors": len(unavailable),
            "observation_limited": bool(stale or unavailable or not monitoring_reliable),
            "monitoring_reliable": monitoring_reliable,
            "stale": self._compact_roles(stale),
            "unreachable": self._compact_roles(unavailable),
            "activity_limited": self._compact_roles(activity_limited),
        }

    def _dashboard_data_quality(
        self,
        profile: dict[str, Any],
        summaries: list[dict[str, Any]],
        sensor_quality: dict[str, Any],
        selected_event_count: int | None,
    ) -> dict[str, Any]:
        learning = profile.get("learning") or {}
        usable_days = sum(1 for item in summaries if item.get("first_activity") or int(item.get("active_minutes") or 0) > 0 or int(item.get("door_events") or 0) > 0)
        baseline_available = any([
            profile.get("average_wakeup_time"),
            float(profile.get("average_active_minutes") or 0) > 0,
            bool((profile.get("normal_door_usage") or {}).get("average_daily_events")),
        ])
        if sensor_quality.get("monitoring_reliable") is False:
            text = "Sensordaten derzeit nicht vollständig verfügbar. Die Tagesbewertung ist nur eingeschränkt belastbar."
        elif selected_event_count == 0:
            text = "Für diesen Tag liegen keine ausreichenden Aktivitätsdaten vor."
        elif bool(learning.get("completed")) and baseline_available:
            text = f"Sentero verfügt über {usable_days} verwertbare Tage im ausgewählten Zeitraum und kann den Verlauf mit dem persönlichen Verhalten vergleichen."
        elif usable_days:
            text = f"Sentero lernt das persönliche Verhalten noch kennen. Im ausgewählten Zeitraum liegen {usable_days} verwertbare Tage vor."
        else:
            text = "Noch nicht genügend Verlaufsdaten für einen persönlichen Vergleich."
        return {
            "usable_days": usable_days,
            "learning_completed": bool(learning.get("completed")),
            "learning_day": int(learning.get("day") or 1),
            "learning_days": int(learning.get("days") or self._learning_days()),
            "baseline_available": baseline_available,
            "sensor_quality": sensor_quality,
            "message": text,
        }

    def _data_quality_findings(self, data_quality: dict[str, Any]) -> list[str]:
        findings: list[str] = []
        if data_quality.get("activity_sensors") == 0:
            findings.append("Es ist kein Aktivitätssensor eingerichtet.")
        elif data_quality.get("monitoring_reliable") is False:
            findings.append("Alle Aktivitätssensoren liefern derzeit keine frischen verwertbaren Daten.")
        elif data_quality.get("stale_sensors"):
            findings.append("Einige Sensorwerte sind nicht frisch und nur als letzter bekannter Zustand zu verstehen.")
        if data_quality.get("unreachable_sensors"):
            findings.append("Mindestens ein Sensor ist als nicht erreichbar markiert.")
        return findings or ["Die Sensor-Datenlage ist eingeschränkt."]

    def _apply_learning_policy(self, assessment: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        learning = payload.get("learning") or {}
        data_quality = payload.get("data_quality") or {}
        score = int(payload.get("anomaly_score") or assessment.get("anomaly_score") or 0)
        status = str(assessment.get("status") or self._status_from_score(score))
        findings = self._list(assessment.get("findings"))
        if data_quality.get("monitoring_reliable") is False:
            status = "yellow"
            assessment["summary"] = "Keine verlässliche Tagesbewertung möglich, weil wichtige Aktivitätssensoren keine frischen Daten liefern."
            assessment["recommendation"] = "Bitte Sensorstatus, Stromversorgung, Batterie und Funkverbindung prüfen."
            assessment["email_subject"] = ""
            assessment["email_body"] = ""
            findings.extend(item for item in self._data_quality_findings(data_quality) if item not in findings)
            assessment["confidence"] = min(float(assessment.get("confidence") or 0), 0.55)
        elif status == "green" and data_quality.get("observation_limited"):
            status = "yellow"
            assessment["summary"] = "Es sind keine Auffälligkeiten erkennbar, die Datenlage ist aber eingeschränkt."
            assessment["recommendation"] = "Bitte Sensorstatus im Blick behalten."
            assessment["email_subject"] = ""
            assessment["email_body"] = ""
            findings.extend(item for item in self._data_quality_findings(data_quality) if item not in findings)
            assessment["confidence"] = min(float(assessment.get("confidence") or 0), 0.6)
        if not learning.get("completed") and status in {"orange", "red"}:
            status = "yellow"
            assessment["summary"] = "Sentero lernt aktuell den gewohnten Tagesablauf kennen. Es gibt erste Hinweise, aber noch keine abschließende Bewertung."
            assessment["recommendation"] = "Keine dringende Aktion erforderlich. Beobachten Sie den Verlauf weiter."
            assessment["email_subject"] = ""
            assessment["email_body"] = ""
        assessment["status"] = status
        assessment["findings"] = findings
        assessment["anomaly_score"] = score
        assessment["learning_completed"] = bool(learning.get("completed"))
        assessment["learning_day"] = int(learning.get("day") or 1)
        assessment["learning_days"] = int(learning.get("days") or self._learning_days())
        return assessment

    def _build_daily_summary(self, day: date, events: list[dict[str, Any]]) -> dict[str, Any]:
        parsed = sorted(
            ((self._parse_time(event.get("event_time")), event) for event in events),
            key=lambda item: item[0],
        )
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

    def _target_date(self, value: str | None) -> date:
        if not value:
            return datetime.now(timezone.utc).date()
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return datetime.now(timezone.utc).date()

    def _events_for_day(self, target_day: date) -> list[dict[str, Any]]:
        start = datetime.combine(target_day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        with self.mapping.connect() as con:
            rows = con.execute(
                "select * from sentero_sensor_events where event_time >= ? and event_time < ? order by event_time asc",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return [dict(row) for row in rows]

    def _timeline_events_for_day(
        self,
        target_day: date,
        events: list[dict[str, Any]],
        summary: dict[str, Any],
        profile: dict[str, Any],
        deviations: dict[str, Any],
    ) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        activity_events = [(self._parse_time(event.get("event_time")), event) for event in events if self._is_activity_event(event)]
        if activity_events:
            first_time, first_event = activity_events[0]
            timeline.append(self._timeline_event(
                target_day, "day-start", first_time, None, "routine", "info",
                "Tag begonnen", "Erste Aktivität nach der Nacht.",
                room=first_event.get("room"), status="resolved",
                observations=[self._observation_label(first_event)],
                source_entities=[first_event.get("entity_id")],
                baseline_comparison=self._wakeup_comparison(summary, profile),
            ))
        timeline.extend(self._room_episode_events(target_day, activity_events))
        timeline.extend(self._door_timeline_events(target_day, events))
        timeline.extend(self._environment_timeline_events(target_day, events))
        timeline.extend(self._anomaly_events_for_summary(target_day, summary, profile, deviations))
        if summary.get("last_activity"):
            last_time = self._parse_time(summary.get("last_activity"))
            if last_time.hour >= 20:
                timeline.append(self._timeline_event(
                    target_day, "night-rest", last_time, None, "routine", "info",
                    "Nachtruhe erkannt", "Seitdem wurde keine spätere Alltagsaktivität erfasst.",
                    room=None, status="observed",
                    baseline_comparison=self._sleep_comparison(summary, profile),
                ))
        return sorted(timeline, key=lambda item: item.get("start_time") or "")

    def _room_episode_events(self, target_day: date, activity_events: list[tuple[datetime, dict[str, Any]]]) -> list[dict[str, Any]]:
        episodes: list[dict[str, Any]] = []
        current: list[tuple[datetime, dict[str, Any]]] = []
        for item in activity_events:
            event_time, event = item
            if not current:
                current = [item]
                continue
            previous_time, previous_event = current[-1]
            same_room = str(previous_event.get("room") or "") == str(event.get("room") or "")
            close = (event_time - previous_time).total_seconds() <= 45 * 60
            if same_room and close:
                current.append(item)
            else:
                episodes.append(self._room_episode(target_day, current))
                current = [item]
        if current:
            episodes.append(self._room_episode(target_day, current))
        return [episode for episode in episodes if episode]

    def _room_episode(self, target_day: date, items: list[tuple[datetime, dict[str, Any]]]) -> dict[str, Any] | None:
        if not items:
            return None
        start, first = items[0]
        end, _ = items[-1]
        minutes = max(1, int((end - start).total_seconds() / 60) + 1)
        room = first.get("room")
        return self._timeline_event(
            target_day,
            f"room-{room or 'unknown'}-{start.strftime('%H%M')}",
            start,
            end if len(items) > 1 else None,
            "activity",
            "normal",
            self._room_label(room) or "Aktivität",
            f"Aktivität {self._room_preposition(room)} erkannt.",
            room=room,
            duration_minutes=minutes,
            status="resolved",
            baseline_comparison="Im üblichen Bereich" if minutes < 70 else "Länger als eine kurze Alltagsaktivität.",
            observations=[self._observation_label(event) for _, event in items[:4]],
            source_entities=[event.get("entity_id") for _, event in items],
        )

    def _door_timeline_events(self, target_day: date, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        door_events = [(self._parse_time(event.get("event_time")), event) for event in events if self._is_door_event(event) and self._is_on(event.get("state"))]
        activity = [self._parse_time(event.get("event_time")) for event in events if self._is_activity_event(event) and not self._is_door_event(event)]
        for event_time, event in door_events:
            next_activity = min((item for item in activity if item > event_time), default=None)
            left_home = bool(next_activity and (next_activity - event_time).total_seconds() > 30 * 60)
            result.append(self._timeline_event(
                target_day,
                f"door-{event_time.strftime('%H%M%S')}",
                event_time,
                None,
                "door",
                "info",
                "Wohnungstür geöffnet" if not left_home else "Wohnung verlassen",
                "Danach wurde längere Zeit keine Aktivität in der Wohnung erkannt." if left_home else "Türkontakt hat eine Öffnung erfasst.",
                room=event.get("room"),
                status="observed",
                observations=[self._observation_label(event)],
                source_entities=[event.get("entity_id")],
            ))
        return result

    def _environment_timeline_events(self, target_day: date, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for event in events:
            device_class = self._device_class(event)
            value = self._number(event.get("state"))
            if device_class == "humidity" and value is not None and value >= 70:
                event_time = self._parse_time(event.get("event_time"))
                result.append(self._timeline_event(
                    target_day, f"humidity-{event_time.strftime('%H%M%S')}", event_time, None,
                    "environment", "notice", "Luftfeuchtigkeit erhöht",
                    f"{self._room_label(event.get('room')) or 'Raum'} · {round(value)} %",
                    room=event.get("room"), status="observed",
                    baseline_comparison="Bitte lüften oder Verlauf prüfen, falls der Wert lange erhöht bleibt.",
                    observations=[self._observation_label(event)],
                    source_entities=[event.get("entity_id")],
                ))
            if self._is_smoke_role(event) and self._is_on(event.get("state")):
                event_time = self._parse_time(event.get("event_time"))
                result.append(self._timeline_event(
                    target_day, f"safety-{event_time.strftime('%H%M%S')}", event_time, None,
                    "safety", "critical", "Sicherheitsereignis erkannt",
                    "Ein Sicherheitsmelder hat ausgelöst.",
                    room=event.get("room"), status="active",
                    observations=[self._observation_label(event)],
                    source_entities=[event.get("entity_id")],
                ))
        return result

    def _anomaly_events_for_summary(
        self,
        target_day: date,
        summary: dict[str, Any],
        profile: dict[str, Any],
        deviations: dict[str, Any],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index, period in enumerate(summary.get("inactivity_periods") or []):
            minutes = int(period.get("minutes") or 0)
            if minutes < 180:
                continue
            start = self._parse_time(period.get("from"))
            end = self._parse_time(period.get("to"))
            severity = "warning" if minutes >= 300 else "notice"
            result.append(self._timeline_event(
                target_day, f"quiet-{index}-{start.strftime('%H%M')}", start, end,
                "anomaly", severity, "Ungewöhnlich lange Ruhephase",
                f"{self._duration_label(minutes)} ohne erkannte Alltagsaktivität.",
                room=None, duration_minutes=minutes, status="resolved",
                status_label=f"Normalisiert um {self._time_label(end.isoformat(timespec='seconds'))}",
                baseline_comparison="Üblich sind deutlich kürzere Ruhephasen zwischen erkannten Aktivitäten.",
                observations=["Keine Aktivitätssensoren mit Bewegung oder Präsenz in diesem Zeitraum"],
                source_entities=[],
            ))
        if int(deviations.get("wakeup_deviation_minutes") or 0) >= 90 and summary.get("wakeup_time"):
            start = self._parse_time(f"{target_day.isoformat()}T{summary['wakeup_time']}:00+00:00")
            result.append(self._timeline_event(
                target_day, "wakeup-late", start, None, "anomaly", "notice",
                "Aufstehzeit ungewohnt", "Der Tag begann deutlich anders als im persönlichen Muster.",
                room=None, status="observed", baseline_comparison=self._wakeup_comparison(summary, profile),
            ))
        if int(summary.get("active_minutes") or 0) == 0:
            noon = datetime.combine(target_day, time(hour=12), tzinfo=timezone.utc)
            result.append(self._timeline_event(
                target_day, "no-activity", noon, None, "anomaly", "warning",
                "Keine Aktivität erfasst", "Für diesen Tag liegen keine verwertbaren Alltagsaktivitäten vor.",
                room=None, status="active" if target_day == datetime.now(timezone.utc).date() else "observed",
                baseline_comparison="Vergleich mit persönlichem Verhalten ist ohne Tagesaktivität nur eingeschränkt möglich.",
            ))
        return result

    def _timeline_event(
        self,
        target_day: date,
        stable_key: str,
        start_time: datetime,
        end_time: datetime | None,
        category: str,
        severity: str,
        title: str,
        description: str,
        **extra: Any,
    ) -> dict[str, Any]:
        duration = extra.pop("duration_minutes", None)
        if duration is None and end_time is not None:
            duration = max(1, int((end_time - start_time).total_seconds() / 60))
        return {
            "id": f"{target_day.isoformat()}-{stable_key}",
            "type": category,
            "category": category,
            "severity": severity,
            "title": title,
            "description": description,
            "room": extra.pop("room", None),
            "start_time": start_time.isoformat(timespec="seconds"),
            "end_time": end_time.isoformat(timespec="seconds") if end_time else None,
            "duration_minutes": duration,
            "duration": self._duration_label(duration) if duration else None,
            "status": extra.pop("status", "observed"),
            "status_label": extra.pop("status_label", None),
            "baseline_comparison": extra.pop("baseline_comparison", None),
            "observations": [item for item in extra.pop("observations", []) if item],
            "source_entities": sorted({str(item) for item in extra.pop("source_entities", []) if item}),
            **extra,
        }

    def _ui_daily_summary(self, summary: dict[str, Any], profile: dict[str, Any], deviations: dict[str, Any], timeline_events: list[dict[str, Any]]) -> dict[str, Any]:
        severity_rank = {"normal": 0, "info": 0, "notice": 1, "warning": 2, "critical": 3}
        max_severity = max((severity_rank.get(str(event.get("severity")), 0) for event in timeline_events), default=0)
        headline = "Überwiegend typischer Tagesablauf"
        if max_severity >= 3:
            headline = "Kritischer Hinweis im Tagesverlauf"
        elif max_severity == 2:
            headline = "Auffälligkeit im Tagesverlauf"
        elif max_severity == 1:
            headline = "Kleinere Abweichungen erkannt"
        anomaly_count = sum(1 for event in timeline_events if event.get("category") in {"anomaly", "safety", "environment"} and event.get("severity") != "normal")
        critical_count = sum(1 for event in timeline_events if event.get("severity") == "critical")
        longest = max([int(item.get("minutes") or 0) for item in summary.get("inactivity_periods", [])], default=0)
        return {
            "date": summary.get("date"),
            "headline": headline,
            "wakeup_time": summary.get("wakeup_time"),
            "first_activity": summary.get("first_activity"),
            "last_activity": summary.get("last_activity"),
            "active_minutes": int(summary.get("active_minutes") or 0),
            "longest_inactivity_minutes": longest,
            "longest_inactivity": self._duration_label(longest) if longest else None,
            "door_events": int(summary.get("door_events") or 0),
            "anomaly_count": anomaly_count,
            "critical_count": critical_count,
            "status_text": f"{anomaly_count} kleinere Abweichungen erkannt" if anomaly_count else "Keine relevanten Abweichungen erkannt",
            "critical_text": "Keine kritischen Ereignisse" if critical_count == 0 else f"{critical_count} kritische Ereignisse",
            "baseline": self._ui_profile(profile),
            "deviations": deviations,
        }

    def _ui_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "average_wakeup_time": profile.get("average_wakeup_time"),
            "average_sleep_time": profile.get("average_sleep_time"),
            "average_active_minutes": profile.get("average_active_minutes"),
            "normal_door_usage": profile.get("normal_door_usage") or {},
            "learning": profile.get("learning") or {},
        }

    def _trend_card(self, key: str, label: str, previous: list[dict[str, Any]], current: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
        if key == "wakeup":
            current_avg = self._average_time([item.get("wakeup_time") for item in current])
            previous_avg = self._average_time([item.get("wakeup_time") for item in previous])
            baseline = profile.get("average_wakeup_time")
            comparison = self._compare_time_label(current_avg, baseline)
            return {"key": key, "label": label, "baseline": baseline, "previous": previous_avg, "current": current_avg, "comparison": comparison}
        if key == "activity":
            current_avg = self._avg([item.get("active_minutes") for item in current])
            previous_avg = self._avg([item.get("active_minutes") for item in previous])
            baseline = float(profile.get("average_active_minutes") or 0)
            return {"key": key, "label": label, "baseline": self._duration_label(round(baseline)), "previous": self._duration_label(round(previous_avg)), "current": self._duration_label(round(current_avg)), "comparison": self._compare_number_label(current_avg, baseline, "Aktivität")}
        if key == "door":
            current_avg = self._avg([item.get("door_events") for item in current])
            previous_avg = self._avg([item.get("door_events") for item in previous])
            baseline = float((profile.get("normal_door_usage") or {}).get("average_daily_events") or 0)
            return {"key": key, "label": label, "baseline": self._count_label(baseline), "previous": self._count_label(previous_avg), "current": self._count_label(current_avg), "comparison": self._compare_number_label(current_avg, baseline, "Türereignisse")}
        current_nights = sum(1 for item in current if self._has_night_activity(item))
        previous_nights = sum(1 for item in previous if self._has_night_activity(item))
        return {"key": key, "label": label, "baseline": "persönlicher Normalbereich", "previous": f"{previous_nights} Nächte", "current": f"{current_nights} Nächte", "comparison": "häufiger als gewöhnlich" if current_nights >= 3 else "im üblichen Bereich"}

    def _trend_series(self, metric: str, label: str, unit: str, points: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
        value_key = {
            "wake_time": "wakeup_minutes",
            "activity": "active_minutes",
            "longest_rest": "longest_inactivity_minutes",
            "night_activity": "night_activity_count",
            "away_time": "away_minutes",
        }[metric]
        values = [point.get(value_key) for point in points if point.get("has_data") and point.get(value_key) is not None]
        baseline = self._series_baseline(metric, values, profile)
        latest = values[-1] if values else None
        return {
            "metric": metric,
            "label": label,
            "unit": unit,
            "points": [
                {
                    "timestamp": point["date"],
                    "value": point.get(value_key),
                    "label": point["date"],
                    "has_data": bool(point.get("has_data") and point.get(value_key) is not None),
                }
                for point in points
            ],
            "baseline": baseline,
            "interpretation": self._series_interpretation(label, latest, baseline, unit, bool(values)),
        }

    def _series_baseline(self, metric: str, values: list[Any], profile: dict[str, Any]) -> dict[str, Any]:
        if metric == "wake_time":
            average = self._minutes_of_day(profile.get("average_wakeup_time"))
            if average is None:
                average = round(sum(float(value) for value in values) / len(values)) if values else None
            return self._baseline_band(average, 30)
        if metric == "activity":
            average = float(profile.get("average_active_minutes") or 0) or (sum(float(value) for value in values) / len(values) if values else None)
            return self._baseline_band(average, max(20, round(float(average or 0) * 0.25)))
        if metric == "longest_rest":
            average = sum(float(value) for value in values) / len(values) if values else None
            return self._baseline_band(average, 45)
        if metric == "night_activity":
            return {"lower": 0, "upper": 2, "average": 1}
        average_door = float((profile.get("normal_door_usage") or {}).get("average_daily_events") or 0)
        average = average_door * 30 if average_door else (sum(float(value) for value in values) / len(values) if values else None)
        return self._baseline_band(average, max(30, round(float(average or 0) * 0.3)))

    def _baseline_band(self, average: float | int | None, spread: float | int) -> dict[str, Any]:
        if average is None:
            return {"lower": None, "upper": None, "average": None}
        return {
            "lower": max(0, round(float(average) - float(spread))),
            "upper": round(float(average) + float(spread)),
            "average": round(float(average)),
        }

    def _series_interpretation(self, label: str, latest: Any, baseline: dict[str, Any], unit: str, has_values: bool) -> str:
        if not has_values:
            return "Noch nicht genügend Verlaufsdaten für einen belastbaren Vergleich."
        lower = baseline.get("lower")
        upper = baseline.get("upper")
        if latest is None or lower is None or upper is None:
            return "Persönlicher Normalbereich wird noch aufgebaut."
        latest_value = float(latest)
        if latest_value < float(lower):
            direction = "früher" if unit == "time" else "geringer"
            return f"{label} aktuell {direction} als gewöhnlich."
        if latest_value > float(upper):
            direction = "später" if unit == "time" else "höher"
            return f"{label} aktuell {direction} als gewöhnlich."
        return f"{label} im persönlichen Bereich."

    def _night_activity_count(self, summary: dict[str, Any]) -> int:
        count = 0
        for period in summary.get("inactivity_periods") or []:
            if str(period.get("label") or "") == "night_activity":
                count += 1
        first = self._parse_time(summary.get("first_activity")) if summary.get("first_activity") else None
        last = self._parse_time(summary.get("last_activity")) if summary.get("last_activity") else None
        return max(count, int(bool((first and first.hour < 5) or (last and last.hour >= 23))))

    def _avg(self, values: list[Any]) -> float:
        numbers = [float(value or 0) for value in values if value is not None]
        return round(sum(numbers) / len(numbers), 1) if numbers else 0

    def _compare_time_label(self, current: str | None, baseline: str | None) -> str:
        diff = self._minute_deviation(current, baseline)
        if not current or not baseline:
            return "noch nicht vergleichbar"
        if diff < 30:
            return "im üblichen Bereich"
        return "etwas später" if self._minutes_of_day(current) and self._minutes_of_day(baseline) and self._minutes_of_day(current) > self._minutes_of_day(baseline) else "anders als gewöhnlich"

    def _compare_number_label(self, current: float, baseline: float, noun: str) -> str:
        if baseline <= 0:
            return "noch nicht vergleichbar"
        change = self._percent_change(current, baseline)
        if abs(change) < 25:
            return "im üblichen Bereich"
        return f"{noun} weniger als gewöhnlich" if change < 0 else f"{noun} häufiger als gewöhnlich"

    def _count_label(self, value: float) -> str:
        return f"{round(value, 1)} pro Tag"

    def _wakeup_comparison(self, summary: dict[str, Any], profile: dict[str, Any]) -> str | None:
        current = summary.get("wakeup_time")
        baseline = profile.get("average_wakeup_time")
        if not current or not baseline:
            return None
        return f"Persönlich typisch: {baseline}. Heute: {current}."

    def _sleep_comparison(self, summary: dict[str, Any], profile: dict[str, Any]) -> str | None:
        baseline = profile.get("average_sleep_time")
        if not summary.get("last_activity") or not baseline:
            return None
        return f"Persönlich typische letzte Aktivität: {baseline}."

    def _observation_label(self, event: dict[str, Any]) -> str:
        room = self._room_label(event.get("room"))
        role = str(event.get("role") or event.get("device_class") or "Sensor").replace("_", " ")
        state = str(event.get("state") or "").strip()
        return " · ".join(item for item in [room, role, state] if item)

    def _room_label(self, room: Any) -> str:
        labels = {
            "living_room": "Wohnzimmer",
            "kitchen": "Küche",
            "bathroom": "Badezimmer",
            "bedroom": "Schlafzimmer",
            "hallway": "Flur",
            "entrance": "Eingang",
        }
        value = str(room or "")
        return labels.get(value, value)

    def _room_preposition(self, room: Any) -> str:
        label = self._room_label(room)
        if label in {"Eingang"}:
            return "am Eingang"
        if label in {"Badezimmer", "Wohnzimmer"}:
            return f"im {label}"
        return f"in der {label}" if label in {"Küche"} else f"im {label}" if label else "in der Wohnung"

    def _duration_label(self, minutes: int | float | None) -> str:
        value = int(minutes or 0)
        if value <= 0:
            return ""
        if value < 60:
            return f"{value} Min."
        hours = value // 60
        rest = value % 60
        return f"{hours} Std. {rest} Min." if rest else f"{hours} Std."

    def _time_label(self, value: Any) -> str:
        parsed = self._parse_time(value)
        return f"{parsed.hour:02d}:{parsed.minute:02d}"

    def _severity_from_status(self, status: Any) -> str:
        if status == "red":
            return "critical"
        if status == "orange":
            return "warning"
        if status == "yellow":
            return "notice"
        return "normal"

    def _status_title(self, status: Any) -> str:
        if status == "red":
            return "Kritischer Hinweis"
        if status == "orange":
            return "Auffälligkeit"
        if status == "yellow":
            return "Leichte Abweichung"
        return "Normaler Verlauf"

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
        if self._is_smoke_role(event):
            return "safety"
        if self._is_door_event(event):
            return "door"
        if self._is_presence_role(event):
            return "presence"
        if self._is_motion_entity(event):
            return "motion"
        return "sensor_state"

    def _is_activity_event(self, event: dict[str, Any]) -> bool:
        return self._is_on(event.get("state")) and not self._is_smoke_role(event) and (self._is_presence_role(event) or self._is_motion_entity(event) or self._is_door_event(event))

    def _is_door_event(self, event: dict[str, Any]) -> bool:
        if self._is_smoke_role(event):
            return False
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

    def _smart_meter_snapshot_events(self, snapshot: list[dict[str, Any]], timestamp: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for item in snapshot:
            event_type = self._smart_meter_event_type(item)
            if not event_type:
                continue
            state = item.get("state")
            if self._number(state) is None:
                continue
            events.append({
                "role": event_type,
                "room": item.get("room") or item.get("area_id"),
                "entity_id": item.get("entity_id") or item.get("source_ref") or item.get("unique_id"),
                "state": state,
                "device_class": item.get("device_class"),
                "source": "smart_meter_snapshot",
                "last_changed": item.get("last_changed") or item.get("changed_at") or timestamp,
                "last_updated": item.get("last_updated") or timestamp,
            })
        return events

    def _smart_meter_event_type(self, item: dict[str, Any]) -> str | None:
        dc = self._device_class(item)
        payload_key = str(item.get("payload_key") or "").strip().lower()
        text = self._entity_text(item)
        if dc == "energy" or payload_key in {"energy", "energy_consumption", "electricity", "electricity_consumption"}:
            return "energy_consumption"
        if dc == "power" or payload_key in {"power", "power_usage"}:
            return "power_usage"
        if dc == "water" or payload_key in {"water", "water_consumption"}:
            return "water_consumption"
        if dc == "gas" or payload_key in {"gas", "gas_consumption"}:
            return "gas_consumption"
        if any(term in text for term in ["energy", "electricity", "stromzaehler", "stromzähler", "kwh"]):
            return "energy_consumption"
        if any(term in text for term in ["power", "leistung", "watt"]):
            return "power_usage"
        if any(term in text for term in ["water", "wasserzaehler", "wasserzähler"]):
            return "water_consumption"
        if any(term in text for term in ["gas", "gaszaehler", "gaszähler"]):
            return "gas_consumption"
        return None

    def _utility_usage_summary(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        meter_events = [event for event in history if str(event.get("role") or "") in {"energy_consumption", "power_usage", "water_consumption", "gas_consumption"}]
        today = datetime.now(timezone.utc).date()
        by_type: dict[str, dict[date, list[tuple[datetime, float]]]] = defaultdict(lambda: defaultdict(list))
        latest: dict[str, dict[str, Any]] = {}
        for event in meter_events:
            value = self._number(event.get("state"))
            if value is None:
                continue
            event_time = self._parse_time(event.get("event_time"))
            event_type = str(event.get("role") or "")
            by_type[event_type][event_time.date()].append((event_time, value))
            latest[event_type] = {
                "event_type": event_type,
                "value": value,
                "entity_id": event.get("entity_id"),
                "room": event.get("room"),
                "event_time": event_time.isoformat(timespec="seconds"),
            }

        summaries = []
        low_usage_today = False
        for event_type, by_day in sorted(by_type.items()):
            today_delta = self._meter_delta(by_day.get(today, []))
            previous_deltas = [self._meter_delta(values) for day, values in by_day.items() if day != today]
            previous_deltas = [value for value in previous_deltas if value is not None and value > 0]
            historical_average = round(sum(previous_deltas) / len(previous_deltas), 3) if previous_deltas else None
            low_usage = bool(today_delta is not None and historical_average and historical_average > 0 and today_delta <= historical_average * 0.3)
            low_usage_today = low_usage_today or low_usage
            summaries.append({
                "event_type": event_type,
                "today_delta": today_delta,
                "historical_daily_average": historical_average,
                "low_usage_today": low_usage,
                "latest": latest.get(event_type),
            })
        return {
            "meters_configured": bool(summaries),
            "low_usage_today": low_usage_today,
            "meters": summaries,
        }

    def _meter_delta(self, values: list[tuple[datetime, float]]) -> float | None:
        if len(values) < 2:
            return None
        ordered = sorted(values, key=lambda item: item[0])
        delta = ordered[-1][1] - ordered[0][1]
        return round(delta, 3) if delta >= 0 else None

    def _fp300_analysis(
        self,
        roles: list[dict[str, Any]],
        source_snapshot: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        devices = []
        for role in roles:
            if not self._is_presence_role(role):
                continue
            related = self._related_presence_entities(role, source_snapshot)
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

    def _related_presence_entities(self, role: dict[str, Any], source_snapshot: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
        role_entity = str(role.get("entity_id") or "")
        device_id = str(role.get("device_id") or "").strip()
        same_device = [item for item in source_snapshot if device_id and str(item.get("device_id") or "") == device_id]
        if not same_device:
            prefix = role_entity.rsplit("_", 1)[0] if "_" in role_entity else role_entity.rsplit(".", 1)[-1]
            same_device = [item for item in source_snapshot if prefix and str(item.get("entity_id") or "").startswith(prefix)]
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
        if self._is_smoke_role(role):
            return False
        text = self._entity_text(role)
        return str(role.get("role") or "").endswith("presence") or self._is_presence_entity(role) or "occupy" in text

    def _is_presence_entity(self, item: dict[str, Any]) -> bool:
        if self._is_smoke_role(item):
            return False
        dc = self._device_class(item)
        text = self._entity_text(item)
        return dc in {"occupancy", "presence"} or any(term in text for term in ["presence", "praesenz", "präsenz", "occupancy", "occupy"])

    def _is_motion_entity(self, item: dict[str, Any]) -> bool:
        if self._is_smoke_role(item):
            return False
        dc = self._device_class(item)
        text = self._entity_text(item)
        return dc == "motion" or any(term in text for term in ["motion", "bewegung", "pir_detection", "pir detection", "pir"])

    def _is_smoke_role(self, item: dict[str, Any]) -> bool:
        text = self._entity_text(item)
        return self._device_class(item) == "smoke" or any(term in text for term in ["smoke", "rauch", "fire_alarm"])

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
        return str(value or "").strip().lower() in {"on", "true", "detected", "occupied", "home", "present", "open", "active", "motion", "moving", "1"}

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
                "stale": role.get("stale"),
                "stale_seconds": role.get("stale_seconds"),
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
        data["data_class"] = data.get("data_class") or classify_assessment(data.get("status"))
        data["aggregation_level"] = data.get("aggregation_level") or aggregation_for_data_class(str(data["data_class"]))
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
