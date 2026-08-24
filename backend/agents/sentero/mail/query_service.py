from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
import os
from zoneinfo import ZoneInfo
from typing import Any

from backend.agents.sentero.mail.models import INTENT_PERMISSIONS, AuthorizedContact, MailIntent, MailPermission, MailThreadContext, QueryResult
from backend.services.device_mapping_service import DeviceMappingService, ROOM_LABELS
from backend.services.service import SenteroService
from backend.services.environment_labels import illuminance_description, illuminance_display

ACTIVITY_CLASSES = {"presence", "motion", "occupancy"}
ENV_CLASSES = {"temperature", "humidity", "illuminance", "illuminance_lux"}
POWER_ROLES = {"energy_consumption", "power_usage"}
CONTACT_CLASSES = {"contact", "door", "window", "opening"}


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
        if intent == MailIntent.POWER_USAGE:
            return self._with_context(self._power_usage(), context)
        if intent == MailIntent.CONTACT_STATUS:
            return self._with_context(self._contact_status(), context)
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
        # Current presence comes from configured live Sentero roles, not from
        # historical movement. A person may be present while sitting still.
        roles = self._configured_roles(include_state=True)
        presence = self._current_presence_from_roles(roles)
        if presence:
            return QueryResult(intent=MailIntent.CURRENT_ACTIVITY, status="ok", facts={"activity": presence})
        event = self._latest_activity_event()
        if not event:
            return QueryResult(intent=MailIntent.CURRENT_ACTIVITY, status="no_data", data_available=False)
        event["freshness"] = self._freshness(event.get("event_time"))
        event["historical"] = True
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

    def _power_usage(self) -> QueryResult:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select * from sentero_sensor_events
                   where role in ('energy_consumption', 'power_usage')
                      or device_class in ('energy', 'power')
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%strom%'
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%energy%'
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%power%'
                   order by event_time asc, id asc"""
            ).fetchall()
        events = [dict(row) for row in rows if _number(row["state"]) is not None]
        readings = self._latest_meter_readings(events)
        if not readings:
            return QueryResult(intent=MailIntent.POWER_USAGE, status="no_data", data_available=False)
        today = datetime.now(timezone.utc).date()
        deltas: dict[str, float | None] = {}
        for reading in readings:
            if reading.get("kind") != "energy_consumption":
                continue
            entity_id = reading.get("entity_id")
            entity_events = []
            for event in events:
                parsed = self._parse_time(event.get("event_time"))
                if parsed and parsed.date() == today and event.get("entity_id") == entity_id:
                    entity_events.append(event)
            deltas["energy_consumption"] = self._meter_delta(entity_events)
        return QueryResult(intent=MailIntent.POWER_USAGE, status="ok", facts={"readings": readings, "today_deltas": deltas})

    def _contact_status(self) -> QueryResult:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select * from sentero_sensor_events
                   where device_class in ('contact', 'door', 'window', 'opening')
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%door%'
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%tuer%'
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%tür%'
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%fenster%'
                      or lower(coalesce(role, '') || ' ' || coalesce(entity_id, '')) like '%contact%'
                   order by event_time asc, id asc"""
            ).fetchall()
        latest_by_contact: dict[str, dict[str, Any]] = {}
        for row in rows:
            event = dict(row)
            if not self._is_contact_event(event):
                continue
            key = str(event.get("entity_id") or event.get("role") or event.get("id"))
            current = latest_by_contact.get(key)
            current_time = self._parse_time(current.get("event_time")) if current else None
            event_time = self._parse_time(event.get("event_time"))
            if event_time and (not current_time or event_time >= current_time):
                event["room_label"] = self._room_label(event.get("room"))
                event["contact_state"] = self._contact_state(event.get("state"))
                event["freshness"] = self._freshness(event.get("event_time"))
                latest_by_contact[key] = event
        contacts = sorted(latest_by_contact.values(), key=lambda item: str(item.get("role") or item.get("entity_id") or ""))
        if not contacts:
            return QueryResult(intent=MailIntent.CONTACT_STATUS, status="no_data", data_available=False)
        open_contacts = [item for item in contacts if item.get("contact_state") == "open"]
        unknown_contacts = [item for item in contacts if item.get("contact_state") == "unknown"]
        return QueryResult(
            intent=MailIntent.CONTACT_STATUS,
            status="ok",
            facts={"contacts": contacts, "open_contacts": open_contacts, "unknown_contacts": unknown_contacts},
        )

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
        configured_roles = self._configured_roles(include_state=True)
        timeline = self.sentero.behavior_timeline_today(live_snapshot=False)
        events = [event for event in timeline.get("events") or [] if self._is_activity_event(event)]
        if not events and activity:
            events = [activity]

        # Live presence is authoritative for "in house" and room. Motion only
        # describes movement. presence=True + motion=still is still "in room".
        current_presence = self._current_presence_from_roles(configured_roles)
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

    def _configured_roles(self, include_state: bool = True) -> list[dict[str, Any]]:
        roles = self.mapping.roles(dev=True, include_state=include_state)
        return [role for role in roles if role.get("active", True) and role.get("enabled", True)]

    def _current_presence_from_roles(self, roles: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for role in roles:
            if role.get("presence") is not True:
                continue
            at = role.get("last_updated") or role.get("last_changed") or role.get("updated_at")
            motion_raw = role.get("motion_state") if role.get("motion_state") is not None else role.get("motion")
            motion_text = str(motion_raw or "").strip().lower()
            motion_active = motion_text in {"moving", "move", "movement", "motion", "active", "detected", "moving_target", "true", "on", "1"}
            candidates.append({
                "role": role.get("role"),
                "room": role.get("room"),
                "room_label": self._room_label(role.get("room")),
                "entity_id": role.get("entity_id"),
                "state": "on",
                "presence": True,
                "motion_state": motion_raw,
                "motion_active": motion_active,
                "event_time": at,
                "event_time_local": self._local_timestamp(at),
                "event_time_label": self._local_time_label(at),
                "freshness": self._freshness(at),
                "relative_time": self._relative_time_label(at),
                "source": role.get("source"),
                "live": True,
            })
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: self._parse_time(item.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0]

    def _environment_readings_from_configured_role(self, role: dict[str, Any]) -> list[dict[str, Any]]:
        at = role.get("last_updated") or role.get("last_changed") or role.get("updated_at")
        label = role.get("friendly_name") or role.get("label") or role.get("role") or role.get("entity_id")
        room_label = self._room_label(role.get("room"))
        readings: list[dict[str, Any]] = []
        for field, key in (("temperature", "temperature_c"), ("humidity", "humidity_percent"), ("illuminance", "illuminance_lux")):
            value = _number(role.get(field))
            if value is None:
                continue
            readings.append({"key": key, "value": value, "at": at, "freshness": self._freshness(at), "source": "sentero_configured_live", "label": label, "room_label": room_label})
        dedicated = self._environment_reading_from_row(role, source="sentero_configured_live")
        if dedicated and all(item["key"] != dedicated["key"] for item in readings):
            readings.append(dedicated)
        return readings

    def _latest_activity_event(self) -> dict[str, Any] | None:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select *
                   from sentero_sensor_events
                   where (device_class in ('presence', 'motion', 'occupancy')
                          or role like '%presence%'
                          or role like '%motion%'
                          or role like '%occupancy%')
                     and lower(coalesce(state, '')) not in ('off', 'false', '0', 'clear', 'none', 'unknown', 'unavailable')
                   order by event_time desc, id desc
                   limit 1"""
            ).fetchall()
        for row in rows:
            event = dict(row)
            if self._is_activity_event(event):
                event["room_label"] = self._room_label(event.get("room"))
                self._add_time_labels(event)
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
        result = {**event, "room_label": self._room_label(event.get("room"))}
        self._add_time_labels(result)
        return result

    def _latest_event(self, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        valid = [event for event in events if self._parse_time(event.get("event_time"))]
        if not valid:
            return None
        event = sorted(valid, key=lambda item: self._parse_time(item.get("event_time")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[0]
        result = {**event, "room_label": self._room_label(event.get("room")), "freshness": self._freshness(event.get("event_time"))}
        self._add_time_labels(result)
        return result

    def _activity_slots(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        slots = [{"hour": hour, "label": str(hour).zfill(2), "active": False} for hour in (0, 6, 12, 18, 24)]
        for event in events:
            parsed = self._parse_time(event.get("event_time"))
            if not parsed:
                continue
            local_hour = parsed.astimezone(self._sentero_timezone()).hour
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
        # User-facing queries must only use sensors explicitly registered in
        # Sentero. mapping.snapshot() sees every MQTT/Zigbee2MQTT device and is
        # intentionally not used here. Telegram and e-mail share this service.
        result: dict[str, Any] = {}
        try:
            roles = self._configured_roles(include_state=True)
        except Exception:
            roles = []
        for role in roles:
            for reading in self._environment_readings_from_configured_role(role):
                key = str(reading["key"])
                current_time = self._parse_time(result.get(f"{key}_at")) if result.get(f"{key}_at") else None
                reading_time = self._parse_time(reading.get("at"))
                if key not in result or (reading_time and (not current_time or reading_time >= current_time)):
                    self._set_environment_result(result, reading)

        try:
            history = self._latest_environment_history(roles)
        except Exception:
            history = {}
        for key, reading in history.items():
            if key in result:
                continue
            reading["fallback_reason"] = "configured_sensor_last_known_value"
            self._set_environment_result(result, reading)
        return result or None

    def _latest_environment_history(self, configured_roles: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        roles = configured_roles if configured_roles is not None else self._configured_roles(include_state=False)
        allowed_roles = {str(role.get("role") or "").strip() for role in roles if role.get("role")}
        allowed_entities: set[str] = set()
        for role in roles:
            for key in ("entity_id", "source_ref", "primary_entity_id"):
                value = str(role.get(key) or "").strip()
                if value:
                    allowed_entities.add(value)
        if not allowed_roles and not allowed_entities:
            return {}
        with self.mapping.connect() as con:
            rows = con.execute(
                """select * from sentero_sensor_events
                   where device_class in ('temperature', 'humidity', 'illuminance')
                      or role like '%temperature%'
                      or role like '%humidity%'
                      or role like '%illuminance%'
                      or role like '%helligkeit%'
                   order by event_time desc, id desc limit 100"""
            ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            event = dict(row)
            role_name = str(event.get("role") or "").strip()
            entity_id = str(event.get("entity_id") or "").split("#", 1)[0].strip()
            if role_name not in allowed_roles and entity_id not in allowed_entities:
                continue
            reading = self._environment_reading_from_row(event, source="history")
            if not reading:
                continue
            key = str(reading["key"])
            if key not in result:
                result[key] = reading
        return result

    def _environment_reading_from_row(self, row: dict[str, Any], source: str) -> dict[str, Any] | None:
        key = self._environment_key(row)
        if not key:
            return None
        value = _number(row.get("state"))
        if value is None:
            return None
        at = row.get("last_updated") or row.get("last_changed") or row.get("event_time") or row.get("updated_at")
        return {
            "key": key,
            "value": value,
            "at": at,
            "freshness": self._freshness(at),
            "source": source,
            "label": row.get("friendly_name") or row.get("label") or row.get("role") or row.get("entity_id"),
            "room_label": self._room_label(row.get("room") or row.get("area_id")),
        }

    def _environment_key(self, row: dict[str, Any]) -> str | None:
        text = " ".join(str(row.get(key) or "").lower() for key in ("device_class", "role", "entity_id", "friendly_name", "label"))
        if "temperature" in text or "temperatur" in text:
            return "temperature_c"
        if "humidity" in text or "luftfeuchtigkeit" in text or "feuchtigkeit" in text:
            return "humidity_percent"
        if "illuminance" in text or "helligkeit" in text or "lux" in text:
            return "illuminance_lux"
        return None

    def _set_environment_result(self, result: dict[str, Any], reading: dict[str, Any]) -> None:
        key = str(reading["key"])
        result[key] = reading.get("value")
        result[f"{key}_at"] = reading.get("at")
        result[f"{key}_freshness"] = reading.get("freshness")
        result[f"{key}_source"] = reading.get("source")
        result[f"{key}_label"] = reading.get("label")
        result[f"{key}_room_label"] = reading.get("room_label")
        if reading.get("fallback_reason"):
            result[f"{key}_fallback_reason"] = reading.get("fallback_reason")
        if key == "illuminance_lux":
            result["illuminance_description"] = illuminance_description(reading.get("value"))
            result["illuminance_display"] = illuminance_display(reading.get("value"))

    def _latest_meter_readings(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for event in events:
            kind = self._meter_kind(event)
            if not kind:
                continue
            parsed = self._parse_time(event.get("event_time"))
            current = latest.get(kind)
            current_time = self._parse_time(current.get("event_time")) if current else None
            current_priority = int(current.get("priority") or 0) if current else -1
            priority = self._meter_priority(event, kind)
            if parsed and (
                not current_time
                or parsed > current_time
                or (parsed == current_time and priority >= current_priority)
            ):
                latest[kind] = {
                    "kind": kind,
                    "value": self._meter_value(event, kind),
                    "entity_id": event.get("entity_id"),
                    "event_time": event.get("event_time"),
                    "freshness": self._freshness(event.get("event_time")),
                    "room_label": self._room_label(event.get("room")),
                    "label": self._meter_label(kind),
                    "priority": priority,
                }
        return [latest[key] for key in ["power_usage", "energy_consumption"] if key in latest]

    def _meter_kind(self, event: dict[str, Any]) -> str | None:
        role = str(event.get("role") or "").lower()
        device_class = str(event.get("device_class") or "").lower()
        text = f"{role} {event.get('entity_id') or ''}".lower()
        if "counterout" in text or "einspeis" in text:
            return None
        if role in POWER_ROLES:
            return role
        if device_class == "power" or "power" in text or "leistung" in text or "watt" in text:
            return "power_usage"
        if device_class == "energy" or "energy" in text or "strom" in text or "kwh" in text:
            return "energy_consumption"
        return None

    def _meter_priority(self, event: dict[str, Any], kind: str) -> int:
        text = f"{event.get('entity_id') or ''} {event.get('role') or ''}".lower()
        if kind == "power_usage":
            if "avg" in text or "durchschnitt" in text:
                return 10
            if text.endswith("power") or "leistung" in text:
                return 30
            return 20
        if kind == "energy_consumption":
            if "counterint1" in text or "counterint2" in text:
                return 10
            if "netzbezug" in text or text.endswith("counterin") or "energycounterin " in text:
                return 30
            return 20
        return 0

    def _meter_value(self, event: dict[str, Any], kind: str) -> float | None:
        value = _number(event.get("state"))
        if value is None or kind != "energy_consumption":
            return value
        entity_id = str(event.get("entity_id") or "").lower()
        if entity_id.startswith("ecotracker.") and value >= 100000:
            return round(value / 1000, 1)
        return value

    def _meter_delta(self, events: list[dict[str, Any]]) -> float | None:
        values = []
        for event in events:
            parsed = self._parse_time(event.get("event_time"))
            value = self._meter_value(event, "energy_consumption")
            if parsed and value is not None:
                values.append((parsed, value))
        if len(values) < 2:
            return None
        values.sort(key=lambda item: item[0])
        delta = values[-1][1] - values[0][1]
        return round(delta, 1) if delta >= 0 else None

    def _meter_label(self, kind: str) -> str:
        if kind == "power_usage":
            return "aktuelle Leistung"
        if kind == "energy_consumption":
            return "Stromzählerstand"
        return kind

    def _is_contact_event(self, event: dict[str, Any]) -> bool:
        role = str(event.get("role") or "").lower()
        device_class = str(event.get("device_class") or "").lower()
        text = f"{role} {event.get('entity_id') or ''}".lower()
        return device_class in CONTACT_CLASSES or any(term in text for term in ["door", "tuer", "tür", "fenster", "contact"])

    def _contact_state(self, state: Any) -> str:
        value = str(state or "").strip().lower()
        if value in {"on", "open", "true", "1", "opened"}:
            return "open"
        if value in {"off", "closed", "false", "0", "zu"}:
            return "closed"
        return "unknown"

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

    def _sentero_timezone(self) -> ZoneInfo:
        name = str(os.getenv("SENTERO_TIMEZONE") or os.getenv("TZ") or "Europe/Berlin").strip()
        try:
            return ZoneInfo(name)
        except Exception:
            return ZoneInfo("Europe/Berlin")

    def _local_timestamp(self, value: Any) -> str | None:
        parsed = self._parse_time(value)
        if not parsed:
            return None
        return parsed.astimezone(self._sentero_timezone()).isoformat(timespec="seconds")

    def _local_time_label(self, value: Any) -> str | None:
        parsed = self._parse_time(value)
        if not parsed:
            return None
        return parsed.astimezone(self._sentero_timezone()).strftime("%H:%M")

    def _relative_time_label(self, value: Any) -> str | None:
        parsed = self._parse_time(value)
        if not parsed:
            return None
        age = max(int((datetime.now(timezone.utc) - parsed).total_seconds()), 0)
        minutes = age // 60
        if minutes < 1:
            return "gerade eben"
        if minutes < 60:
            return f"vor {minutes} {'Minute' if minutes == 1 else 'Minuten'}"
        hours, rest = divmod(minutes, 60)
        if hours < 24:
            if rest == 0:
                return f"vor {hours} {'Stunde' if hours == 1 else 'Stunden'}"
            return f"vor {hours} {'Stunde' if hours == 1 else 'Stunden'} und {rest} {'Minute' if rest == 1 else 'Minuten'}"
        days = hours // 24
        return f"vor {days} {'Tag' if days == 1 else 'Tagen'}"

    def _add_time_labels(self, event: dict[str, Any]) -> dict[str, Any]:
        value = event.get("event_time")
        event["event_time_local"] = self._local_timestamp(value)
        event["event_time_label"] = self._local_time_label(value)
        event["relative_time"] = self._relative_time_label(value)
        return event

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
