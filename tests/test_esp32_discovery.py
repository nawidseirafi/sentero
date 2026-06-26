from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.device_mapping_service import DeviceMappingService
from backend.services.esp32_discovery_service import Esp32DiscoveryService


class Esp32DiscoveryTests(unittest.TestCase):
    def test_valid_udp_discovery_payload_creates_pending_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            service = Esp32DiscoveryService(mapping)

            sensor = service.ingest_datagram(
                {
                    "type": "sentero-discovery",
                    "protocol": 1,
                    "device_id": "c1001-a1b2c3d4",
                    "model": "C1001",
                    "firmware": "1.0.0",
                    "sensor_type": "presence_radar",
                    "http_port": 8088,
                    "capabilities": ["presence", "fall_detection", "respiration_rate"],
                },
                ("192.168.178.44", 37020),
            )
            pending = service.pending()

        self.assertIsNotNone(sensor)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].device_id, "c1001-a1b2c3d4")
        self.assertEqual(pending[0].ip_address, "192.168.178.44")
        self.assertEqual(pending[0].http_port, 8088)
        self.assertIn("presence", pending[0].capabilities)

    def test_invalid_udp_discovery_payload_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            service = Esp32DiscoveryService(mapping)

            sensor = service.ingest_datagram({"type": "other", "device_id": "x"}, ("192.168.178.44", 37020))

        self.assertIsNone(sensor)


if __name__ == "__main__":
    unittest.main()
