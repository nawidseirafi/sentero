from __future__ import annotations

from tests.fakes import NoNetworkSensorSource

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from backend.behavior_agent import SenteroBehaviorAgent
from backend.sensors.service import SenteroSensorService
from backend.services.consent_service import ConsentService
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.export_service import ExportService
from backend.services.service import SenteroService




class FakeMapping:
    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": "sensor.stromzaehler_energy",
                "source": "mqtt",
                "friendly_name": "Stromzaehler Energie",
                "device_class": "energy",
                "state": "1234.5",
                "last_changed": now(),
            }
        ]

    def home_status(self) -> dict[str, bool]:
        return {"connected": True, "sensor_ready": True, "system_ready": True}


class ExportServiceTests(unittest.TestCase):
    def test_event_summary_export_is_aggregated_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping, actor_role="relative")
            classes = ["personal_behavior", "health_adjacent", "emergency"]
            ConsentService(mapping).grant({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": classes})
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            SenteroBehaviorAgent(mapping)._record_snapshot([
                {
                    "role": "living_room_presence",
                    "room": "living_room",
                    "entity_id": "binary_sensor.wohnzimmer_bewegung",
                    "state": "on",
                    "device_class": "motion",
                    "last_changed": timestamp,
                }
            ])
            service = ExportService(mapping, sentero=SenteroService(mapping), sensors=SenteroSensorService(FakeMapping()))
            created = service.create_token({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": classes})

            exported = service.export(created["token"], "event-summary")

            self.assertFalse(exported["meta"]["raw_data_included"])
            self.assertEqual(exported["meta"]["aggregation_level"], "summary")
            self.assertEqual(exported["data"]["event_count"], 1)
            self.assertNotIn("events", exported["data"])
            with mapping.connect() as con:
                audit_count = con.execute("select count(*) as count from aal_export_audit where status = 'sent'").fetchone()["count"]
            self.assertEqual(audit_count, 1)

    def test_revoked_and_expired_tokens_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping, actor_role="relative")
            classes = ["personal_behavior", "health_adjacent", "emergency"]
            ConsentService(mapping).grant({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": classes})
            service = ExportService(mapping, sentero=SenteroService(mapping), sensors=SenteroSensorService(FakeMapping()))
            active = service.create_token({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": classes})
            expired = service.create_token({
                "contact_id": contact_id,
                "purpose": "aal_partner_export",
                "data_classes": classes,
                "expires_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds"),
            })

            service.revoke_token(active["record"]["id"])

            with self.assertRaises(PermissionError):
                service.export(active["token"], "event-summary")
            with self.assertRaises(PermissionError):
                service.export(expired["token"], "event-summary")

    def test_system_status_export_uses_technical_class_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping, actor_role="housing_provider")
            ConsentService(mapping).grant({"contact_id": contact_id, "purpose": "aal_partner_export", "recipient_type": "housing_provider", "data_classes": ["technical"]})
            service = ExportService(mapping, sentero=SenteroService(mapping), sensors=SenteroSensorService(FakeMapping()))
            created = service.create_token({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": ["technical"]})

            exported = service.export(created["token"], "system-status")

            self.assertEqual(exported["meta"]["data_classes"], ["technical"])
            self.assertIn("sensor_count", exported["data"])


def insert_contact(mapping: DeviceMappingService, actor_role: str) -> int:
    timestamp = now()
    with mapping.connect() as con:
        cur = con.execute(
            """insert into trusted_contacts
               (name, relationship, email, active, created_at, updated_at, preferred_channels, notification_enabled, primary_contact, actor_role)
               values (?, ?, ?, 1, ?, ?, ?, 1, 1, ?)""",
            ("Nawid", "owner", "nawid@example.test", timestamp, timestamp, json.dumps(["email"]), actor_role),
        )
        con.commit()
        return int(cur.lastrowid)


if __name__ == "__main__":
    unittest.main()
