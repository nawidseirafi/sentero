from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.sensor_sources.zigbee2mqtt import Zigbee2MqttSensorSource
from backend.services.device_mapping_service import DeviceMappingService
from backend.services.mqtt_service import MqttMessage
from backend.services.sensor_manager import SensorManager
from backend.sensor_sources.base import SensorEvent


class FakeMqtt:
    host = "mosquitto"
    port = 1883

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []
        self.requests: list[tuple[str, str, object]] = []

    def configured(self) -> bool:
        return True

    def publish(self, topic: str, payload: object, retain: bool = False) -> dict:
        self.published.append((topic, payload))
        return {"ok": True, "topic": topic, "payload": payload}

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list:
        return []

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith("/device/rename"):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {"status": "ok", "data": {"from": payload_dict.get("from"), "to": payload_dict.get("to"), "homeassistant_rename": payload_dict.get("homeassistant_rename", False)}}
        elif request_topic.endswith("/device/remove"):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {"status": "ok", "data": {"id": payload_dict.get("id"), "block": False, "force": bool(payload_dict.get("force", False))}}
        elif request_topic.endswith("/permit_join"):
            value = payload.get("value") if isinstance(payload, dict) else payload
            response_payload = {"status": "ok", "data": {"value": value}}
        else:
            response_payload = {"status": "ok", "data": {}}
        return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")


class FakeMessage:
    def __init__(self, topic: str, payload: dict) -> None:
        self.topic = topic
        self.payload = payload
        self.raw_payload = "{}"


class SnapshotMqtt(FakeMqtt):
    def __init__(self, messages: list[FakeMessage]) -> None:
        super().__init__()
        self.messages = messages

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list:
        return self.messages


class FactoryResetMqtt(SnapshotMqtt):
    def __init__(self, device_id: str, messages: list[FakeMessage]) -> None:
        super().__init__(messages)
        self.device_id = device_id

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        response_payload = {"device_id": self.device_id, "status": "factory_resetting"}
        return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")


class TimeoutFactoryResetMqtt(FactoryResetMqtt):
    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        raise TimeoutError("timeout")


class FailingFactoryResetMqtt(FactoryResetMqtt):
    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        raise RuntimeError("mqtt publish failed")


def esp32_presence_messages(device_id: str, availability: str = "online") -> list[FakeMessage]:
    return [
        FakeMessage(f"sentero/{device_id}/state", {
            "device_id": device_id,
            "presence": True,
            "signal_quality": 88,
            "firmware": "1.0.0-test",
        }),
        FakeMessage(f"sentero/{device_id}/availability", {
            "device_id": device_id,
            "status": availability,
            "firmware": "1.0.0-test",
        }),
    ]


def upsert_esp32_presence_role(mapping: DeviceMappingService, device_id: str) -> None:
    mapping.upsert_role({
        "role": "keller_presence",
        "room": "keller",
        "entity_id": f"sentero/{device_id}/state",
        "device_id": device_id,
        "friendly_name": "Keller Präsenzsensor",
        "device_class": "presence",
        "domain": "binary_sensor",
        "source": "mqtt",
        "confidence": 100,
    })


class FailingMqtt(FakeMqtt):
    def publish(self, topic: str, payload: dict, retain: bool = False) -> dict:
        raise RuntimeError("mqtt unavailable")

    def request_response(self, request_topic: str, response_topic: str, payload: dict, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        raise RuntimeError("mqtt unavailable")


class PermitJoinFailingMqtt(FakeMqtt):
    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith("/permit_join"):
            response_payload = {"status": "error", "error": "permit join stop failed"}
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")
        return super().request_response(request_topic, response_topic, payload, timeout=timeout, response_filter=response_filter)


class StringPermitJoinMqtt(FakeMqtt):
    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith("/permit_join"):
            response_payload = {"status": "ok", "data": {"value": "false"}}
        elif request_topic.endswith("/device/remove"):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {"status": "ok", "data": {"id": payload_dict.get("id")}}
        else:
            response_payload = {"status": "ok", "data": {}}
        if response_filter:
            self.assert_filter_result = response_filter(response_payload)
        return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")


class FullPermitJoinInvalidMqtt(FakeMqtt):
    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith("/permit_join") and payload == {"value": False, "time": 0}:
            response_payload = {"status": "error", "error": "Invalid payload", "data": {}}
            if response_filter:
                response_filter(response_payload)
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")
        if request_topic.endswith("/permit_join"):
            value = payload.get("value") if isinstance(payload, dict) else payload
            response_payload = {"status": "ok", "data": {"value": value}}
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")
        if request_topic.endswith("/device/remove"):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {"status": "ok", "data": {"id": payload_dict.get("id")}}
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload="{}")
        return super().request_response(request_topic, response_topic, payload, timeout=timeout, response_filter=response_filter)


