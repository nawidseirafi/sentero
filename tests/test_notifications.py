from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.aal_roles import can_access_data_classes
from backend.services.consent_service import ConsentService
from backend.services.notification_service import NotificationService


class DummyHomeAssistant:
    def configured(self) -> bool:
        return False


class RecordingProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> None:
        self.sent.append({"contact": contact, "title": title, "text": text, "config": config})


class NotificationSystemWarningTests(unittest.TestCase):
    def test_aal_roles_do_not_allow_behavior_raw_data_for_external_actors(self) -> None:
        self.assertTrue(can_access_data_classes("care_service", ["personal_behavior"], aggregation_level="summary"))
        self.assertFalse(can_access_data_classes("care_service", ["personal_behavior"], aggregation_level="raw"))
        self.assertFalse(can_access_data_classes("housing_provider", ["personal_behavior"], aggregation_level="summary"))

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


if __name__ == "__main__":
    unittest.main()
