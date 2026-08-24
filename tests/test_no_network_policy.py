from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from backend.services.device_mapping_service import DeviceMappingService
from tests.fakes import NoNetworkSensorSource


class NoNetworkPolicyTests(unittest.TestCase):
    def test_mapping_can_be_forced_to_no_network_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.sensor_source = NoNetworkSensorSource()
            self.assertEqual(mapping.snapshot(), [])

    def test_no_network_source_is_deterministic(self) -> None:
        source = NoNetworkSensorSource([{"entity_id": "sensor.test", "state": "1"}])
        self.assertEqual(source.snapshot(), [{"entity_id": "sensor.test", "state": "1"}])
        self.assertEqual(source.discover(), [{"entity_id": "sensor.test", "state": "1"}])


if __name__ == "__main__":
    unittest.main()
