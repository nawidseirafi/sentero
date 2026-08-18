from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any

from backend.agents.sentero.mail.models import INTENT_PERMISSIONS, AuthorizedContact, MailIntent, MailPermission, MailThreadContext, QueryResult
from backend.services.device_mapping_service import DeviceMappingService, ROOM_LABELS
from backend.services.service import SenteroService

ACTIVITY_CLASSES = {"presence", "motion", "occupancy"}
ENV_CLASSES = {"temperature", "humidity"}


class MailQueryService:
    def __init__(self, mapping: DeviceMappingService, sentero: SenteroService, *, fresh_seconds: int = 120, recent_seconds: int = 900, stale_seconds: int = 1800) -> None:
        self.mapping = mapping
        self.sentero = sentero
        self.fresh_seconds = fresh_seconds
        self.recent_seconds = recent_seconds
        self.stale_seconds = stale_seconds

    def query(self, intent: MailIntent, contact: AuthorizedContact, context: MailThreadContext | None = None) -> QueryResult:
        required = INTENT_PERMISSIONS.get(intent, set())
        if required and not required.issubset(contact.permissions):
            return self._with_context(QueryResult(intent=intent, status="permission_denied", permission_denied=True), context)
        if intent == MailIntent.HELP:
            return self._with_context(QueryResult(intent=intent, status="ok"), context)
        if intent == MailIntent.STATUS_SUMMARY:
            return self._with_context(self._status_summary(contact), context)
        if intent == MailIntent.CURRENT_ACTIVITY:
            return self._with_context(self._current_activity(), context)
        if intent == MailIntent.LAST_ACTIVITY:
            return self._with_context(self._last_activity(include_room=False), context)
        if intent == MailIntent.LAST_ROOM:
            return self._with_context(self._last_activity(include_room=True), context)
        if intent == MailIntent.TODAY_SUMMARY:
            return self._with_context(self._today_summary(), context)
        if intent == MailIntent.ANOMALIES:
            return self._with_context(self._anomalies(), context)
        if intent == MailIntent.ENVIRONMENT:
            return self._with_context(self._environment(), context)
        if intent == MailIntent.NIGHT_SUMMARY:
            return self._with_context(self._night_summary(), context)
        if intent == MailIntent.SENSOR_HEALTH:
            return self._with_context(self._sensor_health(), context)
        return self._with_context(QueryResult(intent=intent, status="unknown"), context)

    def _with_context(self, result: QueryResult, context: MailThreadContext | None) -> QueryResult:
        if not context:
            return result
        result.facts["thread_context"] = {
            "notification_log_id": context.notification_log_id,
            "contact_id": context.contact_id,
            "severity": context.severity,
            "status": context.status,
            "message_title": context.message_title,
            "created_at": context.created_at,
            "outgoing_message_id": context.outgoing_message_id,
        }
        return result

    def _status_summary(self, contact: AuthorizedContact) -> QueryResult:
        latest = self.sentero.latest_behavior()
        activity = self._latest_activity_event()
        env = self._latest_environment()
        dashboard = self._dashboard_summary(latest, activity, contact)
        facts = {
            "assessment": latest,
            "last_activity": activity,
            "environment": env,
            "dashboard": dashboard,
        }
        has_status_data = bool(activity or latest or dashboard.get("configured_sensor_count"))
        return QueryResult(intent=MailIntent.STATUS_SUMMARY, status="ok" if has_status_data else "no_data", facts=facts, data_available=has_status_data)

    def _current_activity(self) -> QueryResult:
        event = self._latest_activity_event()
        if not event:
            return QueryResult(intent=MailIntent.CURRENT_ACTIVITY, status="no_data", data_available=False)
        event["freshness"] = self._freshness(event.get("event_time"))
        return QueryResult(intent=MailIntent.CURRENT_ACTIVITY, status="ok", facts={"activity": event})

    def _last_activity(self, include_room: bool) -> QueryResult:
        event = self._latest_activity_event()
        intent = MailIntent.LAST_ROOM if include_room else MailIntent.LAST_ACTIVITY
        if not event:
            return QueryResult(intent=intent, status="no_data", data_available=False)
        event["freshness"] = self._freshness(event.get("event_time"))
        return QueryResult(intent=intent, status="ok", facts={"activity": event})

    def _today_summary(self) -> QueryResult:
        timeline = self.sentero.behavior_timeline_today(live_snapshot=False)
        events = [event for event in timeline.get("events") or [] if self._is_activity_event(event)]
        latest = events[-1] if events else None
        if latest:
            latest["freshness"] = self._freshness(latest.get("event_time"))
        rooms: dict[str, int] = {}
        for event in events:
            room = self._room_label(event.get("room"))
            rooms[room] = rooms.get(room, 0) + 1
        return QueryResult(intent=MailIntent.TODAY_SUMMARY, status="ok" if events else "no_data", facts={"event_count": len(events), "last_activity": latest, "rooms": rooms}, data_available=bool(events))

    def _anomalies(self) -> QueryResult:
        latest = self.sentero.latest_behavior()
        findings = latest.get("findings") if latest else []
        status = str(latest.get("status") or "normal") if latest else "unknown"
        return QueryResult(intent=MailIntent.ANOMALIES, status="ok" if latest else "no_data", facts={"assessment": latest, "status": status, "findings": findings or []}, data_available=bool(latest))

    def _environment(self) -> QueryResult:
        env = self._latest_environment()
        if not env:
            return QueryResult(intent=MailIntent.ENVIRONMENT, status="no_data", data_available=False)
        return QueryResult(intent=MailIntent.ENVIRONMENT, status="ok", facts={"environment": env})

    def _night_summary(self) -> QueryResult:
        now_dt = datetime.now(timezone.utc)
        start = datetime.combine(now_dt.date(), time(22, 0), tzinfo=timezone.utc) - timedelta(days=1)
        end = datetime.combine(now_dt.date(), time(6, 0), tzinfo=timezone.utc)
        with self.mapping.connect() as con:
            rows = con.execute(
                """select * from sentero_sensor_events
                   where event_time >= ? and event_time <= ?
                   order by event_time asc""",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        events = [dict(row) for row in rows if self._is_activity_event(dict(row))]
        latest = events[-1] if events else None
        if latest:
            latest["room_label"] = self._room_label(latest.get("room"))
        return QueryResult(intent=MailIntent.NIGHT_SUMMARY, status="ok" if events else "no_data", facts={"night_activity_count": len(events), "last_activity": latest})

    def _sensor_health(self) -> QueryResult:
        roles = self.mapping.roles(dev=True, include_state=True)
        unreachable = [role for role in roles if role.get("reachable") is False]
        low_battery = [role for role in roles if isinstance(role.get("battery_level"), (int, float)) and role.get("battery_level") < 30]
        latest = max((self._parse_time(role.get("last_changed") or role.get("last_updated") or role.get("updated_at")) for role in roles if role.get("last_changed") or role.get("last_updated") or role.get("updated_at")), default=None)
        return QueryResult(intent=MailIntent.SENSOR_HEALTH, status="ok", facts={"sensor_count": len(roles), "unreachable": unreachable, "low_battery": low_battery, "latest_sensor_update": latest.isoformat(timespec="seconds") if latest else None})

    def _dashboard_summary(self, assessment: dict[str, Any] | None, activity: dict[str, Any] | None, contact: AuthorizedContact) -> dict[str, Any]:
        roles = self.mapping.roles(dev=True, include_state=True)
        configured_roles = [role for role in roles if role.get("active", True)]
        timeline = self.sentero.behavior_timeline_today(live_snapshot=False)
        events = [event for event in timeline.get("events") or [] if self._is_activity_event(event)]
        if not events and activity:
            events = [activity]
        current_presence = self._current_presence_event(events)
        first_activity = self._first_activity_event(events)
        latest_activity = self._latest_event(events) or activity
        learning = self.sentero.behavior_learning_status()
        can_see_sensor_health = MailPermission.TECHNICAL_HEALTH in contact.permissions
        dashboard: dict[str, Any] = {
            "person_name": self._profile_name(),
            "current_location": self._location_label(current_presence.get("room") if current_presence else None),
            "current_presence": current_presence,
            "behavior_status": str((assessment or {}).get("status") or "green"),
            "behavior_label": self._behavior_label((assessment or {}).get("status")),
            "behavior_summary": (assessment or {}).get("summary") or "Sentero baut ein persönliches Normalverhalten auf.",
            "learning": learning,
            "activity_event_count_today": len(events),
            "first_activity": first_activity,
            "last_activity": latest_activity,
            "activity_slots": self._activity_slots(events),
            "configured_sensor_count": len(configured_roles),
        }
        if can_see_sensor_health:
            dashboard["sensor_health"] = self._sensor_health_facts(configured_roles)
        return dashboard

    def _sensor_health_facts(self, roles: list[dict[str, Any]]) -> dict[str, Any]:
        unreachable = [self._sensor_health_item(role) for role in roles if role.get("reachable") is False]
        low_battery = [self._sensor_health_item(role) for role in roles if isinstance(role.get("battery_level"), (int, float)) and role.get("battery_level") < 30]
        batteries = [self._sensor_health_item(role) for role in roles if isinstance(role.get("battery_level"), (int, float))]
        return {
            "sensor_count": len(roles),
            "unreachable": unreachable,
            "low_battery": low_battery,
            "batteries": batteries,
        }

    def _sensor_health_item(self, role: dict[str, Any]) -> dict[str, Any]:
        return {
            "label": role.get("friendly_name") or role.get("label") or role.get("role") or "Sensor",
            "room_label": self._room_label(role.get("room")),
            "role": role.get("role"),
            "reachable": role.get("reachable"),
            "battery_level": role.get("battery_level"),
            "last_updated": role.get("last_updated") or role.get("last_changed") or role.get("updated_at"),
        }

    def _latest_activity_event(self) -> dict[str, Any] | None:
        with self.mapping.connect() as con:
            rows = con.execute("select * from sentero_sensor_events order by event_time desc, id desc limit 50").fetchall()
        for row in rows:
            event = dict(row)
            if self._is_activity_event(event):
                event["room_label"] = self._room_label(event.get("room"))
                return event
        return None

    def _current_presence_event(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        by_role: dict[str, dict[str, Any]] = {}
        for event in events:
            role = str(event.get("role") or "").lower()
            if not (role.endswith("_presence") or role.endswith("_motion") or any(item in role for item in ACTIVITY_CLASSES)):
                continue
            current = by_role.get(role)
            event_time = self._parse_time(event.get("event_time"))
            current_time = self._parse_time(current.get("event_time")) if current else None
            if event_time and (not current_time or event_time > current_time):
                by_role[role] = event
        active = [event for event in by_role.values() if self._is_activity_event(event)]
        return self._latest_event(active)

    def _first_activity_event(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid = [event for event in events if self._parse_time(event.get("event_time"))]
        if not valid:
            return None
        event = sorted(valid, key=lambda item: self._parse_time(item.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc))[0]
        return {**event, "room_label": self._room_label(event.get("room"))}

    def _latest_event(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid = [event for event in events if self._parse_time(event.get("event_time"))]
        if not valid:
            return None
        event = sorted(valid, key=lambda item: self._parse_time(item.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0]
        return {**event, "room_label": self._room_label(event.get("room")), "freshness": self._freshness(event.get("event_time"))}

    def _activity_slots(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        slots = [{"hour": hour, "label": str(hour).zfill(2), "active": False} for hour in (0, 6, 12, 18, 24)]
        for event in events:
            parsed = self._parse_time(event.get("event_time"))
            if not parsed:
                continue
            local_hour = parsed.astimezone().hour
            for index, slot in enumerate(slots):
                next_hour = slots[index + 1]["hour"] if index + 1 < len(slots) else 24
                if slot["hour"] <= local_hour < next_hour:
                    slot["active"] = True
                    break
        return slots

    def _profile_name(self) -> str:
        with self.mapping.connect() as con:
            row = con.execute("select name from sentero_profile where id = 1").fetchone()
        name = str(row["name"] if row else "").strip()
        return name or "Person"

    def _location_label(self, room: Any) -> str:
        return f"Im {self._room_label(room)}" if room else "Nicht im Haus"

    def _behavior_label(self, status: Any) -> str:
        value = str(status or "").lower()
        if value == "red":
            return "Kritisch"
        if value == "orange":
            return "Auffällig"
        if value == "yellow":
            return "Leichte Abweichung"
        return "Normal"

    def _latest_environment(self) -> dict[str, Any] | None:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select * from sentero_sensor_events
                   where device_class in ('temperature', 'humidity') or role like '%temperature%' or role like '%humidity%'
                   order by event_time desc, id desc limit 20"""
            ).fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            event = dict(row)
            key = "temperature_c" if "temp" in str(event.get("device_class") or event.get("role") or "") else "humidity_percent"
            if key in result:
                continue
            value = _number(event.get("state"))
            if value is None:
                continue
            result[key] = value
            result[f"{key}_at"] = event.get("event_time")
            result[f"{key}_freshness"] = self._freshness(event.get("event_time"))
        return result or None

    def _is_activity_event(self, event: dict[str, Any]) -> bool:
        device_class = str(event.get("device_class") or "").lower()
        role = str(event.get("role") or "").lower()
        state = str(event.get("state") or "").lower()
        return (device_class in ACTIVITY_CLASSES or any(item in role for item in ACTIVITY_CLASSES)) and state not in {"off", "false", "0", "clear", "none", "unknown", "unavailable"}

    def _freshness(self, timestamp: Any) -> dict[str, Any]:
        parsed = self._parse_time(timestamp)
        if not parsed:
            return {"age_seconds": None, "bucket": "unknown"}
        age = max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)
        bucket = "fresh" if age <= self.fresh_seconds else "recent" if age <= self.recent_seconds else "stale" if age >= self.stale_seconds else "old"
        return {"age_seconds": age, "bucket": bucket}

    def _parse_time(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _room_label(self, room: Any) -> str:
        value = str(room or "").strip()
        return ROOM_LABELS.get(value, value or "unbekannter Raum")


def _number(value: Any) -> float | None:
    try:
        return round(float(str(value).replace(",", ".")), 1)
    except (TypeError, ValueError):
        return None
