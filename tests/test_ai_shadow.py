from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.main import app
from backend.behavior_agent import SenteroBehaviorAgent
from backend.services.ai_shadow import AIShadowConfig, AIShadowService, SituationInterpreter, validate_assessment
from backend.services.llm.factory import LLMResponse


class MemoryMapping:
    def __init__(self, roles: list[dict[str, Any]] | None = None) -> None:
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self._roles = roles or []
        self.con.execute(
            """create table sentero_sensor_events (
                id integer primary key autoincrement,
                event_time text not null,
                role text,
                room text,
                entity_id text,
                state text,
                device_class text,
                source text not null default 'test',
                human_activity_score integer,
                human_activity_confidence real,
                human_activity_classification text,
                created_at text not null
            )"""
        )
        self.con.execute(
            """create table behavior_assessments (
                id integer primary key autoincrement,
                assessment_time text not null,
                status text not null,
                confidence real not null,
                summary text not null,
                recommendation text not null
            )"""
        )
        self.con.execute(
            """create table behavior_profile (
                user_id integer primary key,
                average_wakeup_time text,
                average_sleep_time text,
                average_active_minutes real not null default 0,
                room_usage_patterns text not null default '{}',
                normal_door_usage text not null default '{}',
                learning_completed integer not null default 0,
                learning_started_at text not null,
                learning_completed_at text
            )"""
        )
        self.con.execute(
            """create table behavior_daily_summary (
                date text primary key,
                wakeup_time text,
                first_activity text,
                last_activity text,
                active_minutes integer not null default 0,
                inactivity_periods text not null default '[]',
                room_usage text not null default '{}',
                door_events integer not null default 0,
                occupancy_score real not null default 0,
                anomaly_score integer not null default 0
            )"""
        )
        self.con.commit()

    @contextmanager
    def connect(self):
        yield self.con

    def roles(self, dev: bool = False, include_state: bool = False) -> list[dict[str, Any]]:
        return [dict(item) for item in self._roles]

    def close(self) -> None:
        self.con.close()


class FakeLLM:
    provider = "ollama"

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[dict[str, Any]] = []
        self.config = type("Config", (), {"model": "qwen-test"})()

    def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        self.prompts.append({"prompt": prompt, **kwargs})
        return LLMResponse(self.text)


class FailingLLM:
    provider = "ollama"
    config = type("Config", (), {"model": "qwen-test"})()

    def generate(self, prompt: str, **kwargs: Any) -> LLMResponse:
        raise RuntimeError("ollama unavailable")


