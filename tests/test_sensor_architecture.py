from __future__ import annotations

import json
import unittest

from backend.sensor_sources.base import create_sensor_source
from backend.sensor_sources.zigbee2mqtt import Zigbee2MqttSensorSource
from backend.services.device_mapping_service import find_battery_entity, role_candidate_matches
from backend.services.sensor_manager import public_type_from_mqtt_candidate
from backend.sensors.normalizer import normalize_snapshot
from backend.sensors.service import SenteroSensorService


class FakeMapping:
    def __init__(self, rows):
        self.rows = rows

    def snapshot(self):
        return self.rows

    def home_status(self):
        return {"connected": True, "sensor_ready": True, "system_ready": True}

    def roles(self, dev=False, include_state=False):
        return []


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
    def test_sensor_source_is_direct_mqtt_zigbee2mqtt(self) -> None:
        source = create_sensor_source()
        self.assertEqual(source.name, "zigbee2mqtt")
        self.assertIsInstance(source, Zigbee2MqttSensorSource)

    def test_mqtt_source_snapshot_does_not_crash_when_broker_unavailable(self) -> None:
        source = Zigbee2MqttSensorSource(mqtt=FailingMqtt())
        self.assertEqual(source.snapshot(), [])

    def test_zigbee2mqtt_payload_normalizes_to_internal_device_and_event(self) -> None:
        rows = Zigbee2MqttSensorSource(mqtt=FailingMqtt())._snapshot_from_seed(
            '[{"topic":"zigbee2mqtt/Haustuer","payload":{"contact":true,"battery":29,"linkquality":88}}]'
        )
        devices, events = normalize_snapshot(rows)

        device = next(device for device in devices if device.type == "door_contact")
        self.assertIn("contact", device.capabilities)
        self.assertIn("battery", device.capabilities)
        self.assertIn("signal_quality", device.capabilities)
        self.assertTrue(any(event.event_type == "contact" and event.value == "closed" for event in events))

    def test_zigbee2mqtt_contact_boolean_uses_physical_contact_semantics(self) -> None:
        cases = [
            ("contact", True, "closed"),
            ("contact", False, "open"),
            ("open", True, "open"),
            ("open", False, "closed"),
            ("contact", None, "unknown"),
        ]
        for key, raw_value, expected in cases:
            with self.subTest(key=key, raw_value=raw_value):
                rows = Zigbee2MqttSensorSource(mqtt=FailingMqtt())._snapshot_from_seed(
                    json.dumps([{"topic": "zigbee2mqtt/Haustuer", "payload": {key: raw_value}}])
                )
                _devices, events = normalize_snapshot(rows)
                contact_events = [event for event in events if event.event_type == "contact"]
                self.assertTrue(contact_events)
                self.assertIn(expected, [event.value for event in contact_events])

    def test_contact_fix_does_not_invert_presence_booleans(self) -> None:
        rows = Zigbee2MqttSensorSource(mqtt=FailingMqtt())._snapshot_from_seed(
            '[{"topic":"zigbee2mqtt/Wohnzimmer","payload":{"presence":true,"occupancy":false,"motion":true}}]'
        )
        _devices, events = normalize_snapshot(rows)
        by_type = {(event.event_type, event.raw_payload.get("payload_key")): event.value for event in events}

        self.assertEqual(by_type[("presence", "presence")], "active")
        self.assertEqual(by_type[("presence", "occupancy")], "inactive")
        self.assertEqual(by_type[("motion", "motion")], "active")

    def test_smart_meter_payload_normalizes_to_usage_events(self) -> None:
        rows = Zigbee2MqttSensorSource(mqtt=FailingMqtt())._snapshot_from_seed(
            '[{"topic":"zigbee2mqtt/Stromzaehler","payload":{"energy":1234.5,"power":42.7,"water_consumption":8.25,"gas_consumption":2.5}}]'
        )
        devices, events = normalize_snapshot(rows)

        device = next(device for device in devices if device.type == "smart_meter")
        self.assertIn("energy_consumption", device.capabilities)
        self.assertIn("power_usage", device.capabilities)
        self.assertIn("water_consumption", device.capabilities)
        self.assertIn("gas_consumption", device.capabilities)

        by_type = {event.event_type: event.value for event in events}
        self.assertEqual(by_type["energy_consumption"], 1234.5)
        self.assertEqual(by_type["power_usage"], 42.7)
        self.assertEqual(by_type["water_consumption"], 8.25)
        self.assertEqual(by_type["gas_consumption"], 2.5)

    def test_smart_meter_roles_accept_meter_sensor_classes(self) -> None:
        self.assertTrue(role_candidate_matches("home_energy", {"domain": "sensor", "device_class": "energy", "entity_id": "sensor.stromzaehler_energy"}))
        self.assertTrue(role_candidate_matches("home_water", {"domain": "sensor", "device_class": "water", "entity_id": "sensor.wasserzaehler"}))
        self.assertTrue(role_candidate_matches("home_gas", {"domain": "sensor", "device_class": "gas", "entity_id": "sensor.gaszaehler"}))
        self.assertFalse(role_candidate_matches("home_energy", {"domain": "binary_sensor", "device_class": "motion", "entity_id": "binary_sensor.bewegung"}))

    def test_mqtt_smart_meter_candidate_gets_public_meter_type(self) -> None:
        self.assertEqual(public_type_from_mqtt_candidate({"device_class": "energy", "payload_key": "energy"}), "electricity_meter")
        self.assertEqual(public_type_from_mqtt_candidate({"device_class": "water", "payload_key": "water_consumption"}), "water_meter")
        self.assertEqual(public_type_from_mqtt_candidate({"device_class": "gas", "payload_key": "gas_consumption"}), "gas_meter")

    def test_battery_entity_matches_generic_sensor_prefix(self) -> None:
        match = find_battery_entity(
            {"entity_id": "binary_sensor.keller_sensor_bewegung"},
            [
                {
                    "entity_id": "sensor.keller_sensor_batterie",
                    "device_class": "battery",
                    "state": "100",
                    "friendly_name": "Keller Bewegungsmelder Batterie",
                },
            ],
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["state"], "100")

    def test_battery_entity_matches_stored_zigbee_device_id(self) -> None:
        match = find_battery_entity(
            {"entity_id": "zigbee2mqtt/keller Türkontakt", "device_id": "0xa4c1381219fcffff"},
            [
                {
                    "entity_id": "sensor.0xa4c1381219fcffff_battery",
                    "device_class": "battery",
                    "state": "100",
                    "friendly_name": "Hobby Fenster Links Batterie",
                },
            ],
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["state"], "100")

    def test_dashboard_hides_source_refs_and_raw_payloads(self) -> None:
        service = SenteroSensorService(FakeMapping([
            {
                "entity_id": "zigbee2mqtt/Haustuer/contact",
                "source": "zigbee2mqtt",
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
        self.assertEqual(devices[0]["data_class"], "personal_behavior")
        self.assertEqual(events[0]["data_class"], "personal_behavior")
        self.assertEqual(events[0]["aggregation_level"], "raw")
        self.assertNotIn("entity_id", str(dashboard))

    def test_dashboard_exposes_utility_usage_summary(self) -> None:
        service = SenteroSensorService(FakeMapping([
            {
                "entity_id": "zigbee2mqtt/Stromzaehler/energy",
                "source": "zigbee2mqtt",
                "friendly_name": "Stromzaehler Energie",
                "device_class": "energy",
                "state": "1234.5",
                "last_changed": "2026-06-25T08:00:00+00:00",
            }
        ]))

        dashboard = service.dashboard()

        self.assertEqual(dashboard["summary"]["smart_meter_readings"], 1)
        self.assertTrue(dashboard["utility_usage"]["has_energy"])
        self.assertEqual(dashboard["utility_usage"]["readings"][0]["event_type"], "energy_consumption")
        self.assertEqual(dashboard["utility_usage"]["readings"][0]["data_class"], "utility")
        self.assertEqual(dashboard["utility_usage"]["readings"][0]["aggregation_level"], "aggregate")


if __name__ == "__main__":
    unittest.main()
