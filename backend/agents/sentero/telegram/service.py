from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from backend.agents.sentero.mail.conversation_service import ConversationService, detect_language
from backend.agents.sentero.conversation_store import SenteroConversationStore
from backend.agents.sentero.mail.intent_service import ACTION_RE, MailIntentService
from backend.agents.sentero.mail.models import MailIntent, MailThreadContext, QueryResult
from backend.agents.sentero.mail.query_service import MailQueryService
from backend.agents.sentero.mail.response_service import MailResponseService
from backend.agents.sentero.mail.service import sanitize_question
from backend.agents.sentero.mail.store import contact_from_row
from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.notification_service import NotificationService, _provider_message_id
from backend.services.service import SenteroService

logger = get_logger(__name__)


@dataclass(frozen=True)
class TelegramAssistantConfig:
    enabled: bool = False
    bot_token: str = ""
    poll_interval_seconds: int = 10
    timeout_seconds: int = 10
    hourly_limit: int = 20
    daily_limit: int = 50


class TelegramApiClient:
    def __init__(self, config: TelegramAssistantConfig) -> None:
        self.config = config

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": self.config.timeout_seconds,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            params["offset"] = offset
        # Telegram getUpdates is a long-poll request. The HTTP read timeout must
        # be comfortably longer than Telegram's own long-poll timeout; otherwise a
        # healthy idle poll can be reported as an application error.
        connect_timeout = 5
        read_timeout = max(self.config.timeout_seconds + 15, 20)
        response = requests.get(
            self._url("getUpdates"),
            params=params,
            timeout=(connect_timeout, read_timeout),
        )
        response.raise_for_status()
        data = response.json()
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, list) else []

    def get_me(self) -> dict[str, Any]:
        response = requests.get(self._url("getMe"), timeout=(5, 15))
        response.raise_for_status()
        data = response.json()
        result = data.get("result") if isinstance(data, dict) else None
        return result if isinstance(result, dict) else {}

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.bot_token}/{method}"


