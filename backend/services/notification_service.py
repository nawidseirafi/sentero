from __future__ import annotations

import json
import smtplib
import socket
import uuid
from abc import ABC, abstractmethod
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

import requests

from backend.logging_config import get_logger
from backend.services.messaging import MessagingService

from backend.services.aal_roles import can_access_data_classes
from backend.services.data_classification import aggregation_for_data_class, classify_notification
from backend.services.consent_service import DEFAULT_NOTIFICATION_DATA_CLASSES, DEFAULT_NOTIFICATION_PURPOSE, ConsentService
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.network.connectivity_service import ConnectivityService
from backend.services.network.models import ConnectionType, NetworkStatusCode

logger = get_logger(__name__)

CHANNELS = ("email", "telegram", "whatsapp")
SEVERITIES = ("green", "yellow", "orange", "red")
SECRET_KEYS = {"access_token", "bot_token", "imap_password", "smtp_password", "password", "token"}
EMAIL_FROM = "Sentero <noreply@sentero.de>"
BATTERY_WARNING_THRESHOLD = 30


class NotificationProvider(ABC):
    channel: str

    @abstractmethod
    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError


class EmailNotificationProvider(NotificationProvider):
    channel = "email"

    def __init__(self, messaging: MessagingService | None = None) -> None:
        self.messaging = messaging or MessagingService()

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any] | None:
        smtp_user = str(config.get("smtp_login") or config.get("smtp_user") or "").strip()
        to_email = str(contact.get("email") or config.get("test_recipient") or config.get("smtp_user") or smtp_user).strip()
        if not config.get("smtp_host"):
            raise ValueError("email_not_configured")
        if not to_email:
            raise ValueError("email_recipient_missing")
        message_id = str(config.get("message_id") or generate_sentero_message_id(config)).strip()
        from_header = sentero_mail_from(config)
        message = EmailMessage()
        message["Subject"] = title
        message["From"] = from_header
        message["To"] = to_email
        message["Message-ID"] = message_id
        message["X-Sentero-Generated"] = "true"
        message["Auto-Submitted"] = "auto-generated"
        reply_to = mail_assistant_reply_to(contact, config)
        if reply_to:
            message["Reply-To"] = reply_to
        message.set_content(text)
        for key, value in (config.get("headers") or {}).items():
            if value:
                message[str(key)] = str(value)
        smtp_encryption = str(config.get("smtp_encryption") or "").strip().upper()
        use_ssl = smtp_encryption == "SSL" or as_bool(config.get("smtp_ssl", False))
        use_starttls = smtp_encryption == "STARTTLS" or (not use_ssl and as_bool(config.get("smtp_starttls", True)))
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(str(config["smtp_host"]), int(config.get("smtp_port") or 587), timeout=10) as smtp:
            if use_starttls:
                smtp.starttls()
            if smtp_user:
                smtp.login(smtp_user, str(config.get("smtp_password") or ""))
            smtp.send_message(message, from_addr=str(config.get("smtp_user") or parseaddr(from_header)[1] or EMAIL_FROM), to_addrs=[to_email])
        return {"message_id": message_id}


class TelegramNotificationProvider(NotificationProvider):
    channel = "telegram"

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any] | None:
        token = str(config.get("bot_token") or "").strip()
        chat_id = str(contact.get("telegram_chat_id") or config.get("default_chat_id") or "").strip()
        if not token or not chat_id:
            raise ValueError("telegram_not_configured")
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{title}\n\n{text}"},
            timeout=10,
        )
        response.raise_for_status()
        return None


class WhatsAppNotificationProvider(NotificationProvider):
    channel = "whatsapp"

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any] | None:
        access_token = str(config.get("access_token") or "").strip()
        phone_number_id = str(config.get("phone_number_id") or "").strip()
        recipient = str(contact.get("whatsapp_phone_number") or config.get("test_recipient") or "").strip()
        api_version = str(config.get("api_version") or "v20.0").strip()
        if not access_token or not phone_number_id or not recipient:
            raise ValueError("whatsapp_not_configured")
        response = requests.post(
            f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": f"{title}\n\n{text}"},
            },
            timeout=10,
        )
        response.raise_for_status()
        return None


