from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.services.device_mapping_service import DeviceMappingService
from backend.services.ecotracker_service import normalize_ecotracker_host
from backend.services.sensor_manager import SensorManager


class DummyHomeAssistant:
    def configured(self) -> bool:
        return False

    def get_states(self) -> list[dict[str, Any]]:
        raise AssertionError("Home Assistant must not be used for EcoTracker")


class FakeEcoTrackerResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return {
            "power": 125,
            "powerAvg": 100,
            "energyCounterIn": 145000,
            "energyCounterOut": 4500,
        }


class EcoTrackerServiceTests(unittest.TestCase):
    def test_normalize_ecotracker_host_accepts_plain_ip_or_url(self) -> None:
        self.assertEqual(normalize_ecotracker_host("192.168.1.42"), "192.168.1.42")
        self.assertEqual(normalize_ecotracker_host("http://192.168.1.42/v1/json"), "192.168.1.42")

    def test_connect_registers_local_home_energy_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            manager = SensorManager(mapping)

            with patch("backend.services.ecotracker_service.requests.get", return_value=FakeEcoTrackerResponse()) as get:
                result = manager.connect_ecotracker("192.168.1.42")
                roles = mapping.roles(dev=True, include_state=True)

            self.assertEqual(result["sensor"]["id"], "home_energy")
            self.assertEqual(result["reading"]["power_w"], 125)
            self.assertEqual(get.call_args.args[0], "http://192.168.1.42/v1/json")
            self.assertEqual(len(roles), 1)
            self.assertEqual(roles[0]["role"], "home_energy")
            self.assertEqual(roles[0]["source"], "ecotracker")
            self.assertEqual(roles[0]["state"], 145000)
            self.assertTrue(roles[0]["reachable"])

    def test_snapshot_contains_ecotracker_rows_without_home_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=DummyHomeAssistant())
            SensorManager(mapping)
            with mapping.connect() as con:
                con.execute(
                    "update ecotracker_settings set host = ?, enabled = 1, last_payload_json = ?, updated_at = ? where id = 1",
                    ("192.168.1.42", json.dumps({}), "2026-08-19T10:00:00+00:00"),
                )
                con.commit()

            with patch("backend.services.ecotracker_service.requests.get", return_value=FakeEcoTrackerResponse()):
                rows = mapping.snapshot()

            by_entity = {row["entity_id"]: row for row in rows}
            self.assertEqual(by_entity["ecotracker.power"]["state"], 125)
            self.assertEqual(by_entity["ecotracker.energyCounterIn"]["device_class"], "energy")


if __name__ == "__main__":
    unittest.main()
