from __future__ import annotations

import time
import json
import re
from email.utils import parseaddr
from typing import Any

from backend.agents.sentero.mail.conversation_service import ConversationService
from backend.agents.sentero.mail.imap_client import ImapMailClient
from backend.agents.sentero.mail.intent_service import MailIntentService
from backend.agents.sentero.mail.models import InboundMail, MailAssistantConfig, MailIntent
from backend.agents.sentero.mail.query_service import MailQueryService
from backend.agents.sentero.mail.response_service import MailResponseService
from backend.agents.sentero.mail.store import MailAssistantStore
from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.notification_service import NotificationService, sentero_mail_from
from backend.services.service import SenteroService

logger = get_logger(__name__)

REPLY_SEPARATOR_RE = re.compile(
    r"^\s*(?:"
    r"[-_]{2,}\s*(?:original message|ursprüngliche nachricht|weitergeleitete nachricht)\s*[-_]{2,}|"
    r"(?:am|on)\s+.+\b(?:schrieb|wrote)\b.*:|"
    r"from:|von:|sent:|gesendet:|to:|an:|cc:|betreff:|subject:"
    r")",
    re.I,
)


class SenteroMailAssistant:
    def __init__(
        self,
        mapping: DeviceMappingService,
        sentero: SenteroService,
        notification: NotificationService,
        config: MailAssistantConfig | None = None,
        imap_client: ImapMailClient | None = None,
        conversation: ConversationService | None = None,
    ) -> None:
        self.mapping = mapping
        self.sentero = sentero
        self.notification = notification
        self._fixed_config = config
        self.config = config or config_from_notification_settings(mapping)
        self.store = MailAssistantStore(mapping)
        self.intent = MailIntentService()
        self.query_service = MailQueryService(
            mapping,
            sentero,
            fresh_seconds=self.config.fresh_seconds,
            recent_seconds=self.config.recent_seconds,
            stale_seconds=self.config.stale_seconds,
        )
        self.response = MailResponseService()
        self.conversation = conversation or ConversationService()
        self.imap = imap_client or ImapMailClient(self.config)

    def _refresh_runtime_config(self) -> None:
        self.config = self._fixed_config or config_from_notification_settings(self.mapping)
        self.query_service.fresh_seconds = self.config.fresh_seconds
        self.query_service.recent_seconds = self.config.recent_seconds
        self.query_service.stale_seconds = self.config.stale_seconds
        self.imap.config = self.config

    def enabled(self) -> bool:
        self._refresh_runtime_config()
        return self.config.enabled

    def poll_once(self) -> dict[str, Any]:
        self._refresh_runtime_config()
        if not self.enabled():
            return {"processed": 0, "skipped": "disabled"}
        messages = self.imap.fetch_unseen()
        processed = 0
        for message in messages:
            try:
                self.process_message(message)
            except Exception:
                logger.exception("Mail message processing failed", extra={"component": "mail_assistant"})
                continue
            try:
                self.imap.mark_processed(message.uid)
            except Exception:
                logger.exception("Mail processed but IMAP mark failed", extra={"component": "mail_assistant"})
            processed += 1
        return {"processed": processed}

    def process_message(self, message: InboundMail) -> dict[str, Any]:
        started = time.perf_counter()
        if is_generated_or_auto_submitted(message):
            self.store.record_query(received_at=message.received_at, message_id=message.message_id, contact_id=None, sender_email=message.sender_email, intent=None, confidence=None, question="", response_status="ignored", error_code="auto_submitted", processing_ms=_elapsed_ms(started))
            logger.info("Mail message ignored because it is generated or auto-submitted", extra={"component": "mail_assistant"})
            return {"status": "ignored", "error": "auto_submitted"}

        if self.store.already_processed(message.message_id):
            self.store.record_query(received_at=message.received_at, message_id=message.message_id, contact_id=None, sender_email=message.sender_email, intent=None, confidence=None, question="", response_status="duplicate", error_code="duplicate")
            return {"status": "duplicate"}

        contact, auth_error = self.store.find_authorized_contact(message.sender_email, message.recipient_addresses)
        if not contact:
            self._send_neutral_rejection(message)
            self.store.record_query(received_at=message.received_at, message_id=message.message_id, contact_id=None, sender_email=message.sender_email, intent=None, confidence=None, question="", response_status="rejected", error_code=auth_error, processing_ms=_elapsed_ms(started), response_sent_at=now())
            logger.info("Mail query rejected", extra={"component": "mail_assistant", "reason": auth_error})
            return {"status": "rejected", "error": auth_error}

        if self.store.rate_limit_exceeded(contact.id, self.config.hourly_limit, self.config.daily_limit):
            body = "Guten Tag,\n\ndas Anfrage-Limit für E-Mail-Statusabfragen ist erreicht. Bitte versuchen Sie es später erneut.\n\nViele Grüße\nSentero"
            self._send_response(message, contact.email, body, contact_id=contact.id)
            self.store.record_query(received_at=message.received_at, message_id=message.message_id, contact_id=contact.id, sender_email=message.sender_email, intent=None, confidence=None, question="", response_status="rate_limited", error_code="rate_limit", processing_ms=_elapsed_ms(started), response_sent_at=now())
            return {"status": "rate_limited"}

        context = self.store.find_thread_context(reply_context_message_id(message))
        if context and context.contact_id not in {None, contact.id}:
            context = None
        question = sanitize_question(message.body or message.subject)
        routed = self.conversation.classify(question, self.intent)
        if routed.is_action_request:
            body = self.response.read_only_action_rejected()
            intent_name = MailIntent.UNKNOWN.value
            confidence = routed.confidence
        else:
            query = self.query_service.query(routed.intent, contact, context=context)
            body = self.conversation.build_response(query, self.response)
            intent_name = routed.intent.value
            confidence = routed.confidence
        try:
            self._send_response(message, contact.email, body, contact_id=contact.id)
        except Exception as exc:
            self.store.record_query(received_at=message.received_at, message_id=message.message_id, contact_id=contact.id, sender_email=message.sender_email, intent=intent_name, confidence=confidence, question=question, response_status="failed", error_code=exc.__class__.__name__, processing_ms=_elapsed_ms(started))
            logger.exception("Mail query response failed", extra={"component": "mail_assistant", "contact_id": contact.id, "intent": intent_name})
            return {"status": "failed", "intent": intent_name, "error": exc.__class__.__name__}
        self.store.record_query(received_at=message.received_at, message_id=message.message_id, contact_id=contact.id, sender_email=message.sender_email, intent=intent_name, confidence=confidence, question=question, response_status="sent", processing_ms=_elapsed_ms(started), response_sent_at=now())
        logger.info("Mail query answered", extra={"component": "mail_assistant", "contact_id": contact.id, "intent": intent_name, "intent_source": routed.source})
        return {"status": "sent", "intent": intent_name, "intent_source": routed.source, "thread_context": bool(context)}

    def _send_response(self, message: InboundMail, recipient: str, body: str, contact_id: int | None = None) -> None:
        incoming_subject = sanitize_header_value(message.subject)
        subject = incoming_subject if incoming_subject.lower().startswith("re:") else f"Re: {incoming_subject or 'Sentero – Status'}"
        headers = {
            "In-Reply-To": sanitize_message_id(message.message_id),
            "References": sanitize_references(message.references, message.message_id),
        }
        result = self.notification.send_email_direct(
            sanitize_email_address(recipient),
            subject,
            body,
            config={
                "smtp_host": self.config.smtp_host,
                "smtp_port": self.config.smtp_port,
                "smtp_user": self.config.smtp_username,
                "smtp_password": self.config.smtp_password,
                "smtp_starttls": True,
                "mail_from": self.config.mail_from,
            },
            headers=headers,
        )
        self._log_response_message(contact_id, subject, provider_message_id(result))

    def _log_response_message(self, contact_id: int | None, subject: str, message_id: str | None) -> None:
        if not message_id or not hasattr(self.notification, "_log"):
            return
        self.notification._log(contact_id, "email", "green", "mail_assistant_response", subject, None, outgoing_message_id=message_id)

    def _send_neutral_rejection(self, message: InboundMail) -> None:
        if not message.sender_email:
            return
        self._send_response(message, message.sender_email, "Guten Tag,\n\ndiese Adresse ist nicht für Statusabfragen freigeschaltet.\n\nViele Grüße\nSentero")


