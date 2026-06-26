from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.device_mapping_service import DeviceMappingService
from backend.services.esp32_provisioning_service import Esp32ProvisioningService, masked_payload, resolve_secret
from backend.services.mqtt_service import MqttMessage
from backend.services.sensor_manager import SensorManager


class FakeResponse:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> dict:
        return self.body


class FakeHttp:
    def __init__(self, body: dict | None = None, status_code: int = 200) -> None:
        self.body = body or {"success": True, "device_id": "c1001-living-01", "model": "C1001", "firmware": "1.0.0"}
        self.status_code = status_code
        self.requests: list[tuple[str, dict, float, dict | None]] = []

    def post(self, url: str, json: dict, timeout: float, headers: dict | None = None) -> FakeResponse:
        self.requests.append((url, json, timeout, headers))
        return FakeResponse(self.body, self.status_code)


class FakeMqtt:
    host = "mosquitto"
    port = 1883
    username = "sentero"
    password = "mqtt-secret"

    def __init__(self, messages: dict[str, list[MqttMessage]] | None = None) -> None:
        self.messages = messages or {}
        self.requested_topics: list[str] = []

    def configured(self) -> bool:
        return True

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list[MqttMessage]:
        self.requested_topics.append(topic)
        return self.messages.get(topic, [])


class TimeoutMqtt(FakeMqtt):
    def retained_messages(self, topic: str, timeout: float = 2.5) -> list[MqttMessage]:
        self.requested_topics.append(topic)
        return []


