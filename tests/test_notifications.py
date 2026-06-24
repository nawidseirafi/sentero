from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from backend.services.device_mapping_service import DeviceMappingService, now
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


if __name__ == "__main__":
    unittest.main()
