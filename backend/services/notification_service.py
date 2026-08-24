from __future__ import annotations

import json
import os
import re
import smtplib
import sqlite3
import socket
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, time
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from backend.logging_config import get_logger
from backend.config import config_float, config_int
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
DEFAULT_TEMPERATURE_MIN_CELSIUS = 16.0
DEFAULT_TEMPERATURE_MAX_CELSIUS = 28.0
DEFAULT_HUMIDITY_MAX_PERCENT = 70.0
DEFAULT_INCIDENT_RECOVERY_HEALTHY_CHECKS = 3
SEVERITY_RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}


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

    def get_me(self, config: dict[str, Any]) -> dict[str, Any]:
        token = str(config.get("bot_token") or "").strip()
        if not token:
            raise ValueError("telegram_not_configured")
        response = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        response.raise_for_status()
        data = response.json()
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict) or not result.get("username"):
            raise ValueError("telegram_bot_not_found")
        return result

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any] | None:
        token = str(config.get("bot_token") or "").strip()
        chat_id = str(contact.get("telegram_chat_id") or config.get("default_chat_id") or "").strip()
        if not token or not chat_id:
            raise ValueError("telegram_not_configured")
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{text}"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("result") if isinstance(data, dict) else None
        message_id = message.get("message_id") if isinstance(message, dict) else None
        return {"message_id": f"telegram:{chat_id}:{message_id}"} if message_id is not None else None