class Esp32ProvisioningTests(unittest.TestCase):
    def test_payload_masking_does_not_leak_passwords_or_token(self) -> None:
        service, _mapping, _http, _mqtt = self.service()
        service_with_token = service
        with patch.dict(os.environ, {"SENTERO_ESP32_DEVICE_TOKEN": "device-token"}, clear=False):
            payload = service_with_token.build_payload()
            masked = masked_payload(payload)

        self.assertEqual(masked["wifi"]["password"], "***")
        self.assertEqual(masked["mqtt"]["password"], "***")
        self.assertEqual(masked["device"]["token"], "***")
        self.assertNotIn("wifi-secret", str(masked))
        self.assertNotIn("mqtt-secret", str(masked))
        self.assertNotIn("device-token", str(masked))

    def test_config_token_placeholder_resolves_from_environment(self) -> None:
        with patch.dict(os.environ, {"SENTERO_ESP32_DEVICE_TOKEN": "device-token"}, clear=False):
            self.assertEqual(resolve_secret("SENTERO_ESP32_DEVICE_TOKEN"), "device-token")

    def test_config_token_placeholder_is_empty_when_environment_is_missing(self) -> None:
        with patch.dict(os.environ, {"SENTERO_ESP32_DEVICE_TOKEN": ""}, clear=False):
            self.assertEqual(resolve_secret("SENTERO_ESP32_DEVICE_TOKEN"), "")

    def test_config_token_can_be_literal_value(self) -> None:
        self.assertEqual(resolve_secret("literal-device-token"), "literal-device-token")

    def test_missing_wifi_data_returns_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / "sentero.db")
            SensorManager(mapping)
            service = Esp32ProvisioningService(mapping, mqtt=FakeMqtt(), http_client=FakeHttp())

            with self.assertRaises(ValueError) as exc:
                service.build_payload()

        self.assertIn("WLAN", str(exc.exception))

    def test_successful_sensor_response_registers_presence_sensor(self) -> None:
        topic = "sentero/c1001-living-01/state"
        state = MqttMessage(topic=topic, payload={"presence": True, "respiration_rate": 14, "battery": 98}, raw_payload="{}")
        service, mapping, http, mqtt = self.service(messages={topic: [state]})

        result = service.provision("living_room", "Wohnzimmer Präsenzsensor")
        role = mapping.get_role("living_room_presence", dev=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["device"]["id"], "c1001-living-01")
        self.assertEqual(result["device"]["type"], "presence_radar")
        self.assertEqual(result["device"]["source"], "mqtt")
        self.assertEqual(role["entity_id"], topic)
        self.assertEqual(role["source"], "mqtt")
        self.assertEqual(http.requests[0][0], "http://localhost:8088/api/provision")
        self.assertEqual(http.requests[0][3]["Accept"], "application/json")
        self.assertEqual(http.requests[0][1]["device"]["room_id"], "living_room")
        self.assertEqual(http.requests[0][1]["device"]["display_name"], "Wohnzimmer Präsenzsensor")
        self.assertIn("sentero/c1001-living-01/availability", mqtt.requested_topics)
        self.assertIn(topic, mqtt.requested_topics)

    def test_payload_includes_room_and_display_name_for_sensor_mqtt_metadata(self) -> None:
        service, _mapping, _http, _mqtt = self.service()

        payload = service.build_payload(room_id="living_room", display_name="Wohnzimmer Präsenzsensor")

        self.assertEqual(payload["device"]["room_id"], "living_room")
        self.assertEqual(payload["device"]["display_name"], "Wohnzimmer Präsenzsensor")

    def test_successful_java_sensor_response_uses_camel_case_device_id(self) -> None:
        topic = "sentero/c1001-living-02/state"
        state = MqttMessage(topic=topic, payload={"presence": True, "battery": 99}, raw_payload="{}")
        http = FakeHttp({"success": True, "deviceId": "c1001-living-02", "model": "C1001", "firmware": "1.0.0"})
        service, mapping, _http, mqtt = self.service(messages={topic: [state]}, http=http)

        result = service.provision("living_room", "Wohnzimmer Präsenzsensor")
        role = mapping.get_role("living_room_presence", dev=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["device"]["id"], "c1001-living-02")
        self.assertEqual(role["entity_id"], topic)
        self.assertIn("sentero/c1001-living-02/availability", mqtt.requested_topics)

    def test_mqtt_timeout_after_successful_response(self) -> None:
        service, _mapping, _http, _mqtt = self.service(mqtt=TimeoutMqtt())
        with patch.dict(os.environ, {"SENTERO_ESP32_MQTT_WAIT_TIMEOUT": "0.1"}, clear=False):
            with self.assertRaises(RuntimeError) as exc:
                service.provision("living_room", "Wohnzimmer Präsenzsensor")

        self.assertIn("nicht im Sensornetzwerk gemeldet", str(exc.exception))

    def test_invalid_sensor_response_is_rejected(self) -> None:
        service, _mapping, _http, _mqtt = self.service(http=FakeHttp({"success": False}))

        with self.assertRaises(RuntimeError) as exc:
            service.provision("living_room", "Wohnzimmer Präsenzsensor")

        self.assertIn("abgelehnt", str(exc.exception))

    def test_invalid_state_payload_is_rejected(self) -> None:
        topic = "sentero/c1001-living-01/state"
        state = MqttMessage(topic=topic, payload={"firmware": "1.0.0"}, raw_payload="{}")
        service, _mapping, _http, _mqtt = self.service(messages={topic: [state]})

        with self.assertRaises(RuntimeError) as exc:
            service.provision("living_room", "Wohnzimmer Präsenzsensor")

        self.assertIn("keine nutzbaren Messwerte", str(exc.exception))

    def service(
        self,
        messages: dict[str, list[MqttMessage]] | None = None,
        http: FakeHttp | None = None,
        mqtt: FakeMqtt | None = None,
    ) -> tuple[Esp32ProvisioningService, DeviceMappingService, FakeHttp, FakeMqtt]:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Keep the temp directory alive by materializing it under /private/tmp for the test object lifetime.
            path = Path(tempfile.mkdtemp(dir="/private/tmp")) / "sentero.db"
        mapping = DeviceMappingService(database_path=path)
        manager = SensorManager(mapping)
        manager.save_network_settings({"wifi_ssid": "MeinWLAN", "wifi_password": "wifi-secret"})
        fake_http = http or FakeHttp()
        fake_mqtt = mqtt or FakeMqtt(messages)
        return Esp32ProvisioningService(mapping, mqtt=fake_mqtt, http_client=fake_http), mapping, fake_http, fake_mqtt


if __name__ == "__main__":
    unittest.main()
