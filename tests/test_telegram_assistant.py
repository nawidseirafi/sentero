from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from backend.agents.sentero.telegram.service import SenteroTelegramAssistant, TelegramAssistantConfig
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.notification_service import NotificationService
from backend.services.service import SenteroService


class DummyHomeAssistant:
    def configured(self) -> bool:
        return False


class RecordingTelegramProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any]:
        self.sent.append({"contact": contact, "title": title, "text": text, "config": config})
        return {"message_id": f"telegram:{contact['telegram_chat_id']}:{len(self.sent)}"}


class TelegramAssistantTests(unittest.TestCase):
    def test_authorized_telegram_contact_receives_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            contact_id = insert_contact(mapping)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
            )

            result = assistant.process_update(update("Ist alles in Ordnung?"))

            self.assertEqual(result["status"], "sent")
            self.assertEqual(provider.sent[-1]["contact"]["telegram_chat_id"], "6516768203")
            self.assertIn("nicht genügend aktuelle Sensordaten", provider.sent[-1]["text"])
            with mapping.connect() as con:
                query = con.execute("select * from sentero_telegram_queries where update_id = 1001").fetchone()
                log = con.execute(
                    "select * from notification_logs where contact_id = ? and channel = 'telegram' and status = 'telegram_assistant_response'",
                    (contact_id,),
                ).fetchone()
            self.assertIsNotNone(query)
            self.assertIsNotNone(log)


def insert_contact(mapping: DeviceMappingService) -> int:
    timestamp = now()
    with mapping.connect() as con:
        cur = con.execute(
            """insert into trusted_contacts
               (name, relationship, email, active, created_at, updated_at, preferred_channels,
                notification_enabled, primary_contact, actor_role, telegram_chat_id, email_permissions)
               values (?, ?, ?, 1, ?, ?, ?, 1, 1, 'relative', ?, ?)""",
            (
                "Nawid",
                "owner",
                "nawid@example.test",
                timestamp,
                timestamp,
                json.dumps(["email", "telegram"]),
                "6516768203",
                json.dumps(["STATUS", "ACTIVITY", "ROOM", "ENVIRONMENT", "NIGHT", "TECHNICAL_HEALTH"]),
            ),
        )
        con.commit()
        return int(cur.lastrowid)


def update(text: str) -> dict[str, Any]:
    return {
        "update_id": 1001,
        "message": {
            "message_id": 55,
            "from": {"id": 6516768203, "is_bot": False, "first_name": "Nawid"},
            "chat": {"id": 6516768203, "type": "private"},
            "date": 1787080000,
            "text": text,
        },
    }


if __name__ == "__main__":
    unittest.main()