def config_from_notification_settings(mapping: DeviceMappingService) -> MailAssistantConfig:
    with mapping.connect() as con:
        row = con.execute("select * from notification_channel_settings where channel = 'email'").fetchone()
    if not row:
        return MailAssistantConfig(enabled=False)
    try:
        data = json.loads(row["config_json"] or "{}")
    except json.JSONDecodeError:
        data = {}
    smtp_user = str(data.get("smtp_login") or data.get("smtp_user") or "").strip()
    imap_user = _mailbox_username(data.get("imap_user"), smtp_user, data.get("imap_host"))
    smtp_password = str(data.get("smtp_password") or "").strip()
    imap_password = str(data.get("imap_password") or smtp_password).strip()
    mail_from = sentero_mail_from({"mail_from": data.get("mail_from"), "smtp_user": smtp_user})
    enabled = bool(row["enabled"]) and bool(
        data.get("smtp_host")
        and smtp_user
        and smtp_password
        and data.get("imap_host")
        and imap_user
        and imap_password
    )
    return MailAssistantConfig(
        enabled=enabled,
        poll_interval_seconds=_int_from_value(data.get("poll_interval_seconds"), 60, minimum=10),
        imap_host=str(data.get("imap_host") or ""),
        imap_port=_int_from_value(data.get("imap_port"), 993, minimum=1),
        imap_username=imap_user,
        imap_password=imap_password,
        smtp_host=str(data.get("smtp_host") or ""),
        smtp_port=_int_from_value(data.get("smtp_port"), 587, minimum=1),
        smtp_username=smtp_user,
        smtp_password=smtp_password,
        mail_from=mail_from,
        fresh_seconds=_int_from_value(data.get("fresh_seconds"), 120, minimum=10),
        recent_seconds=_int_from_value(data.get("recent_seconds"), 900, minimum=60),
        stale_seconds=_int_from_value(data.get("stale_seconds"), 1800, minimum=120),
        hourly_limit=_int_from_value(data.get("hourly_limit"), 20, minimum=1),
        daily_limit=_int_from_value(data.get("daily_limit"), 50, minimum=1),
    )