class TelegramAssistantStore:
    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists sentero_telegram_assistant_state (
                    id integer primary key check (id = 1),
                    last_update_id integer not null default 0,
                    updated_at text not null
                )"""
            )
            con.execute(
                """create table if not exists sentero_telegram_queries (
                    id integer primary key autoincrement,
                    received_at text not null,
                    update_id integer not null unique,
                    message_id integer,
                    chat_id text not null,
                    contact_id integer,
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
            con.execute("insert or ignore into sentero_telegram_assistant_state (id, last_update_id, updated_at) values (1, 0, ?)", (now(),))
            con.commit()

    def next_offset(self) -> int | None:
        with self.mapping.connect() as con:
            row = con.execute("select last_update_id from sentero_telegram_assistant_state where id = 1").fetchone()
        last_update_id = int(row["last_update_id"] if row else 0)
        return last_update_id + 1 if last_update_id else None

    def mark_update(self, update_id: int) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "update sentero_telegram_assistant_state set last_update_id = max(last_update_id, ?), updated_at = ? where id = 1",
                (update_id, now()),
            )
            con.commit()

    def already_processed(self, update_id: int) -> bool:
        with self.mapping.connect() as con:
            row = con.execute("select id from sentero_telegram_queries where update_id = ?", (update_id,)).fetchone()
        return row is not None

    def find_authorized_contact(self, chat_id: str) -> tuple[dict[str, Any] | None, str | None]:
        with self.mapping.connect() as con:
            row = con.execute(
                """select *
                   from trusted_contacts
                   where telegram_chat_id = ? and active = 1 and notification_enabled = 1""",
                (chat_id,),
            ).fetchone()
        if not row:
            return None, "unknown_chat"
        data = dict(row)
        if not bool(data.get("email_queries_enabled")):
            return None, "queries_disabled"
        channels = decode_json(data.get("preferred_channels"), [])
        if "telegram" not in channels:
            return None, "telegram_not_enabled_for_contact"
        return data, None

    def link_invite(self, invite_code: str, chat_id: str) -> dict[str, Any] | None:
        code = str(invite_code or "").strip()
        if not code:
            return None
        with self.mapping.connect() as con:
            row = con.execute(
                "select * from trusted_contacts where telegram_invite_code = ? and active = 1",
                (code,),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            channels = decode_json(data.get("preferred_channels"), [])
            if "telegram" not in channels:
                channels.append("telegram")
            con.execute(
                """update trusted_contacts
                   set telegram_chat_id = null, telegram_linked_at = null, updated_at = ?
                   where active = 1 and telegram_chat_id = ? and id != ?""",
                (now(), chat_id, data["id"]),
            )
            con.execute(
                """update trusted_contacts
                   set telegram_chat_id = ?, telegram_linked_at = ?, preferred_channels = ?,
                       notification_enabled = 1, updated_at = ?
                   where id = ?""",
                (chat_id, now(), json.dumps(channels), now(), data["id"]),
            )
            con.commit()
        data["telegram_chat_id"] = chat_id
        data["telegram_linked_at"] = now()
        data["preferred_channels"] = json.dumps(channels)
        data["notification_enabled"] = 1
        return data

    def find_thread_context(self, chat_id: str, reply_to_message_id: int | None) -> MailThreadContext | None:
        if reply_to_message_id is None:
            return None
        outgoing_id = f"telegram:{chat_id}:{reply_to_message_id}"
        with self.mapping.connect() as con:
            row = con.execute(
                """select *
                   from notification_logs
                   where outgoing_message_id = ?
                   order by created_at desc, id desc
                   limit 1""",
                (outgoing_id,),
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
            outgoing_message_id=outgoing_id,
        )

    def rate_limit_exceeded(self, contact_id: int, hourly_limit: int, daily_limit: int) -> bool:
        current = datetime.now(timezone.utc)
        hour_since = (current - timedelta(hours=1)).isoformat(timespec="seconds")
        day_since = (current - timedelta(days=1)).isoformat(timespec="seconds")
        with self.mapping.connect() as con:
            hour = con.execute(
                """select count(*) as count
                   from sentero_telegram_queries
                   where contact_id = ? and received_at >= ? and response_status not in ('rejected', 'duplicate')""",
                (contact_id, hour_since),
            ).fetchone()
            day = con.execute(
                """select count(*) as count
                   from sentero_telegram_queries
                   where contact_id = ? and received_at >= ? and response_status not in ('rejected', 'duplicate')""",
                (contact_id, day_since),
            ).fetchone()
        return int(hour["count"] if hour else 0) >= hourly_limit or int(day["count"] if day else 0) >= daily_limit

    def record_query(
        self,
        *,
        received_at: str,
        update_id: int,
        message_id: int | None,
        chat_id: str,
        contact_id: int | None,
        intent: str | None,
        confidence: float | None,
        question: str,
        response_status: str,
        error_code: str | None = None,
        processing_ms: int | None = None,
        response_sent_at: str | None = None,
    ) -> None:
        from backend.agents.sentero.mail.store import question_hash

        with self.mapping.connect() as con:
            con.execute(
                """insert or ignore into sentero_telegram_queries
                   (received_at, update_id, message_id, chat_id, contact_id, intent, confidence, question_hash,
                    response_status, response_sent_at, error_code, processing_ms, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    received_at,
                    update_id,
                    message_id,
                    chat_id,
                    contact_id,
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


class SenteroTelegramAssistant:
    def __init__(
        self,
        mapping: DeviceMappingService,
        sentero: SenteroService,
        notification: NotificationService,
        config: TelegramAssistantConfig | None = None,
        client: TelegramApiClient | None = None,
        conversation: ConversationService | None = None,
    ) -> None:
        self.mapping = mapping
        self.sentero = sentero
        self.notification = notification
        self._fixed_config = config
        self.config = config or config_from_notification_settings(mapping)
        self.store = TelegramAssistantStore(mapping)
        self.conversation_store = SenteroConversationStore(mapping)
        self.intent = MailIntentService()
        self.query_service = MailQueryService(mapping, sentero)
        self.response = MailResponseService()
        self.conversation = conversation or ConversationService()
        self.client = client or TelegramApiClient(self.config)

    def _refresh_runtime_config(self) -> None:
        self.config = self._fixed_config or config_from_notification_settings(self.mapping)
        self.client.config = self.config

    def enabled(self) -> bool:
        self._refresh_runtime_config()
        return self.config.enabled

    def poll_once(self) -> dict[str, Any]:
        self._refresh_runtime_config()
        if not self.enabled():
            return {"processed": 0, "skipped": "disabled"}
        updates = self.client.get_updates(self.store.next_offset())
        processed = 0
        for update in updates:
            update_id = int(update.get("update_id") or 0)
            try:
                self.process_update(update)
            except Exception:
                logger.exception("Telegram message processing failed", extra={"component": "telegram_assistant", "update_id": update_id})
            finally:
                if update_id:
                    self.store.mark_update(update_id)
            processed += 1
        return {"processed": processed}

    def process_update(self, update: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        update_id = int(update.get("update_id") or 0)
        message = update.get("message") if isinstance(update.get("message"), dict) else {}
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        from_user = message.get("from") if isinstance(message.get("from"), dict) else {}
        chat_id = str(chat.get("id") or "").strip()
        message_id = int(message.get("message_id") or 0) or None
        received_at = now()
        if not update_id or not chat_id:
            return {"status": "ignored", "error": "invalid_update"}
        if self.store.already_processed(update_id):
            return {"status": "duplicate"}
        if bool(from_user.get("is_bot")):
            self._record(update_id, message_id, chat_id, None, None, None, "", "ignored", "bot_message", started, received_at)
            return {"status": "ignored", "error": "bot_message"}
        question = sanitize_question(str(message.get("text") or ""))
        if not question:
            self._record(update_id, message_id, chat_id, None, None, None, "", "ignored", "empty_message", started, received_at)
            return {"status": "ignored", "error": "empty_message"}
        invite_code = telegram_start_code(question)
        if invite_code:
            linked = self.store.link_invite(invite_code, chat_id)
            if not linked:
                self._send(chat_id, "Dieser Sentero-Einladungslink ist ungültig oder nicht mehr aktiv.")
                self._record(update_id, message_id, chat_id, None, None, None, question, "rejected", "invalid_invite", started, received_at)
                return {"status": "rejected", "error": "invalid_invite"}
            self._send(chat_id, "Telegram ist jetzt mit Sentero verbunden. Sie erhalten wichtige Hinweise hier in diesem Chat.", linked)
            self._record(update_id, message_id, chat_id, int(linked["id"]), MailIntent.UNKNOWN.value, None, question, "sent", None, started, received_at, response_sent_at=now())
            self.notification._log(linked["id"], "telegram", "green", "telegram_pairing", "Sentero Telegram verbunden", None)
            return {"status": "linked", "contact_id": int(linked["id"])}
        contact_row, auth_error = self.store.find_authorized_contact(chat_id)
        if not contact_row:
            self._send(chat_id, "Dieser Telegram-Chat ist nicht für Sentero freigeschaltet.")
            self._record(update_id, message_id, chat_id, None, None, None, question, "rejected", auth_error, started, received_at)
            return {"status": "rejected", "error": auth_error}
        contact = contact_from_row(contact_row)
        command = telegram_command(question)
        if command in {"start", "help"}:
            body = telegram_welcome_text(contact.name)
            result = self._send(chat_id, body, contact_row)
            self._record(update_id, message_id, chat_id, contact.id, MailIntent.HELP.value, 1.0, question, "sent", None, started, received_at, response_sent_at=now())
            self.notification._log(contact.id, "telegram", "green", "telegram_assistant_response", "Sentero Telegram Hilfe", None, outgoing_message_id=_provider_message_id(result))
            return {"status": "sent", "intent": MailIntent.HELP.value, "intent_source": "telegram_command", "thread_context": False}
        if self.store.rate_limit_exceeded(contact.id, self.config.hourly_limit, self.config.daily_limit):
            self._send(chat_id, "Das Anfrage-Limit für Telegram-Statusabfragen ist erreicht. Bitte versuchen Sie es später erneut.", contact_row)
            self._record(update_id, message_id, chat_id, contact.id, None, None, question, "rate_limited", "rate_limit", started, received_at)
            return {"status": "rate_limited"}
        conversation_key = f"telegram:{chat_id}"
        history = self.conversation_store.recent(
            channel="telegram", conversation_key=conversation_key, contact_id=contact.id
        )
        routed = self.conversation.classify(question, self.intent, history=history)
        context = self.store.find_thread_context(chat_id, reply_to_message_id(message))
        if getattr(context, "contact_id", contact.id) not in {None, contact.id}:
            context = None
        query = None
        if routed.is_action_request or ACTION_RE.search(question.lower()):
            body = self.response.read_only_action_rejected()
            intent_name = MailIntent.UNKNOWN.value
            confidence = routed.confidence
        else:
            query = self.query_service.query(
                routed.intent,
                contact,
                context=context,
                slots=routed.slots,
                conversation_history=history,
            )
            body = self.conversation.build_response(
                query, self.response, question=question, history=history
            )
            body = telegram_response_text(query, body, question=question, language=(routed.slots or {}).get("language"))
            intent_name = routed.intent.value
            confidence = routed.confidence
        try:
            result = self._send(chat_id, telegram_text(body), contact_row)
        except Exception as exc:
            self._record(update_id, message_id, chat_id, contact.id, intent_name, confidence, question, "failed", exc.__class__.__name__, started, received_at)
            raise
        self._record(update_id, message_id, chat_id, contact.id, intent_name, confidence, question, "sent", None, started, received_at, response_sent_at=now())
        self.conversation_store.add_exchange(
            channel="telegram",
            conversation_key=conversation_key,
            contact_id=contact.id,
            question=question,
            answer=body,
            intent=intent_name,
            slots=routed.slots,
            facts=query.facts if query is not None else {},
        )
        self.notification._log(contact.id, "telegram", "green", "telegram_assistant_response", "Sentero Telegram Antwort", None, outgoing_message_id=_provider_message_id(result))
        return {"status": "sent", "intent": intent_name, "intent_source": routed.source, "thread_context": bool(context)}

    def _send(self, chat_id: str, text: str, contact: dict[str, Any] | None = None) -> dict[str, Any] | None:
        recipient = dict(contact or {})
        recipient["telegram_chat_id"] = chat_id
        return self.notification.providers["telegram"].send(recipient, "Sentero Antwort", text, {"bot_token": self.config.bot_token})

    def _record(
        self,
        update_id: int,
        message_id: int | None,
        chat_id: str,
        contact_id: int | None,
        intent: str | None,
        confidence: float | None,
        question: str,
        status: str,
        error: str | None,
        started: float,
        received_at: str,
        response_sent_at: str | None = None,
    ) -> None:
        self.store.record_query(
            received_at=received_at,
            update_id=update_id,
            message_id=message_id,
            chat_id=chat_id,
            contact_id=contact_id,
            intent=intent,
            confidence=confidence,
            question=question,
            response_status=status,
            error_code=error,
            processing_ms=round((time.perf_counter() - started) * 1000),
            response_sent_at=response_sent_at,
        )


def config_from_notification_settings(mapping: DeviceMappingService) -> TelegramAssistantConfig:
    with mapping.connect() as con:
        row = con.execute("select * from notification_channel_settings where channel = 'telegram'").fetchone()
    if not row:
        return TelegramAssistantConfig(enabled=False)
    config = decode_json(row["config_json"], {})
    bot_token = str(config.get("bot_token") or "").strip()
    return TelegramAssistantConfig(
        enabled=bool(row["enabled"]) and bool(bot_token),
        bot_token=bot_token,
        poll_interval_seconds=int_value(config.get("poll_interval_seconds"), 10, minimum=3),
        timeout_seconds=int_value(config.get("timeout_seconds"), 10, minimum=1),
        hourly_limit=int_value(config.get("hourly_limit"), 20, minimum=1),
        daily_limit=int_value(config.get("daily_limit"), 50, minimum=1),
    )


def decode_json(value: Any, fallback: Any) -> Any:
    try:
        decoded = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return decoded


def int_value(value: Any, fallback: int, minimum: int) -> int:
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return fallback


def reply_to_message_id(message: dict[str, Any]) -> int | None:
    reply = message.get("reply_to_message")
    if not isinstance(reply, dict):
        return None
    try:
        return int(reply.get("message_id") or 0) or None
    except (TypeError, ValueError):
        return None



def telegram_command(text: str) -> str | None:
    token = str(text or "").strip().split(maxsplit=1)[0].lower() if str(text or "").strip() else ""
    if token.startswith("/start"):
        return "start"
    if token.startswith("/help"):
        return "help"
    return None


def telegram_welcome_text(contact_name: str = "") -> str:
    greeting = f"Hallo {contact_name.strip()}," if str(contact_name or "").strip() else "Hallo,"
    return (
        f"{greeting} ich bin Sentero. Sie können mich zum Beispiel fragen: "
        "‚Ist alles in Ordnung?‘, ‚Wo wurde zuletzt Aktivität erkannt?‘, "
        "‚Wie geht es den Sensoren?‘ oder ‚Wie ist die Temperatur?‘. "
        "Sie können auch in einer anderen Sprache schreiben."
    )

def telegram_start_code(text: str) -> str | None:
    parts = str(text or "").strip().split(maxsplit=1)
    if not parts or parts[0].lower() != "/start":
        return None
    return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None


def telegram_text(value: str) -> str:
    return str(value or "").replace("Guten Tag,\n\n", "").replace("\n\nViele Grüße\nSentero", "").strip()[:4000]


def telegram_response_text(result: QueryResult, body: str, *, question: str = "", language: Any = None) -> str:
    if result.intent != MailIntent.STATUS_SUMMARY:
        return body
    response_language = str(language or detect_language(question) or "de").lower()
    if response_language not in {"de", "en"}:
        return body
    if not result.data_available:
        if response_language == "en":
            return "There is not enough fresh sensor data for a reliable answer right now. Sentero is still monitoring the sensor connection."
        return "Ich habe momentan nicht genug aktuelle Sensordaten, um das sicher zu beantworten. Sentero überwacht die Sensorverbindung weiter."

    facts = result.facts or {}
    assessment = facts.get("assessment") if isinstance(facts.get("assessment"), dict) else {}
    dashboard = facts.get("dashboard") if isinstance(facts.get("dashboard"), dict) else {}
    findings = assessment.get("findings") or []
    status = str(assessment.get("status") or dashboard.get("behavior_status") or "normal").lower()
    normal = status in {"green", "normal", "ok"} and not findings
    person = str(dashboard.get("person_name") or "Mutter").strip()
    activity = facts.get("last_activity") or dashboard.get("last_activity") or {}
    location = str(dashboard.get("current_location") or "").strip()
    room = str(activity.get("room_label") or activity.get("room") or "").strip()
    if location:
        detail = location
    elif room:
        detail = f"zuletzt Aktivität im {room}"
    else:
        detail = "keine aktuelle Aktivität eindeutig zuordenbar"
    if normal:
        if response_language == "en":
            return f"Yes, Sentero is not seeing any notable unusual signs for {person} right now. {detail}."
        return f"Ja, bei {person} gibt es aktuell keine auffälligen Hinweise. {detail}."
    if response_language == "en":
        return f"Not completely clear: Sentero sees signs that differ from the usual pattern. {detail}."
    return f"Nicht ganz eindeutig: Sentero sieht Hinweise, die vom üblichen Verlauf abweichen. {detail}."
