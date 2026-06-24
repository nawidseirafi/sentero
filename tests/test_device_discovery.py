from __future__ import annotations

import unittest

from backend.services.device_mapping_service import score_candidates


class DeviceDiscoveryTests(unittest.TestCase):
    def test_zigbee_new_motion_sensor_wins_over_battery_entity(self) -> None:
        started_at = "2026-06-25T08:00:00+00:00"
        current = [
            {
                "entity_id": "sensor.wohnzimmer_bewegung_battery",
                "device_id": "zigbee-device-1",
                "domain": "sensor",
                "device_class": "battery",
                "friendly_name": "Wohnzimmer Bewegung Batterie",
                "state": "100",
                "last_changed": "2026-06-25T08:01:00+00:00",
                "last_updated": "2026-06-25T08:01:00+00:00",
            },
            {
                "entity_id": "binary_sensor.wohnzimmer_bewegung",
                "device_id": "zigbee-device-1",
                "domain": "binary_sensor",
                "device_class": "motion",
                "friendly_name": "Wohnzimmer Bewegung",
                "state": "unavailable",
                "last_changed": "2026-06-25T08:01:00+00:00",
                "last_updated": "2026-06-25T08:01:00+00:00",
            },
        ]

        scored = score_candidates([], current, "living_presence", "living_room", started_at)

        self.assertGreaterEqual(len(scored), 1)
        self.assertEqual(scored[0]["entity_id"], "binary_sensor.wohnzimmer_bewegung")
        self.assertNotIn("sensor.wohnzimmer_bewegung_battery", {item["entity_id"] for item in scored})


if __name__ == "__main__":
    unittest.main()
