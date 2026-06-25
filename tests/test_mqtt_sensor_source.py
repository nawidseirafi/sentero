from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sensor_sources.zigbee2mqtt import Zigbee2MqttSensorSource
from backend.services.device_mapping_service import DeviceMappingService


class FakeMqtt:
    host = "mosquitto"
    port = 1883

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []

    def configured(self) -> bool:
        return True

    def publish(self, topic: str, payload: dict, retain: bool = False) -> dict:
        self.published.append((topic, payload))
        return {"ok": True, "topic": topic, "payload": payload}

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list:
        return []


class FailingMqtt(FakeMqtt):
    def publish(self, topic: str, payload: dict, retain: bool = False) -> dict:
        raise RuntimeError("mqtt unavailable")


class FakeHomeAssistant:
    base_url = "http://homeassistant.local:8123"

    def __init__(self) -> None:
        self.service_calls: list[tuple[str, str, dict]] = []

    def get_states(self) -> list[dict]:
        return []

    def call_service(self, domain: str, service: str, payload: dict) -> dict:
        self.service_calls.append((domain, service, payload))
        return {"ok": True}


class MqttSensorSourceTests(unittest.TestCase):
    def test_zigbee2mqtt_seed_creates_sensor_and_battery_entities(self) -> None:
        seed = '[{"topic":"zigbee2mqtt/Wohnzimmer Bewegung","payload":{"occupancy":true,"battery":29,"linkquality":110}}]'
        with patch.dict(os.environ, {"SENTERO_MQTT_BOOTSTRAP_EVENTS": seed, "SENTERO_ZIGBEE2MQTT_TOPIC_PREFIX": "zigbee2mqtt"}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=FakeMqtt())
            rows = source.snapshot()

        by_id = {row["entity_id"]: row for row in rows}
        self.assertIn("binary_sensor.wohnzimmer_bewegung", by_id)
        self.assertIn("sensor.wohnzimmer_bewegung_battery", by_id)
        self.assertEqual(by_id["binary_sensor.wohnzimmer_bewegung"]["device_class"], "occupancy")
        self.assertEqual(by_id["sensor.wohnzimmer_bewegung_battery"]["state"], "29")

    def test_device_mapping_uses_direct_mqtt_for_zigbee_permit_join(self) -> None:
        fake = FakeMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            service.mqtt = fake
            detail = service._open_zigbee_permit_join(60)

        self.assertTrue(detail["ok"])
        self.assertEqual(detail["provider"], "zigbee2mqtt")
        self.assertEqual(fake.published, [("zigbee2mqtt/bridge/request/permit_join", {"value": True, "time": 60})])

    def test_homeassistant_source_uses_zigbee2mqtt_permit_join_when_available(self) -> None:
        fake = FakeMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "homeassistant"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FakeHomeAssistant())
            service.mqtt = fake
            result = service.start_zigbee_pairing("living_room_presence", "living_room", duration=60)

        self.assertEqual(result["status"], "pairing_started")
        self.assertEqual(result["detail"]["provider"], "zigbee2mqtt")
        self.assertEqual(fake.published, [("zigbee2mqtt/bridge/request/permit_join", {"value": True, "time": 60})])

    def test_homeassistant_source_falls_back_to_discovery_when_permit_join_unavailable(self) -> None:
        fake = FailingMqtt()
        fake_ha = FakeHomeAssistant()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "homeassistant"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=fake_ha)
            service.mqtt = fake
            result = service.start_zigbee_pairing("living_room_presence", "living_room", duration=60)

        self.assertEqual(result["status"], "pairing_started")
        self.assertEqual(result["detail"]["provider"], "zigbee2mqtt")
        self.assertEqual(fake_ha.service_calls[0][0:2], ("mqtt", "publish"))
        self.assertEqual(fake_ha.service_calls[0][2]["topic"], "zigbee2mqtt/bridge/request/permit_join")

    def test_homeassistant_source_uses_discovery_when_direct_and_ha_mqtt_publish_fail(self) -> None:
        class FailingHomeAssistant(FakeHomeAssistant):
            def call_service(self, domain: str, service: str, payload: dict) -> dict:
                raise RuntimeError("ha mqtt unavailable")

        fake = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "homeassistant"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FailingHomeAssistant())
            service.mqtt = fake
            result = service.start_zigbee_pairing("living_room_presence", "living_room", duration=60)

        self.assertEqual(result["status"], "waiting_for_signal")
        self.assertEqual(result["detail"]["provider"], "homeassistant")
        self.assertFalse(result["detail"]["permit_join_available"])


if __name__ == "__main__":
    unittest.main()
