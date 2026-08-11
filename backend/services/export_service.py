from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.aal_roles import DEFAULT_CONTACT_AAL_ROLE, can_access_data_classes, normalize_aal_role
from backend.services.audit_service import record_audit
from backend.services.consent_service import ConsentService
from backend.services.data_classification import aggregation_for_data_class
from backend.services.device_mapping_service import DeviceMappingService, now

DEFAULT_EXPORT_PURPOSE = "aal_partner_export"
DEFAULT_EXPORT_DATA_CLASSES = ["technical", "utility", "health_adjacent", "emergency"]
EXPORT_TOKEN_DAYS = 30


class ExportService:
    def __init__(self, mapping: DeviceMappingService | None = None, sentero: Any | None = None, sensors: Any | None = None) -> None:
        self.mapping = mapping or DeviceMappingService()
        self.sentero = sentero
        self.sensors = sensors
        self.consent = ConsentService(self.mapping)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists aal_export_tokens (
                    id integer primary key autoincrement,
                    contact_id integer not null,
                    actor_role text not null,
                    purpose text not null,
                    data_classes_json text not null default '[]',
                    token_hash text not null unique,
                    expires_at text not null,
                    revoked_at text,
                    created_at text not null,
                    updated_at text not null,
                    last_used_at text,
                    foreign key(contact_id) references trusted_contacts(id)
                )"""
            )
            con.execute("create index if not exists idx_aal_export_tokens_hash on aal_export_tokens(token_hash)")
            con.execute(
                """create table if not exists aal_export_audit (
                    id integer primary key autoincrement,
                    token_id integer,
                    contact_id integer,
                    actor_role text,
                    purpose text not null,
                    export_type text not null,
                    data_classes_json text not null default '[]',
                    period_start text,
                    period_end text,
                    aggregation_level text not null default 'summary',
                    raw_data_included integer not null default 0,
                    status text not null,
                    created_at text not null
                )"""
            )
            con.commit()

    def create_token(self, payload: dict[str, Any]) -> dict[str, Any]:
        contact_id = int(payload.get("contact_id") or 0)
        purpose = str(payload.get("purpose") or DEFAULT_EXPORT_PURPOSE).strip()
        requested_classes = clean_data_classes(payload.get("data_classes") or DEFAULT_EXPORT_DATA_CLASSES)
        expires_at = str(payload.get("expires_at") or "").strip()
        if not expires_at:
            expires_at = (datetime.now(timezone.utc) + timedelta(days=EXPORT_TOKEN_DAYS)).isoformat(timespec="seconds")
        if not contact_id:
            raise ValueError("contact_id is required")
        if not requested_classes:
            raise ValueError("data_classes are required")
        with self.mapping.connect() as con:
            contact = con.execute("select * from trusted_contacts where id = ? and active = 1", (contact_id,)).fetchone()
            if not contact:
                raise ValueError("contact not found")
            actor_role = normalize_aal_role(contact["actor_role"], default=DEFAULT_CONTACT_AAL_ROLE)
        if not can_access_data_classes(actor_role, requested_classes, aggregation_level="summary"):
            raise ValueError("actor role may not access requested data classes")
        if not self.consent.has_active_consent(contact_id, purpose, requested_classes):
            raise ValueError("active consent is required for this export purpose")
        token = secrets.token_urlsafe(32)
        timestamp = now()
        with self.mapping.connect() as con:
            cur = con.execute(
                """insert into aal_export_tokens
                   (contact_id, actor_role, purpose, data_classes_json, token_hash, expires_at, revoked_at, created_at, updated_at, last_used_at)
                   values (?, ?, ?, ?, ?, ?, null, ?, ?, null)""",
                (contact_id, actor_role, purpose, json.dumps(requested_classes), hash_token(token), expires_at, timestamp, timestamp),
            )
            con.commit()
            row = con.execute("select * from aal_export_tokens where id = ?", (int(cur.lastrowid),)).fetchone()
        record_audit(
            self.mapping,
            event_type="export_token_created",
            category="security",
            status="active",
            summary="Export-Token erstellt.",
            contact_id=contact_id,
            actor_role=actor_role,
            purpose=purpose,
            data_classes=requested_classes,
            metadata={"expires_at": expires_at},
        )
        return {"token": token, "record": self._public_token(dict(row))}

    def list_tokens(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select e.*, t.name as contact_name, t.email as contact_email
                   from aal_export_tokens e
                   left join trusted_contacts t on t.id = e.contact_id
                   order by e.revoked_at is not null, e.created_at desc, e.id desc"""
            ).fetchall()
        return {"tokens": [self._public_token(dict(row)) for row in rows]}

    def revoke_token(self, token_id: int) -> dict[str, Any]:
        timestamp = now()
        revoked: dict[str, Any] | None = None
        with self.mapping.connect() as con:
            row = con.execute("select * from aal_export_tokens where id = ?", (int(token_id),)).fetchone()
            revoked = dict(row) if row else None
            con.execute("update aal_export_tokens set revoked_at = coalesce(revoked_at, ?), updated_at = ? where id = ?", (timestamp, timestamp, int(token_id)))
            con.commit()
        if revoked:
            record_audit(
                self.mapping,
                event_type="export_token_revoked",
                category="security",
                status="revoked",
                summary="Export-Token widerrufen.",
                contact_id=revoked.get("contact_id"),
                actor_role=revoked.get("actor_role"),
                purpose=revoked.get("purpose"),
                data_classes=clean_data_classes(json.loads(revoked.get("data_classes_json") or "[]")),
            )
        return self.list_tokens()

    def export(self, token: str, export_type: str, period_start: str | None = None, period_end: str | None = None) -> dict[str, Any]:
        record = self._token_record(token)
        classes = clean_data_classes(json.loads(record.get("data_classes_json") or "[]"))
        export_type = str(export_type or "").strip()
        start, end = normalized_period(period_start, period_end)
        if export_type == "daily-status":
            payload = self._daily_status(record, classes, start, end)
        elif export_type == "event-summary":
            payload = self._event_summary(record, classes, start, end)
        elif export_type == "system-status":
            payload = self._system_status(record, classes, start, end)
        else:
            raise ValueError("unsupported export type")
        self._mark_used(int(record["id"]))
        self._audit(record, export_type, classes, start, end, "sent")
        return payload

    def _daily_status(self, token: dict[str, Any], classes: list[str], period_start: str, period_end: str) -> dict[str, Any]:
        assessment = self.sentero.latest_behavior() if self.sentero else None
        dashboard = self.sensors.dashboard() if self.sensors else {}
        body: dict[str, Any] = {
            "assessment": keep_if_class(assessment, classes),
            "summary": filter_dict_by_class((dashboard or {}).get("summary") or {}, "technical", classes),
            "utility_usage": filter_utility((dashboard or {}).get("utility_usage") or {}, classes),
        }
        return self._envelope(token, "daily-status", classes, period_start, period_end, body)

    def _event_summary(self, token: dict[str, Any], classes: list[str], period_start: str, period_end: str) -> dict[str, Any]:
        timeline = self.sentero.behavior_timeline_today(live_snapshot=False) if self.sentero else {"events": []}
        events = [event for event in timeline.get("events", []) if event.get("data_class") in classes and period_contains(event.get("event_time"), period_start, period_end)]
        by_class = Counter(str(event.get("data_class") or "technical") for event in events)
        by_room = Counter(str(event.get("room") or "unknown") for event in events)
        body = {
            "event_count": len(events),
            "by_data_class": dict(by_class),
            "by_room": dict(by_room),
            "latest_event_at": max((str(event.get("event_time") or "") for event in events), default=None),
        }
        return self._envelope(token, "event-summary", classes, period_start, period_end, body)

    def _system_status(self, token: dict[str, Any], classes: list[str], period_start: str, period_end: str) -> dict[str, Any]:
        sensor_roles = self.mapping.roles(dev=False, include_state=True)
        body = {
            "sensor_source": self.sensors.source_status() if self.sensors else {},
            "sensor_count": len(sensor_roles),
            "offline_sensors": sum(1 for sensor in sensor_roles if sensor.get("reachable") is False),
            "low_batteries": sum(1 for sensor in sensor_roles if isinstance(sensor.get("battery_level"), (int, float)) and sensor.get("battery_level") < 30),
        }
        return self._envelope(token, "system-status", ["technical"], period_start, period_end, body if "technical" in classes else {})

    def _envelope(self, token: dict[str, Any], export_type: str, classes: list[str], period_start: str, period_end: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "meta": {
                "export_type": export_type,
                "recipient": {
                    "contact_id": token.get("contact_id"),
                    "actor_role": token.get("actor_role"),
                },
                "purpose": token.get("purpose"),
                "data_classes": classes,
                "period": {"start": period_start, "end": period_end},
                "aggregation_level": "summary",
                "raw_data_included": False,
                "generated_at": now(),
            },
            "data": body,
        }

    def _token_record(self, token: str) -> dict[str, Any]:
        token_hash = hash_token(str(token or "").strip())
        with self.mapping.connect() as con:
            row = con.execute(
                """select e.*, t.active as contact_active
                   from aal_export_tokens e
                   left join trusted_contacts t on t.id = e.contact_id
                   where e.token_hash = ?""",
                (token_hash,),
            ).fetchone()
        if not row:
            raise PermissionError("invalid export token")
        record = dict(row)
        if record.get("revoked_at"):
            raise PermissionError("export token revoked")
        if not record.get("contact_active"):
            raise PermissionError("export recipient inactive")
        if is_expired(record.get("expires_at")):
            raise PermissionError("export token expired")
        classes = clean_data_classes(json.loads(record.get("data_classes_json") or "[]"))
        if not can_access_data_classes(record.get("actor_role"), classes, aggregation_level="summary"):
            raise PermissionError("actor role denied")
        if not self.consent.has_active_consent(record.get("contact_id"), record.get("purpose"), classes):
            raise PermissionError("active consent missing")
        return record

    def _mark_used(self, token_id: int) -> None:
        with self.mapping.connect() as con:
            con.execute("update aal_export_tokens set last_used_at = ?, updated_at = ? where id = ?", (now(), now(), token_id))
            con.commit()

    def _audit(self, token: dict[str, Any], export_type: str, classes: list[str], period_start: str, period_end: str, status: str) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """insert into aal_export_audit
                   (token_id, contact_id, actor_role, purpose, export_type, data_classes_json, period_start, period_end, aggregation_level, raw_data_included, status, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, 'summary', 0, ?, ?)""",
                (token.get("id"), token.get("contact_id"), token.get("actor_role"), token.get("purpose"), export_type, json.dumps(classes), period_start, period_end, status, now()),
            )
            con.commit()

    def _public_token(self, row: dict[str, Any]) -> dict[str, Any]:
        classes = clean_data_classes(json.loads(row.get("data_classes_json") or "[]"))
        return {
            "id": row.get("id"),
            "contact_id": row.get("contact_id"),
            "contact_name": row.get("contact_name"),
            "contact_email": row.get("contact_email"),
            "actor_role": row.get("actor_role"),
            "purpose": row.get("purpose"),
            "data_classes": classes,
            "expires_at": row.get("expires_at"),
            "revoked_at": row.get("revoked_at"),
            "last_used_at": row.get("last_used_at"),
            "active": not row.get("revoked_at") and not is_expired(row.get("expires_at")),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clean_data_classes(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    clean: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in clean:
            clean.append(item)
    return clean


def normalized_period(period_start: str | None, period_end: str | None) -> tuple[str, str]:
    end = period_end or now()
    start = period_start or (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    return start, end


def period_contains(value: Any, period_start: str, period_end: str) -> bool:
    text = str(value or "")
    return bool(text and period_start <= text <= period_end)


def is_expired(value: Any) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


def keep_if_class(value: dict[str, Any] | None, classes: list[str]) -> dict[str, Any] | None:
    if not value:
        return None
    data_class = str(value.get("data_class") or "health_adjacent")
    if data_class not in classes:
        return None
    return {key: item for key, item in value.items() if key not in {"llm_response", "email_body"}}


def filter_dict_by_class(value: dict[str, Any], data_class: str, classes: list[str]) -> dict[str, Any]:
    return value if data_class in classes else {}


def filter_utility(value: dict[str, Any], classes: list[str]) -> dict[str, Any]:
    if "utility" not in classes:
        return {}
    readings = [item for item in value.get("readings", []) if item.get("data_class") == "utility" and item.get("aggregation_level") != "raw"]
    return {**value, "readings": readings}
