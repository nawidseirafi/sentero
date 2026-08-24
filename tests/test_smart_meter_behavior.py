from __future__ import annotations

from tests.fakes import NoNetworkSensorSource

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.behavior_agent import SenteroBehaviorAgent
from backend.services.device_mapping_service import DeviceMappingService




class SmartMeterBehaviorTests(unittest.TestCase):
    def test_smart_meter_snapshots_are_stored_as_utility_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            agent = SenteroBehaviorAgent(mapping)
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

            written = agent._record_snapshot([], [
                {
                    "entity_id": "sensor.stromzaehler_energy",
                    "friendly_name": "Stromzaehler Energie",
                    "device_class": "energy",
                    "state": "1234.5",
                    "last_changed": timestamp,
                    "last_updated": timestamp,
                }
            ])

            history = agent._history(days=30)

        self.assertEqual(written, 1)
        self.assertEqual(history[0]["role"], "energy_consumption")
        self.assertEqual(history[0]["state"], "1234.5")
        self.assertEqual(history[0]["data_class"], "utility")
        self.assertEqual(history[0]["aggregation_level"], "raw")

    def test_utility_usage_summary_detects_low_today_delta(self) -> None:
        agent = SenteroBehaviorAgent.__new__(SenteroBehaviorAgent)
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        history = [
            self._event(yesterday.replace(hour=8, minute=0), "100"),
            self._event(yesterday.replace(hour=20, minute=0), "110"),
            self._event(now.replace(hour=8, minute=0), "200"),
            self._event(now.replace(hour=20, minute=0), "202"),
        ]

        summary = agent._utility_usage_summary(history)

        self.assertTrue(summary["meters_configured"])
        self.assertTrue(summary["low_usage_today"])
        self.assertEqual(summary["meters"][0]["event_type"], "energy_consumption")
        self.assertEqual(summary["meters"][0]["today_delta"], 2.0)
        self.assertEqual(summary["meters"][0]["historical_daily_average"], 10.0)

    def _event(self, event_time: datetime, state: str) -> dict:
        return {
            "event_time": event_time.isoformat(timespec="seconds"),
            "role": "energy_consumption",
            "state": state,
            "entity_id": "sensor.stromzaehler_energy",
            "room": None,
        }


if __name__ == "__main__":
    unittest.main()
