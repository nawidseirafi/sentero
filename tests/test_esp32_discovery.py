from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.services.device_mapping_service import DeviceMappingService
from backend.services.esp32_discovery_service import Esp32DiscoveryService


class Esp32DiscoveryTests(unittest.TestCase):
    def test_status_does_not_start_udp_listener(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            service = Esp32DiscoveryService(mapping)

            status = service.status()

        self.assertFalse(status["listening"])
        self.assertFalse(service.is_listening())

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

    def test_repeated_identical_discovery_is_throttled(self) -> None:
        payload = {
            "type": "sentero-discovery",
            "protocol": 1,
            "device_id": "c1001-a1b2c3d4",
            "model": "C1001",
            "firmware": "1.0.0",
            "sensor_type": "presence_radar",
            "http_port": 8088,
            "capabilities": ["presence"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            service = Esp32DiscoveryService(mapping)

            first = service.ingest_datagram(payload, ("192.168.178.44", 37020))
            first_seen = service.get_pending("c1001-a1b2c3d4")
            second = service.ingest_datagram(payload, ("192.168.178.44", 37020))
            second_seen = service.get_pending("c1001-a1b2c3d4")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first_seen.last_seen_at, second_seen.last_seen_at)


if __name__ == "__main__":
    unittest.main()
