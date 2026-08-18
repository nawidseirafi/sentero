from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.services.device_mapping_service import DeviceMappingService, SensorTransport, role_candidate_matches
from backend.services.sensor_manager import SensorManager, wifi_sensor_setup_enabled


class FakeMessage:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class FakeMqtt:
    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []
        self.requests: list[tuple[str, str, object]] = []

    def publish(self, topic: str, payload: object) -> dict:
        self.published.append((topic, payload))
        return {"ok": True, "topic": topic, "payload": payload}

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float, response_filter) -> FakeMessage:
        self.requests.append((request_topic, response_topic, payload))
        response = {"status": "ok", "data": payload}
        return FakeMessage(response)

    def client_available(self) -> bool:
        return True


class FakeSource:
    name = "zigbee2mqtt"

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def configured(self) -> bool:
        return True

    def snapshot(self) -> list[dict]:
        return self.rows


class SensorOnboardingTests(unittest.TestCase):
    def test_presence_setup_uses_zigbee_discovery_by_default(self) -> None:
        manager, mapping, source, mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})

        result = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")

        self.assertEqual(result["status"], "searching")
        self.assertEqual(result["transport"], SensorTransport.ZIGBEE.value)
        self.assertEqual(result["expires_in_seconds"], 120)
        self.assertEqual(mqtt.published[0][0], "zigbee2mqtt/bridge/request/permit_join")

    def test_door_setup_uses_zigbee_discovery(self) -> None:
        manager, _mapping, _source, mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})

        result = manager.start_discovery("door_contact", room_id="hallway", role="main_door")

        self.assertEqual(result["status"], "searching")
        self.assertEqual(result["transport"], SensorTransport.ZIGBEE.value)
        self.assertEqual(mqtt.published[0][0], "zigbee2mqtt/bridge/request/permit_join")

    def test_pairing_timeout_closes_join_window(self) -> None:
        manager, mapping, _source, mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        started = manager.start_discovery("door_contact", room_id="hallway", role="main_door", duration=10)
        old = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat(timespec="seconds")
        with mapping.connect() as con:
            con.execute("update sensor_discovery_sessions set started_at = ? where id = ?", (old, started["discovery_id"]))
            con.commit()

        result = manager.discovered(started["discovery_id"])

        self.assertEqual(result["status"], "not_found")
        self.assertTrue(any(payload in (False, {"value": False}) or (isinstance(payload, dict) and payload.get("value") is False) for _topic, payload in mqtt.published))

    def test_single_presence_device_with_multiple_entities_is_assigned_to_room(self) -> None:
        manager, mapping, source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        started = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")
        source.rows = [
            zigbee_entity("binary_sensor.hallway_presence_occupancy", "occupancy", "on", "0xaaa"),
            zigbee_entity("sensor.hallway_presence_temperature", "temperature", "21", "0xaaa"),
            zigbee_entity("sensor.hallway_presence_humidity", "humidity", "45", "0xaaa"),
        ]

        found = manager.discovered(started["discovery_id"])
        registered = manager.register(found["sensor"]["id"], started["discovery_id"], name="Flur Präsenz", room_id="hallway", dev=True)

        self.assertEqual(found["status"], "found")
        self.assertEqual(registered["sensor"]["room_id"], "hallway")
        role = mapping.get_role("hallway_presence", dev=True)
        self.assertEqual(role["transport"], SensorTransport.ZIGBEE.value)
        self.assertEqual(role["sensor_type"], "presence")
        self.assertIn("sensor.hallway_presence_temperature", json.loads(role["entity_ids_json"]))
        with mapping.connect() as con:
            device = con.execute("select * from sensor_devices where room_id = ? and sensor_type = ? and enabled = 1", ("hallway", "presence")).fetchone()
        self.assertIsNotNone(device)
        self.assertEqual(device["device_id"], "0xaaa")

    def test_multiple_found_devices_are_returned_once_per_physical_device(self) -> None:
        manager, _mapping, source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        started = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")
        source.rows = [
            zigbee_entity("binary_sensor.hallway_presence_occupancy", "occupancy", "on", "0xaaa"),
            zigbee_entity("sensor.hallway_presence_temperature", "temperature", "21", "0xaaa"),
            zigbee_entity("binary_sensor.kitchen_presence_occupancy", "occupancy", "on", "0xbbb"),
        ]

        result = manager.discovered(started["discovery_id"])

        self.assertEqual(result["status"], "found")
        self.assertEqual(len(result["devices"]), 2)

    def test_wrong_device_type_is_not_returned_for_presence(self) -> None:
        manager, _mapping, source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        started = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")
        source.rows = [zigbee_entity("sensor.hallway_battery", "battery", "100", "0xaaa")]

        result = manager.discovered(started["discovery_id"])

        self.assertEqual(result["status"], "searching")
        self.assertIsNone(result["sensor"])

    def test_door_sensor_is_detected_by_device_class(self) -> None:
        manager, _mapping, source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        started = manager.start_discovery("door_contact", room_id="hallway", role="main_door")
        source.rows = [zigbee_entity("binary_sensor.front_door_contact", "opening", "on", "0xdoor")]

        result = manager.discovered(started["discovery_id"])

        self.assertEqual(result["status"], "found")
        self.assertEqual(result["sensor"]["type"], "door_contact")

    def test_restart_keeps_existing_sensor_assignment(self) -> None:
        manager, mapping, source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        started = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")
        source.rows = [zigbee_entity("binary_sensor.hallway_presence_occupancy", "occupancy", "on", "0xaaa")]
        found = manager.discovered(started["discovery_id"])
        manager.register(found["sensor"]["id"], started["discovery_id"], name="Flur Präsenz", room_id="hallway", dev=True)

        restarted = DeviceMappingService(database_path=mapping.database_path)
        role = restarted.get_role("hallway_presence", dev=True)

        self.assertEqual(role["room"], "hallway")
        self.assertEqual(role["transport"], SensorTransport.ZIGBEE.value)

    def test_sensor_replace_keeps_old_mapping_until_new_pairing_succeeds(self) -> None:
        manager, mapping, source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        first = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")
        source.rows = [zigbee_entity("binary_sensor.hallway_presence_occupancy", "occupancy", "on", "0xaaa")]
        found = manager.discovered(first["discovery_id"])
        manager.register(found["sensor"]["id"], first["discovery_id"], name="Alt", room_id="hallway", dev=True)

        second = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")
        role_before = mapping.get_role("hallway_presence", dev=True)

        self.assertEqual(role_before["device_id"], "0xaaa")
        manager.cancel_discovery(second["discovery_id"])
        self.assertEqual(mapping.get_role("hallway_presence", dev=True)["device_id"], "0xaaa")

    def test_esp32_dataset_remains_compatible(self) -> None:
        _manager, mapping, _source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"})
        role = mapping.upsert_role({
            "role": "living_room_presence",
            "room": "living_room",
            "entity_id": "sentero/c1001-living/state",
            "device_id": "c1001-living",
            "friendly_name": "Wohnzimmer Präsenz",
            "device_class": "presence",
            "domain": "binary_sensor",
            "source": "mqtt",
            "confidence": 100,
        })

        self.assertEqual(role["transport"], SensorTransport.WIFI_ESPHOME.value)
        self.assertEqual(role["sensor_type"], "presence")

    def test_wifi_feature_flag_defaults_off(self) -> None:
        with patch.dict(os.environ, {"SENTERO_PRESENCE_SENSOR_TRANSPORT": "zigbee"}, clear=True):
            self.assertFalse(wifi_sensor_setup_enabled())

    def test_presence_transport_env_selects_wifi_setup(self) -> None:
        with patch.dict(os.environ, {"SENTERO_PRESENCE_SENSOR_TRANSPORT": "wifi_esphome"}, clear=False):
            manager, _mapping, _source, _mqtt = self.manager({"SENTERO_PRESENCE_SENSOR_TRANSPORT": "wifi_esphome"})

            status = manager.status()
            result = manager.start_discovery("presence", room_id="hallway", role="hallway_presence")

        self.assertEqual(status["presence_sensor_transport"], "wifi_esphome")
        self.assertTrue(status["wifi_sensor_setup_enabled"])
        self.assertEqual(result["status"], "manual_action")
        self.assertEqual(result["transport"], "wifi_esphome")

    def test_presence_classes_include_target_entities(self) -> None:
        self.assertTrue(role_candidate_matches("hallway_presence", {"domain": "binary_sensor", "device_class": "moving_target", "entity_id": "binary_sensor.mmwave_moving_target"}))
        self.assertTrue(role_candidate_matches("hallway_presence", {"domain": "binary_sensor", "device_class": "static_target", "entity_id": "binary_sensor.mmwave_static_target"}))

    def test_v1_sensor_wizard_hides_wifi_qr_by_default(self) -> None:
        text = Path("frontend/src/components/SensorWizard.tsx").read_text(encoding="utf-8")

        self.assertIn("presenceTransport = 'zigbee'", text)
        self.assertIn("presenceTransport === 'wifi_esphome'", text)
        self.assertNotIn("Zigbee", text)
        self.assertNotIn("MQTT", text)

    def manager(self, env: dict[str, str] | None = None) -> tuple[SensorManager, DeviceMappingService, FakeSource, FakeMqtt]:
        path = Path(tempfile.mkdtemp(dir="/private/tmp")) / "sentero.db"
        os.environ.update({"SENTERO_SENSOR_SOURCE": "mqtt", **(env or {})})
        mapping = DeviceMappingService(database_path=path)
        source = FakeSource()
        mqtt = FakeMqtt()
        mapping.source_mode = "mqtt"
        mapping.sensor_source = source
        mapping.mqtt = mqtt
        manager = SensorManager(mapping)
        return manager, mapping, source, mqtt


def zigbee_entity(entity_id: str, device_class: str, state: str, device_id: str) -> dict:
    return {
        "entity_id": entity_id,
        "domain": entity_id.split(".", 1)[0],
        "state": state,
        "friendly_name": entity_id.rsplit(".", 1)[-1].replace("_", " ").title(),
        "device_class": device_class,
        "device_id": device_id,
        "identifiers": [["zigbee2mqtt", device_id]],
        "source": "zigbee2mqtt",
        "source_ref": f"zigbee2mqtt/{device_id}",
        "last_changed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    unittest.main()
