from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.sensor_sources.base import create_sensor_source
from backend.sensor_sources.zigbee2mqtt import Zigbee2MqttSensorSource
from backend.sensors.normalizer import normalize_snapshot
from backend.sensors.service import SenteroSensorService


class FakeMapping:
    def __init__(self, rows):
        self.rows = rows

    def snapshot(self):
        return self.rows

    def home_status(self):
        return {"connected": True, "sensor_ready": True, "system_ready": True}


class FailingMqtt:
    host = "localhost"
    port = 1883

    def configured(self):
        return True

    def retained_messages(self, *_args, **_kwargs):
        raise RuntimeError("mqtt unavailable")

    def publish(self, *_args, **_kwargs):
        raise RuntimeError("mqtt unavailable")


class SensorArchitectureTests(unittest.TestCase):
    def test_mixed_source_can_be_selected_explicitly(self) -> None:
        with patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mixed"}, clear=True):
            source = create_sensor_source()
        self.assertEqual(source.name, "mixed")

    def test_mqtt_source_can_be_selected_explicitly(self) -> None:
        with patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=True):
            source = create_sensor_source()
        self.assertEqual(source.name, "zigbee2mqtt")

    def test_mqtt_source_snapshot_does_not_crash_when_broker_unavailable(self) -> None:
        source = Zigbee2MqttSensorSource(mqtt=FailingMqtt())
        self.assertEqual(source.snapshot(), [])

    def test_zigbee2mqtt_payload_normalizes_to_internal_device_and_event(self) -> None:
        rows = Zigbee2MqttSensorSource(mqtt=FailingMqtt())._snapshot_from_seed(
            '[{"topic":"zigbee2mqtt/Haustuer","payload":{"contact":true,"battery":29,"linkquality":88}}]'
        )
        devices, events = normalize_snapshot(rows)

        self.assertEqual(devices[0].type, "door_contact")
        self.assertIn("contact", devices[0].capabilities)
        self.assertIn("battery", devices[0].capabilities)
        self.assertIn("signal_quality", devices[0].capabilities)
        self.assertTrue(any(event.event_type == "contact" and event.value == "open" for event in events))

    def test_dashboard_is_public_and_hides_source_refs_and_raw_payloads(self) -> None:
        service = SenteroSensorService(FakeMapping([
            {
                "entity_id": "binary_sensor.haustuer",
                "source": "homeassistant",
                "friendly_name": "Haustuer",
                "device_class": "opening",
                "state": "on",
                "last_changed": "2026-06-25T08:00:00+00:00",
                "battery_level": 80,
            }
        ]))

        dashboard = service.dashboard()
        devices = service.devices()["devices"]
        events = service.events()["events"]

        self.assertEqual(dashboard["summary"]["open_doors"], 1)
        self.assertNotIn("source_ref", devices[0])
        self.assertNotIn("raw_payload", events[0])
        self.assertNotIn("entity_id", str(dashboard))


if __name__ == "__main__":
    unittest.main()
