from __future__ import annotations

from tests.fakes import NoNetworkSensorSource

import json
import sqlite3
import tempfile
import threading
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
from backend.behavior_agent import SenteroBehaviorAgent
from backend.services.notification_service import NotificationService, mail_assistant_reply_to, sentero_mail_from
from backend.services.network.models import NetworkStatusCode
from backend.services.setup_service import SenteroSetupService
from backend.agents.sentero.mail.store import MailAssistantStore




class RecordingProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.sent.append({"contact": contact, "title": title, "text": text, "config": config})
            return {"message_id": f"<sentero-recording-{len(self.sent)}@sentero.local>"}


class RecordingMessaging:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def create_message(self, **payload: Any) -> dict[str, Any]:
        self.messages.append(payload)
        return payload


class FakeRolesMapping:
    def __init__(self) -> None:
        self.has_roles = True

    def roles(self, dev: bool = False, include_state: bool = False) -> list[dict[str, Any]]:
        return [{"role": "living_presence"}] if self.has_roles else []


class FakeAssessmentNotifications:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.resolved = 0

    def notify_assessment(self, assessment: dict[str, Any], contacts: list[dict[str, Any]]) -> dict[str, Any]:
        return self.results.pop(0)

    def resolve_behavior_notification(self) -> None:
        self.resolved += 1


class MemoryMapping:
    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row

    @contextmanager
    def connect(self):
        yield self.con

    def close(self) -> None:
        self.con.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


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