class RemoveFailingSnapshotMqtt(SnapshotMqtt):
    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float = 8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith("/permit_join"):
            value = payload.get("value") if isinstance(payload, dict) else payload
            return MqttMessage(topic=response_topic, payload={"status": "ok", "data": {"value": value}}, raw_payload="{}")
        if request_topic.endswith("/device/remove"):
            payload_dict = payload if isinstance(payload, dict) else {}
            return MqttMessage(
                topic=response_topic,
                payload={"status": "error", "error": f"Device '{payload_dict.get('id')}' does not exist", "data": {}},
                raw_payload="{}",
            )
        return super().request_response(request_topic, response_topic, payload, timeout=timeout, response_filter=response_filter)


class FakeHomeAssistant:
    base_url = "http://homeassistant.local:8123"

    def __init__(self) -> None:
        self.service_calls: list[tuple[str, str, dict]] = []

    def get_states(self) -> list[dict]:
        return []

    def call_service(self, domain: str, service: str, payload: dict) -> dict:
        self.service_calls.append((domain, service, payload))
        return {"ok": True}


class FailingGetStatesHomeAssistant(FakeHomeAssistant):
    def get_states(self) -> list[dict]:
        raise RuntimeError("home assistant should not be called")


class MqttSensorSourceTests(unittest.TestCase):
    def test_zigbee2mqtt_seed_creates_sensor_and_battery_entities(self) -> None:
        seed = '[{"topic":"zigbee2mqtt/Wohnzimmer Bewegung","payload":{"occupancy":true,"battery":29,"linkquality":110}}]'
        with patch.dict(os.environ, {"SENTERO_MQTT_BOOTSTRAP_EVENTS": seed}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=FakeMqtt())
            rows = source.snapshot()

        by_id = {row["entity_id"]: row for row in rows}
        self.assertIn("binary_sensor.wohnzimmer_bewegung", by_id)
        self.assertIn("sensor.wohnzimmer_bewegung_battery", by_id)
        self.assertEqual(by_id["binary_sensor.wohnzimmer_bewegung"]["device_class"], "occupancy")
        self.assertEqual(by_id["sensor.wohnzimmer_bewegung_battery"]["state"], "29")

    def test_zigbee2mqtt_snapshot_keeps_topic_source_ref(self) -> None:
        mqtt = SnapshotMqtt([FakeMessage("zigbee2mqtt/Haustuer", {"contact": False, "battery": 88, "linkquality": 120})])
        with patch.dict(os.environ, {"SENTERO_MQTT_BOOTSTRAP_EVENTS": ""}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=mqtt)
            rows = source.snapshot()

        contact = next(row for row in rows if row["device_class"] == "opening")
        self.assertEqual(contact["source_ref"], "zigbee2mqtt/Haustuer")
        self.assertEqual(contact["topic"], "zigbee2mqtt/Haustuer")
        self.assertEqual(contact["payload_key"], "contact")

    def test_sentero_c1001_snapshot_normalizes_presence_capabilities(self) -> None:
        mqtt = SnapshotMqtt([
            FakeMessage(
                "sentero/c1001-living-01/state",
                {"presence": True, "fall_detected": False, "breathing_detected": True, "respiration_rate": 14, "battery": 98, "signal_quality": 82},
            )
        ])
        with patch.dict(os.environ, {"SENTERO_MQTT_BOOTSTRAP_EVENTS": ""}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=mqtt)
            rows = source.snapshot()

        by_key = {row["payload_key"]: row for row in rows}
        self.assertEqual(by_key["presence"]["source"], "mqtt")
        self.assertEqual(by_key["presence"]["source_ref"], "sentero/c1001-living-01/state")
        self.assertEqual(by_key["fall_detected"]["device_class"], "fall_detected")
        self.assertEqual(by_key["breathing_detected"]["device_class"], "breathing_detected")
        self.assertEqual(by_key["respiration_rate"]["state"], "14")
        self.assertEqual(by_key["battery"]["state"], "98")
        self.assertEqual(by_key["signal_quality"]["state"], "82")

    def test_device_mapping_uses_direct_mqtt_for_zigbee_permit_join(self) -> None:
        fake = FakeMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            service.mqtt = fake
            detail = service._open_zigbee_permit_join(60)

        self.assertTrue(detail["ok"])
        self.assertEqual(detail["provider"], "zigbee2mqtt")
        self.assertEqual(fake.published, [("zigbee2mqtt/bridge/request/permit_join", {"value": True, "time": 60})])

    def test_sensor_manager_uses_mqtt_discovery_and_registers_topic_source(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="entrance", duration=60)
            mqtt.messages = [FakeMessage("zigbee2mqtt/Haustuer", {"contact": False, "battery": 88, "linkquality": 120})]
            found = manager.discovered(started["discovery_id"])
            registered = manager.register(found["sensor"]["id"], started["discovery_id"], room_id="entrance")
            role = mapping.get_role("main_door", dev=True)

        self.assertEqual(found["status"], "found")
        self.assertEqual(found["sensor"]["source"], "zigbee2mqtt")
        self.assertEqual(found["sensor"]["source_ref"], "zigbee2mqtt/Haustuer")
        self.assertEqual(found["sensor"]["type"], "door_contact")
        self.assertEqual(registered["status"], "registered")
        self.assertEqual(role["source"], "zigbee2mqtt")
        self.assertEqual(role["entity_id"], "zigbee2mqtt/Haustuer")
        self.assertEqual(mqtt.requests[0][0], "zigbee2mqtt/bridge/request/device/rename")
        self.assertIn(("zigbee2mqtt/bridge/request/permit_join", {"value": False, "time": 0}), mqtt.published)

    def test_mqtt_discovery_stops_permit_join_when_candidate_is_found(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="entrance", duration=60)
            mqtt.messages = [FakeMessage("zigbee2mqtt/Haustuer", {"contact": False})]
            found = manager.discovered(started["discovery_id"])

        self.assertEqual(found["status"], "found")
        self.assertEqual(mqtt.published[-1], ("zigbee2mqtt/bridge/request/permit_join", {"value": False, "time": 0}))

    def test_mqtt_discovery_stops_permit_join_on_timeout(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="entrance", duration=60)
            old = (datetime.now(timezone.utc) - timedelta(seconds=240)).isoformat(timespec="seconds")
            with mapping.connect() as con:
                con.execute("update sensor_discovery_sessions set started_at = ? where id = ?", (old, started["discovery_id"]))
                con.commit()
            result = manager.discovered(started["discovery_id"])

        self.assertEqual(result["status"], "not_found")
        self.assertEqual(mqtt.published[-1], ("zigbee2mqtt/bridge/request/permit_join", {"value": False, "time": 0}))

    def test_cancel_discovery_stops_permit_join(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="entrance", duration=60)
            result = manager.cancel_discovery(started["discovery_id"])

        self.assertTrue(result["ok"])
        self.assertEqual(mqtt.published[-1], ("zigbee2mqtt/bridge/request/permit_join", {"value": False, "time": 0}))

    def test_sensor_register_renames_zigbee2mqtt_device_before_saving(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="Keller", duration=60)
            mqtt.messages = [FakeMessage("zigbee2mqtt/0xa4c13811eb64ffff", {"contact": False})]
            found = manager.discovered(started["discovery_id"])
            manager.register(found["sensor"]["id"], started["discovery_id"], name="Keller Hobby Rechts", room_id="Keller")
            role = mapping.get_role("Keller_door", dev=True)

        self.assertEqual(mqtt.requests[0], (
            "zigbee2mqtt/bridge/request/device/rename",
            "zigbee2mqtt/bridge/response/device/rename",
            {"from": "0xa4c13811eb64ffff", "to": "Keller Hobby Rechts", "homeassistant_rename": False},
        ))
        self.assertEqual(role["entity_id"], "zigbee2mqtt/Keller Hobby Rechts")
        self.assertEqual(role["friendly_name"], "Keller Hobby Rechts")

    def test_delete_zigbee2mqtt_sensor_removes_external_device_before_local_mapping(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/0xa4c13811eb64ffff",
                "device_id": "0xa4c13811eb64ffff",
                "friendly_name": "Keller Hobby Rechts",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            result = mapping.delete_role("Keller_door")
            role = mapping.get_role("Keller_door", dev=True)

        self.assertTrue(result["deleted"])
        self.assertEqual(result["removal"]["provider"], "zigbee2mqtt")
        self.assertEqual(mqtt.requests[0], (
            "zigbee2mqtt/bridge/request/permit_join",
            "zigbee2mqtt/bridge/response/permit_join",
            {"value": False, "time": 0},
        ))
        self.assertEqual(mqtt.requests[1], (
            "zigbee2mqtt/bridge/request/device/remove",
            "zigbee2mqtt/bridge/response/device/remove",
            {"id": "0xa4c13811eb64ffff", "force": "true", "block": "false"},
        ))
        self.assertIsNone(role)

    def test_delete_zigbee2mqtt_sensor_keeps_local_mapping_when_external_remove_fails(self) -> None:
        mqtt = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/0xa4c13811eb64ffff",
                "device_id": "0xa4c13811eb64ffff",
                "friendly_name": "Keller Hobby Rechts",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            with self.assertRaises(RuntimeError):
                mapping.delete_role("Keller_door")
            role = mapping.get_role("Keller_door", dev=True)

        self.assertIsNotNone(role)

    def test_delete_zigbee2mqtt_sensor_does_not_remove_when_permit_join_stop_fails(self) -> None:
        mqtt = PermitJoinFailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/0xa4c13811eb64ffff",
                "device_id": "0xa4c13811eb64ffff",
                "friendly_name": "Keller Hobby Rechts",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            with self.assertRaises(RuntimeError):
                mapping.delete_role("Keller_door")
            role = mapping.get_role("Keller_door", dev=True)

        self.assertIsNotNone(role)
        self.assertEqual(mqtt.requests, [
            (
                "zigbee2mqtt/bridge/request/permit_join",
                "zigbee2mqtt/bridge/response/permit_join",
                {"value": False, "time": 0},
            ),
            (
                "zigbee2mqtt/bridge/request/permit_join",
                "zigbee2mqtt/bridge/response/permit_join",
                {"value": False, "time": 0, "device": None},
            ),
            (
                "zigbee2mqtt/bridge/request/permit_join",
                "zigbee2mqtt/bridge/response/permit_join",
                {"value": False},
            ),
            (
                "zigbee2mqtt/bridge/request/permit_join",
                "zigbee2mqtt/bridge/response/permit_join",
                False,
            ),
        ])

    def test_delete_zigbee2mqtt_sensor_accepts_string_false_permit_join_response(self) -> None:
        mqtt = StringPermitJoinMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/0xa4c13811eb64ffff",
                "device_id": "0xa4c13811eb64ffff",
                "friendly_name": "Keller Hobby Rechts",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            result = mapping.delete_role("Keller_door")

        self.assertTrue(result["deleted"])
        self.assertTrue(mqtt.assert_filter_result)
        self.assertEqual(mqtt.requests[0][0], "zigbee2mqtt/bridge/request/permit_join")
        self.assertEqual(mqtt.requests[1][0], "zigbee2mqtt/bridge/request/device/remove")

    def test_delete_zigbee2mqtt_sensor_retries_permit_join_stop_after_invalid_payload(self) -> None:
        mqtt = FullPermitJoinInvalidMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/0xa4c1381219fcffff",
                "device_id": "0xa4c1381219fcffff",
                "friendly_name": "Keller Hobby Rechts",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            result = mapping.delete_role("Keller_door")

        self.assertTrue(result["deleted"])
        self.assertEqual(mqtt.requests[0], (
            "zigbee2mqtt/bridge/request/permit_join",
            "zigbee2mqtt/bridge/response/permit_join",
            {"value": False, "time": 0},
        ))
        self.assertEqual(mqtt.requests[1], (
            "zigbee2mqtt/bridge/request/permit_join",
            "zigbee2mqtt/bridge/response/permit_join",
            {"value": False, "time": 0, "device": None},
        ))
        self.assertEqual(mqtt.requests[2][0], "zigbee2mqtt/bridge/request/device/remove")

    def test_delete_zigbee2mqtt_sensor_prefers_ieee_over_stale_friendly_name(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/Keller Türkontakt",
                "device_id": "0xa4c1381219fcffff",
                "friendly_name": "Keller Türkontakt2",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            result = mapping.delete_role("Keller_door")

        self.assertTrue(result["deleted"])
        self.assertEqual(mqtt.requests[1], (
            "zigbee2mqtt/bridge/request/device/remove",
            "zigbee2mqtt/bridge/response/device/remove",
            {"id": "0xa4c1381219fcffff", "force": "true", "block": "false"},
        ))

    def test_delete_zigbee2mqtt_sensor_does_not_try_child_entity_names(self) -> None:
        mqtt = RemoveFailingSnapshotMqtt([
            FakeMessage(
                "zigbee2mqtt/Keller Türkontakt2",
                {
                    "contact": False,
                    "battery": 100,
                    "voltage": 3000,
                    "tamper": False,
                    "linkquality": 80,
                },
            )
        ])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/Keller Türkontakt",
                "device_id": "0xa4c1381219fcffff",
                "friendly_name": "Keller Türkontakt2",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 100,
            })
            with self.assertRaises(RuntimeError):
                mapping.delete_role("Keller_door")

        remove_ids = [
            payload.get("id")
            for request_topic, _, payload in mqtt.requests
            if request_topic.endswith("/device/remove") and isinstance(payload, dict)
        ]
        self.assertIn("0xa4c1381219fcffff", remove_ids)
        self.assertIn("Keller Türkontakt", remove_ids)
        self.assertIn("Keller Türkontakt2", remove_ids)
        self.assertNotIn("zigbee2mqtt/Keller Türkontakt", remove_ids)
        self.assertNotIn("zigbee2mqtt/Keller Türkontakt2", remove_ids)
        self.assertNotIn("keller_t_rkontakt2", remove_ids)
        self.assertNotIn("Keller Türkontakt2 Batterie", remove_ids)
        self.assertNotIn("Keller Türkontakt2 Spannung", remove_ids)
        self.assertNotIn("Keller Türkontakt2 Manipulation", remove_ids)

    def test_registered_mqtt_sensor_uses_discovery_cache_when_no_retained_state_exists(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="entrance", duration=60)
            mqtt.messages = [FakeMessage("zigbee2mqtt/Haustuer", {"contact": False})]
            found = manager.discovered(started["discovery_id"])
            manager.register(found["sensor"]["id"], started["discovery_id"], room_id="entrance")
            mqtt.messages = []
            result = mapping.test_role("main_door")
            role = mapping.roles(include_state=True)[0]

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "state_check")
        self.assertTrue(role["reachable"])

    def test_cached_mqtt_sensor_matches_topic_entity_and_ieee_identity(self) -> None:
        mqtt = SnapshotMqtt([])
        ieee = "0xa4c13811eb64ffff"
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="Keller", duration=60)
            mqtt.messages = [FakeMessage(f"zigbee2mqtt/{ieee}", {"contact": False, "battery": 88})]
            found = manager.discovered(started["discovery_id"])
            manager.register(found["sensor"]["id"], started["discovery_id"], room_id="Keller")
            mqtt.messages = []
            result = mapping.test_role("Keller_door")
            role = mapping.roles(include_state=True)[0]
            stored = mapping.get_role("Keller_door", dev=True)

        self.assertTrue(result["ok"])
        self.assertEqual(stored["entity_id"], f"zigbee2mqtt/{ieee}")
        self.assertEqual(result["entity_id"], f"binary_sensor.{ieee}")
        self.assertTrue(role["reachable"])

    def test_cached_mqtt_sensor_with_unknown_contact_but_telemetry_is_reachable(self) -> None:
        mqtt = SnapshotMqtt([])
        ieee = "0xa4c13811eb64ffff"
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="Keller", duration=60)
            mqtt.messages = [FakeMessage(f"zigbee2mqtt/{ieee}", {"contact": None, "battery": 100, "linkquality": 124, "tamper": True})]
            found = manager.discovered(started["discovery_id"])
            manager.register(found["sensor"]["id"], started["discovery_id"], room_id="Keller")
            mqtt.messages = []
            result = mapping.test_role("Keller_door")
            role = mapping.roles(include_state=True)[0]

        self.assertTrue(result["ok"])
        self.assertTrue(role["reachable"])
        self.assertEqual(role["battery_level"], 100)

    def test_mqtt_availability_offline_marks_presence_sensor_unreachable(self) -> None:
        device_id = "c1001-test-01"
        mqtt = SnapshotMqtt([
            FakeMessage(f"sentero/{device_id}/state", {
                "device_id": device_id,
                "firmware": "1.0.0-test",
                "presence": True,
                "signal_quality": 88,
            }),
            FakeMessage(f"sentero/{device_id}/availability", {
                "device_id": device_id,
                "firmware": "1.0.0-test",
                "status": "offline",
            }),
        ])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            mapping.upsert_role({
                "role": "keller_presence",
                "room": "keller",
                "entity_id": f"sentero/{device_id}/state",
                "device_id": device_id,
                "friendly_name": "Keller Präsenzsensor",
                "device_class": "presence",
                "domain": "binary_sensor",
                "source": "mqtt",
                "confidence": 100,
            })
            role = mapping.roles(include_state=True)[0]

        self.assertFalse(role["reachable"])
        self.assertEqual(role["state"], "on")

    def test_mqtt_presence_sensor_exposes_usb_power_source(self) -> None:
        device_id = "c1001-test-01"
        mqtt = SnapshotMqtt([
            FakeMessage(f"sentero/{device_id}/state", {
                "device_id": device_id,
                "presence": True,
                "power_source": "usb",
                "signal_quality": 88,
            }),
            FakeMessage(f"sentero/{device_id}/availability", {
                "device_id": device_id,
                "status": "online",
            }),
        ])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            upsert_esp32_presence_role(mapping, device_id)
            role = mapping.roles(include_state=True)[0]

        self.assertEqual(role["power_source"], "usb")
        self.assertIsNone(role["battery_level"])

    def test_delete_esp32_presence_sensor_sends_factory_reset_before_local_delete(self) -> None:
        device_id = "c1001-test-01"
        mqtt = FactoryResetMqtt(device_id, esp32_presence_messages(device_id, availability="online"))
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            upsert_esp32_presence_role(mapping, device_id)

            result = mapping.delete_role("keller_presence")
            stored = mapping.get_role("keller_presence", dev=True)

        self.assertTrue(result["deleted"])
        self.assertIsNone(stored)
        self.assertEqual(mqtt.requests[0][0], f"sentero/{device_id}/command")
        self.assertEqual(mqtt.requests[0][1], f"sentero/{device_id}/status")
        self.assertEqual(mqtt.requests[0][2], {"command": "factory_reset", "reason": "removed_from_sentero"})

    def test_delete_esp32_presence_sensor_timeout_keeps_local_mapping(self) -> None:
        device_id = "c1001-test-01"
        mqtt = TimeoutFactoryResetMqtt(device_id, esp32_presence_messages(device_id, availability="online"))
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            upsert_esp32_presence_role(mapping, device_id)

            with self.assertRaisesRegex(RuntimeError, "Factory Reset nicht bestätigt"):
                mapping.delete_role("keller_presence")
            stored = mapping.get_role("keller_presence", dev=True)

        self.assertIsNotNone(stored)

    def test_delete_esp32_presence_sensor_publish_error_keeps_local_mapping(self) -> None:
        device_id = "c1001-test-01"
        mqtt = FailingFactoryResetMqtt(device_id, esp32_presence_messages(device_id, availability="online"))
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            upsert_esp32_presence_role(mapping, device_id)

            with self.assertRaisesRegex(RuntimeError, "mqtt publish failed"):
                mapping.delete_role("keller_presence")
            stored = mapping.get_role("keller_presence", dev=True)

        self.assertIsNotNone(stored)

    def test_delete_esp32_presence_sensor_offline_blocks_external_reset(self) -> None:
        device_id = "c1001-test-01"
        mqtt = FactoryResetMqtt(device_id, esp32_presence_messages(device_id, availability="offline"))
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            upsert_esp32_presence_role(mapping, device_id)

            with self.assertRaisesRegex(RuntimeError, "nicht erreichbar"):
                mapping.delete_role("keller_presence")
            stored = mapping.get_role("keller_presence", dev=True)

        self.assertIsNotNone(stored)
        self.assertEqual(mqtt.requests, [])

    def test_delete_esp32_presence_sensor_local_only_skips_factory_reset(self) -> None:
        device_id = "c1001-test-01"
        mqtt = FactoryResetMqtt(device_id, esp32_presence_messages(device_id, availability="offline"))
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mqtt"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            upsert_esp32_presence_role(mapping, device_id)

            result = mapping.delete_role("keller_presence", local_only=True)
            stored = mapping.get_role("keller_presence", dev=True)

        self.assertTrue(result["deleted"])
        self.assertIsNone(stored)
        self.assertEqual(mqtt.requests, [])

    def test_mixed_mode_resolves_homeassistant_entity_with_ieee_suffix_for_mqtt_mapping(self) -> None:
        class MixedSource:
            def configured(self) -> bool:
                return True

            def snapshot(self) -> list:
                return [
                    {
                        "entity_id": "binary_sensor.0xa4c13811eb64ffff",
                        "domain": "binary_sensor",
                        "state": "None",
                        "friendly_name": "0xa4c13811eb64ffff",
                        "device_class": "opening",
                        "device_id": "0xa4c13811eb64ffff",
                        "topic": "zigbee2mqtt/0xa4c13811eb64ffff",
                        "source_ref": "zigbee2mqtt/0xa4c13811eb64ffff",
                        "source": "zigbee2mqtt",
                    },
                    SensorEvent(
                        source="homeassistant",
                        sensor_id="binary_sensor.0xa4c13811eb64ffff_turkontakt",
                        role=None,
                        room="Keller",
                        state="on",
                        changed_at="2026-06-25T14:19:40+00:00",
                        metadata={"device_class": "door", "friendly_name": "Keller Hobby Rechts Türkontakt"},
                    ),
                    SensorEvent(
                        source="homeassistant",
                        sensor_id="sensor.0xa4c13811eb64ffff_battery",
                        role=None,
                        room="Keller",
                        state="100",
                        changed_at="2026-06-25T14:05:05+00:00",
                        metadata={"device_class": "battery", "friendly_name": "Keller Hobby Rechts Batterie", "unit_of_measurement": "%"},
                    )
                ]

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mixed"}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FakeHomeAssistant())
            mapping.sensor_source = MixedSource()
            mapping.upsert_role({
                "role": "Keller_door",
                "room": "Keller",
                "entity_id": "zigbee2mqtt/0xa4c13811eb64ffff",
                "device_id": "0xa4c13811eb64ffff",
                "friendly_name": "hobby Türkontakt",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "zigbee2mqtt",
                "confidence": 210,
            })
            result = mapping.test_role("Keller_door")
            role = mapping.roles(include_state=True)[0]

        self.assertTrue(result["ok"])
        self.assertEqual(result["entity_id"], "binary_sensor.0xa4c13811eb64ffff_turkontakt")
        self.assertEqual(role["state"], "on")
        self.assertTrue(role["reachable"])
        self.assertEqual(role["battery_level"], 100)

    def test_presence_sensor_discovery_is_blocked_for_esp32_provisioning(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mixed", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FakeHomeAssistant())
            mapping.mqtt = mqtt
            mapping.sensor_source.sources[0].mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("presence_sensor", room_id="living_room", duration=60)

        self.assertEqual(started["status"], "manual_action")
        self.assertEqual(started["discovery_id"], 0)
        self.assertEqual(started["detail"]["reason"], "presence_requires_provisioning")
        self.assertEqual(mqtt.published, [])

    def test_mixed_mqtt_discovery_does_not_call_homeassistant(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mixed", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FailingGetStatesHomeAssistant())
            mapping.mqtt = mqtt
            mapping.sensor_source.sources[0].mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="entrance", duration=60)
            mqtt.messages = [FakeMessage("zigbee2mqtt/Haustuer", {"contact": False})]
            found = manager.discovered(started["discovery_id"])

        self.assertEqual(found["status"], "found")
        self.assertEqual(found["sensor"]["source_ref"], "zigbee2mqtt/Haustuer")

    def test_mqtt_discovery_ignores_existing_sensor_state_changes(self) -> None:
        mqtt = SnapshotMqtt([FakeMessage("zigbee2mqtt/Keller", {"contact": False})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {"SENTERO_SENSOR_SOURCE": "mqtt", "SENTERO_MQTT_BOOTSTRAP_EVENTS": ""},
            clear=False,
        ):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            mapping.mqtt = mqtt
            mapping.sensor_source.mqtt = mqtt
            manager = SensorManager(mapping)
            started = manager.start_discovery("door_contact", room_id="Keller", duration=60)
            mqtt.messages = [FakeMessage("zigbee2mqtt/Keller", {"contact": True})]
            found = manager.discovered(started["discovery_id"])

        self.assertEqual(found["status"], "searching")
        self.assertIsNone(found["sensor"])

    def test_delete_old_homeassistant_mapping_only_deactivates_local_role(self) -> None:
        fake = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mixed"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FakeHomeAssistant())
            service.mqtt = fake
            service.upsert_role({
                "role": "main_door",
                "room": "entrance",
                "entity_id": "binary_sensor.alte_tuer",
                "device_id": "ha-device-1",
                "friendly_name": "Alte Tuer",
                "device_class": "opening",
                "domain": "binary_sensor",
                "source": "wizard",
                "confidence": 100,
            })
            result = service.delete_role("main_door")
            role = service.get_role("main_door", dev=True)

        self.assertTrue(result["deleted"])
        self.assertEqual(result["removal"]["reason"], "local_mapping_removed")
        self.assertIsNone(role)

    def test_mixed_snapshot_accepts_sensor_event_rows(self) -> None:
        class MixedSource:
            def snapshot(self) -> list:
                return [
                    SensorEvent(
                        source="homeassistant",
                        sensor_id="binary_sensor.alte_tuer",
                        role=None,
                        room="entrance",
                        state="off",
                        changed_at="2026-06-25T13:00:00+00:00",
                        metadata={"device_class": "opening", "friendly_name": "Alte Tuer"},
                    )
                ]

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mixed"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FakeHomeAssistant())
            service.sensor_source = MixedSource()
            rows = service.snapshot()

        self.assertEqual(rows[0]["entity_id"], "binary_sensor.alte_tuer")
        self.assertEqual(rows[0]["device_class"], "opening")
        self.assertEqual(rows[0]["friendly_name"], "Alte Tuer")

    def test_mqtt_home_status_does_not_load_sensor_snapshot(self) -> None:
        class FailingSnapshotSource:
            name = "mixed"

            def configured(self) -> bool:
                return True

            def snapshot(self) -> list:
                raise RuntimeError("snapshot should not be called")

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {"SENTERO_SENSOR_SOURCE": "mixed"}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db", ha=FakeHomeAssistant())
            service.sensor_source = FailingSnapshotSource()
            status = service.home_status()

        self.assertEqual(status, {"connected": True, "sensor_ready": True, "system_ready": True})

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
