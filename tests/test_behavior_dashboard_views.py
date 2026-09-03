from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from backend.behavior_agent import SenteroBehaviorAgent
from backend.services.device_mapping_service import DeviceMappingService
from backend.services.service import SenteroService
from tests.fakes import NoNetworkSensorSource


class BehaviorDashboardViewTests(unittest.TestCase):
    def test_behavior_day_builds_semantic_timeline_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            agent = SenteroBehaviorAgent(mapping)
            self._insert_sensor_event(agent, "2026-09-03T07:18:00+00:00", "bedroom_presence", "bedroom", "on", "presence")
            self._insert_sensor_event(agent, "2026-09-03T07:43:00+00:00", "kitchen_motion", "kitchen", "active", "motion")
            self._insert_sensor_event(agent, "2026-09-03T14:22:00+00:00", "living_room_motion", "living_room", "on", "motion")
            self._insert_sensor_event(agent, "2026-09-03T18:43:00+00:00", "bathroom_humidity", "bathroom", "72", "humidity")

            result = agent.behavior_day(day="2026-09-03")

        titles = [item["title"] for item in result["timeline_events"]]
        self.assertIn("Tag begonnen", titles)
        self.assertIn("Küche", titles)
        self.assertIn("Ungewöhnlich lange Ruhephase", titles)
        self.assertIn("Luftfeuchtigkeit erhöht", titles)
        self.assertGreaterEqual(result["summary"]["anomaly_count"], 2)

    def test_behavior_trends_uses_daily_summary_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            agent = SenteroBehaviorAgent(mapping)
            with mapping.connect() as con:
                con.execute(
                    "update behavior_profile set average_wakeup_time = ?, average_active_minutes = ?, normal_door_usage = ? where user_id = 1",
                    ("07:24", 90, '{"average_daily_events": 2}'),
                )
                con.execute(
                    """insert into behavior_daily_summary
                       (date, wakeup_time, first_activity, last_activity, active_minutes, inactivity_periods, room_usage, door_events, occupancy_score, anomaly_score)
                       values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("2026-09-03", "07:41", "2026-09-03T07:41:00+00:00", "2026-09-03T22:10:00+00:00", 58, "[]", "{}", 1, 10, 0),
                )
                con.commit()

            result = agent.behavior_trends(days=14)

        labels = {item["label"] for item in result["cards"]}
        metrics = {item["metric"] for item in result["series"]}
        self.assertIn("Aufstehzeit", labels)
        self.assertIn("Aktivität", labels)
        self.assertIn("wake_time", metrics)
        self.assertIn("activity", metrics)
        self.assertIn("longest_rest", metrics)
        self.assertIn("night_activity", metrics)
        self.assertIn("away_time", metrics)
        self.assertEqual(len(result["points"]), 14)
        self.assertIsNotNone(result["series"][0]["baseline"]["average"])

    def test_sentero_service_exposes_dashboard_behavior_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            service = SenteroService(mapping)

            day = service.behavior_day(day="2026-09-03")
            trends = service.behavior_trends(days=14)
            hints = service.behavior_hints(days=14)

        self.assertEqual(day["date"], "2026-09-03")
        self.assertEqual(len(trends["points"]), 14)
        self.assertIn("current", hints)

    def _insert_sensor_event(self, agent: SenteroBehaviorAgent, event_time: str, role: str, room: str, state: str, device_class: str) -> None:
        with agent.mapping.connect() as con:
            con.execute(
                """insert into sentero_sensor_events
                   (event_time, role, room, entity_id, state, device_class, source, created_at, data_class, aggregation_level)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_time,
                    role,
                    room,
                    f"sensor.{role}",
                    state,
                    device_class,
                    "test",
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "personal_behavior" if device_class in {"presence", "motion"} else "health_adjacent",
                    "event",
                ),
            )
            con.commit()


if __name__ == "__main__":
    unittest.main()