class OfflineConnectivity:
    def check(self, connection_type: Any) -> Any:
        return type("ConnectivityResult", (), {"status": NetworkStatusCode.OFFLINE})()


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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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

    def test_email_connection_refused_reports_smtp_host_and_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            service = NotificationService(mapping)
            service.save_channel(
                "email",
                False,
                {
                    "smtp_host": "smtp.example.test",
                    "smtp_port": "587",
                    "smtp_user": "status@example.test",
                    "smtp_login": "status@example.test",
                    "smtp_password": "secret",
                    "test_recipient": "test@example.test",
                },
            )

            with patch("backend.services.notification_service.smtplib.SMTP", side_effect=ConnectionRefusedError()):
                result = service.test("email")

            self.assertFalse(result["ok"])
            self.assertIn("smtp.example.test:587", result["message"])
            self.assertIn("ConnectionRefusedError", result["message"])

    def test_email_from_uses_sentero_mailbox_when_display_name_only_is_configured(self) -> None:
        self.assertEqual(
            sentero_mail_from({"mail_from": "Sentero", "smtp_user": "nawid@seirafi.de"}),
            "Sentero <nawid@seirafi.de>",
        )

    def test_transparency_includes_mail_queries_and_cleanup_retains_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            MailAssistantStore(mapping)
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

    def test_auto_submitted_mail_is_transparency_metadata_not_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            MailAssistantStore(mapping)
            with mapping.connect() as con:
                con.execute(
                    """insert into sentero_mail_queries
                       (received_at, message_id, contact_id, sender_email, intent, confidence, question_hash,
                        response_status, response_sent_at, error_code, processing_ms, created_at)
                       values ('2026-08-21T05:58:00+00:00', '<daemon@example.test>', null, 'mailer-daemon@example.test',
                               null, null, null, 'ignored', null, 'auto_submitted', 1, '2026-08-21T05:58:00+00:00')"""
                )
                con.commit()

            transparency = AuditService(mapping).transparency()
            item = next(item for item in transparency["items"] if item["id"].startswith("mail-query-"))

            self.assertEqual(transparency["summary"]["mail_queries"], 0)
            self.assertEqual(item["category"], "metadata")
            self.assertEqual(item["event_type"], "mail_auto_ignored")
            self.assertEqual(item["purpose"], "mail_auto_ignored")
            self.assertEqual(item["data_classes"], ["metadata"])

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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            service = NotificationService(mapping)
            service.save_channel("telegram", False, {"bot_token": "telegram-secret"})

            with patch("backend.services.notification_service.requests.get") as get, patch("backend.services.notification_service.requests.post") as post:
                get.return_value = FakeJsonResponse({"ok": True, "result": {"id": 99, "username": "sentero_test_bot"}})
                post.return_value = FakeJsonResponse({"ok": True, "result": True})
                result = service.test("telegram")

            self.assertTrue(result["ok"])
            self.assertIn("Einladungslinks", result["message"])
            channel = next(item for item in service.channels()["channels"] if item["channel"] == "telegram")
            self.assertTrue(channel["enabled"])
            self.assertTrue(channel["configured"])

    def test_telegram_branding_rate_limit_does_not_fail_bot_only_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            service = NotificationService(mapping)
            service.save_channel("telegram", False, {"bot_token": "telegram-secret"})

            rate_limit = FakeJsonResponse(
                {
                    "ok": False,
                    "description": "Too Many Requests: retry after 42",
                    "parameters": {"retry_after": 42},
                },
                status_code=429,
            )
            with patch("backend.services.notification_service.requests.get") as get, patch("backend.services.notification_service.requests.post") as post:
                get.return_value = FakeJsonResponse({"ok": True, "result": {"id": 99, "username": "sentero_test_bot"}})
                post.return_value = rate_limit
                result = service.test("telegram")

            self.assertTrue(result["ok"])
            self.assertIn("limitiert", result["message"])
            self.assertIn("42", result["message"])
            channel = next(item for item in service.channels()["channels"] if item["channel"] == "telegram")
            self.assertTrue(channel["enabled"])

    def test_contact_update_preserves_query_settings_when_not_in_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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

    def test_trusted_contact_requires_email_and_keeps_email_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            setup = SenteroSetupService(mapping)

            with self.assertRaises(ValueError):
                setup.contact({"name": "Nawid", "preferred_channels": ["telegram"], "telegram_chat_id": "123"})

            setup.contact({"name": "Nawid", "email": "nawid@example.test", "preferred_channels": ["telegram"], "telegram_chat_id": "123"})

            with mapping.connect() as con:
                row = con.execute("select preferred_channels from trusted_contacts where email = ?", ("nawid@example.test",)).fetchone()

            self.assertEqual(json.loads(row["preferred_channels"]), ["email", "telegram"])

    def test_whatsapp_channel_can_be_saved_tested_and_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            service = NotificationService(mapping)
            service.save_channel("telegram", False, {"bot_token": "telegram-secret", "default_chat_id": "Sentero_bot"})

            with patch("backend.services.notification_service.requests.post") as post:
                post.return_value = FakeJsonResponse({"ok": False, "description": "Bad Request: chat not found"}, status_code=400)
                result = service.test("telegram")

            self.assertFalse(result["ok"])
            self.assertIn("chat not found", result["message"])

    def test_system_warnings_are_deduplicated_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
                active = con.execute(
                    "select count(*) as count from system_warning_state where status = 'active' and consecutive_healthy_checks = 1"
                ).fetchone()["count"]
            self.assertEqual(active, 2)

    def test_temperature_warning_is_red_and_not_learning_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            timestamp = now()
            with mapping.connect() as con:
                con.execute(
                    """insert into trusted_contacts
                       (name, relationship, email, active, created_at, updated_at, preferred_channels, notification_enabled, primary_contact)
                       values (?, ?, ?, 1, ?, ?, ?, 1, 1)""",
                    ("Nawid", "owner", "nawid@example.test", timestamp, timestamp, json.dumps(["email"])),
                )
                con.execute("update notification_channel_settings set enabled = 1, config_json = '{}' where channel = 'email'")
                con.commit()

            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            result = service.notify_system_warnings(
                sensors=[],
                environmental_sensors=[
                    {
                        "entity_id": "sensor.living_room_temperature",
                        "friendly_name": "Wohnzimmer Temperatur",
                        "room": "Wohnzimmer",
                        "device_class": "temperature",
                        "state": "15.5",
                    }
                ],
            )

            self.assertEqual(result["sent"], 1)
            self.assertEqual(result["warnings"][0]["type"], "temperature_low")
            self.assertEqual(result["warnings"][0]["severity"], "red")
            self.assertEqual(result["warnings"][0]["data_class"], "environmental")
            self.assertIn("15,5 °C", provider.sent[0]["text"])

            second = service.notify_system_warnings(
                sensors=[],
                environmental_sensors=[
                    {
                        "entity_id": "sensor.living_room_temperature",
                        "friendly_name": "Wohnzimmer Temperatur",
                        "room": "Wohnzimmer",
                        "device_class": "temperature",
                        "state": "15.5",
                    }
                ],
            )
            self.assertEqual(second["sent"], 0)

    def test_humidity_warning_is_orange(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            timestamp = now()
            with mapping.connect() as con:
                con.execute(
                    """insert into trusted_contacts
                       (name, relationship, email, active, created_at, updated_at, preferred_channels, notification_enabled, primary_contact)
                       values (?, ?, ?, 1, ?, ?, ?, 1, 1)""",
                    ("Nawid", "owner", "nawid@example.test", timestamp, timestamp, json.dumps(["email"])),
                )
                con.execute("update notification_channel_settings set enabled = 1, config_json = '{}' where channel = 'email'")
                con.commit()

            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            result = service.notify_system_warnings(
                sensors=[],
                environmental_sensors=[
                    {
                        "entity_id": "sensor.bathroom_humidity",
                        "friendly_name": "Bad Luftfeuchtigkeit",
                        "room": "Bad",
                        "device_class": "humidity",
                        "state": 75,
                    }
                ],
            )

            self.assertEqual(result["sent"], 1)
            self.assertEqual(result["warnings"][0]["type"], "humidity_high")
            self.assertEqual(result["warnings"][0]["severity"], "orange")
            self.assertEqual(result["warnings"][0]["data_class"], "environmental")

    def test_same_sensor_warning_rechecked_after_restart_sends_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            first_service = NotificationService(mapping)
            first_service.providers["email"] = provider
            sensor = sensor_warning_row(device_id="0xaaa", role="hallway_presence", label="Flur Sensor", reachable=False)

            first = first_service.notify_system_warnings(sensors=[sensor])
            restarted = NotificationService(mapping)
            restarted.providers["email"] = provider
            second = restarted.notify_system_warnings(sensors=[{**sensor, "label": "Flur Sensor neu"}])

            self.assertEqual(first["sent"], 1)
            self.assertEqual(second["sent"], 0)
            self.assertEqual(len(provider.sent), 1)
            with mapping.connect() as con:
                row = con.execute("select * from system_warning_state where warning_key = ?", ("sensor_unreachable:0xaaa",)).fetchone()
            self.assertEqual(row["status"], "active")
            self.assertIsNotNone(row["last_seen_at"])

    def test_system_recovery_requires_stable_healthy_checks_before_new_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider
            offline = sensor_warning_row(device_id="0xaaa", reachable=False)
            healthy = {**offline, "reachable": True}

            service.notify_system_warnings(sensors=[offline])
            service.notify_system_warnings(sensors=[healthy])
            service.notify_system_warnings(sensors=[offline])
            self.assertEqual(len(provider.sent), 1)

            service.notify_system_warnings(sensors=[healthy])
            service.notify_system_warnings(sensors=[healthy])
            service.notify_system_warnings(sensors=[healthy])
            with mapping.connect() as con:
                row = con.execute("select status from system_warning_state where warning_key = ?", ("sensor_unreachable:0xaaa",)).fetchone()
            self.assertEqual(row["status"], "resolved")

            service.notify_system_warnings(sensors=[offline])
            self.assertEqual(len(provider.sent), 2)

    def test_sensor_rename_keeps_same_incident_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            service.notify_system_warnings(sensors=[sensor_warning_row(device_id="0xaaa", label="Flur Sensor", reachable=False)])
            service.notify_system_warnings(sensors=[sensor_warning_row(device_id="0xaaa", label="Wohnzimmer Präsenz", role="living_presence", reachable=False)])

            self.assertEqual(len(provider.sent), 1)
            with mapping.connect() as con:
                keys = [row["warning_key"] for row in con.execute("select warning_key from system_warning_state").fetchall()]
            self.assertEqual(keys, ["sensor_unreachable:0xaaa"])

    def test_parallel_system_checks_create_at_most_one_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            sensor = sensor_warning_row(device_id="0xaaa", reachable=False)

            def run_check() -> None:
                service = NotificationService(mapping)
                service.providers["email"] = provider
                service.notify_system_warnings(sensors=[sensor])

            threads = [threading.Thread(target=run_check) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertLessEqual(len(provider.sent), 1)

    def test_offline_outbox_is_deduplicated_for_active_incident(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            service = NotificationService(mapping, connectivity=OfflineConnectivity())
            sensor = sensor_warning_row(device_id="0xaaa", reachable=False)

            service.notify_system_warnings(sensors=[sensor])
            service.notify_system_warnings(sensors=[sensor])
            service.notify_system_warnings(sensors=[sensor])

            with mapping.connect() as con:
                row = con.execute("select count(*) as count from notification_outbox where incident_key = ?", ("sensor_unreachable:0xaaa",)).fetchone()
            self.assertEqual(row["count"], 1)

    def test_humidity_high_repeated_checks_keep_one_active_incident_and_one_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider
            values = [71, 71, 71, 72, 73]
            observed_keys: list[str] = []

            for value in values:
                result = service.notify_system_warnings(
                    sensors=[],
                    environmental_sensors=[
                        {
                            "domain": "sensor",
                            "device_id": "0xaaa",
                            "entity_id": "sensor.bathroom_humidity",
                            "friendly_name": "Bad Luftfeuchtigkeit",
                            "room": "Bad",
                            "device_class": "humidity",
                            "state": value,
                        }
                    ],
                )
                observed_keys.extend(warning["key"] for warning in result["warnings"])
                with mapping.connect() as con:
                    row = con.execute("select status from system_warning_state where warning_key = ?", ("humidity_high:0xaaa",)).fetchone()
                self.assertEqual(row["status"], "active")

            self.assertEqual(len(provider.sent), 1)
            self.assertEqual(observed_keys, ["humidity_high:0xaaa"] * len(values))
            with mapping.connect() as con:
                row = con.execute(
                    """select status, last_notified_severity, resolved_at
                       from system_warning_state where warning_key = ?""",
                    ("humidity_high:0xaaa",),
                ).fetchone()
            self.assertEqual(row["status"], "active")
            self.assertEqual(row["last_notified_severity"], "orange")
            self.assertIsNone(row["resolved_at"])

    def test_different_sensors_and_warning_types_are_distinct_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            result = service.notify_system_warnings(
                sensors=[
                    sensor_warning_row(device_id="0xaaa", battery_level=20, reachable=True),
                    sensor_warning_row(device_id="0xbbb", role="bath_presence", battery_level=20, reachable=True),
                    sensor_warning_row(device_id="0xccc", battery_level=20, reachable=False),
                ]
            )

            keys = {warning["key"] for warning in result["warnings"]}
            self.assertIn("battery_low:0xaaa", keys)
            self.assertIn("battery_low:0xbbb", keys)
            self.assertIn("battery_low:0xccc", keys)
            self.assertIn("sensor_unreachable:0xccc", keys)
            self.assertEqual(len(provider.sent), 4)

    def test_environmental_warning_ignores_battery_voltage_pressure_and_calibration_entities(self) -> None:
        mapping = MemoryMapping()
        try:
            service = NotificationService(mapping)

            warnings = service._environmental_warnings([
                {"domain": "sensor", "entity_id": "sensor.kitchen_temperature_sensor_battery", "friendly_name": "Kitchen Temperature Sensor Batterie", "device_class": "battery", "state": 100},
                {"domain": "sensor", "entity_id": "sensor.kitchen_temperature_sensor_voltage", "friendly_name": "Kitchen Temperature Sensor Spannung", "device_class": "voltage", "state": 3000},
                {"domain": "sensor", "entity_id": "sensor.kitchen_temperature_sensor_pressure", "friendly_name": "Kitchen Temperature Sensor Pressure", "device_class": "pressure", "state": 1000},
                {"domain": "number", "entity_id": "number.guest_wc_presence_sensor_temperature_calibration", "friendly_name": "Guest WC Presence Sensor Temperature calibration", "state": -2},
            ])
        finally:
            mapping.close()

        self.assertEqual(warnings, [])

    def test_system_warning_sends_once_to_each_contact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            insert_contact(mapping)
            with mapping.connect() as con:
                con.execute("update trusted_contacts set email = 'second@example.test', primary_contact = 0 where id = 2")
                con.commit()

            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            result = service.notify_system_warnings(
                sensors=[],
                environmental_sensors=[
                    {
                        "domain": "sensor",
                        "entity_id": "sensor.bathroom_humidity",
                        "friendly_name": "Bad Luftfeuchtigkeit",
                        "room": "Bad",
                        "device_class": "humidity",
                        "state": 75,
                    }
                ],
            )

            self.assertEqual(result["sent"], 2)
            self.assertEqual(len(provider.sent), 2)
            self.assertEqual(provider.sent[0]["contact"]["email"], "nawid@example.test")
            self.assertEqual(provider.sent[1]["contact"]["email"], "second@example.test")

            second = service.notify_system_warnings(
                sensors=[],
                environmental_sensors=[
                    {
                        "domain": "sensor",
                        "entity_id": "sensor.bathroom_humidity",
                        "friendly_name": "Bad Luftfeuchtigkeit",
                        "room": "Bad",
                        "device_class": "humidity",
                        "state": 75,
                    }
                ],
            )

            self.assertEqual(second["sent"], 0)
            self.assertEqual(len(provider.sent), 2)

    def test_behavior_notifications_require_active_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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

    def test_behavior_warning_is_sent_once_until_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping)
            ConsentService(mapping).grant({"contact_id": contact_id})
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
            service.notify_assessment({**assessment, "summary": "Weiterhin keine Aktivität erkannt."}, [contact(mapping, contact_id)])

            self.assertEqual(len(provider.sent), 1)

            service.notify_assessment({"status": "green", "summary": "Alles unauffällig."}, [contact(mapping, contact_id)])
            service.notify_assessment(assessment, [contact(mapping, contact_id)])
            self.assertEqual(len(provider.sent), 1)

            service.notify_assessment({"status": "green", "summary": "Alles unauffällig."}, [contact(mapping, contact_id)])
            service.notify_assessment({"status": "green", "summary": "Alles unauffällig."}, [contact(mapping, contact_id)])
            service.notify_assessment({"status": "green", "summary": "Alles unauffällig."}, [contact(mapping, contact_id)])
            service.notify_assessment(assessment, [contact(mapping, contact_id)])

            self.assertEqual(len(provider.sent), 2)

    def test_behavior_orange_is_sent_once_and_red_escalates_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping)
            ConsentService(mapping).grant({"contact_id": contact_id})
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider
            orange = {"status": "orange", "summary": "Auffälligkeit erkannt.", "recommendation": "Bitte nachfragen."}
            red = {"status": "red", "summary": "Kritische Auffälligkeit erkannt.", "recommendation": "Bitte sofort nachfragen."}

            service.notify_assessment(orange, [contact(mapping, contact_id)])
            service.notify_assessment(orange, [contact(mapping, contact_id)])
            service.notify_assessment(orange, [contact(mapping, contact_id)])
            self.assertEqual(len(provider.sent), 1)

            service.notify_assessment(red, [contact(mapping, contact_id)])
            service.notify_assessment(red, [contact(mapping, contact_id)])
            self.assertEqual(len(provider.sent), 2)

    def test_behavior_red_to_orange_does_not_send_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping)
            ConsentService(mapping).grant({"contact_id": contact_id})
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            service.notify_assessment({"status": "red", "summary": "Kritisch.", "recommendation": "Bitte nachfragen."}, [contact(mapping, contact_id)])
            service.notify_assessment({"status": "orange", "summary": "Weiterhin auffällig.", "recommendation": "Bitte nachfragen."}, [contact(mapping, contact_id)])

            self.assertEqual(len(provider.sent), 1)
            with mapping.connect() as con:
                row = con.execute("select status, last_notified_severity from behavior_notification_state where state_key = 'behavior_anomaly'").fetchone()
            self.assertEqual(row["status"], "active")
            self.assertEqual(row["last_notified_severity"], "red")

    def test_behavior_agent_writes_internal_warning_only_when_notification_is_new(self) -> None:
        agent = SenteroBehaviorAgent.__new__(SenteroBehaviorAgent)
        agent.mapping = FakeRolesMapping()
        agent.messaging = RecordingMessaging()
        agent.notifications = FakeAssessmentNotifications()
        agent.notifications.results = [{"sent": 1}, {"sent": 0, "skipped": "already_active"}]
        assessment = {
            "id": 1,
            "status": "red",
            "learning_completed": True,
            "summary": "Keine Aktivität erkannt.",
            "email_subject": "Sentero Warnung",
        }
        contacts = [{"name": "Nawid", "email": "nawid@example.test"}]

        agent._notify_if_needed(assessment, contacts)
        agent._notify_if_needed({**assessment, "id": 2}, contacts)

        self.assertEqual(len(agent.messaging.messages), 1)

    def test_daily_summary_is_sent_once_after_configured_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping)
            provider = RecordingProvider()
            service = NotificationService(mapping)
            service.providers["email"] = provider

            result = service.send_daily_summary_if_due(datetime(2026, 8, 18, 18, 1, tzinfo=timezone.utc))

            self.assertEqual(result["skipped"], "disabled")
            self.assertEqual(provider.sent, [])

    def test_expired_consent_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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


def sensor_warning_row(
    *,
    device_id: str = "0xaaa",
    role: str = "hallway_presence",
    label: str = "Flur Sensor",
    battery_level: int = 80,
    reachable: bool = True,
) -> dict[str, Any]:
    return {
        "role": role,
        "label": label,
        "room": "Flur",
        "configured": True,
        "device_id": device_id,
        "primary_entity_id": f"sensor.{role}",
        "entity_id": f"sensor.{role}",
        "battery_level": battery_level,
        "reachable": reachable,
    }


def notification_log_columns(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("pragma table_info(notification_logs)").fetchall()
    return [str(row["name"]) for row in rows]


if __name__ == "__main__":
    unittest.main()