def sanitize_question(value: str) -> str:
    lines = []
    for line in str(value or "").splitlines():
        if line.strip().startswith(">") or REPLY_SEPARATOR_RE.match(line):
            break
        lines.append(line)
    return "\n".join(lines).strip()[:2000]


def sanitize_header_value(value: Any) -> str:
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()


def sanitize_message_id(value: Any) -> str:
    text = sanitize_header_value(value)
    return text if re.fullmatch(r"<[^<>\s]+>", text) else ""


def sanitize_references(*values: Any) -> str:
    ids: list[str] = []
    for value in values:
        for match in re.findall(r"<[^<>\s]+>", sanitize_header_value(value)):
            if match not in ids:
                ids.append(match)
    return " ".join(ids)


def sanitize_email_address(value: Any) -> str:
    address = parseaddr(sanitize_header_value(value))[1].strip()
    return address or sanitize_header_value(value)


def is_generated_or_auto_submitted(message: InboundMail) -> bool:
    if str(message.x_sentero_generated or "").strip().lower() == "true":
        return True
    auto_submitted = str(message.auto_submitted or "").strip().lower()
    return auto_submitted in {"auto-generated", "auto-replied"}


def reply_context_message_id(message: InboundMail) -> str | None:
    if message.in_reply_to:
        return message.in_reply_to
    references = str(message.references or "").split()
    return references[-1] if references else None


def provider_message_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    value = str(result.get("message_id") or "").strip()
    return value or None


def _int_from_value(raw: Any, default: int, minimum: int) -> int:
    try:
        value = int(raw if raw not in {None, ""} else default)
    except ValueError:
        value = default
    return max(value, minimum)


def _mailbox_username(raw_imap_user: Any, smtp_user: str, imap_host: Any) -> str:
    raw = str(raw_imap_user or "").strip()
    host = str(imap_host or "").strip().lower()
    if raw and raw.lower() != host:
        return raw
    return smtp_user


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class SenteroMailAssistantSettings:
    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
        self.store = MailAssistantStore(mapping)

    def status(self) -> dict[str, Any]:
        config = config_from_notification_settings(self.mapping)
        with self.mapping.connect() as con:
            rows = con.execute("select * from trusted_contacts where active = 1 order by primary_contact desc, id").fetchall()
        contacts = []
        for row in rows:
            data = dict(row)
            contacts.append({
                "id": data.get("id"),
                "name": data.get("name"),
                "email": data.get("email"),
                "email_queries_enabled": bool(data.get("email_queries_enabled")),
                "email_permissions": _decode_permissions(data.get("email_permissions")),
            })
        return {"enabled": config.enabled, "contacts": contacts}

    def update_contact(self, contact_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        permissions = [str(item) for item in payload.get("email_permissions") or [] if str(item) in {"STATUS", "ACTIVITY", "ROOM", "ENVIRONMENT", "NIGHT", "HISTORY", "TECHNICAL_HEALTH"}]
        if not permissions:
            permissions = ["STATUS", "ACTIVITY", "ROOM", "ENVIRONMENT", "NIGHT"]
        with self.mapping.connect() as con:
            row = con.execute("select id from trusted_contacts where id = ? and active = 1", (contact_id,)).fetchone()
            if not row:
                raise ValueError("contact not found")
            con.execute(
                "update trusted_contacts set email_queries_enabled = ?, email_permissions = ?, updated_at = ? where id = ?",
                (int(bool(payload.get("email_queries_enabled"))), __import__("json").dumps(permissions), now(), contact_id),
            )
            con.commit()
        return self.status()


def _decode_permissions(value: Any) -> list[str]:
    import json

    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed if str(item)]
