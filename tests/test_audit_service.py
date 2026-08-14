from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.services.audit_service import AuditService, record_audit
from backend.services.consent_service import ConsentService
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.export_service import ExportService
from backend.services.service import SenteroService


class DummyHomeAssistant:
    def configured(self) -> bool:
        return False


class AuditServiceTests(unittest.TestCase):
    def test_transparency_includes_consent_export_and_no_token_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping, "relative")
            classes = ["personal_behavior", "health_adjacent", "emergency"]

            ConsentService(mapping).grant({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": classes})
            service = ExportService(mapping, sentero=SenteroService(mapping))
            created = service.create_token({"contact_id": contact_id, "purpose": "aal_partner_export", "data_classes": classes})
            service.export(created["token"], "event-summary")

            result = AuditService(mapping).transparency()
            text = json.dumps(result)

            self.assertGreaterEqual(result["summary"]["consents"], 1)
            self.assertGreaterEqual(result["summary"]["exports"], 1)
            self.assertGreaterEqual(result["summary"]["security"], 1)
            self.assertNotIn(created["token"], text)
            self.assertNotIn("token_hash", text)

    def test_cleanup_removes_old_audit_rows_and_records_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            record_audit(mapping, event_type="consent_granted", category="consent", status="active", summary="Alt")
            with mapping.connect() as con:
                con.execute("update aal_audit_log set created_at = '2020-01-01T00:00:00+00:00'")
                con.commit()

            result = AuditService(mapping).cleanup(days=30)

            self.assertEqual(result["deleted"]["aal_audit_log"], 1)
            transparency = AuditService(mapping).transparency()
            self.assertTrue(any(item["event_type"] == "retention_cleanup" for item in transparency["items"]))


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
