from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.agents.sentero.mail.models import (
    DEFAULT_CONTACT_PERMISSIONS,
    OWNER_PERMISSIONS,
    AuthorizedContact,
    MailThreadContext,
    MailPermission,
)
from backend.services.device_mapping_service import DeviceMappingService, now


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_message_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("<") and text.endswith(">") else f"<{text.strip('<>')}>"


def question_hash(question: str) -> str:
    return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()


class MailAssistantStore:
    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            for statement in [
                "alter table trusted_contacts add column email_queries_enabled integer not null default 0",
                "alter table trusted_contacts add column email_permissions text not null default '[]'",
            ]:
                try:
                    con.execute(statement)
                except sqlite3.OperationalError:
                    pass
            con.execute(
                """create table if not exists sentero_mail_queries (
                    id integer primary key autoincrement,
                    received_at text not null,
                    message_id text not null unique,
                    contact_id integer,
                    sender_email text,
                    intent text,
                    confidence real,
                    question_hash text,
                    response_status text not null,
                    response_sent_at text,
                    error_code text,
                    processing_ms integer,
                    created_at text not null,
                    foreign key(contact_id) references trusted_contacts(id)
                )"""
            )
            con.execute("create index if not exists idx_sentero_mail_queries_contact_received on sentero_mail_queries(contact_id, received_at)")
            con.execute(
                """create table if not exists sentero_mail_reviewed_messages (
                    message_id text primary key,
                    review_status text not null,
                    reviewed_at text not null
                )"""
            )
            con.commit()

    def already_processed(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self.mapping.connect() as con:
            row = con.execute("select id from sentero_mail_queries where message_id = ?", (message_id,)).fetchone()
        return row is not None

    def response_was_sent(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self.mapping.connect() as con:
            row = con.execute(
                """select response_sent_at, response_status
                   from sentero_mail_queries
                   where message_id = ?
                   limit 1""",
                (message_id,),
            ).fetchone()
        if not row:
            return False
        return bool(row["response_sent_at"]) or str(row["response_status"] or "") in {"sent", "rate_limited"}

    def already_reviewed(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self.mapping.connect() as con:
            row = con.execute(
                "select 1 from sentero_mail_reviewed_messages where message_id = ?",
                (message_id,),
            ).fetchone()
        return row is not None

    def record_reviewed(self, message_id: str, status: str) -> None:
        if not message_id:
            return
        with self.mapping.connect() as con:
            con.execute(
                """insert or ignore into sentero_mail_reviewed_messages
                   (message_id, review_status, reviewed_at)
                   values (?, ?, ?)""",
                (message_id, str(status or "reviewed"), now()),
            )
            con.commit()

    def find_authorized_contact(self, sender_email: str, recipient_addresses: list[str]) -> tuple[AuthorizedContact | None, str | None]:
        sender = normalize_email(sender_email)
        if not sender:
            return None, "missing_sender"
        with self.mapping.connect() as con:
            row = con.execute(
                "select * from trusted_contacts where lower(email) = ? and active = 1",
                (sender,),
            ).fetchone()
        if not row:
            return None, "unknown_sender"
        data = dict(row)
        if not bool(data.get("email_queries_enabled")):
            return None, "email_queries_disabled"
        return contact_from_row(data), None

    def rate_limit_exceeded(self, contact_id: int, hourly_limit: int, daily_limit: int) -> bool:
        current = datetime.now(timezone.utc)
        hour_since = (current - timedelta(hours=1)).isoformat(timespec="seconds")
        day_since = (current - timedelta(days=1)).isoformat(timespec="seconds")
        with self.mapping.connect() as con:
            hour = con.execute(
                "select count(*) as count from sentero_mail_queries where contact_id = ? and received_at >= ? and response_status not in ('rejected', 'duplicate')",
                (contact_id, hour_since),
            ).fetchone()
            day = con.execute(
                "select count(*) as count from sentero_mail_queries where contact_id = ? and received_at >= ? and response_status not in ('rejected', 'duplicate')",
                (contact_id, day_since),
            ).fetchone()
        return int(hour["count"] if hour else 0) >= hourly_limit or int(day["count"] if day else 0) >= daily_limit

    def find_thread_context(self, message_id: str | None) -> MailThreadContext | None:
        normalized = normalize_message_id(message_id)
        if not normalized:
            return None
        with self.mapping.connect() as con:
            row = con.execute(
                """select *
                   from notification_logs
                   where outgoing_message_id = ?
                   order by created_at desc, id desc
                   limit 1""",
                (normalized,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        return MailThreadContext(
            notification_log_id=int(data["id"]),
            contact_id=int(data["contact_id"]) if data.get("contact_id") is not None else None,
            channel=str(data.get("channel") or ""),
            severity=str(data.get("severity") or ""),
            status=str(data.get("status") or ""),
            message_title=data.get("message_title"),
            created_at=str(data.get("created_at") or ""),
            outgoing_message_id=normalized,
        )

    def record_query(
        self,
        *,
        received_at: str,
        message_id: str,
        contact_id: int | None,
        sender_email: str,
        intent: str | None,
        confidence: float | None,
        question: str,
        response_status: str,
        error_code: str | None = None,
        processing_ms: int | None = None,
        response_sent_at: str | None = None,
    ) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """insert or ignore into sentero_mail_queries
                   (received_at, message_id, contact_id, sender_email, intent, confidence, question_hash,
                    response_status, response_sent_at, error_code, processing_ms, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    received_at,
                    message_id,
                    contact_id,
                    normalize_email(sender_email),
                    intent,
                    confidence,
                    question_hash(question) if question else None,
                    response_status,
                    response_sent_at,
                    error_code,
                    processing_ms,
                    now(),
                ),
            )
            con.commit()


def contact_from_row(row: dict[str, Any]) -> AuthorizedContact:
    role = str(row.get("actor_role") or "relative").lower()
    permissions = OWNER_PERMISSIONS if role in {"owner", "admin", "resident"} or bool(row.get("primary_contact")) else DEFAULT_CONTACT_PERMISSIONS
    raw = row.get("email_permissions")
    try:
        configured = json.loads(raw or "[]")
    except json.JSONDecodeError:
        configured = []
    if configured:
        permissions = {MailPermission(item) for item in configured if item in MailPermission._value2member_map_}
    return AuthorizedContact(
        id=int(row["id"]),
        name=str(row.get("name") or ""),
        email=normalize_email(row.get("email")),
        permissions=permissions,
        primary_contact=bool(row.get("primary_contact")),
    )
