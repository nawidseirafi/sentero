from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MailEncryption(str, Enum):
    SSL = "SSL"
    STARTTLS = "STARTTLS"
    NONE = "NONE"


class MailConfig(BaseModel):
    """Connection settings for an IMAP/SMTP mailbox."""

    imap_host: str
    imap_port: int = Field(ge=1, le=65535)
    imap_encryption: MailEncryption
    smtp_host: str
    smtp_port: int = Field(ge=1, le=65535)
    smtp_encryption: MailEncryption
    auth_method: str | None = None
    requires_app_password: bool = False
    app_password_help_url: str | None = None
    source: Literal["ispdb", "fallback", "manual"]


class MailIntent(str, Enum):
    STATUS_SUMMARY = "STATUS_SUMMARY"
    POWER_USAGE = "POWER_USAGE"
    CONTACT_STATUS = "CONTACT_STATUS"
    CURRENT_ACTIVITY = "CURRENT_ACTIVITY"
    LAST_ACTIVITY = "LAST_ACTIVITY"
    LAST_ROOM = "LAST_ROOM"
    TODAY_SUMMARY = "TODAY_SUMMARY"
    ANOMALIES = "ANOMALIES"
    ENVIRONMENT = "ENVIRONMENT"
    NIGHT_SUMMARY = "NIGHT_SUMMARY"
    SENSOR_HEALTH = "SENSOR_HEALTH"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"


class MailPermission(str, Enum):
    STATUS = "STATUS"
    ACTIVITY = "ACTIVITY"
    ROOM = "ROOM"
    ENVIRONMENT = "ENVIRONMENT"
    NIGHT = "NIGHT"
    HISTORY = "HISTORY"
    TECHNICAL_HEALTH = "TECHNICAL_HEALTH"


INTENT_PERMISSIONS: dict[MailIntent, set[MailPermission]] = {
    MailIntent.STATUS_SUMMARY: {MailPermission.STATUS},
    MailIntent.POWER_USAGE: {MailPermission.STATUS},
    MailIntent.CONTACT_STATUS: {MailPermission.STATUS},
    MailIntent.CURRENT_ACTIVITY: {MailPermission.ACTIVITY, MailPermission.ROOM},
    MailIntent.LAST_ACTIVITY: {MailPermission.ACTIVITY},
    MailIntent.LAST_ROOM: {MailPermission.ROOM},
    MailIntent.TODAY_SUMMARY: {MailPermission.STATUS, MailPermission.ACTIVITY, MailPermission.HISTORY},
    MailIntent.ANOMALIES: {MailPermission.STATUS},
    MailIntent.ENVIRONMENT: {MailPermission.ENVIRONMENT},
    MailIntent.NIGHT_SUMMARY: {MailPermission.NIGHT},
    MailIntent.SENSOR_HEALTH: {MailPermission.TECHNICAL_HEALTH},
    MailIntent.HELP: set(),
    MailIntent.UNKNOWN: set(),
}

OWNER_PERMISSIONS = {permission for permission in MailPermission}
DEFAULT_CONTACT_PERMISSIONS = {
    MailPermission.STATUS,
    MailPermission.ACTIVITY,
    MailPermission.ROOM,
    MailPermission.ENVIRONMENT,
    MailPermission.NIGHT,
    MailPermission.TECHNICAL_HEALTH,
}


@dataclass(frozen=True)
class MailAssistantConfig:
    enabled: bool = False
    poll_interval_seconds: int = 60
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    mail_from: str = "Sentero <noreply@sentero.de>"
    fresh_seconds: int = 120
    recent_seconds: int = 900
    stale_seconds: int = 1800
    hourly_limit: int = 20
    daily_limit: int = 50


@dataclass(frozen=True)
class InboundMail:
    uid: str
    message_id: str
    sender_email: str
    recipient_addresses: list[str]
    subject: str
    body: str
    received_at: str
    in_reply_to: str | None = None
    references: str | None = None
    x_sentero_generated: str | None = None
    auto_submitted: str | None = None
    has_attachments: bool = False


@dataclass(frozen=True)
class IntentResult:
    intent: MailIntent
    confidence: float


@dataclass(frozen=True)
class AuthorizedContact:
    id: int
    name: str
    email: str
    permissions: set[MailPermission]
    primary_contact: bool = False


@dataclass
class QueryResult:
    intent: MailIntent
    status: str
    facts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    permission_denied: bool = False
    data_available: bool = True


@dataclass(frozen=True)
class MailThreadContext:
    notification_log_id: int
    contact_id: int | None
    channel: str
    severity: str
    status: str
    message_title: str | None
    created_at: str
    outgoing_message_id: str