class RecordingNotifications:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        def recorder(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
        return recorder


class FakeAuth:
    def __init__(self, user: dict[str, Any] | None) -> None:
        self.user = user

    def user_from_request(self, request: Any, required: bool = True) -> dict[str, Any] | None:
        if self.user:
            return self.user
        if required:
            raise HTTPException(status_code=401, detail="Nicht angemeldet.")
        return None


class FakeServices:
    def __init__(self, ai_shadow: AIShadowService, *, user: dict[str, Any] | None = None) -> None:
        self.auth = FakeAuth(user)
        self.sentero = type("Sentero", (), {"behavior": type("Behavior", (), {"ai_shadow": ai_shadow})()})()
        self.notification = RecordingNotifications()


class AIShadowTests(unittest.TestCase):
    def tearDown(self) -> None:
        mapping = getattr(self, "mapping", None)
        if mapping:
            mapping.close()

    def test_shower_context_is_built_validated_and_stored_without_notifications(self) -> None:
        end = datetime(2026, 9, 6, 21, 56, tzinfo=timezone.utc)
        self.mapping = MemoryMapping(roles=[{
            "role": "bathroom_presence",
            "room": "bathroom",
            "presence": True,
            "motion_state": "moving",
            "last_updated": end.isoformat(timespec="seconds"),
            "reachable": True,
            "stale": False,
        }])
        for minutes_ago, value in [(15, 54), (10, 61), (5, 70), (0, 75)]:
            self._insert_event(end - timedelta(minutes=minutes_ago), "bathroom", str(value), "humidity")
        for minutes_ago in [12, 8, 4, 1]:
            self._insert_event(end - timedelta(minutes=minutes_ago), "bathroom", "on", "motion")
        llm = FakeLLM(json.dumps({
            "situation": "showering",
            "confidence": 0.86,
            "severity": "normal",
            "reason_codes": ["bathroom_presence", "rapid_humidity_rise", "sustained_activity"],
            "summary": "Aktivität im Bad mit deutlichem Feuchtigkeitsanstieg ist mit normaler Dusch-/Badnutzung vereinbar.",
            "recommended_action": "observe",
        }))
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=llm)

        context = interpreter.build_context(
            window_start=end - timedelta(minutes=60),
            window_end=end,
            current_behavior_state={"status": "green", "summary": "Regelbasierte Bewertung unauffällig."},
        )
        stored = interpreter.interpret_and_store(context)

        self.assertTrue(context["rooms"]["bathroom"]["presence_now"])
        self.assertEqual(context["rooms"]["bathroom"]["humidity"]["now"], 75)
        self.assertEqual(context["rooms"]["bathroom"]["humidity"]["delta_15m"], 21)
        self.assertGreaterEqual(context["rooms"]["bathroom"]["activity_event_count"], 4)
        self.assertEqual(context["rooms"]["bathroom"]["presence_confidence"], "fresh")
        self.assertEqual(context["rooms"]["bathroom"]["motion_confidence"], "fresh")
        self.assertIn('"rooms"', llm.prompts[0]["prompt"])
        self.assertEqual(stored["situation"], "showering")
        self.assertEqual(stored["severity"], "normal")
        self.assertEqual(stored["recommended_action"], "observe")
        with self.mapping.connect() as con:
            self.assertEqual(con.execute("select count(*) from sentero_ai_shadow_assessments").fetchone()[0], 1)

        notifications = RecordingNotifications()
        agent = SenteroBehaviorAgent.__new__(SenteroBehaviorAgent)
        agent.ai_shadow = interpreter
        agent.notifications = notifications
        agent._run_ai_shadow_if_due({"status": "green"})
        self.assertEqual(notifications.calls, [])

    def test_ollama_unavailable_is_stored_as_graceful_unknown(self) -> None:
        self.mapping = MemoryMapping()
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FailingLLM())
        stored = interpreter.interpret_and_store(self._empty_context())

        self.assertEqual(stored["situation"], "unknown")
        self.assertEqual(stored["status"], "failed")
        self.assertEqual(stored["error_code"], "RuntimeError")

    def test_invalid_json_falls_back_to_unknown(self) -> None:
        result = validate_assessment("not json")

        self.assertEqual(result["situation"], "unknown")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["recommended_action"], "none")

    def test_hallucinated_situation_is_rejected(self) -> None:
        result = validate_assessment({
            "situation": "definite_medical_emergency",
            "confidence": 0.8,
            "severity": "warning",
            "reason_codes": [],
            "summary": "Nicht erlaubt.",
            "recommended_action": "observe",
        })

        self.assertEqual(result["situation"], "unknown")

    def test_confidence_outside_range_is_rejected(self) -> None:
        for value in (-0.1, 1.2):
            with self.subTest(value=value):
                result = validate_assessment({
                    "situation": "bathroom_use",
                    "confidence": value,
                    "severity": "normal",
                    "reason_codes": [],
                    "summary": "Badnutzung möglich.",
                    "recommended_action": "observe",
                })
                self.assertEqual(result["situation"], "unknown")

    def test_old_sensor_value_is_not_current_context(self) -> None:
        end = datetime(2026, 9, 6, 21, 56, tzinfo=timezone.utc)
        self.mapping = MemoryMapping(roles=[{
            "role": "bathroom_presence",
            "room": "bathroom",
            "presence": False,
            "last_updated": (end - timedelta(hours=3)).isoformat(timespec="seconds"),
            "stale": True,
        }])
        self._insert_event(end - timedelta(hours=2), "bathroom", "82", "humidity")
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        context = interpreter.build_context(window_start=end - timedelta(minutes=60), window_end=end)

        self.assertIsNone(context["rooms"]["bathroom"]["presence_now"])
        self.assertEqual(context["rooms"]["bathroom"]["presence_confidence"], "stale")
        self.assertIsNone(context["rooms"]["bathroom"]["humidity"]["now"])
        self.assertEqual(context["rooms"]["bathroom"]["stale_sensor_count"], 1)

    def test_insufficient_data_can_be_saved_as_unknown(self) -> None:
        self.mapping = MemoryMapping()
        llm = FakeLLM(json.dumps({
            "situation": "unknown",
            "confidence": 0.0,
            "severity": "unknown",
            "reason_codes": [],
            "summary": "Nicht genug Daten.",
            "recommended_action": "none",
        }))
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=llm)
        stored = interpreter.interpret_and_store(self._empty_context())

        self.assertEqual(stored["situation"], "unknown")
        self.assertEqual(stored["status"], "ok")

    def test_presence_motion_contact_semantics_are_unchanged(self) -> None:
        agent = SenteroBehaviorAgent.__new__(SenteroBehaviorAgent)
        role = {"role": "bathroom_presence", "room": "bathroom", "source": "zigbee2mqtt"}

        events = agent._mqtt_behavior_events(role, "zigbee2mqtt/bath", {"presence": False, "motion_state": "still"}, "2026-09-06T21:56:00+00:00")

        presence_events = [event for event in events if event["event_type"] == "presence"]
        motion_events = [event for event in events if event["event_type"] == "motion"]
        self.assertEqual(presence_events[0]["state"], "on")
        self.assertEqual(motion_events[0]["state"], "off")

    def test_situation_interpreter_does_not_depend_on_database_or_notifications(self) -> None:
        llm = FakeLLM(json.dumps({
            "situation": "bathroom_use",
            "confidence": 0.7,
            "severity": "normal",
            "reason_codes": ["bathroom_presence"],
            "summary": "Mit normaler Badnutzung vereinbar.",
            "recommended_action": "observe",
        }))
        interpreter = SituationInterpreter(config=AIShadowConfig(enabled=True), llm_client=llm)

        result = interpreter.interpret(self._empty_context())

        self.assertEqual(result["assessment"]["situation"], "bathroom_use")
        self.assertFalse(hasattr(interpreter, "mapping"))
        self.assertFalse(hasattr(interpreter, "notifications"))

    def test_behavior_profile_is_added_as_compact_personal_routine(self) -> None:
        end = datetime(2026, 9, 6, 9, 30, tzinfo=timezone.utc)
        self.mapping = MemoryMapping()
        self._insert_behavior_profile(learning_completed=True)
        self._insert_daily_summary(
            "2026-09-06",
            first_activity="06:45",
            last_activity="09:15",
            active_minutes=75,
            room_usage={"bathroom": 12, "kitchen": 25},
            door_events=1,
            anomaly_score=10,
        )
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        context = interpreter.build_context(window_start=end - timedelta(minutes=60), window_end=end)

        routine = context["personal_routine"]
        self.assertTrue(routine["routine_available"])
        self.assertTrue(routine["learning_completed"])
        self.assertEqual(routine["typical"]["average_wakeup_time"], "06:40")
        self.assertEqual(routine["typical"]["average_sleep_time"], "22:15")
        self.assertEqual(routine["typical"]["average_active_minutes"], 180.5)
        self.assertEqual(routine["typical"]["room_usage_patterns"]["kitchen"], 35)
        self.assertEqual(routine["typical"]["normal_door_usage"]["average_daily_events"], 2)
        self.assertEqual(routine["today"]["first_activity"], "06:45")
        self.assertEqual(routine["today"]["active_minutes"], 75)
        self.assertEqual(routine["today"]["anomaly_score"], 10)

    def test_incomplete_learning_profile_marks_routine_unavailable(self) -> None:
        end = datetime(2026, 9, 6, 9, 30, tzinfo=timezone.utc)
        self.mapping = MemoryMapping()
        self._insert_behavior_profile(learning_completed=False)
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        context = interpreter.build_context(window_start=end - timedelta(minutes=60), window_end=end)

        self.assertEqual(context["personal_routine"], {"routine_available": False, "learning_completed": False})

    def test_time_of_day_uses_local_box_timezone(self) -> None:
        end = datetime(2026, 9, 6, 3, 30, tzinfo=timezone.utc)
        self.mapping = MemoryMapping()
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        with patch.dict("os.environ", {"SENTERO_TIMEZONE": "America/New_York"}, clear=False):
            context = interpreter.build_context(window_start=end - timedelta(minutes=60), window_end=end)

        self.assertEqual(context["local_time"], "2026-09-05T23:30:00-04:00")
        self.assertEqual(context["time_of_day"], "night")

    def test_stale_presence_true_is_not_current_presence_evidence(self) -> None:
        end = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        self.mapping = MemoryMapping(roles=[{
            "role": "bathroom_presence",
            "room": "bathroom",
            "presence": True,
            "last_updated": (end - timedelta(hours=2)).isoformat(timespec="seconds"),
            "stale": True,
            "reachable": True,
        }])
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        context = interpreter.build_context(window_start=end - timedelta(minutes=60), window_end=end)

        self.assertIsNone(context["rooms"]["bathroom"]["presence_now"])
        self.assertEqual(context["rooms"]["bathroom"]["presence_confidence"], "stale")
        self.assertIsNone(context["known_away_signal"]["any_presence_now"])
        self.assertEqual(context["known_away_signal"]["presence_confidence"], "stale")

    def test_unreachable_active_motion_is_not_current_motion_evidence(self) -> None:
        end = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        self.mapping = MemoryMapping(roles=[{
            "role": "bathroom_presence",
            "room": "bathroom",
            "motion_state": "moving",
            "last_updated": end.isoformat(timespec="seconds"),
            "stale": False,
            "reachable": False,
        }])
        interpreter = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        context = interpreter.build_context(window_start=end - timedelta(minutes=60), window_end=end)

        self.assertIsNone(context["rooms"]["bathroom"]["motion_now"])
        self.assertEqual(context["rooms"]["bathroom"]["motion_confidence"], "stale")

    def test_export_without_data_returns_empty_limited_export(self) -> None:
        self.mapping = MemoryMapping()
        service = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        payload = service.export(period_to="2026-09-06T12:00:00+00:00")

        self.assertEqual(payload["export_version"], 1)
        self.assertEqual(payload["period"]["from"], "2026-09-05T12:00:00+00:00")
        self.assertEqual(payload["period"]["to"], "2026-09-06T12:00:00+00:00")
        self.assertEqual(payload["assessment_count"], 0)
        self.assertEqual(payload["assessments"], [])

    def test_export_multiple_assessments_context_ai_feedback_and_filtering(self) -> None:
        self.mapping = MemoryMapping()
        service = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))
        old_id = self._insert_shadow_assessment(
            created_at="2026-09-05T09:00:00+00:00",
            situation="normal_activity",
            context={"rooms": {"kitchen": {"presence_now": True}}, "email": "resident@example.test"},
        )
        kept_id = self._insert_shadow_assessment(
            created_at="2026-09-06T10:00:00+00:00",
            situation="showering",
            context={
                "rooms": {"bathroom": {"presence_now": True, "humidity": {"now": 75}}},
                "telegram_chat_id": "12345",
                "mqtt_password": "secret",
            },
            human_label="showering",
            human_correct=1,
            human_comment="Plausibel",
            reviewed_at="2026-09-07T08:00:00+00:00",
        )

        before = self._shadow_count()
        payload = service.export(period_from="2026-09-06T00:00:00+00:00", period_to="2026-09-06T23:59:00+00:00")
        after = self._shadow_count()

        self.assertEqual(before, after)
        self.assertEqual(payload["assessment_count"], 1)
        exported = payload["assessments"][0]
        self.assertNotEqual(old_id, kept_id)
        self.assertEqual(exported["created_at"], "2026-09-06T10:00:00+00:00")
        self.assertEqual(exported["context"]["rooms"]["bathroom"]["humidity"]["now"], 75)
        self.assertEqual(exported["ai"]["situation"], "showering")
        self.assertEqual(exported["ai"]["confidence"], 0.86)
        self.assertEqual(exported["ai"]["reason_codes"], ["bathroom_presence"])
        self.assertEqual(exported["feedback"]["human_label"], "showering")
        self.assertEqual(exported["feedback"]["human_correct"], 1)
        self.assertEqual(exported["technical"]["model_name"], "qwen-test")
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("resident@example.test", serialized)
        self.assertNotIn("telegram_chat_id", serialized)
        self.assertNotIn("mqtt_password", serialized)
        self.assertNotIn("secret", serialized)

    def test_export_invalid_period_is_rejected(self) -> None:
        self.mapping = MemoryMapping()
        service = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))

        with self.assertRaises(ValueError):
            service.export(period_from="not-a-date")
        with self.assertRaises(ValueError):
            service.export(period_from="2026-09-07T00:00:00+00:00", period_to="2026-09-06T00:00:00+00:00")

    def test_export_endpoint_requires_auth_and_returns_json_download(self) -> None:
        self.mapping = MemoryMapping()
        service = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))
        self._insert_shadow_assessment(created_at="2026-09-06T10:00:00+00:00", situation="bathroom_use", context={"rooms": {"bathroom": {}}})
        unauthenticated = FakeServices(service, user=None)
        authenticated = FakeServices(service, user={"id": 1, "role": "admin", "email": "admin@example.test"})

        with patch("backend.main.get_services", return_value=unauthenticated), patch("backend.api.routes.get_services", return_value=unauthenticated):
            response = TestClient(app).get("/api/sentero/admin/ai-shadow/export")
        self.assertEqual(response.status_code, 401)

        with patch("backend.main.get_services", return_value=authenticated), patch("backend.api.routes.get_services", return_value=authenticated):
            response = TestClient(app).get(
                "/api/sentero/admin/ai-shadow/export",
                params={"from": "2026-09-06T00:00:00+00:00", "to": "2026-09-06T23:59:00+00:00"},
                cookies={"sentero_session": "test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertIn("sentero-shadow-export-", response.headers["content-disposition"])
        self.assertEqual(response.json()["assessment_count"], 1)
        self.assertEqual(authenticated.notification.calls, [])

    def test_export_endpoint_rejects_invalid_period_with_400(self) -> None:
        self.mapping = MemoryMapping()
        service = AIShadowService(self.mapping, config=AIShadowConfig(enabled=True), llm_client=FakeLLM("{}"))
        services = FakeServices(service, user={"id": 1, "role": "owner", "email": "owner@example.test"})

        with patch("backend.main.get_services", return_value=services), patch("backend.api.routes.get_services", return_value=services):
            response = TestClient(app).get(
                "/api/sentero/admin/ai-shadow/export",
                params={"from": "invalid"},
                cookies={"sentero_session": "test"},
            )

        self.assertEqual(response.status_code, 400)

    def _empty_context(self) -> dict[str, Any]:
        return {
            "window_minutes": 60,
            "window_start": "2026-09-06T20:56:00+00:00",
            "window_end": "2026-09-06T21:56:00+00:00",
            "current_time": "2026-09-06T21:56:00+00:00",
            "time_of_day": "evening",
            "rooms": {},
            "door_events": [],
            "room_transitions": [],
            "known_away_signal": {"any_presence_now": False, "last_activity_minutes_ago": None, "door_events_in_window": 0},
            "sensor_freshness": {"configured_sensors": 0, "fresh_sensors": 0, "stale_sensors": 0, "max_age_minutes": None},
            "current_behavior_state": None,
        }

    def _insert_event(self, event_time: datetime, room: str, state: str, device_class: str) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """insert into sentero_sensor_events
                   (event_time, role, room, entity_id, state, device_class, source, created_at)
                   values (?, ?, ?, ?, ?, ?, 'test', ?)""",
                (
                    event_time.isoformat(timespec="seconds"),
                    f"{room}_{device_class}",
                    room,
                    f"sensor.{room}_{device_class}",
                    state,
                    device_class,
                    event_time.isoformat(timespec="seconds"),
                ),
            )
            con.commit()

    def _insert_behavior_profile(self, *, learning_completed: bool) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """insert into behavior_profile
                   (user_id, average_wakeup_time, average_sleep_time, average_active_minutes,
                    room_usage_patterns, normal_door_usage, learning_completed, learning_started_at, learning_completed_at)
                   values (1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "06:40",
                    "22:15",
                    180.5,
                    json.dumps({"bathroom": 20, "kitchen": 35}),
                    json.dumps({"average_daily_events": 2}),
                    int(learning_completed),
                    "2026-08-20T00:00:00+00:00",
                    "2026-09-03T00:00:00+00:00" if learning_completed else None,
                ),
            )
            con.commit()

    def _insert_daily_summary(
        self,
        day: str,
        *,
        first_activity: str,
        last_activity: str,
        active_minutes: int,
        room_usage: dict[str, Any],
        door_events: int,
        anomaly_score: int,
    ) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """insert into behavior_daily_summary
                   (date, wakeup_time, first_activity, last_activity, active_minutes, inactivity_periods,
                    room_usage, door_events, occupancy_score, anomaly_score)
                   values (?, ?, ?, ?, ?, '[]', ?, ?, 0, ?)""",
                (day, first_activity, first_activity, last_activity, active_minutes, json.dumps(room_usage), door_events, anomaly_score),
            )
            con.commit()

    def _insert_shadow_assessment(
        self,
        *,
        created_at: str,
        situation: str,
        context: dict[str, Any],
        human_label: str | None = None,
        human_correct: int | None = None,
        human_comment: str | None = None,
        reviewed_at: str | None = None,
    ) -> int:
        with self.mapping.connect() as con:
            cur = con.execute(
                """insert into sentero_ai_shadow_assessments
                   (created_at, window_start, window_end, situation, confidence, severity, reason_codes_json,
                    summary, recommended_action, model_name, prompt_version, context_hash, inference_duration_ms,
                    status, error_code, context_snapshot_json, human_label, human_correct, human_comment, reviewed_at)
                   values (?, ?, ?, ?, 0.86, 'normal', ?, 'Zusammenfassung', 'observe', 'qwen-test',
                           'sentero-situation-shadow-v1', 'hash', 123, 'ok', null, ?, ?, ?, ?, ?)""",
                (
                    created_at,
                    "2026-09-06T09:00:00+00:00",
                    "2026-09-06T10:00:00+00:00",
                    situation,
                    json.dumps(["bathroom_presence"]),
                    json.dumps(context),
                    human_label,
                    human_correct,
                    human_comment,
                    reviewed_at,
                ),
            )
            con.commit()
            return int(cur.lastrowid)

    def _shadow_count(self) -> int:
        with self.mapping.connect() as con:
            return int(con.execute("select count(*) from sentero_ai_shadow_assessments").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
