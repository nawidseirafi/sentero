from __future__ import annotations

from tests.fakes import NoNetworkSensorSource

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.agents.sentero.mail.conversation_service import ConversationService
from backend.agents.sentero.telegram.service import SenteroTelegramAssistant, TelegramAssistantConfig
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.notification_service import NotificationService
from backend.services.service import SenteroService




class RecordingTelegramProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, contact: dict[str, Any], title: str, text: str, config: dict[str, Any]) -> dict[str, Any]:
        self.sent.append({"contact": contact, "title": title, "text": text, "config": config})
        return {"message_id": f"telegram:{contact['telegram_chat_id']}:{len(self.sent)}"}


class FakeLLM:
    provider = "test"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[dict[str, Any]] = []

    def generate(self, prompt: str, **kwargs: Any) -> Any:
        self.prompts.append({"prompt": prompt, "kwargs": kwargs})
        return type("LLMResponse", (), {"text": self.responses.pop(0)})()


class TelegramAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict("os.environ", {"SENTERO_LLM_PROVIDER": "rule_based"})
        self.env_patch.start()

    def tearDown(self) -> None:
        self.env_patch.stop()

    def test_authorized_telegram_contact_receives_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping, queries_enabled=True)
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

    def test_telegram_status_question_accepts_colloquial_contraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping, queries_enabled=True)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
            )

            result = assistant.process_update(update("Mama geht's gut?"))

            self.assertEqual(result["status"], "sent")
            self.assertEqual(result["intent"], "STATUS_SUMMARY")
            self.assertNotIn("nicht sicher einordnen", provider.sent[-1]["text"])

    def test_telegram_status_question_accepts_english_short_chat_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping, queries_enabled=True)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
            )

            result = assistant.process_update(update("Is mom ok?"))

            self.assertEqual(result["status"], "sent")
            self.assertEqual(result["intent"], "STATUS_SUMMARY")
            self.assertIn("not enough fresh sensor data", provider.sent[-1]["text"])
            self.assertNotIn("nicht sicher einordnen", provider.sent[-1]["text"])

    def test_telegram_today_summary_accepts_english_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping, queries_enabled=True)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
            )

            result = assistant.process_update(update("What happened today?"))

            self.assertEqual(result["status"], "sent")
            self.assertEqual(result["intent"], "TODAY_SUMMARY")

    def test_telegram_uses_llm_as_primary_router_for_unlisted_languages(self) -> None:
        llm = FakeLLM(
            [
                '{"intent":"STATUS_SUMMARY","confidence":0.96,"is_action_request":false,"slots":{"language":"ja"}}',
                "現時点では、信頼できる回答に十分な新しいセンサーデータがありません。",
            ]
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping, queries_enabled=True)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
                conversation=ConversationService(llm),
            )

            result = assistant.process_update(update("母は大丈夫ですか？"))

            self.assertEqual(result["status"], "sent")
            self.assertEqual(result["intent"], "STATUS_SUMMARY")
            self.assertIn("十分な新しいセンサーデータ", provider.sent[-1]["text"])
            self.assertEqual(len(llm.prompts), 2)

    def test_start_invite_links_contact_to_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            contact_id = insert_contact(mapping, chat_id="", queries_enabled=True)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
            )
            with mapping.connect() as con:
                code = con.execute("select telegram_invite_code from trusted_contacts where id = ?", (contact_id,)).fetchone()["telegram_invite_code"]

            result = assistant.process_update(update(f"/start {code}"))

            self.assertEqual(result["status"], "linked")
            with mapping.connect() as con:
                row = con.execute("select telegram_chat_id, preferred_channels from trusted_contacts where id = ?", (contact_id,)).fetchone()
            self.assertEqual(row["telegram_chat_id"], "6516768203")
            self.assertIn("telegram", json.loads(row["preferred_channels"]))
            self.assertIn("Telegram ist jetzt mit Sentero verbunden", provider.sent[-1]["text"])

    def test_start_invite_moves_chat_id_from_other_contact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            first_id = insert_contact(mapping, chat_id="6516768203", invite_code="firstinvite", queries_enabled=True)
            second_id = insert_contact(mapping, name="Steve", email="steve@example.test", chat_id="", invite_code="secondinvite", queries_enabled=True)
            notification = NotificationService(mapping)
            provider = RecordingTelegramProvider()
            notification.providers["telegram"] = provider
            assistant = SenteroTelegramAssistant(
                mapping,
                SenteroService(mapping),
                notification,
                config=TelegramAssistantConfig(enabled=True, bot_token="secret"),
            )

            result = assistant.process_update(update("/start secondinvite"))

            self.assertEqual(result["status"], "linked")
            with mapping.connect() as con:
                first = con.execute("select telegram_chat_id from trusted_contacts where id = ?", (first_id,)).fetchone()
                second = con.execute("select telegram_chat_id from trusted_contacts where id = ?", (second_id,)).fetchone()
            self.assertIsNone(first["telegram_chat_id"])
            self.assertEqual(second["telegram_chat_id"], "6516768203")

    def test_telegram_contact_needs_queries_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            insert_contact(mapping, queries_enabled=False)
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

            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["error"], "queries_disabled")


def insert_contact(
    mapping: DeviceMappingService,
    name: str = "Nawid",
    email: str = "nawid@example.test",
    chat_id: str = "6516768203",
    invite_code: str = "invitecode123",
    queries_enabled: bool = False,
) -> int:
    timestamp = now()
    with mapping.connect() as con:
        cur = con.execute(
            """insert into trusted_contacts
               (name, relationship, email, active, created_at, updated_at, preferred_channels,
                notification_enabled, primary_contact, actor_role, telegram_chat_id, telegram_invite_code,
                email_queries_enabled, email_permissions)
               values (?, ?, ?, 1, ?, ?, ?, 1, 1, 'relative', ?, ?, ?, ?)""",
            (
                name,
                "owner",
                email,
                timestamp,
                timestamp,
                json.dumps(["email", "telegram"]),
                chat_id,
                invite_code,
                int(queries_enabled),
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