class WhatsAppNotificationProvider(NotificationProvider):
    channel = "whatsapp"

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any] | None:
        access_token = str(config.get("access_token") or "").strip()
        phone_number_id = str(config.get("phone_number_id") or "").strip()
        recipient = str(contact.get("whatsapp_phone_number") or config.get("test_recipient") or "").strip()
        api_version = str(config.get("api_version") or "v23.0").strip()
        if not access_token or not phone_number_id or not recipient:
            raise ValueError("whatsapp_not_configured")
        response = requests.post(
            f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"preview_url": False, "body": f"{text}"},
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        messages = data.get("messages") if isinstance(data, dict) else None
        message = messages[0] if isinstance(messages, list) and messages else None
        message_id = message.get("id") if isinstance(message, dict) else None
        return {"message_id": str(message_id)} if message_id else None


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
                    incident_key text,
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
            con.execute(
                """create table if not exists daily_summary_notification_state (
                    summary_date text primary key,
                    sent_at text not null
                )"""
            )
            con.execute(
                """create table if not exists behavior_notification_state (
                    state_key text primary key,
                    incident_key text,
                    category text,
                    subject_id text,
                    status text not null,
                    severity text,
                    first_seen_at text not null,
                    last_seen_at text not null,
                    last_sent_at text,
                    last_notified_severity text,
                    resolved_at text,
                    consecutive_healthy_checks integer not null default 0,
                    reminder_count integer not null default 0,
                    assessment_json text not null default '{}'
                )"""
            )
            con.execute(
                """create table if not exists system_warning_state (
                    warning_key text primary key,
                    incident_key text,
                    category text,
                    subject_id text,
                    status text not null,
                    severity text,
                    first_seen_at text not null,
                    last_seen_at text not null,
                    last_sent_at text,
                    last_notified_severity text,
                    resolved_at text,
                    consecutive_healthy_checks integer not null default 0,
                    reminder_count integer not null default 0,
                    payload_json text not null default '{}'
                )"""
            )
            for statement in [
                "alter table notification_outbox add column incident_key text",
                "alter table notification_logs add column incident_key text",
                "alter table behavior_notification_state add column incident_key text",
                "alter table behavior_notification_state add column category text",
                "alter table behavior_notification_state add column subject_id text",
                "alter table behavior_notification_state add column severity text",
                "alter table behavior_notification_state add column last_notified_severity text",
                "alter table behavior_notification_state add column consecutive_healthy_checks integer not null default 0",
                "alter table behavior_notification_state add column reminder_count integer not null default 0",
                "alter table system_warning_state add column incident_key text",
                "alter table system_warning_state add column category text",
                "alter table system_warning_state add column subject_id text",
                "alter table system_warning_state add column severity text",
                "alter table system_warning_state add column last_notified_severity text",
                "alter table system_warning_state add column consecutive_healthy_checks integer not null default 0",
                "alter table system_warning_state add column reminder_count integer not null default 0",
            ]:
                try:
                    con.execute(statement)
                except sqlite3.OperationalError:
                    pass
            con.execute("update behavior_notification_state set incident_key = state_key where incident_key is null or trim(incident_key) = ''")
            con.execute("update system_warning_state set incident_key = warning_key where incident_key is null or trim(incident_key) = ''")
            try:
                con.execute("create index if not exists idx_notification_logs_incident on notification_logs(incident_key, contact_id, channel, severity, status)")
            except sqlite3.OperationalError:
                pass
            con.execute("create index if not exists idx_notification_outbox_incident on notification_outbox(incident_key, contact_id, channel, severity, status)")
            con.commit()

    def channels(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            rows = con.execute("select * from notification_channel_settings order by channel").fetchall()
        by_channel = {row["channel"]: self._public_channel(dict(row)) for row in rows}
        return {"channels": [by_channel.get(channel) or self._empty_channel(channel) for channel in CHANNELS]}

    def telegram_bot_info(self) -> dict[str, Any]:
        setting = self._setting("telegram")
        provider = self.providers["telegram"]
        if not isinstance(provider, TelegramNotificationProvider):
            raise ValueError("telegram_not_configured")
        bot = provider.get_me(setting.get("config") or {})
        username = str(bot.get("username") or "").strip()
        return {
            "id": bot.get("id"),
            "username": username,
            "first_name": bot.get("first_name"),
            "invite_base_url": f"https://t.me/{username}" if username else "",
        }

    def save_channel(self, channel: str, enabled: bool, config: dict[str, Any]) -> dict[str, Any]:
        self._validate_channel(channel)
        existing = self._setting(channel).get("config") or {}
        clean_config = self._merge_secret_config(channel, config)
        still_valid = bool(self._setting(channel).get("enabled")) and clean_config == existing
        enabled_after_save = (bool(enabled) or still_valid) and self._is_configured(channel, clean_config)
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

    def stored_channel_config(self, channel: str) -> dict[str, Any]:
        """Return raw stored channel config for internal backend use only.

        Public API output remains masked by _public_channel()/mask_config().
        """
        self._validate_channel(channel)
        return dict(self._setting(channel).get("config") or {})

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
            if channel == "telegram" and not contact.get("telegram_chat_id"):
                provider = self.providers[channel]
                if not isinstance(provider, TelegramNotificationProvider):
                    raise ValueError("telegram_not_configured")
                bot = provider.get_me(setting.get("config") or {})
                result = {"message_id": f"telegram:bot:{bot.get('id')}"}
            else:
                result = self.providers[channel].send(contact, title, text, setting.get("config") or {})
            self._mark_channel_enabled(channel, True)
            self._log(contact.get("id"), channel, "yellow", "sent", title, None, outgoing_message_id=_provider_message_id(result))
            message = "Telegram Bot verbunden. Einladungslinks sind verfügbar." if channel == "telegram" and not contact.get("telegram_chat_id") else "Testnachricht gesendet."
            return {"ok": True, "message": message}
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
                self._log(item.get("contact_id"), channel, str(item["severity"]), "sent", str(item["title"]), None, outgoing_message_id=_provider_message_id(result), incident_key=item.get("incident_key"))
                sent += 1
            except Exception as exc:
                self._mark_outbox(int(item["id"]), "failed", self._safe_error(exc))
        return {"sent": sent, "remaining": self._pending_count()}

    def send_daily_summary_if_due(self, now_dt: datetime | None = None) -> dict[str, Any]:
        if not self._daily_summary_enabled():
            return {"sent": 0, "skipped": "disabled"}
        local_now = local_summary_now(now_dt)
        scheduled = daily_summary_time()
        if local_now.time() < scheduled:
            return {"sent": 0, "skipped": "not_due", "scheduled_time": scheduled.strftime("%H:%M")}
        summary_date = local_now.date().isoformat()
        if self._daily_summary_already_sent(summary_date):
            return {"sent": 0, "skipped": "already_sent", "summary_date": summary_date}
        contacts = self._trusted_contacts()
        text = self._daily_summary_text(summary_date)
        sent = 0
        for contact in contacts:
            if not bool(contact.get("notification_enabled", 1)):
                continue
            if not can_access_data_classes(contact.get("actor_role") or "relative", DEFAULT_NOTIFICATION_DATA_CLASSES, aggregation_level="summary"):
                self._log(contact.get("id"), "consent", "yellow", "skipped_role_denied", "Sentero Tageszusammenfassung", None)
                continue
            if not self.consent.has_active_consent(contact.get("id"), DEFAULT_NOTIFICATION_PURPOSE, DEFAULT_NOTIFICATION_DATA_CLASSES):
                self._log(contact.get("id"), "consent", "yellow", "skipped_no_consent", "Sentero Tageszusammenfassung", None)
                continue
            for channel in self._channels_for_contact(contact, "yellow"):
                before = self._log_count()
                self._send_with_log(contact, channel, "yellow", "Sentero Tageszusammenfassung", text, fallback=False)
                if self._log_count() > before:
                    sent += 1
        if sent:
            self._mark_daily_summary_sent(summary_date)
        return {"sent": sent, "summary_date": summary_date, "scheduled_time": scheduled.strftime("%H:%M")}

    def send_email_direct(self, to_email: str, title: str, text: str, config: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any] | None:
        clean_config = {**(config or {}), "headers": headers or {}}
        return self.providers["email"].send({"email": to_email}, title, text, clean_config)

    def notify_assessment(self, assessment: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, Any]:
        severity = str(assessment.get("status") or "green")
        if severity == "green":
            self.resolve_behavior_notification()
            return {"sent": 0, "skipped": "resolved"}
        if severity == "yellow" and not self._daily_summary_enabled():
            self.resolve_behavior_notification()
            return {"sent": 0, "skipped": "resolved"}
        state_key = "behavior_anomaly"
        incident_key = f"behavior:{state_key}"
        title, email_text, short_text = self._message(assessment)
        eligible: list[dict[str, Any]] = []
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
            eligible.append(contact)
        if not eligible:
            return {"sent": 0, "skipped": "no_eligible_contacts"}
        if severity in {"orange", "red"}:
            action = self._claim_behavior_incident(state_key, incident_key, assessment, severity)
            if action == "suppress":
                return {"sent": 0, "skipped": "already_active"}
        else:
            action = "send"
        delivered = 0
        for contact in eligible:
            channels = self._channels_for_contact(contact, severity)
            for channel in channels:
                text = email_text if channel == "email" else short_text
                before = self._log_count()
                self._send_with_log(contact, channel, severity, title, text, fallback=severity == "red", incident_key=incident_key)
                if self._log_count() > before:
                    delivered += 1
        return {"sent": delivered, "incident_action": action}

    def resolve_behavior_notification(self, state_key: str = "behavior_anomaly") -> None:
        timestamp = now()
        required = self._incident_recovery_healthy_checks()
        with self.mapping.connect() as con:
            row = con.execute(
                "select consecutive_healthy_checks from behavior_notification_state where state_key = ? and status = 'active'",
                (state_key,),
            ).fetchone()
            if not row:
                return
            healthy_checks = int(row["consecutive_healthy_checks"] or 0) + 1
            if healthy_checks < required:
                con.execute(
                    "update behavior_notification_state set consecutive_healthy_checks = ?, last_seen_at = ? where state_key = ? and status = 'active'",
                    (healthy_checks, timestamp, state_key),
                )
                con.commit()
                return
            con.execute(
                """update behavior_notification_state
                   set status = 'resolved', resolved_at = ?, last_seen_at = ?, consecutive_healthy_checks = ?
                   where state_key = ? and status = 'active'""",
                (timestamp, timestamp, healthy_checks, state_key),
            )
            con.commit()

    def notify_system_warnings(
        self,
        sensors: list[dict[str, Any]] | None = None,
        battery_threshold: int = BATTERY_WARNING_THRESHOLD,
        environmental_sensors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self._critical_notifications_enabled():
            return {"sent": 0, "warnings": [], "skipped": "critical_notifications_disabled"}

        # Notification scope is the configured Sentero sensor set only. Never use
        # arbitrary MQTT devices visible on the broker for customer notifications.
        sensor_rows = sensors if sensors is not None else self.mapping.roles(dev=True, include_state=True)
        sensor_rows = [row for row in sensor_rows if row.get("active", True) and row.get("enabled", True)]
        environmental_rows = self._environmental_snapshot_rows(sensor_rows)
        active_warnings = self._system_warnings(sensor_rows, battery_threshold=battery_threshold, environmental_sensors=environmental_rows)
        logger.debug(
            "System warnings evaluated",
            extra={"component": "notification", "sensor_count": len(sensor_rows), "warning_count": len(active_warnings)},
        )
        active_keys = {warning["key"] for warning in active_warnings}
        self._resolve_inactive_system_warnings(active_keys)

        contacts = self._trusted_contacts()
        sent = 0
        for warning in active_warnings:
            action = self._claim_system_warning_incident(warning)
            if action == "suppress":
                continue

            delivered = self._send_system_warning(warning, contacts)
            if delivered:
                sent += delivered

        return {"sent": sent, "warnings": active_warnings}

    def _system_warnings(
        self,
        sensors: list[dict[str, Any]],
        battery_threshold: int,
        environmental_sensors: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        for sensor in sensors:
            if not sensor.get("configured", sensor.get("active", True)):
                continue
            role = str(sensor.get("role") or "").strip()
            if not role:
                continue
            label = str(sensor.get("label") or sensor.get("friendly_name") or role).strip()
            room = str(sensor.get("room") or "").strip()
            subject_id = self._sensor_subject_id(sensor)
            battery = sensor.get("battery_level")
            if isinstance(battery, (int, float)) and battery < battery_threshold:
                warnings.append({
                    "key": f"battery_low:{subject_id}",
                    "type": "battery_low",
                    "severity": "orange",
                    "title": "Sentero Sensor-Batterie schwach",
                    "summary": f"Die Batterie von {label} liegt bei {int(battery)}%.",
                    "recommendation": "Bitte wechseln Sie die Batterie zeitnah, damit Sentero zuverlässig bleibt.",
                    "role": role,
                    "subject_id": subject_id,
                    "label": label,
                    "room": room,
                    "battery_level": int(battery),
                })
            if sensor.get("reachable") is False:
                warnings.append({
                    "key": f"sensor_unreachable:{subject_id}",
                    "type": "sensor_unreachable",
                    "severity": "red",
                    "title": "Sentero Sensor nicht erreichbar",
                    "summary": f"{label} ist aktuell nicht erreichbar.",
                    "recommendation": "Bitte prüfen Sie Stromversorgung, Funkverbindung oder Gateway, damit Warnungen zuverlässig erkannt werden.",
                    "role": role,
                    "subject_id": subject_id,
                    "label": label,
                    "room": room,
                    "battery_level": battery if isinstance(battery, (int, float)) else None,
                })
        for warning in self._environmental_warnings(environmental_sensors or sensors):
            warnings.append(warning)
        return warnings

    def _environmental_snapshot_rows(self, sensors: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if sensors is not None:
            return [row for row in sensors if row.get("active", True) and row.get("enabled", True)]
        try:
            return [
                row for row in self.mapping.roles(dev=True, include_state=True)
                if row.get("active", True) and row.get("enabled", True)
            ]
        except Exception:
            logger.exception("Configured environmental sensor snapshot unavailable", extra={"component": "notification"})
            return []

    def _environmental_warnings(self, sensors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        thresholds = {
            "temperature_min_celsius": config_float("sentero.environment.temperature_min_celsius", DEFAULT_TEMPERATURE_MIN_CELSIUS),
            "temperature_max_celsius": config_float("sentero.environment.temperature_max_celsius", DEFAULT_TEMPERATURE_MAX_CELSIUS),
            "humidity_max_percent": config_float("sentero.environment.humidity_max_percent", DEFAULT_HUMIDITY_MAX_PERCENT),
        }
        warnings: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sensor in sensors:
            if not sensor.get("active", True) or not sensor.get("enabled", True):
                continue

            # Configured combined presence sensors may expose environmental values
            # as fields on the same role (temperature/humidity/illuminance).
            measurements: list[tuple[str, float]] = []
            for kind, field in (("temperature", "temperature"), ("humidity", "humidity")):
                raw = sensor.get(field)
                if isinstance(raw, (int, float)):
                    measurements.append((kind, float(raw)))
            if not measurements:
                kind = self._environmental_kind(sensor)
                value = self._measurement_value(sensor)
                if kind in {"temperature", "humidity"} and value is not None:
                    measurements.append((kind, value))

            for kind, value in measurements:
                key_base = self._sensor_subject_id(sensor)
                if kind == "temperature" and value < thresholds["temperature_min_celsius"]:
                    warning = self._environmental_warning(sensor, "temperature_low", value, "°C", "red", f"unter {self._format_measurement(thresholds['temperature_min_celsius'])} °C")
                elif kind == "temperature" and value > thresholds["temperature_max_celsius"]:
                    warning = self._environmental_warning(sensor, "temperature_high", value, "°C", "red", f"über {self._format_measurement(thresholds['temperature_max_celsius'])} °C")
                elif kind == "humidity" and value > thresholds["humidity_max_percent"]:
                    warning = self._environmental_warning(sensor, "humidity_high", value, "%", "orange", f"über {self._format_measurement(thresholds['humidity_max_percent'])} %")
                    warning["threshold_value"] = thresholds["humidity_max_percent"]
                else:
                    continue
                warning["raw_measurement_value"] = value
                warning["key"] = f"{warning['type']}:{key_base}"
                if warning["key"] in seen:
                    continue
                seen.add(warning["key"])
                warnings.append(warning)
        return warnings

    def _environmental_warning(self, sensor: dict[str, Any], warning_type: str, value: float, unit: str, severity: str, threshold_text: str) -> dict[str, Any]:
        role = str(sensor.get("role") or sensor.get("entity_id") or warning_type).strip()
        label = str(sensor.get("label") or sensor.get("friendly_name") or sensor.get("original_name") or sensor.get("entity_id") or role).strip()
        room = str(sensor.get("room") or sensor.get("area_id") or "").strip()
        if warning_type.startswith("temperature"):
            direction = "zu niedrige" if warning_type == "temperature_low" else "zu hohe"
            summary = f"{label} meldet eine {direction} Raumtemperatur von {self._format_measurement(value)} {unit}."
            recommendation = "Bitte prüfen Sie Heizung, Lüftung oder Klimatisierung und kontaktieren Sie die betreute Person zeitnah."
            title = "Sentero Raumtemperatur kritisch"
        else:
            summary = f"{label} meldet eine hohe Luftfeuchtigkeit von {self._format_measurement(value)} {unit}."
            recommendation = "Bitte prüfen Sie Lüftung, Bad-/Küchennutzung oder mögliche Feuchtigkeitsschäden zeitnah."
            title = "Sentero Luftfeuchtigkeit auffällig"
        return {
            "key": "",
            "type": warning_type,
            "severity": severity,
            "title": title,
            "summary": f"{summary} Der konfigurierte Grenzwert liegt {threshold_text}.",
            "recommendation": recommendation,
            "role": role,
            "subject_id": self._sensor_subject_id(sensor),
            "label": label,
            "room": room,
            "measurement_value": self._format_measurement(value),
            "measurement_unit": unit,
            "data_class": "environmental",
            "aggregation_level": "raw",
        }

    def _environmental_kind(self, sensor: dict[str, Any]) -> str | None:
        domain = str(sensor.get("domain") or "").lower()
        if domain and domain != "sensor":
            return None
        device_class = str(sensor.get("device_class") or "").lower()
        if device_class == "temperature":
            return "temperature"
        if device_class == "humidity":
            return "humidity"
        if device_class:
            return None
        text = " ".join(str(sensor.get(key) or "").lower() for key in ("entity_id", "label", "friendly_name"))
        if any(term in text for term in ("battery", "batterie", "voltage", "spannung", "pressure", "druck", "calibration", "kalibrierung")):
            return None
        if "temperature" in text or "temperatur" in text:
            return "temperature"
        if "humidity" in text or "luftfeuchtigkeit" in text:
            return "humidity"
        return None

    def _measurement_value(self, sensor: dict[str, Any]) -> float | None:
        for key in ("state", "value", "measurement_value"):
            value = sensor.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                match = re.search(r"-?\d+(?:[,.]\d+)?", value)
                if match:
                    try:
                        return float(match.group(0).replace(",", "."))
                    except ValueError:
                        return None
        return None

    def _format_measurement(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.1f}".replace(".", ",")

    def _send_system_warning(self, warning: dict[str, Any], contacts: list[dict[str, Any]]) -> int:
        title = str(warning.get("title") or "Sentero Systemwarnung")
        email_text = self._system_warning_email_text(warning)
        severity = str(warning.get("severity") or "orange")
        incident_key = str(warning.get("key") or "")
        delivered = 0
        for contact in contacts:
            if not bool(contact.get("notification_enabled", 1)):
                continue
            for channel in self._channels_for_contact(contact, severity):
                before = self._log_count()
                self._send_with_log(contact, channel, severity, title, email_text, fallback=False, incident_key=incident_key)
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
        if warning.get("measurement_value") is not None and warning.get("measurement_unit"):
            lines.append(f"Messwert: {warning.get('measurement_value')} {warning.get('measurement_unit')}")
        if warning.get("data_class"):
            lines.append(f"Datenklasse: {warning.get('data_class')}")
        lines.extend(["", str(warning.get("recommendation") or "Bitte prüfen Sie das System.")])
        return "\n".join(lines).strip()

    def _send_with_log(self, contact: dict[str, Any], channel: str, severity: str, title: str, text: str, fallback: bool, incident_key: str | None = None) -> None:
        setting = self._setting(channel)
        if not setting.get("enabled"):
            return
        if channel == "email":
            text = add_mail_assistant_footer(text, setting.get("config") or {})
        if self._should_queue_offline():
            self._enqueue(contact, channel, severity, title, text, incident_key=incident_key)
            self._log(contact.get("id"), channel, severity, "pending", title, None, incident_key=incident_key)
            return
        try:
            result = self.providers[channel].send(contact, title, text, setting.get("config") or {})
            self._log(contact.get("id"), channel, severity, "sent", title, None, outgoing_message_id=_provider_message_id(result), incident_key=incident_key)
        except Exception as exc:
            safe_error = self._safe_error(exc)
            logger.exception(
                "Notification delivery failed",
                extra={"component": "notification", "channel": channel, "contact_id": contact.get("id"), "severity": severity},
            )
            self._log(contact.get("id"), channel, severity, "failed", title, safe_error, incident_key=incident_key)
            if channel != "email" and fallback:
                try:
                    email_setting = self._setting("email")
                    result = self.providers["email"].send(contact, title, email_text_for_fallback(text), email_setting.get("config") or {})
                    self._log(contact.get("id"), "email", severity, "fallback_sent", title, None, outgoing_message_id=_provider_message_id(result), incident_key=incident_key)
                except Exception as fallback_exc:
                    logger.exception(
                        "Notification fallback email failed",
                        extra={"component": "notification", "contact_id": contact.get("id"), "severity": severity},
                    )
                    self._log(contact.get("id"), "email", severity, "failed", title, self._safe_error(fallback_exc), incident_key=incident_key)

    def _should_queue_offline(self) -> bool:
        if not self.connectivity:
            return False
        check = self.connectivity.check(ConnectionType.NONE)
        return check.status in {NetworkStatusCode.OFFLINE, NetworkStatusCode.LOCAL_ONLY}

    def _enqueue(self, contact: dict[str, Any], channel: str, severity: str, title: str, text: str, incident_key: str | None = None) -> None:
        timestamp = now()
        safe_contact = {key: contact.get(key) for key in ("id", "name", "email", "telegram_chat_id", "whatsapp_phone_number") if contact.get(key)}
        with self.mapping.connect() as con:
            if incident_key:
                existing = con.execute(
                    """select id from notification_outbox
                       where incident_key = ? and contact_id is ? and channel = ? and severity = ? and status in ('pending', 'failed')
                       limit 1""",
                    (incident_key, contact.get("id"), channel, severity),
                ).fetchone()
                if existing:
                    return
            con.execute(
                """insert into notification_outbox
                   (incident_key, contact_id, channel, severity, title, text, contact_json, status, original_created_at)
                   values (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (incident_key, contact.get("id"), channel, severity, title, text, json.dumps(safe_contact, ensure_ascii=False, sort_keys=True), timestamp),
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

    def _incident_recovery_healthy_checks(self) -> int:
        return max(1, config_int("notifications.incident_recovery.healthy_checks_required", DEFAULT_INCIDENT_RECOVERY_HEALTHY_CHECKS))

    def _sensor_subject_id(self, sensor: dict[str, Any]) -> str:
        for key in ("device_id", "physical_device_id", "sentero_device_id"):
            value = str(sensor.get(key) or "").strip()
            if value:
                return value.lower()
        identifiers = sensor.get("identifiers")
        if isinstance(identifiers, list):
            for item in identifiers:
                if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
                    return str(item[1]).strip().lower()
                if isinstance(item, str) and item.strip():
                    return item.strip().lower()
        for key in ("primary_entity_id", "source_ref", "entity_id", "resolved_entity_id", "role"):
            value = str(sensor.get(key) or "").strip()
            if value:
                return value.lower()
        return "unknown"

    def _system_warning_state(self, key: str) -> dict[str, Any] | None:
        with self.mapping.connect() as con:
            row = con.execute("select * from system_warning_state where warning_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    def _behavior_notification_active(self, state_key: str) -> bool:
        with self.mapping.connect() as con:
            row = con.execute(
                "select status, last_sent_at from behavior_notification_state where state_key = ?",
                (state_key,),
            ).fetchone()
        return bool(row and row["status"] == "active" and row["last_sent_at"])

    def _claim_behavior_incident(self, state_key: str, incident_key: str, assessment: dict[str, Any], severity: str) -> str:
        timestamp = now()
        payload = json.dumps(assessment, ensure_ascii=False, sort_keys=True)
        with self.mapping.connect() as con:
            con.execute("begin immediate")
            row = con.execute("select * from behavior_notification_state where state_key = ?", (state_key,)).fetchone()
            existing = dict(row) if row else None
            last_notified = str((existing or {}).get("last_notified_severity") or "")
            active = bool(existing and existing.get("status") == "active" and existing.get("last_sent_at"))
            escalated = active and SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(last_notified, 0)
            should_send = not active or escalated
            first_seen = existing.get("first_seen_at") if existing and existing.get("status") == "active" else timestamp
            con.execute(
                """insert into behavior_notification_state
                   (state_key, incident_key, category, subject_id, status, severity, first_seen_at, last_seen_at,
                    last_sent_at, last_notified_severity, resolved_at, consecutive_healthy_checks, reminder_count, assessment_json)
                   values (?, ?, 'behavior', ?, 'active', ?, ?, ?, ?, ?, null, 0, ?, ?)
                   on conflict(state_key) do update set
                       incident_key = excluded.incident_key,
                       category = excluded.category,
                       subject_id = excluded.subject_id,
                       status = 'active',
                       severity = excluded.severity,
                       first_seen_at = excluded.first_seen_at,
                       last_seen_at = excluded.last_seen_at,
                       last_sent_at = coalesce(excluded.last_sent_at, behavior_notification_state.last_sent_at),
                       last_notified_severity = coalesce(excluded.last_notified_severity, behavior_notification_state.last_notified_severity),
                       resolved_at = null,
                       consecutive_healthy_checks = 0,
                       reminder_count = excluded.reminder_count,
                       assessment_json = excluded.assessment_json""",
                (
                    state_key,
                    incident_key,
                    "behavior_anomaly",
                    severity,
                    first_seen,
                    timestamp,
                    timestamp if should_send else None,
                    severity if should_send else None,
                    int((existing or {}).get("reminder_count") or 0),
                    payload,
                ),
            )
            con.commit()
        return "send" if not active else "escalate" if escalated else "suppress"

    def _claim_system_warning_incident(self, warning: dict[str, Any]) -> str:
        timestamp = now()
        warning_key = str(warning["key"])
        severity = str(warning.get("severity") or "orange")
        payload = json.dumps(warning, ensure_ascii=False, sort_keys=True)
        with self.mapping.connect() as con:
            con.execute("begin immediate")
            row = con.execute("select * from system_warning_state where warning_key = ?", (warning_key,)).fetchone()
            existing = dict(row) if row else None
            outbox_existing = self._outbox_existing_for_incident(con, warning_key, severity)
            last_notified = str((existing or {}).get("last_notified_severity") or "")
            active = bool(existing and existing.get("status") == "active" and existing.get("last_sent_at"))
            escalated = active and SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK.get(last_notified, 0)
            should_send = not active or escalated
            first_seen = existing.get("first_seen_at") if existing and existing.get("status") == "active" else timestamp
            action = "send" if not active else "escalate" if escalated else "suppress"
            reason = self._incident_decision_reason(existing, active, escalated, should_send)
            if warning.get("type") == "humidity_high":
                logger.info("Humidity warning incident decision", extra=self._humidity_warning_debug_payload(warning, existing, outbox_existing, action, reason))
            con.execute(
                """insert into system_warning_state
                   (warning_key, incident_key, category, subject_id, status, severity, first_seen_at, last_seen_at,
                    last_sent_at, last_notified_severity, resolved_at, consecutive_healthy_checks, reminder_count, payload_json)
                   values (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, null, 0, ?, ?)
                   on conflict(warning_key) do update set
                       incident_key = excluded.incident_key,
                       category = excluded.category,
                       subject_id = excluded.subject_id,
                       status = 'active',
                       severity = excluded.severity,
                       first_seen_at = excluded.first_seen_at,
                       last_seen_at = excluded.last_seen_at,
                       last_sent_at = coalesce(excluded.last_sent_at, system_warning_state.last_sent_at),
                       last_notified_severity = coalesce(excluded.last_notified_severity, system_warning_state.last_notified_severity),
                       resolved_at = null,
                       consecutive_healthy_checks = 0,
                       reminder_count = excluded.reminder_count,
                       payload_json = excluded.payload_json""",
                (
                    warning_key,
                    warning_key,
                    str(warning.get("type") or "system"),
                    str(warning.get("subject_id") or ""),
                    severity,
                    first_seen,
                    timestamp,
                    timestamp if should_send else None,
                    severity if should_send else None,
                    int((existing or {}).get("reminder_count") or 0),
                    payload,
                ),
            )
            con.commit()
        return action

    def _outbox_existing_for_incident(self, con: sqlite3.Connection, incident_key: str, severity: str) -> bool:
        row = con.execute(
            """select id from notification_outbox
               where incident_key = ? and severity = ? and status in ('pending', 'failed')
               limit 1""",
            (incident_key, severity),
        ).fetchone()
        return row is not None

    def _incident_decision_reason(self, existing: dict[str, Any] | None, active: bool, escalated: bool, should_send: bool) -> str:
        if not existing:
            return "incident_not_found"
        if active and not escalated:
            return "active_incident_already_notified"
        if escalated:
            return "active_incident_severity_escalated"
        if existing.get("status") == "resolved":
            return "previous_incident_resolved"
        if should_send:
            return "incident_not_previously_notified"
        return "suppressed"

    def _humidity_warning_debug_payload(
        self,
        warning: dict[str, Any],
        existing: dict[str, Any] | None,
        outbox_existing: bool,
        decision: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "component": "notification",
            "warning_type": "humidity_high",
            "device_id": warning.get("subject_id"),
            "incident_key": warning.get("key"),
            "current_value": warning.get("raw_measurement_value") or warning.get("measurement_value"),
            "threshold": warning.get("threshold_value"),
            "incident_found": existing is not None,
            "incident_status": (existing or {}).get("status"),
            "first_seen_at": (existing or {}).get("first_seen_at"),
            "last_seen_at": (existing or {}).get("last_seen_at"),
            "last_sent_at": (existing or {}).get("last_sent_at"),
            "resolved_at": (existing or {}).get("resolved_at"),
            "last_notified_severity": (existing or {}).get("last_notified_severity"),
            "outbox_existing": outbox_existing,
            "decision": decision,
            "reason": reason,
        }

    def _upsert_behavior_notification(self, state_key: str, assessment: dict[str, Any], sent_now: bool) -> None:
        timestamp = now()
        with self.mapping.connect() as con:
            existing = con.execute("select * from behavior_notification_state where state_key = ?", (state_key,)).fetchone()
            first_seen = existing["first_seen_at"] if existing else timestamp
            last_sent = timestamp if sent_now else (existing["last_sent_at"] if existing else None)
            con.execute(
                """insert into behavior_notification_state
                   (state_key, status, first_seen_at, last_seen_at, last_sent_at, resolved_at, assessment_json)
                   values (?, 'active', ?, ?, ?, null, ?)
                   on conflict(state_key) do update set
                       status = 'active',
                       last_seen_at = excluded.last_seen_at,
                       last_sent_at = coalesce(excluded.last_sent_at, behavior_notification_state.last_sent_at),
                       resolved_at = null,
                       assessment_json = excluded.assessment_json""",
                (state_key, first_seen, timestamp, last_sent, json.dumps(assessment, ensure_ascii=False, sort_keys=True)),
            )
            con.commit()

    def _touch_behavior_notification(self, state_key: str, assessment: dict[str, Any]) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "update behavior_notification_state set last_seen_at = ?, assessment_json = ? where state_key = ?",
                (now(), json.dumps(assessment, ensure_ascii=False, sort_keys=True), state_key),
            )
            con.commit()

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
        required = self._incident_recovery_healthy_checks()
        with self.mapping.connect() as con:
            rows = con.execute("select warning_key, consecutive_healthy_checks from system_warning_state where status = 'active'").fetchall()
            for row in rows:
                key = str(row["warning_key"] or "")
                if key not in active_keys:
                    healthy_checks = int(row["consecutive_healthy_checks"] or 0) + 1
                    if healthy_checks >= required:
                        con.execute(
                            """update system_warning_state
                               set status = 'resolved', resolved_at = ?, last_seen_at = ?, consecutive_healthy_checks = ?
                               where warning_key = ?""",
                            (timestamp, timestamp, healthy_checks, key),
                        )
                    else:
                        con.execute(
                            "update system_warning_state set consecutive_healthy_checks = ?, last_seen_at = ? where warning_key = ?",
                            (healthy_checks, timestamp, key),
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
        channels = [channel for channel in preferred if channel in CHANNELS and self._contact_channel_ready(contact, channel)]
        if not channels:
            return []
        if "email" not in channels and severity in {"yellow", "red"} and contact.get("email"):
            channels.insert(0, "email")
        if severity == "yellow":
            return ["email"] if "email" in channels else []
        return channels

    def _contact_channel_ready(self, contact: dict[str, Any], channel: str) -> bool:
        if channel == "email":
            return bool(contact.get("email"))
        if channel == "telegram":
            return bool(contact.get("telegram_chat_id"))
        if channel == "whatsapp":
            return bool(contact.get("whatsapp_phone_number"))
        return False

    def _daily_summary_enabled(self) -> bool:
        with self.mapping.connect() as con:
            row = con.execute("select daily_summary from notification_preferences where id = 1").fetchone()
        return bool(row and row["daily_summary"])

    def _daily_summary_already_sent(self, summary_date: str) -> bool:
        with self.mapping.connect() as con:
            row = con.execute("select summary_date from daily_summary_notification_state where summary_date = ?", (summary_date,)).fetchone()
        return row is not None

    def _mark_daily_summary_sent(self, summary_date: str) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "insert or replace into daily_summary_notification_state (summary_date, sent_at) values (?, ?)",
                (summary_date, now()),
            )
            con.commit()

    def _daily_summary_text(self, summary_date: str) -> str:
        try:
            with self.mapping.connect() as con:
                row = con.execute("select * from behavior_daily_summary where date = ?", (summary_date,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if not row:
            return "Heute liegen noch keine verwertbaren Tagesdaten vor. Sentero überwacht die verbundenen Sensoren weiter."
        summary = dict(row)
        lines = ["Tägliche Zusammenfassung:", ""]
        first = _time_text(summary.get("first_activity"))
        last = _time_text(summary.get("last_activity"))
        if first:
            lines.append(f"Erste Aktivität: {first}.")
        if last:
            lines.append(f"Letzte Aktivität: {last}.")
        lines.append(f"Aktive Zeit: {int(summary.get('active_minutes') or 0)} Minuten.")
        lines.append(f"Türereignisse: {int(summary.get('door_events') or 0)}.")
        room_usage = self._decode_json(summary.get("room_usage"))
        if isinstance(room_usage, dict) and room_usage:
            rooms = "; ".join(f"{room}: {count}" for room, count in room_usage.items())
            lines.append(f"Raumaktivität: {rooms}.")
        anomaly_score = int(summary.get("anomaly_score") or 0)
        lines.append("Bewertung: keine auffälligen Hinweise." if anomaly_score == 0 else f"Bewertung: Auffälligkeitsscore {anomaly_score}.")
        return "\n".join(lines).strip()

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
            return bool(config.get("bot_token"))
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

    def _log(self, contact_id: Any, channel: str, severity: str, status: str, title: str, error: str | None, outgoing_message_id: str | None = None, incident_key: str | None = None) -> None:
        data_class = classify_notification(severity, channel)
        with self.mapping.connect() as con:
            con.execute(
                """insert into notification_logs
                   (incident_key, contact_id, channel, severity, status, message_title, error_message, data_class, aggregation_level, outgoing_message_id, created_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (incident_key, contact_id, channel, severity, status, title, error, data_class, aggregation_for_data_class(data_class), outgoing_message_id, now()),
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
            if detail == "telegram_not_configured":
                return "Telegram Bot Token ist nicht konfiguriert."
            if detail == "telegram_bot_not_found":
                return "Telegram Bot konnte nicht erkannt werden. Bitte Token prüfen."
            if detail == "whatsapp_not_configured":
                return "WhatsApp Access Token, Phone Number ID und Empfänger sind nicht vollständig konfiguriert."
            return detail or "Ungültige E-Mail-Konfiguration."
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return "SMTP Anmeldung fehlgeschlagen. Bitte Benutzername und Passwort prüfen."
        if isinstance(exc, smtplib.SMTPConnectError):
            return "SMTP Verbindung fehlgeschlagen. Bitte Host und Port prüfen."
        if isinstance(exc, smtplib.SMTPServerDisconnected):
            return "SMTP Server hat die Verbindung getrennt."
        if isinstance(exc, smtplib.SMTPException):
            return f"SMTP Fehler: {exc.__class__.__name__}"
        if isinstance(exc, requests.HTTPError):
            status_code = exc.response.status_code if exc.response is not None else ""
            detail = http_error_detail(exc)
            suffix = f": {detail}" if detail else ""
            return f"HTTP Fehler beim Benachrichtigungskanal: {status_code or exc.__class__.__name__}{suffix}"
        if isinstance(exc, requests.RequestException):
            return f"Netzwerkfehler beim Benachrichtigungskanal: {exc.__class__.__name__}"
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


def http_error_detail(exc: requests.HTTPError) -> str:
    response = exc.response
    if response is None:
        return ""
    try:
        data = response.json()
    except ValueError:
        text = str(getattr(response, "text", "") or "").strip()
        return text[:200]
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("error_user_msg") or "").strip()
        return message[:200]
    description = str(data.get("description") or data.get("message") or "").strip()
    return description[:200]


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


def daily_summary_time() -> time:
    raw = str(os.getenv("SENTERO_DAILY_SUMMARY_TIME") or "20:00").strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        return time(hour=max(0, min(int(hour_text), 23)), minute=max(0, min(int(minute_text), 59)))
    except (TypeError, ValueError):
        return time(hour=20, minute=0)


def local_summary_now(value: datetime | None = None) -> datetime:
    timezone_name = str(os.getenv("SENTERO_TIMEZONE") or os.getenv("TZ") or "Europe/Berlin").strip()
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Europe/Berlin")
    current = value or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    return current.astimezone(tz)


def _time_text(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:5] if len(raw) >= 5 else raw
    return local_summary_now(parsed).strftime("%H:%M")