class NotificationService:
    def __init__(self, mapping: DeviceMappingService | None = None, messaging: MessagingService | None = None, connectivity: ConnectivityService | None = None) -> None:
        self.mapping = mapping or DeviceMappingService()
        self.consent = ConsentService(self.mapping)
        self.connectivity = connectivity
        self.providers: dict[str, NotificationProvider] = {
            "email": EmailNotificationProvider(messaging),
            "telegram": TelegramNotificationProvider(),
            "whatsapp": WhatsAppNotificationProvider(),
        }
        self.ensure_queue_schema()

    def ensure_queue_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists notification_outbox (
                    id integer primary key autoincrement,
                    contact_id integer,
                    channel text not null,
                    severity text not null,
                    title text not null,
                    text text not null,
                    contact_json text not null,
                    status text not null,
                    attempts integer not null default 0,
                    original_created_at text not null,
                    last_attempt_at text,
                    sent_at text,
                    error_message text
                )"""
            )
            con.commit()

    def channels(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            rows = con.execute("select * from notification_channel_settings order by channel").fetchall()
        by_channel = {row["channel"]: self._public_channel(dict(row)) for row in rows}
        return {"channels": [by_channel.get(channel) or self._empty_channel(channel) for channel in CHANNELS]}

    def save_channel(self, channel: str, enabled: bool, config: dict[str, Any]) -> dict[str, Any]:
        self._validate_channel(channel)
        existing = self._setting(channel).get("config") or {}
        clean_config = self._merge_secret_config(channel, config)
        still_valid = bool(self._setting(channel).get("enabled")) and clean_config == existing
        enabled_after_save = still_valid and self._is_configured(channel, clean_config)
        timestamp = now()
        with self.mapping.connect() as con:
            con.execute(
                """insert into notification_channel_settings (channel, enabled, config_json, created_at, updated_at)
                   values (?, ?, ?, ?, ?)
                   on conflict(channel) do update set enabled = excluded.enabled, config_json = excluded.config_json, updated_at = excluded.updated_at""",
                (channel, int(enabled_after_save), json.dumps(clean_config, ensure_ascii=False, sort_keys=True), timestamp, timestamp),
            )
            con.commit()
        return self.channels()

    def test(self, channel: str, dev: bool = False) -> dict[str, Any]:
        self._validate_channel(channel)
        setting = self._setting(channel)
        contact = self._test_contact(channel, setting.get("config") or {})
        title = "Sentero Hinweis"
        text = {
            "email": "Sentero Testnachricht: E-Mail ist verbunden.",
            "telegram": "Sentero Testnachricht: Telegram ist verbunden.",
            "whatsapp": "Sentero Testnachricht: WhatsApp ist verbunden.",
        }[channel]
        try:
            result = self.providers[channel].send(contact, title, text, setting.get("config") or {})
            self._mark_channel_enabled(channel, True)
            self._log(contact.get("id"), channel, "yellow", "sent", title, None, outgoing_message_id=_provider_message_id(result))
            return {"ok": True, "message": "Testnachricht gesendet."}
        except Exception as exc:
            logger.exception("Notification test failed", extra={"component": "notification", "channel": channel})
            self._mark_channel_enabled(channel, False)
            self._log(contact.get("id"), channel, "yellow", "failed", title, self._safe_error(exc))
            return self._test_error(dev, self._safe_error(exc))

    def logs(self, limit: int = 100) -> dict[str, Any]:
        limit = min(max(int(limit or 100), 1), 500)
        with self.mapping.connect() as con:
            rows = con.execute("select * from notification_logs order by created_at desc, id desc limit ?", (limit,)).fetchall()
        return {"logs": [self._public_log(dict(row)) for row in rows]}

    def queue_status(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            rows = con.execute("select status, count(*) as count from notification_outbox group by status").fetchall()
        return {"queue": {row["status"]: row["count"] for row in rows}}

    def process_pending_queue(self, limit: int = 50) -> dict[str, Any]:
        if self.connectivity and self.connectivity.check(ConnectionType.NONE).status in {NetworkStatusCode.OFFLINE, NetworkStatusCode.LOCAL_ONLY}:
            return {"sent": 0, "remaining": self._pending_count(), "skipped": "offline"}
        with self.mapping.connect() as con:
            rows = con.execute(
                "select * from notification_outbox where status in ('pending', 'failed') order by original_created_at, id limit ?",
                (min(max(int(limit or 50), 1), 200),),
            ).fetchall()
        sent = 0
        for row in rows:
            item = dict(row)
            contact = self._decode_json(item.get("contact_json"))
            channel = str(item["channel"])
            try:
                text = add_original_timestamp(str(item["text"]), str(item["original_created_at"]))
                result = self.providers[channel].send(contact, str(item["title"]), text, self._setting(channel).get("config") or {})
                self._mark_outbox(int(item["id"]), "sent", None)
                self._log(item.get("contact_id"), channel, str(item["severity"]), "sent", str(item["title"]), None, outgoing_message_id=_provider_message_id(result))
                sent += 1
            except Exception as exc:
                self._mark_outbox(int(item["id"]), "failed", self._safe_error(exc))
        return {"sent": sent, "remaining": self._pending_count()}

    def send_email_direct(self, to_email: str, title: str, text: str, config: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any] | None:
        clean_config = {**(config or {}), "headers": headers or {}}
        return self.providers["email"].send({"email": to_email}, title, text, clean_config)

    def notify_assessment(self, assessment: dict[str, Any], contacts: list[dict[str, Any]]) -> None:
        severity = str(assessment.get("status") or "green")
        if severity == "green":
            return
        if severity == "yellow" and not self._daily_summary_enabled():
            return
        title, email_text, short_text = self._message(assessment)
        for contact in contacts:
            if not bool(contact.get("notification_enabled", 1)):
                continue
            if not can_access_data_classes(contact.get("actor_role") or "relative", DEFAULT_NOTIFICATION_DATA_CLASSES, aggregation_level="summary"):
                self._log(contact.get("id"), "consent", severity, "skipped_role_denied", title, None)
                logger.info(
                    "Notification skipped because actor role is not allowed for data classes",
                    extra={"component": "notification", "contact_id": contact.get("id"), "actor_role": contact.get("actor_role")},
                )
                continue
            if not self.consent.has_active_consent(contact.get("id"), DEFAULT_NOTIFICATION_PURPOSE, DEFAULT_NOTIFICATION_DATA_CLASSES):
                self._log(contact.get("id"), "consent", severity, "skipped_no_consent", title, None)
                logger.info(
                    "Notification skipped because no active consent exists",
                    extra={"component": "notification", "contact_id": contact.get("id"), "purpose": DEFAULT_NOTIFICATION_PURPOSE},
                )
                continue
            channels = self._channels_for_contact(contact, severity)
            for channel in channels:
                text = email_text if channel == "email" else short_text
                self._send_with_log(contact, channel, severity, title, text, fallback=severity == "red")

    def notify_system_warnings(self, sensors: list[dict[str, Any]] | None = None, battery_threshold: int = BATTERY_WARNING_THRESHOLD) -> dict[str, Any]:
        if not self._critical_notifications_enabled():
            return {"sent": 0, "warnings": [], "skipped": "critical_notifications_disabled"}

        sensor_rows = sensors if sensors is not None else self.mapping.roles(dev=True, include_state=True)
        active_warnings = self._system_warnings(sensor_rows, battery_threshold=battery_threshold)
        logger.debug(
            "System warnings evaluated",
            extra={"component": "notification", "sensor_count": len(sensor_rows), "warning_count": len(active_warnings)},
        )
        active_keys = {warning["key"] for warning in active_warnings}
        self._resolve_inactive_system_warnings(active_keys)

        contacts = self._trusted_contacts()
        sent = 0
        for warning in active_warnings:
            state = self._system_warning_state(warning["key"])
            if state and state.get("status") == "active" and state.get("last_sent_at"):
                self._touch_system_warning(warning)
                continue

            self._upsert_system_warning(warning, sent_now=False)
            delivered = self._send_system_warning(warning, contacts)
            if delivered:
                sent += delivered
                self._upsert_system_warning(warning, sent_now=True)

        return {"sent": sent, "warnings": active_warnings}

    def _system_warnings(self, sensors: list[dict[str, Any]], battery_threshold: int) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        for sensor in sensors:
            if not sensor.get("configured", sensor.get("active", True)):
                continue
            role = str(sensor.get("role") or "").strip()
            if not role:
                continue
            label = str(sensor.get("label") or sensor.get("friendly_name") or role).strip()
            room = str(sensor.get("room") or "").strip()
            battery = sensor.get("battery_level")
            if isinstance(battery, (int, float)) and battery < battery_threshold:
                warnings.append({
                    "key": f"battery_low:{role}",
                    "type": "battery_low",
                    "severity": "orange",
                    "title": "Sentero Sensor-Batterie schwach",
                    "summary": f"Die Batterie von {label} liegt bei {int(battery)}%.",
                    "recommendation": "Bitte wechseln Sie die Batterie zeitnah, damit Sentero zuverlässig bleibt.",
                    "role": role,
                    "label": label,
                    "room": room,
                    "battery_level": int(battery),
                })
            if sensor.get("reachable") is False:
                warnings.append({
                    "key": f"sensor_unreachable:{role}",
                    "type": "sensor_unreachable",
                    "severity": "red",
                    "title": "Sentero Sensor nicht erreichbar",
                    "summary": f"{label} ist aktuell nicht erreichbar.",
                    "recommendation": "Bitte prüfen Sie Stromversorgung, Funkverbindung oder Gateway, damit Warnungen zuverlässig erkannt werden.",
                    "role": role,
                    "label": label,
                    "room": room,
                    "battery_level": battery if isinstance(battery, (int, float)) else None,
                })
        return warnings

    def _send_system_warning(self, warning: dict[str, Any], contacts: list[dict[str, Any]]) -> int:
        delivered = 0
        title = str(warning.get("title") or "Sentero Systemwarnung")
        email_text = self._system_warning_email_text(warning)
        short_text = f"{warning.get('summary')} {warning.get('recommendation')}".strip()
        severity = str(warning.get("severity") or "orange")
        for contact in contacts:
            if not bool(contact.get("notification_enabled", 1)):
                continue
            for channel in self._channels_for_contact(contact, severity):
                text = email_text if channel == "email" else short_text
                before = self._log_count()
                self._send_with_log(contact, channel, severity, title, text, fallback=severity == "red")
                if self._log_count() > before:
                    delivered += 1
        return delivered

    def _system_warning_email_text(self, warning: dict[str, Any]) -> str:
        lines = [
            str(warning.get("summary") or "Sentero hat eine Systemwarnung erkannt."),
            "",
            f"Sensor: {warning.get('label') or warning.get('role')}",
        ]
        if warning.get("room"):
            lines.append(f"Raum: {warning.get('room')}")
        if warning.get("battery_level") is not None:
            lines.append(f"Batterie: {warning.get('battery_level')}%")
        lines.extend(["", str(warning.get("recommendation") or "Bitte prüfen Sie das System.")])
        return "\n".join(lines).strip()

    def _send_with_log(self, contact: dict[str, Any], channel: str, severity: str, title: str, text: str, fallback: bool) -> None:
        setting = self._setting(channel)
        if not setting.get("enabled"):
            return
        if channel == "email":
            text = add_mail_assistant_footer(text, setting.get("config") or {})
        if self._should_queue_offline():
            self._enqueue(contact, channel, severity, title, text)
            self._log(contact.get("id"), channel, severity, "pending", title, None)
            return
        try:
            result = self.providers[channel].send(contact, title, text, setting.get("config") or {})
            self._log(contact.get("id"), channel, severity, "sent", title, None, outgoing_message_id=_provider_message_id(result))
        except Exception as exc:
            safe_error = self._safe_error(exc)
            logger.exception(
                "Notification delivery failed",
                extra={"component": "notification", "channel": channel, "contact_id": contact.get("id"), "severity": severity},
            )
            self._log(contact.get("id"), channel, severity, "failed", title, safe_error)
            if channel != "email" and fallback:
                try:
                    email_setting = self._setting("email")
                    result = self.providers["email"].send(contact, title, email_text_for_fallback(text), email_setting.get("config") or {})
                    self._log(contact.get("id"), "email", severity, "fallback_sent", title, None, outgoing_message_id=_provider_message_id(result))
                except Exception as fallback_exc:
                    logger.exception(
                        "Notification fallback email failed",
                        extra={"component": "notification", "contact_id": contact.get("id"), "severity": severity},
                    )
                    self._log(contact.get("id"), "email", severity, "failed", title, self._safe_error(fallback_exc))

    def _should_queue_offline(self) -> bool:
        if not self.connectivity:
            return False
        check = self.connectivity.check(ConnectionType.NONE)
        return check.status in {NetworkStatusCode.OFFLINE, NetworkStatusCode.LOCAL_ONLY}

    def _enqueue(self, contact: dict[str, Any], channel: str, severity: str, title: str, text: str) -> None:
        timestamp = now()
        safe_contact = {key: contact.get(key) for key in ("id", "name", "email", "telegram_chat_id", "whatsapp_phone_number") if contact.get(key)}
        with self.mapping.connect() as con:
            con.execute(
                """insert into notification_outbox
                   (contact_id, channel, severity, title, text, contact_json, status, original_created_at)
                   values (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (contact.get("id"), channel, severity, title, text, json.dumps(safe_contact, ensure_ascii=False, sort_keys=True), timestamp),
            )
            con.commit()

    def _mark_outbox(self, outbox_id: int, status: str, error: str | None) -> None:
        timestamp = now()
        with self.mapping.connect() as con:
            con.execute(
                """update notification_outbox
                   set status = ?, attempts = attempts + 1, last_attempt_at = ?, sent_at = case when ? = 'sent' then ? else sent_at end, error_message = ?
                   where id = ?""",
                (status, timestamp, status, timestamp, error, outbox_id),
            )
            con.commit()

    def _pending_count(self) -> int:
        with self.mapping.connect() as con:
            row = con.execute("select count(*) as count from notification_outbox where status in ('pending', 'failed')").fetchone()
        return int(row["count"] if row else 0)

    def _setting(self, channel: str) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from notification_channel_settings where channel = ?", (channel,)).fetchone()
        if not row:
            return {"channel": channel, "enabled": False, "config": {}}
        data = dict(row)
        return {"channel": channel, "enabled": bool(data.get("enabled")), "config": self._decode_json(data.get("config_json"))}

    def _trusted_contacts(self) -> list[dict[str, Any]]:
        with self.mapping.connect() as con:
            rows = con.execute("select * from trusted_contacts where active = 1 order by primary_contact desc, id").fetchall()
        return [dict(row) for row in rows]

    def _critical_notifications_enabled(self) -> bool:
        with self.mapping.connect() as con:
            row = con.execute("select critical from notification_preferences where id = 1").fetchone()
        return bool(row is None or row["critical"])

    def _system_warning_state(self, key: str) -> dict[str, Any] | None:
        with self.mapping.connect() as con:
            row = con.execute("select * from system_warning_state where warning_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def _upsert_system_warning(self, warning: dict[str, Any], sent_now: bool) -> None:
        timestamp = now()
        existing = self._system_warning_state(str(warning["key"]))
        first_seen = existing.get("first_seen_at") if existing else timestamp
        last_sent = timestamp if sent_now else (existing.get("last_sent_at") if existing else None)
        with self.mapping.connect() as con:
            con.execute(
                """insert into system_warning_state
                   (warning_key, status, first_seen_at, last_seen_at, last_sent_at, resolved_at, payload_json)
                   values (?, 'active', ?, ?, ?, null, ?)
                   on conflict(warning_key) do update set
                       status = 'active',
                       last_seen_at = excluded.last_seen_at,
                       last_sent_at = coalesce(excluded.last_sent_at, system_warning_state.last_sent_at),
                       resolved_at = null,
                       payload_json = excluded.payload_json""",
                (warning["key"], first_seen, timestamp, last_sent, json.dumps(warning, ensure_ascii=False, sort_keys=True)),
            )
            con.commit()

    def _touch_system_warning(self, warning: dict[str, Any]) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "update system_warning_state set last_seen_at = ?, payload_json = ? where warning_key = ?",
                (now(), json.dumps(warning, ensure_ascii=False, sort_keys=True), warning["key"]),
            )
            con.commit()

    def _resolve_inactive_system_warnings(self, active_keys: set[str]) -> None:
        timestamp = now()
        with self.mapping.connect() as con:
            rows = con.execute("select warning_key from system_warning_state where status = 'active'").fetchall()
            for row in rows:
                key = str(row["warning_key"] or "")
                if key not in active_keys:
                    con.execute(
                        "update system_warning_state set status = 'resolved', resolved_at = ?, last_seen_at = ? where warning_key = ?",
                        (timestamp, timestamp, key),
                    )
            con.commit()

    def _log_count(self) -> int:
        with self.mapping.connect() as con:
            row = con.execute("select count(*) as count from notification_logs").fetchone()
        return int(row["count"] if row else 0)

    def _merge_secret_config(self, channel: str, config: dict[str, Any]) -> dict[str, Any]:
        existing = self._setting(channel).get("config") or {}
        clean: dict[str, Any] = {}
        for key, value in (config or {}).items():
            if value is None:
                continue
            if key in SECRET_KEYS and self._looks_masked(value):
                clean[key] = existing.get(key, "")
            else:
                clean[key] = str(value).strip() if isinstance(value, str) else value
        for key, value in existing.items():
            if key in SECRET_KEYS and key not in clean:
                clean[key] = value
        return clean

    def _channels_for_contact(self, contact: dict[str, Any], severity: str) -> list[str]:
        raw_channels = contact.get("preferred_channels")
        preferred = self._decode_json(raw_channels) if raw_channels else (["email"] if contact.get("email") else [])
        channels = [channel for channel in preferred if channel in CHANNELS]
        if not channels:
            return []
        if "email" not in channels and severity in {"yellow", "red"} and contact.get("email"):
            channels.insert(0, "email")
        if severity == "yellow":
            return ["email"] if "email" in channels else []
        return channels

    def _daily_summary_enabled(self) -> bool:
        with self.mapping.connect() as con:
            row = con.execute("select daily_summary from notification_preferences where id = 1").fetchone()
        return bool(row and row["daily_summary"])

    def _message(self, assessment: dict[str, Any]) -> tuple[str, str, str]:
        title = assessment.get("email_subject") or "Sentero Hinweis"
        summary = assessment.get("summary") or "Heute wurde eine Auffälligkeit im Tagesablauf erkannt."
        recommendation = assessment.get("recommendation") or "Bitte fragen Sie kurz nach, ob alles in Ordnung ist."
        findings = assessment.get("findings") or []
        email_body = assessment.get("email_body") or "\n\n".join(
            [
                "Heute wurde eine Auffälligkeit im Tagesablauf erkannt.",
                summary,
                "Beobachtungen:\n" + "\n".join(f"- {item}" for item in findings) if findings else "",
                f"Empfehlung:\n{recommendation}",
            ]
        ).strip()
        short = f"Heute wurde eine Auffälligkeit im Tagesablauf erkannt. {recommendation}"
        return title, email_body, short

    def _test_contact(self, channel: str, config: dict[str, Any]) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from trusted_contacts where active = 1 order by primary_contact desc, id limit 1").fetchone()
        contact = dict(row) if row else {"id": None}
        if channel == "email":
            contact["email"] = contact.get("email") or config.get("test_recipient") or config.get("smtp_user")
            contact["name"] = contact.get("name") or "SMTP Test"
        if channel == "telegram":
            contact["telegram_chat_id"] = config.get("test_recipient") or contact.get("telegram_chat_id") or config.get("default_chat_id")
        if channel == "whatsapp":
            contact["whatsapp_phone_number"] = contact.get("whatsapp_phone_number") or config.get("test_recipient")
        return contact

    def _public_channel(self, row: dict[str, Any]) -> dict[str, Any]:
        config = self._decode_json(row.get("config_json"))
        return {
            "channel": row["channel"],
            "enabled": bool(row.get("enabled")) and self._is_configured(row["channel"], config),
            "configured": self._is_configured(row["channel"], config),
            "config": mask_config(config),
            "updated_at": row.get("updated_at"),
        }

    def _empty_channel(self, channel: str) -> dict[str, Any]:
        return {"channel": channel, "enabled": False, "configured": False, "config": {}, "updated_at": None}

    def _is_configured(self, channel: str, config: dict[str, Any]) -> bool:
        if channel == "email":
            return bool(config.get("smtp_host") and config.get("smtp_user") and config.get("smtp_password"))
        if channel == "telegram":
            return bool(config.get("bot_token") and config.get("default_chat_id"))
        if channel == "whatsapp":
            return bool(config.get("access_token") and config.get("phone_number_id"))
        return False

    def _mark_channel_enabled(self, channel: str, enabled: bool) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "update notification_channel_settings set enabled = ?, updated_at = ? where channel = ?",
                (int(enabled), now(), channel),
            )
            con.commit()

    def _log(self, contact_id: Any, channel: str, severity: str, status: str, title: str, error: str | None, outgoing_message_id: str | None = None) -> None:
        data_class = classify_notification(severity, channel)
        with self.mapping.connect() as con:
            con.execute(
                """insert into notification_logs
                   (contact_id, channel, severity, status, message_title, error_message, data_class, aggregation_level, outgoing_message_id, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (contact_id, channel, severity, status, title, error, data_class, aggregation_for_data_class(data_class), outgoing_message_id, now()),
            )
            con.commit()

    def _public_log(self, row: dict[str, Any]) -> dict[str, Any]:
        data_class = row.get("data_class") or classify_notification(row.get("severity"), row.get("channel"))
        row["data_class"] = data_class
        row["aggregation_level"] = row.get("aggregation_level") or aggregation_for_data_class(str(data_class))
        return row

    def _validate_channel(self, channel: str) -> None:
        if channel not in CHANNELS:
            raise ValueError("unsupported_channel")

    def _decode_json(self, value: Any) -> Any:
        try:
            return json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    def _looks_masked(self, value: Any) -> bool:
        return isinstance(value, str) and ("•" in value or value.startswith("***"))

    def _safe_error(self, exc: Exception) -> str:
        if isinstance(exc, ValueError):
            detail = str(exc)
            if detail == "email_not_configured":
                return "SMTP Host ist nicht konfiguriert."
            if detail == "email_recipient_missing":
                return "Kein Testempfänger gefunden. Bitte Testempfänger oder Vertrauensperson mit E-Mail hinterlegen."
            return detail or "Ungültige E-Mail-Konfiguration."
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return "SMTP Anmeldung fehlgeschlagen. Bitte Benutzername und Passwort prüfen."
        if isinstance(exc, smtplib.SMTPConnectError):
            return "SMTP Verbindung fehlgeschlagen. Bitte Host und Port prüfen."
        if isinstance(exc, smtplib.SMTPServerDisconnected):
            return "SMTP Server hat die Verbindung getrennt."
        if isinstance(exc, smtplib.SMTPException):
            return f"SMTP Fehler: {exc.__class__.__name__}"
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return "SMTP Verbindung ist abgelaufen."
        if isinstance(exc, OSError):
            return f"Netzwerkfehler: {exc.__class__.__name__}"
        return exc.__class__.__name__

    def _test_error(self, dev: bool, detail: str) -> dict[str, Any]:
        response = {"ok": False, "message": detail or "Die Testnachricht konnte nicht gesendet werden. Bitte prüfen Sie die Zugangsdaten."}
        if dev:
            response["detail"] = detail
        return response


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = dict(config or {})
    for key in list(masked.keys()):
        if key in SECRET_KEYS:
            masked[key] = mask_secret(masked.get(key))
    return masked


def mask_secret(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    suffix = text[-4:] if len(text) > 4 else text
    return f"••••••••••••{suffix}"


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "nein"}
    return bool(value)


def email_text_for_fallback(text: str) -> str:
    return f"{text}\n\nHinweis: Ein zusätzlicher Benachrichtigungskanal konnte nicht erreicht werden."


def generate_sentero_message_id(config: dict[str, Any] | None = None) -> str:
    domain = _message_id_domain(config or {})
    return f"<sentero-{uuid.uuid4()}@{domain}>"


def sentero_mail_from(config: dict[str, Any] | None = None) -> str:
    data = config or {}
    smtp_user = _email_address(data.get("smtp_user"))
    configured = str(data.get("mail_from") or "").strip()
    configured_address = _email_address(configured)
    address = configured_address or smtp_user or _email_address(EMAIL_FROM)
    name = "Sentero"
    if configured_address:
        raw_name = configured.rsplit("<", 1)[0].strip().strip('"')
        name = raw_name or name
    return f"{name} <{address}>" if address else name


def _email_address(value: Any) -> str:
    address = parseaddr(str(value or ""))[1].strip()
    return address if "@" in address else ""


def _message_id_domain(config: dict[str, Any]) -> str:
    address = parseaddr(sentero_mail_from(config))[1]
    domain = address.rsplit("@", 1)[1].strip().lower() if "@" in address else ""
    return domain or "sentero.local"


def _provider_message_id(result: dict[str, Any] | None) -> str | None:
    if not isinstance(result, dict):
        return None
    value = str(result.get("message_id") or "").strip()
    return value or None


def add_mail_assistant_footer(text: str, config: dict[str, Any] | None = None) -> str:
    if not mail_assistant_configured(config or {}):
        return text
    footer = "Antworten Sie einfach auf diese E-Mail, um eine Statusfrage an Sentero zu stellen."
    if footer in text:
        return text
    return f"{text.rstrip()}\n\n---\n{footer}"


def mail_assistant_reply_to(contact: dict[str, Any], config: dict[str, Any]) -> str | None:
    if not mail_assistant_configured(config):
        return None
    if not bool(contact.get("email_queries_enabled")):
        return None
    raw_address = (
        str((config or {}).get("reply_to") or "")
        or str((config or {}).get("imap_username") or "")
        or str((config or {}).get("imap_user") or "")
        or str((config or {}).get("smtp_user") or "")
    )
    address = parseaddr(str(raw_address or ""))[1]
    return address if "@" in address else None


def mail_assistant_configured(config: dict[str, Any]) -> bool:
    smtp_user = str((config or {}).get("smtp_user") or "").strip()
    raw_imap_user = str((config or {}).get("imap_user") or (config or {}).get("imap_username") or "").strip()
    imap_host = str((config or {}).get("imap_host") or "").strip().lower()
    imap_user = smtp_user if raw_imap_user.lower() == imap_host else (raw_imap_user or smtp_user)
    smtp_password = str((config or {}).get("smtp_password") or "").strip()
    imap_password = str((config or {}).get("imap_password") or smtp_password).strip()
    return bool(
        (config or {}).get("smtp_host")
        and smtp_user
        and smtp_password
        and (config or {}).get("imap_host")
        and imap_user
        and imap_password
    )


def add_original_timestamp(text: str, original_created_at: str) -> str:
    return f"{text}\n\nUrsprünglicher Zeitpunkt der Warnung: {original_created_at}"
