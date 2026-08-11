from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.services.aal_roles import DEFAULT_CONTACT_AAL_ROLE, can_access_data_classes, normalize_aal_role
from backend.services.audit_service import record_audit
from backend.services.device_mapping_service import DeviceMappingService, now

DEFAULT_NOTIFICATION_DATA_CLASSES = ["personal_behavior", "health_adjacent", "emergency"]
DEFAULT_NOTIFICATION_PURPOSE = "behavior_notification"
DEFAULT_NOTIFICATION_RECIPIENT_TYPE = "relative"
VALID_DATA_CLASSES = {
    "technical",
    "environmental",
    "utility",
    "personal_behavior",
    "health_adjacent",
    "emergency",
}


class ConsentService:
    def __init__(self, mapping: DeviceMappingService | None = None) -> None:
        self.mapping = mapping or DeviceMappingService()

    def list(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select c.*, t.name as contact_name, t.relationship as contact_relationship, t.email as contact_email
                   from data_consents c
                   left join trusted_contacts t on t.id = c.contact_id
                   order by c.revoked_at is not null, c.valid_until is not null, c.created_at desc, c.id desc"""
            ).fetchall()
        return {"consents": [self._public(dict(row)) for row in rows]}

    def grant(self, payload: dict[str, Any]) -> dict[str, Any]:
        contact_id = int(payload.get("contact_id") or 0)
        purpose = clean_text(payload.get("purpose") or DEFAULT_NOTIFICATION_PURPOSE)
        recipient_type = normalize_aal_role(payload.get("recipient_type") or DEFAULT_NOTIFICATION_RECIPIENT_TYPE, default=DEFAULT_CONTACT_AAL_ROLE)
        data_classes = clean_data_classes(payload.get("data_classes") or DEFAULT_NOTIFICATION_DATA_CLASSES)
        valid_until = clean_optional_text(payload.get("valid_until"))
        if not contact_id:
            raise ValueError("contact_id is required")
        if not purpose:
            raise ValueError("purpose is required")
        if not data_classes:
            raise ValueError("data_classes are required")
        timestamp = now()
        with self.mapping.connect() as con:
            contact = con.execute("select id, actor_role from trusted_contacts where id = ? and active = 1", (contact_id,)).fetchone()
            if not contact:
                raise ValueError("contact not found")
            contact_role = normalize_aal_role(payload.get("recipient_type") or contact["actor_role"], default=DEFAULT_CONTACT_AAL_ROLE)
            if not can_access_data_classes(contact_role, data_classes, aggregation_level="summary"):
                raise ValueError("actor role may not access requested data classes")
            recipient_type = contact_role
            con.execute(
                """update data_consents
                   set revoked_at = ?, updated_at = ?
                   where contact_id = ? and purpose = ? and revoked_at is null""",
                (timestamp, timestamp, contact_id, purpose),
            )
            con.execute(
                """insert into data_consents
                   (contact_id, recipient_type, purpose, data_classes_json, valid_until, revoked_at, created_at, updated_at)
                   values (?, ?, ?, ?, ?, null, ?, ?)""",
                (contact_id, recipient_type, purpose, json.dumps(data_classes), valid_until, timestamp, timestamp),
            )
            con.commit()
        record_audit(
            self.mapping,
            event_type="consent_granted",
            category="consent",
            status="active",
            summary=f"Freigabe fuer {purpose} erteilt.",
            contact_id=contact_id,
            actor_role=recipient_type,
            purpose=purpose,
            data_classes=data_classes,
        )
        return self.list()

    def revoke(self, consent_id: int) -> dict[str, Any]:
        timestamp = now()
        revoked: dict[str, Any] | None = None
        with self.mapping.connect() as con:
            row = con.execute("select * from data_consents where id = ?", (int(consent_id),)).fetchone()
            revoked = dict(row) if row else None
            con.execute(
                "update data_consents set revoked_at = coalesce(revoked_at, ?), updated_at = ? where id = ?",
                (timestamp, timestamp, int(consent_id)),
            )
            con.commit()
        if revoked:
            record_audit(
                self.mapping,
                event_type="consent_revoked",
                category="consent",
                status="revoked",
                summary=f"Freigabe fuer {revoked.get('purpose')} widerrufen.",
                contact_id=revoked.get("contact_id"),
                actor_role=revoked.get("recipient_type"),
                purpose=revoked.get("purpose"),
                data_classes=clean_data_classes(json.loads(revoked.get("data_classes_json") or "[]")),
            )
        return self.list()

    def ensure_default_notification_consent(self, contact_id: int, recipient_type: str | None = None) -> None:
        if self.has_active_consent(contact_id, DEFAULT_NOTIFICATION_PURPOSE, DEFAULT_NOTIFICATION_DATA_CLASSES):
            return
        self.grant(
            {
                "contact_id": contact_id,
                "recipient_type": recipient_type or DEFAULT_NOTIFICATION_RECIPIENT_TYPE,
                "purpose": DEFAULT_NOTIFICATION_PURPOSE,
                "data_classes": DEFAULT_NOTIFICATION_DATA_CLASSES,
            }
        )

    def has_active_consent(self, contact_id: Any, purpose: str, required_data_classes: list[str] | set[str]) -> bool:
        try:
            normalized_contact_id = int(contact_id)
        except (TypeError, ValueError):
            return False
        required = set(clean_data_classes(list(required_data_classes)))
        if not required:
            return False
        with self.mapping.connect() as con:
            rows = con.execute(
                """select * from data_consents
                   where contact_id = ? and purpose = ? and revoked_at is null
                   order by created_at desc, id desc""",
                (normalized_contact_id, purpose),
            ).fetchall()
        current = datetime.now(timezone.utc)
        for row in rows:
            consent = dict(row)
            if is_expired(consent.get("valid_until"), current):
                continue
            if not can_access_data_classes(consent.get("recipient_type"), required, aggregation_level="summary"):
                continue
            granted = set(clean_data_classes(json.loads(consent.get("data_classes_json") or "[]")))
            if required.issubset(granted):
                return True
        return False

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        data_classes = clean_data_classes(json.loads(row.get("data_classes_json") or "[]"))
        active = row.get("revoked_at") is None and not is_expired(row.get("valid_until"), datetime.now(timezone.utc))
        return {
            "id": row.get("id"),
            "contact_id": row.get("contact_id"),
            "contact_name": row.get("contact_name"),
            "contact_relationship": row.get("contact_relationship"),
            "contact_email": row.get("contact_email"),
            "recipient_type": row.get("recipient_type"),
            "purpose": row.get("purpose"),
            "data_classes": data_classes,
            "valid_until": row.get("valid_until"),
            "revoked_at": row.get("revoked_at"),
            "active": active,
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }


def clean_data_classes(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    clean: list[str] = []
    for value in values:
        item = clean_text(value)
        if item in VALID_DATA_CLASSES and item not in clean:
            clean.append(item)
    return clean


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def clean_optional_text(value: Any) -> str | None:
    text = clean_text(value)
    return text or None


def is_expired(value: Any, reference: datetime) -> bool:
    text = clean_text(value)
    if not text:
        return False
    try:
        expires_at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= reference
