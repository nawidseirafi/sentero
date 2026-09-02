from __future__ import annotations

from tests.fakes import NoNetworkSensorSource

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.services.device_mapping_service import DeviceMappingService
from backend.services.ecotracker_service import normalize_ecotracker_host
from backend.services.sensor_manager import SensorManager, public_ecotracker_reading




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
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            manager = SensorManager(mapping)

            with patch("backend.services.ecotracker_service.requests.get", return_value=FakeEcoTrackerResponse()) as get:
                result = manager.connect_ecotracker("192.168.1.42")
                roles = mapping.roles(dev=True, include_state=True)

            self.assertEqual(result["sensor"]["id"], "home_energy")
            self.assertEqual(result["reading"]["power_w"], 125)
            self.assertEqual(result["reading"]["meter_reading_kwh"], 145.0)
            self.assertEqual(get.call_args.args[0], "http://192.168.1.42/v1/json")
            self.assertEqual(len(roles), 1)
            self.assertEqual(roles[0]["role"], "home_energy")
            self.assertEqual(roles[0]["source"], "ecotracker")
            self.assertEqual(roles[0]["state"], 125)
            self.assertEqual(roles[0]["device_class"], "power")
            self.assertTrue(roles[0]["reachable"])

    def test_public_reading_labels_import_counter_as_meter_reading(self) -> None:
        reading = public_ecotracker_reading({"power": 125, "energyCounterIn": 145000, "energyCounterOut": 4500})

        self.assertEqual(reading["meter_reading_kwh"], 145.0)
        self.assertEqual(reading["energy_in_kwh"], 145.0)
        self.assertEqual(reading["energy_out_kwh"], 4.5)

    def test_status_includes_current_ecotracker_reading_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            manager = SensorManager(mapping)
            with mapping.connect() as con:
                con.execute(
                    "update ecotracker_settings set host = ?, enabled = 1, updated_at = ? where id = 1",
                    ("192.168.1.42", "2026-08-19T10:00:00+00:00"),
                )
                con.commit()

            with patch("backend.services.ecotracker_service.requests.get", return_value=FakeEcoTrackerResponse()):
                status = manager.ecotracker_status()

            self.assertEqual(status["reading"]["meter_reading_kwh"], 145.0)
            self.assertEqual(status["reading"]["power_w"], 125)

    def test_existing_ecotracker_energy_role_is_migrated_to_power(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            timestamp = "2026-08-19T10:00:00+00:00"
            with mapping.connect() as con:
                con.execute(
                    """insert into sensor_roles
                       (role, room, entity_id, device_id, friendly_name, device_class, domain, source, confidence, active, created_at, updated_at)
                       values ('home_energy', 'home', 'ecotracker.energyCounterIn', 'ecotracker:192.168.1.42', 'everHome EcoTracker IR', 'energy', 'sensor', 'ecotracker', 100, 1, ?, ?)""",
                    (timestamp, timestamp),
                )
                con.commit()

            SensorManager(mapping)
            roles = mapping.roles(dev=True, include_state=False)

            self.assertEqual(roles[0]["entity_id"], "ecotracker.power")
            self.assertEqual(roles[0]["device_class"], "power")

    def test_snapshot_contains_ecotracker_rows_without_external_sensor_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
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
            self.assertEqual(by_entity["ecotracker.power"]["friendly_name"], "EcoTracker Verbrauch")
            self.assertEqual(by_entity["ecotracker.energyCounterIn"]["device_class"], "energy")


if __name__ == "__main__":
    unittest.main()
