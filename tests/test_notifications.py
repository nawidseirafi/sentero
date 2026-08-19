from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import requests

from backend.services.audit_service import ensure_audit_schema
from backend.services.audit_service import AuditService
from backend.services.device_mapping_service import DeviceMappingService, ensure_schema, now
from backend.services.aal_roles import can_access_data_classes
from backend.services.consent_service import ConsentService
from backend.services.notification_service import NotificationService, mail_assistant_reply_to, sentero_mail_from
from backend.services.setup_service import SenteroSetupService


class DummyHomeAssistant:
    def configured(self) -> bool:
        return False


class RecordingProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any]:
        self.sent.append({"contact": contact, "title": title, "text": text, "config": config})
        return {"message_id": f"<sentero-recording-{len(self.sent)}@sentero.local>"}


class MemoryMapping:
    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row

    @contextmanager
    def connect(self):
        yield self.con

    def close(self) -> None:
        self.con.close()


class FakeSmtp:
    sent_messages: list[Any] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def starttls(self) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, message: Any, from_addr: str, to_addrs: list[str]) -> None:
        self.sent_messages.append(message)


class FakeJsonResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self) -> dict[str, Any]:
        return self.payload


class NotificationSystemWarningTests(unittest.TestCase):
    def test_notification_log_schema_definitions_have_same_columns(self) -> None:
        device_mapping = MemoryMapping()
        audit_mapping = MemoryMapping()

        ensure_schema(device_mapping.con)
        ensure_audit_schema(audit_mapping)

        device_columns = notification_log_columns(device_mapping.con)
        audit_columns = notification_log_columns(audit_mapping.con)
        self.assertEqual(device_columns, audit_columns)
        self.assertIn("outgoing_message_id", device_columns)
        device_mapping.close()
        audit_mapping.close()

    def test_email_message_id_is_generated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping)
            with mapping.connect() as con:
                con.execute(
                    "update notification_channel_settings set enabled = 1, config_json = ? where channel = 'email'",
                    (
                        json.dumps(
                            {
                                "smtp_host": "smtp.example.test",
                                "smtp_user": "status@example.test",
                                "smtp_password": "secret",
                                "mail_from": "Sentero <status@example.test>",
                            }
                        ),
                    ),
                )
                con.commit()

            FakeSmtp.sent_messages = []
            service = NotificationService(mapping)
            with patch("backend.services.notification_service.smtplib.SMTP", FakeSmtp):
                result = service.test("email")

            self.assertTrue(result["ok"])
            message = FakeSmtp.sent_messages[-1]
            message_id = str(message["Message-ID"])
            self.assertRegex(message_id, r"^<sentero-[0-9a-f-]+@example\.test>$")
            self.assertEqual(message["X-Sentero-Generated"], "true")
            self.assertEqual(message["Auto-Submitted"], "auto-generated")
            with mapping.connect() as con:
                row = con.execute(
                    "select outgoing_message_id from notification_logs where contact_id = ? and channel = 'email' and status = 'sent'",
                    (contact_id,),
                ).fetchone()
            self.assertEqual(row["outgoing_message_id"], message_id)

    def test_email_from_uses_sentero_mailbox_when_display_name_only_is_configured(self) -> None:
        self.assertEqual(
            sentero_mail_from({"mail_from": "Sentero", "smtp_user": "nawid@seirafi.de"}),
            "Sentero <nawid@seirafi.de>",
        )

    def test_transparency_includes_mail_queries_and_cleanup_retains_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping)
            with mapping.connect() as con:
                con.execute(
                    """insert into sentero_mail_queries
                       (received_at, message_id, contact_id, sender_email, intent, confidence, question_hash,
                        response_status, response_sent_at, error_code, processing_ms, created_at)
                       values ('2026-08-18T10:00:00+00:00', '<mail-query@example.test>', ?, 'nawid@example.test',
                               'STATUS_SUMMARY', 0.9, 'hash', 'failed', null, 'ValueError', 12, '2026-08-18T10:00:00+00:00')""",
                    (contact_id,),
                )
                con.execute(
                    """insert into sentero_mail_queries
                       (received_at, message_id, contact_id, sender_email, intent, confidence, question_hash,
                        response_status, response_sent_at, error_code, processing_ms, created_at)
                       values ('2020-01-01T10:00:00+00:00', '<old-mail-query@example.test>', ?, 'nawid@example.test',
                               'STATUS_SUMMARY', 0.9, 'hash', 'sent', '2020-01-01T10:00:01+00:00', null, 12, '2020-01-01T10:00:00+00:00')""",
                    (contact_id,),
                )
                con.commit()

            service = AuditService(mapping)
            transparency = service.transparency()

            self.assertEqual(transparency["summary"]["mail_queries"], 2)
            self.assertTrue(any(item["id"].startswith("mail-query-") for item in transparency["items"]))
            cleanup = service.cleanup(days=30)
            self.assertEqual(cleanup["deleted"]["sentero_mail_queries"], 1)

    def test_aal_roles_do_not_allow_behavior_raw_data_for_external_actors(self) -> None:
        self.assertTrue(can_access_data_classes("care_service", ["personal_behavior"], aggregation_level="summary"))
        self.assertFalse(can_access_data_classes("care_service", ["personal_behavior"], aggregation_level="raw"))
        self.assertFalse(can_access_data_classes("housing_provider", ["personal_behavior"], aggregation_level="summary"))

    def test_mail_assistant_reply_to_is_only_added_for_enabled_contacts(self) -> None:
        config = {
            "smtp_host": "smtp.example.test",
            "smtp_user": "status@example.test",
            "smtp_password": "secret",
            "imap_host": "imap.example.test",
            "imap_user": "status@example.test",
            "imap_password": "secret",
            "mail_from": "Sentero <noreply@example.test>",
        }
        self.assertEqual(
            mail_assistant_reply_to(
                {"email_queries_enabled": 1},
                config,
            ),
            "status@example.test",
        )
        self.assertIsNone(
            mail_assistant_reply_to(
                {"email_queries_enabled": 0},
                config,
            )
        )

    def test_masked_email_channel_save_keeps_existing_passwords(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            service = NotificationService(mapping)
            service.save_channel(
                "email",
                False,
                {
                    "smtp_host": "smtp.example.test",
                    "smtp_user": "status@example.test",
                    "smtp_password": "smtp-secret",
                    "imap_host": "imap.example.test",
                    "imap_user": "status@example.test",
                    "imap_password": "imap-secret",
                },
            )

            public_email = next(item for item in service.channels()["channels"] if item["channel"] == "email")
            self.assertNotEqual(public_email["config"]["smtp_password"], "smtp-secret")
            self.assertNotEqual(public_email["config"]["imap_password"], "imap-secret")
            self.assertTrue(public_email["config"]["smtp_password"].startswith("••••"))
            self.assertTrue(public_email["config"]["imap_password"].startswith("••••"))

            masked_config = dict(public_email["config"])
            masked_config["test_recipient"] = "test@example.test"
            service.save_channel("email", False, masked_config)

            with mapping.connect() as con:
                row = con.execute("select config_json from notification_channel_settings where channel = 'email'").fetchone()
            stored = json.loads(row["config_json"])
            self.assertEqual(stored["smtp_password"], "smtp-secret")
            self.assertEqual(stored["imap_password"], "imap-secret")
            self.assertEqual(stored["test_recipient"], "test@example.test")

    def test_telegram_channel_can_be_saved_tested_and_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            service = NotificationService(mapping)
            service.save_channel("telegram", False, {"bot_token": "telegram-secret", "default_chat_id": "12345"})

            with patch("backend.services.notification_service.requests.post") as post:
                post.return_value = FakeJsonResponse({"ok": True, "result": {"message_id": 42}})
                result = service.test("telegram")

            self.assertTrue(result["ok"])
            self.assertEqual(post.call_args.kwargs["json"]["chat_id"], "12345")
            channel = next(item for item in service.channels()["channels"] if item["channel"] == "telegram")
            self.assertTrue(channel["enabled"])
            self.assertTrue(channel["configured"])
            with mapping.connect() as con:
                row = con.execute(
                    "select outgoing_message_id from notification_logs where channel = 'telegram' and status = 'sent'"
                ).fetchone()
            self.assertEqual(row["outgoing_message_id"], "telegram:12345:42")

    def test_telegram_channel_can_be_enabled_with_bot_token_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            service = NotificationService(mapping)
            service.save_channel("telegram", False, {"bot_token": "telegram-secret"})

            with patch("backend.services.notification_service.requests.get") as get:
                get.return_value = FakeJsonResponse({"ok": True, "result": {"id": 99, "username": "sentero_test_bot"}})
                result = service.test("telegram")

            self.assertTrue(result["ok"])
            self.assertIn("Einladungslinks", result["message"])
            channel = next(item for item in service.channels()["channels"] if item["channel"] == "telegram")
            self.assertTrue(channel["enabled"])
            self.assertTrue(channel["configured"])

    def test_contact_update_preserves_query_settings_when_not_in_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            setup = SenteroSetupService(mapping)
            setup.contact({"name": "Nawid", "email": "nawid@example.test", "preferred_channels": ["email"]})
            with mapping.connect() as con:
                contact_id = int(con.execute("select id from trusted_contacts").fetchone()["id"])
                con.execute(
                    "update trusted_contacts set email_queries_enabled = 1, email_permissions = ? where id = ?",
                    (json.dumps(["STATUS", "ACTIVITY"]), contact_id),
                )
                con.commit()

            setup.update_contact(contact_id, {"name": "Nawid", "email": "nawid@example.test", "preferred_channels": ["email"]})

            with mapping.connect() as con:
                row = con.execute("select email_queries_enabled, email_permissions from trusted_contacts where id = ?", (contact_id,)).fetchone()
            self.assertEqual(row["email_queries_enabled"], 1)
            self.assertEqual(json.loads(row["email_permissions"]), ["STATUS", "ACTIVITY"])

    def test_whatsapp_channel_can_be_saved_tested_and_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            service = NotificationService(mapping)
            service.save_channel(
                "whatsapp",
                False,
                {
                    "access_token": "wa-secret",
                    "phone_number_id": "999",
                    "business_account_id": "888",
                    "test_recipient": "491701234567",
                },
            )

            with patch("backend.services.notification_service.requests.post") as post:
                post.return_value = FakeJsonResponse({"messages": [{"id": "wamid.test"}]})
                result = service.test("whatsapp")

            self.assertTrue(result["ok"])
            self.assertIn("/v23.0/999/messages", post.call_args.args[0])
            self.assertEqual(post.call_args.kwargs["json"]["to"], "491701234567")
            channel = next(item for item in service.channels()["channels"] if item["channel"] == "whatsapp")
            self.assertTrue(channel["enabled"])
            self.assertTrue(channel["configured"])
            with mapping.connect() as con:
                row = con.execute(
                    "select outgoing_message_id from notification_logs where channel = 'whatsapp' and status = 'sent'"
                ).fetchone()
            self.assertEqual(row["outgoing_message_id"], "wamid.test")

    def test_telegram_http_errors_include_provider_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            service = NotificationService(mapping)
            service.save_channel("telegram", False, {"bot_token": "telegram-secret", "default_chat_id": "Sentero_bot"})

            with patch("backend.services.notification_service.requests.post") as post:
                post.return_value = FakeJsonResponse({"ok": False, "description": "Bad Request: chat not found"}, status_code=400)
                result = service.test("telegram")

            self.assertFalse(result["ok"])
            self.assertIn("chat not found", result["message"])

    def test_system_warnings_are_deduplicated_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            timestamp = now()
            with mapping.connect() as con:
                con.execute(
                    """insert into trusted_contacts
                       (name, relationship, email, active, created_at, updated_at, preferred_channels, notification_enabled, primary_contact)
                       values (?, ?, ?, 1, ?, ?, ?, 1, 1)""",
                    ("Nawid", "owner", "nawid@example.test", timestamp, timestamp, json.dumps(["email"])),
                )
                con.execute(
                    "update notification_channel_settings set enabled = 1, config_json = '{}' where channel = 'email'"
                )
                con.commit()

            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider
            sensors = [
                {
                    "role": "living_presence",
                    "label": "Wohnzimmer Sensor",
                    "room": "Wohnzimmer",
                    "configured": True,
                    "battery_level": 29,
                    "reachable": True,
                },
                {
                    "role": "main_door",
                    "label": "Haustuer Sensor",
                    "room": "Eingang",
                    "configured": True,
                    "battery_level": 80,
                    "reachable": False,
                },
            ]

            first = service.notify_system_warnings(sensors=sensors)
            self.assertEqual(first["sent"], 2)
            self.assertEqual(len(provider.sent), 2)

            second = service.notify_system_warnings(sensors=sensors)
            self.assertEqual(second["sent"], 0)
            self.assertEqual(len(provider.sent), 2)

            recovered = service.notify_system_warnings(sensors=[{**sensor, "battery_level": 80, "reachable": True} for sensor in sensors])
            self.assertEqual(recovered["warnings"], [])

            with mapping.connect() as con:
                resolved = con.execute(
                    "select count(*) as count from system_warning_state where status = 'resolved'"
                ).fetchone()["count"]
            self.assertEqual(resolved, 2)

    def test_behavior_notifications_require_active_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider
            assessment = {
                "status": "red",
                "summary": "Keine Aktivität erkannt.",
                "recommendation": "Bitte nachfragen.",
                "findings": ["Keine Bewegung seit dem Morgen."],
            }

            service.notify_assessment(assessment, [contact(mapping, contact_id)])
            self.assertEqual(provider.sent, [])

            consents = ConsentService(mapping).grant({"contact_id": contact_id})
            service.notify_assessment(assessment, [contact(mapping, contact_id)])
            self.assertEqual(len(provider.sent), 1)

            ConsentService(mapping).revoke(consents["consents"][0]["id"])
            service.notify_assessment(assessment, [contact(mapping, contact_id)])
            self.assertEqual(len(provider.sent), 1)

            with mapping.connect() as con:
                skipped = con.execute(
                    "select count(*) as count from notification_logs where status = 'skipped_no_consent'"
                ).fetchone()["count"]
            self.assertEqual(skipped, 2)

            logs = service.logs()["logs"]
            self.assertTrue(all("data_class" in log and "aggregation_level" in log for log in logs))

    def test_daily_summary_is_sent_once_after_configured_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping)
            ConsentService(mapping).grant({"contact_id": contact_id})
            with mapping.connect() as con:
                con.execute("update notification_preferences set daily_summary = 1 where id = 1")
                con.commit()

            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            early = service.send_daily_summary_if_due(datetime(2026, 8, 18, 17, 59, tzinfo=timezone.utc))
            first = service.send_daily_summary_if_due(datetime(2026, 8, 18, 18, 1, tzinfo=timezone.utc))
            second = service.send_daily_summary_if_due(datetime(2026, 8, 18, 18, 2, tzinfo=timezone.utc))

            self.assertEqual(early["skipped"], "not_due")
            self.assertEqual(first["sent"], 1)
            self.assertEqual(second["skipped"], "already_sent")
            self.assertEqual(len(provider.sent), 1)
            self.assertEqual(provider.sent[0]["title"], "Sentero Tageszusammenfassung")

    def test_daily_summary_disabled_does_not_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            result = service.send_daily_summary_if_due(datetime(2026, 8, 18, 18, 1, tzinfo=timezone.utc))

            self.assertEqual(result["skipped"], "disabled")
            self.assertEqual(provider.sent, [])

    def test_expired_consent_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping)
            ConsentService(mapping).grant({"contact_id": contact_id, "valid_until": "2020-01-01T00:00:00+00:00"})

            allowed = ConsentService(mapping).has_active_consent(
                contact_id,
                "behavior_notification",
                ["personal_behavior", "health_adjacent", "emergency"],
            )

            self.assertFalse(allowed)

    def test_housing_provider_cannot_receive_behavior_notification(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping, actor_role="housing_provider")
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            with self.assertRaises(ValueError):
                ConsentService(mapping).grant({"contact_id": contact_id, "recipient_type": "housing_provider"})

            service.notify_assessment(
                {"status": "red", "summary": "Keine Aktivität erkannt.", "recommendation": "Bitte nachfragen."},
                [contact(mapping, contact_id)],
            )

            self.assertEqual(provider.sent, [])
            with mapping.connect() as con:
                skipped = con.execute(
                    "select count(*) as count from notification_logs where status = 'skipped_role_denied'"
                ).fetchone()["count"]
            self.assertEqual(skipped, 1)


def insert_contact(mapping: DeviceMappingService, actor_role: str = "relative") -> int:
    timestamp = now()
    with mapping.connect() as con:
        cur = con.execute(
            """insert into trusted_contacts
               (name, relationship, email, active, created_at, updated_at, preferred_channels, notification_enabled, primary_contact, actor_role)
               values (?, ?, ?, 1, ?, ?, ?, 1, 1, ?)""",
            ("Nawid", "owner", "nawid@example.test", timestamp, timestamp, json.dumps(["email"]), actor_role),
        )
        con.execute("update notification_channel_settings set enabled = 1, config_json = '{}' where channel = 'email'")
        con.commit()
        return int(cur.lastrowid)


def contact(mapping: DeviceMappingService, contact_id: int) -> dict[str, Any]:
    with mapping.connect() as con:
        row = con.execute("select * from trusted_contacts where id = ?", (contact_id,)).fetchone()
    return dict(row)


def notification_log_columns(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("pragma table_info(notification_logs)").fetchall()
    return [str(row["name"]) for row in rows]


if __name__ == "__main__":
    unittest.main()
