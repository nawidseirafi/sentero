from __future__ import annotations

from tests.fakes import NoNetworkSensorSource
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from backend.sensor_sources.zigbee2mqtt import Zigbee2MqttSensorSource
from backend.services.device_mapping_service import DeviceMappingService, environmental_metrics_from_state, find_battery_entity, generic_presence_telemetry_from_state
from backend.services.mqtt_service import MqttMessage
from backend.services.sensor_manager import SensorManager
from backend.sensor_sources.base import SensorEvent

class FakeMqtt:
    host = 'mosquitto'
    port = 1883

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []
        self.requests: list[tuple[str, str, object]] = []

    def configured(self) -> bool:
        return True

    def publish(self, topic: str, payload: object, retain: bool=False) -> dict:
        self.published.append((topic, payload))
        return {'ok': True, 'topic': topic, 'payload': payload}

    def retained_messages(self, topic: str, timeout: float=2.5) -> list:
        return []

    def start_listener(self, topics: list[str]) -> None:
        return None

    def cached_messages(self, topic: str) -> list:
        return self.retained_messages(topic)

    def seed_cache(self, messages: list) -> None:
        return None

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith('/device/rename'):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {'status': 'ok', 'data': {'from': payload_dict.get('from'), 'to': payload_dict.get('to')}}
        elif request_topic.endswith('/device/remove'):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {'status': 'ok', 'data': {'id': payload_dict.get('id'), 'block': False, 'force': bool(payload_dict.get('force', False))}}
        elif request_topic.endswith('/permit_join'):
            value = payload.get('value') if isinstance(payload, dict) else payload
            response_payload = {'status': 'ok', 'data': {'value': value}}
        else:
            response_payload = {'status': 'ok', 'data': {}}
        return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')

class FakeMessage:

    def __init__(self, topic: str, payload: dict) -> None:
        self.topic = topic
        self.payload = payload
        self.raw_payload = '{}'

class SnapshotMqtt(FakeMqtt):

    def __init__(self, messages: list[FakeMessage]) -> None:
        super().__init__()
        self.messages = messages

    def retained_messages(self, topic: str, timeout: float=2.5) -> list:
        return self.messages

class FactoryResetMqtt(SnapshotMqtt):

    def __init__(self, device_id: str, messages: list[FakeMessage]) -> None:
        super().__init__(messages)
        self.device_id = device_id

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        response_payload = {'device_id': self.device_id, 'status': 'factory_resetting'}
        return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')

class TimeoutFactoryResetMqtt(FactoryResetMqtt):

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        raise TimeoutError('timeout')

class FailingFactoryResetMqtt(FactoryResetMqtt):

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        raise RuntimeError('mqtt publish failed')

def zigbee_bridge_device(ieee: str, friendly_name: str, model: str) -> dict:
    return {'ieee_address': ieee, 'friendly_name': friendly_name, 'definition': {'model': model, 'vendor': 'HOBEIAN', 'exposes': [{'type': 'binary', 'name': 'occupancy', 'property': 'occupancy'}, {'type': 'numeric', 'name': 'temperature', 'property': 'temperature'}, {'type': 'numeric', 'name': 'humidity', 'property': 'humidity'}, {'type': 'numeric', 'name': 'illuminance', 'property': 'illuminance'}, {'type': 'numeric', 'name': 'battery', 'property': 'battery'}]}}

def esp32_presence_messages(device_id: str, availability: str='online') -> list[FakeMessage]:
    return [FakeMessage(f'sentero/{device_id}/state', {'device_id': device_id, 'presence': True, 'signal_quality': 88, 'firmware': '1.0.0-test'}), FakeMessage(f'sentero/{device_id}/availability', {'device_id': device_id, 'status': availability, 'firmware': '1.0.0-test'})]

def upsert_esp32_presence_role(mapping: DeviceMappingService, device_id: str) -> None:
    mapping.upsert_role({'role': 'keller_presence', 'room': 'keller', 'entity_id': f'sentero/{device_id}/state', 'device_id': device_id, 'friendly_name': 'Keller Präsenzsensor', 'device_class': 'presence', 'domain': 'binary_sensor', 'source': 'mqtt', 'confidence': 100})

class FailingMqtt(FakeMqtt):

    def publish(self, topic: str, payload: dict, retain: bool=False) -> dict:
        raise RuntimeError('mqtt unavailable')

    def request_response(self, request_topic: str, response_topic: str, payload: dict, timeout: float=8.0, response_filter=None) -> MqttMessage:
        raise RuntimeError('mqtt unavailable')

class PermitJoinFailingMqtt(FakeMqtt):

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith('/permit_join'):
            response_payload = {'status': 'error', 'error': 'permit join stop failed'}
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')
        return super().request_response(request_topic, response_topic, payload, timeout=timeout, response_filter=response_filter)

class StringPermitJoinMqtt(FakeMqtt):

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith('/permit_join'):
            response_payload = {'status': 'ok', 'data': {'value': 'false'}}
        elif request_topic.endswith('/device/remove'):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {'status': 'ok', 'data': {'id': payload_dict.get('id')}}
        else:
            response_payload = {'status': 'ok', 'data': {}}
        if response_filter:
            self.assert_filter_result = response_filter(response_payload)
        return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')

class FullPermitJoinInvalidMqtt(FakeMqtt):

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith('/permit_join') and payload == {'value': False, 'time': 0}:
            response_payload = {'status': 'error', 'error': 'Invalid payload', 'data': {}}
            if response_filter:
                response_filter(response_payload)
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')
        if request_topic.endswith('/permit_join'):
            value = payload.get('value') if isinstance(payload, dict) else payload
            response_payload = {'status': 'ok', 'data': {'value': value}}
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')
        if request_topic.endswith('/device/remove'):
            payload_dict = payload if isinstance(payload, dict) else {}
            response_payload = {'status': 'ok', 'data': {'id': payload_dict.get('id')}}
            return MqttMessage(topic=response_topic, payload=response_payload, raw_payload='{}')
        return super().request_response(request_topic, response_topic, payload, timeout=timeout, response_filter=response_filter)

class RemoveFailingSnapshotMqtt(SnapshotMqtt):

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float=8.0, response_filter=None) -> MqttMessage:
        self.requests.append((request_topic, response_topic, payload))
        if request_topic.endswith('/permit_join'):
            value = payload.get('value') if isinstance(payload, dict) else payload
            return MqttMessage(topic=response_topic, payload={'status': 'ok', 'data': {'value': value}}, raw_payload='{}')
        if request_topic.endswith('/device/remove'):
            payload_dict = payload if isinstance(payload, dict) else {}
            return MqttMessage(topic=response_topic, payload={'status': 'error', 'error': f"Device '{payload_dict.get('id')}' does not exist", 'data': {}}, raw_payload='{}')
        return super().request_response(request_topic, response_topic, payload, timeout=timeout, response_filter=response_filter)


class MqttSensorSourceTests(unittest.TestCase):

    def test_zigbee2mqtt_seed_creates_sensor_and_battery_entities(self) -> None:
        seed = '[{"topic":"zigbee2mqtt/Wohnzimmer Bewegung","payload":{"occupancy":true,"battery":29,"linkquality":110}}]'
        with patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': seed}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=FakeMqtt())
            rows = source.snapshot()
        by_id = {row['entity_id']: row for row in rows}
        self.assertIn('binary_sensor.wohnzimmer_bewegung', by_id)
        self.assertIn('sensor.wohnzimmer_bewegung_battery', by_id)
        self.assertEqual(by_id['binary_sensor.wohnzimmer_bewegung']['device_class'], 'occupancy')
        self.assertEqual(by_id['sensor.wohnzimmer_bewegung_battery']['state'], '29')

    def test_zigbee2mqtt_snapshot_keeps_topic_source_ref(self) -> None:
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/Haustuer', {'contact': False, 'battery': 88, 'linkquality': 120})])
        with patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=mqtt)
            rows = source.snapshot()
        contact = next((row for row in rows if row['device_class'] == 'opening'))
        self.assertEqual(contact['source_ref'], 'zigbee2mqtt/Haustuer')
        self.assertEqual(contact['topic'], 'zigbee2mqtt/Haustuer')
        self.assertEqual(contact['payload_key'], 'contact')

    def test_zigbee2mqtt_bridge_devices_create_presence_candidate_entities(self) -> None:
        ieee = '0xa4c1389a3e0a13e3'
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/bridge/devices', [{'ieee_address': ieee, 'friendly_name': ieee, 'definition': {'model': 'ZG-204ZH', 'vendor': 'HOBEIAN', 'exposes': [{'type': 'binary', 'name': 'occupancy', 'property': 'occupancy'}, {'type': 'numeric', 'name': 'battery', 'property': 'battery'}]}}])])
        with patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=mqtt)
            rows = source.snapshot()
        by_key = {row['payload_key']: row for row in rows}
        self.assertEqual(by_key['occupancy']['entity_id'], f'binary_sensor.{ieee}')
        self.assertEqual(by_key['occupancy']['device_class'], 'occupancy')
        self.assertEqual(by_key['occupancy']['device_id'], ieee)
        self.assertEqual(by_key['occupancy']['manufacturer'], 'HOBEIAN')
        self.assertEqual(by_key['occupancy']['model'], 'ZG-204ZH')
        self.assertEqual(by_key['battery']['domain'], 'sensor')

    def test_zigbee2mqtt_topic_entities_use_bridge_ieee_as_device_id(self) -> None:
        ieee = '0xa4c1389a3e0a13e3'
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/bridge/devices', [{'ieee_address': ieee, 'friendly_name': 'Haustuer', 'definition': {'model': 'MCCGQ11LM', 'vendor': 'Aqara'}}]), FakeMessage('zigbee2mqtt/Haustuer', {'contact': False, 'battery': 88, 'linkquality': 120})])
        with patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=mqtt)
            rows = source.snapshot()
        contact = next((row for row in rows if row['entity_id'] == 'binary_sensor.haustuer'))
        self.assertEqual(contact['device_id'], ieee)
        self.assertEqual(contact['identifiers'], [['zigbee2mqtt', ieee]])
        self.assertEqual(contact['attributes']['ieee_address'], ieee)

    def test_sentero_c1001_snapshot_normalizes_presence_capabilities(self) -> None:
        mqtt = SnapshotMqtt([FakeMessage('sentero/c1001-living-01/state', {'presence': True, 'fall_detected': False, 'breathing_detected': True, 'respiration_rate': 14, 'battery': 98, 'signal_quality': 82})])
        with patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            source = Zigbee2MqttSensorSource(mqtt=mqtt)
            rows = source.snapshot()
        by_key = {row['payload_key']: row for row in rows}
        self.assertEqual(by_key['presence']['source'], 'mqtt')
        self.assertEqual(by_key['presence']['source_ref'], 'sentero/c1001-living-01/state')
        self.assertEqual(by_key['fall_detected']['device_class'], 'fall_detected')
        self.assertEqual(by_key['breathing_detected']['device_class'], 'breathing_detected')
        self.assertEqual(by_key['respiration_rate']['state'], '14')
        self.assertEqual(by_key['battery']['state'], '98')
        self.assertEqual(by_key['signal_quality']['state'], '82')

    def test_device_mapping_uses_direct_mqtt_for_zigbee_permit_join(self) -> None:
        fake = FakeMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.mqtt = fake
            detail = service._open_zigbee_permit_join(60)
        self.assertTrue(detail['ok'])
        self.assertEqual(detail['provider'], 'zigbee2mqtt')
        self.assertEqual(fake.published, [('zigbee2mqtt/bridge/request/permit_join', {'value': True, 'time': 60})])

    def test_sensor_manager_uses_mqtt_discovery_and_registers_topic_source(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='entrance', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/Haustuer', {'contact': False, 'battery': 88, 'linkquality': 120})]
            found = manager.discovered(started['discovery_id'])
            registered = manager.register(found['sensor']['id'], started['discovery_id'], room_id='entrance')
            role = mapping.get_role('main_door', dev=True)
        self.assertEqual(found['status'], 'found')
        self.assertEqual(found['sensor']['source'], 'zigbee2mqtt')
        self.assertEqual(found['sensor']['source_ref'], 'zigbee2mqtt/Haustuer')
        self.assertEqual(found['sensor']['type'], 'door_contact')
        self.assertEqual(registered['status'], 'registered')
        self.assertEqual(role['source'], 'zigbee2mqtt')
        self.assertEqual(role['entity_id'], 'zigbee2mqtt/Haustuer')
        self.assertEqual(mqtt.requests[0][0], 'zigbee2mqtt/bridge/request/device/rename')
        self.assertIn(('zigbee2mqtt/bridge/request/permit_join', {'value': False, 'time': 0}), mqtt.published)

    def test_mqtt_discovery_does_not_offer_existing_unassigned_presence_device(self) -> None:
        ieee = '0xa4c1389a3e0a13e3'
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/bridge/devices', [{'ieee_address': ieee, 'friendly_name': ieee, 'definition': {'model': 'ZG-204ZH', 'vendor': 'HOBEIAN', 'exposes': [{'type': 'binary', 'name': 'occupancy', 'property': 'occupancy'}]}}])])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence', duration=60)
            found = manager.discovered(started['discovery_id'], dev=True)
            raw = mapping.candidates(started['discovery_id'], dev=True)
        self.assertEqual(found['status'], 'searching')
        self.assertIsNone(found['sensor'])
        self.assertIsNone(raw['candidate'])

    def test_zigbee_discovery_only_scores_physical_devices_added_after_session_start(self) -> None:
        old_ieee = '0x111'
        new_ieee = '0xa4c1389a3e0a13e3'
        old_name = 'Guest WC Presence Sensor'
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/bridge/devices', [zigbee_bridge_device(old_ieee, old_name, 'ZG-204ZX')]), FakeMessage(f'zigbee2mqtt/{old_name}', {'occupancy': False, 'temperature': 21.5, 'humidity': 44, 'illuminance': 30})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/bridge/devices', [zigbee_bridge_device(old_ieee, old_name, 'ZG-204ZX'), zigbee_bridge_device(new_ieee, new_ieee, 'ZG-204ZH')]), FakeMessage(f'zigbee2mqtt/{old_name}', {'occupancy': True, 'temperature': 22.1, 'humidity': 45, 'illuminance': 40}), FakeMessage(f'zigbee2mqtt/{new_ieee}', {'occupancy': True, 'temperature': 21.0, 'humidity': 48, 'illuminance': 15, 'battery': 100})]
            raw = mapping.candidates(started['discovery_id'], dev=True)
            found = manager.discovered(started['discovery_id'], dev=True)
            with self.assertRaises(ValueError):
                mapping.confirm(started['discovery_id'], old_ieee, name='Flur Präsenz', room='hallway')
        self.assertEqual([item['device_id'] for item in raw['candidates']], [new_ieee])
        self.assertEqual(raw['candidate']['device_id'], new_ieee)
        self.assertEqual(found['sensor']['id'], new_ieee)
        self.assertNotIn(old_ieee, [item['device_id'] for item in raw['candidates']])
        self.assertEqual(len(raw['candidates']), 1)
        self.assertCountEqual(raw['candidate']['entity_ids'], [f'binary_sensor.{new_ieee}', f'sensor.{new_ieee}_temperature', f'sensor.{new_ieee}_humidity', f'sensor.{new_ieee}_illuminance', f'sensor.{new_ieee}_battery', f'zigbee2mqtt/{new_ieee}'])
        self.assertFalse(any((request[0].endswith('/device/rename') for request in mqtt.requests)))

    def test_mqtt_discovery_does_not_offer_existing_assigned_presence_device(self) -> None:
        ieee = '0xa4c1389a3e0a13e3'
        mqtt = SnapshotMqtt([FakeMessage(f'zigbee2mqtt/{ieee}', {'occupancy': False, 'battery': 100})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'living_room_presence', 'room': 'living_room', 'entity_id': f'zigbee2mqtt/{ieee}', 'device_id': ieee, 'friendly_name': 'Wohnzimmer Präsenz', 'device_class': 'occupancy', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            manager = SensorManager(mapping)
            started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence', duration=60)
            found = manager.discovered(started['discovery_id'])
        self.assertEqual(found['status'], 'searching')
        self.assertIsNone(found['sensor'])

    def test_zigbee_presence_role_exposes_environment_metrics(self) -> None:
        ieee = '0xa4c1389a3e0a13e3'
        mqtt = SnapshotMqtt([FakeMessage(f'zigbee2mqtt/{ieee}', {'occupancy': False, 'battery': 90, 'temperature': 22.6, 'humidity': 48, 'illuminance': 96})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'living_room_presence', 'room': 'living_room', 'entity_id': f'binary_sensor.{ieee}', 'device_id': ieee, 'friendly_name': 'Wohnzimmer Präsenz', 'device_class': 'occupancy', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            role = mapping.roles(include_state=True)[0]
        self.assertEqual(role['battery_level'], 90)
        self.assertEqual(role['temperature'], 22.6)
        self.assertEqual(role['humidity'], 48.0)
        self.assertEqual(role['illuminance'], 96.0)

    def test_zigbee2mqtt_device_state_payload_exposes_presence_false_and_metrics(self) -> None:
        payload = {'battery': 100, 'fading_time': 30, 'humidity': 44, 'humidity_calibration': 0, 'illuminance': 54, 'illuminance_interval': 1, 'indicator': 'OFF', 'linkquality': 76, 'motion_detection_mode': 'pir_and_radar', 'motion_detection_sensitivity': 7, 'motion_state': 'none', 'presence': False, 'static_detection_distance': 5, 'static_detection_sensitivity': 6, 'temperature': 23.4, 'temperature_calibration': 0, 'temperature_unit': 'celsius'}
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/Wohnzimmer Presence', payload)])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'living_room_presence', 'room': 'living_room', 'entity_id': 'zigbee2mqtt/Wohnzimmer Presence', 'friendly_name': 'Wohnzimmer Presence', 'device_class': 'presence', 'domain': 'mqtt', 'source': 'zigbee2mqtt', 'confidence': 100})
            role = mapping.roles(include_state=True)[0]
        self.assertIs(role['presence'], False)
        self.assertEqual(role['motion_state'], 'none')
        self.assertEqual(role['battery_level'], 100)
        self.assertEqual(role['temperature'], 23.4)
        self.assertEqual(role['humidity'], 44.0)
        self.assertEqual(role['illuminance'], 54.0)
        self.assertTrue(role['reachable'])

    def test_zigbee2mqtt_device_state_payload_exposes_presence_true_and_moving(self) -> None:
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/Wohnzimmer Presence', {'battery': 88, 'motion_state': 'moving', 'presence': True, 'temperature': 22.1})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'living_room_presence', 'room': 'living_room', 'entity_id': 'zigbee2mqtt/Wohnzimmer Presence', 'friendly_name': 'Wohnzimmer Presence', 'device_class': 'presence', 'domain': 'mqtt', 'source': 'zigbee2mqtt', 'confidence': 100})
            role = mapping.roles(include_state=True)[0]
        self.assertIs(role['presence'], True)
        self.assertEqual(role['motion_state'], 'moving')
        self.assertEqual(role['motion'], 'Active')
        self.assertEqual(role['battery_level'], 88)
        self.assertTrue(role['reachable'])

    def test_presence_false_does_not_fall_back_to_none(self) -> None:
        state = {'entity_id': 'zigbee2mqtt/Wohnzimmer Presence', 'domain': 'mqtt', 'state': 'online', 'source': 'zigbee2mqtt', 'source_ref': 'zigbee2mqtt/Wohnzimmer Presence', 'attributes': {'presence': False, 'motion_state': 'none'}}
        telemetry = generic_presence_telemetry_from_state({'role': 'living_room_presence', 'entity_id': 'zigbee2mqtt/Wohnzimmer Presence', 'source': 'zigbee2mqtt'}, state, [state])
        self.assertIs(telemetry['presence'], False)

    def test_zigbee2mqtt_per_key_states_still_expose_presence_false_and_metrics(self) -> None:
        states = [{'entity_id': 'binary_sensor.wohnzimmer_presence', 'domain': 'binary_sensor', 'state': 'off', 'device_class': 'presence', 'payload_key': 'presence', 'source': 'zigbee2mqtt', 'source_ref': 'zigbee2mqtt/Wohnzimmer Presence', 'topic': 'zigbee2mqtt/Wohnzimmer Presence', 'attributes': {'presence': False, 'battery': 100, 'temperature': 23.4, 'humidity': 44, 'illuminance': 54, 'motion_state': 'none'}}, {'entity_id': 'sensor.wohnzimmer_presence_battery', 'domain': 'sensor', 'state': '100', 'device_class': 'battery', 'payload_key': 'battery', 'source': 'zigbee2mqtt', 'source_ref': 'zigbee2mqtt/Wohnzimmer Presence', 'topic': 'zigbee2mqtt/Wohnzimmer Presence', 'attributes': {'battery': 100}}]
        with tempfile.TemporaryDirectory() as tmpdir:
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.sensor_source = NoNetworkSensorSource()
            mapping.snapshot = lambda: states
            mapping.upsert_role({'role': 'living_room_presence', 'room': 'living_room', 'entity_id': 'zigbee2mqtt/Wohnzimmer Presence', 'friendly_name': 'Wohnzimmer Presence', 'device_class': 'presence', 'domain': 'mqtt', 'source': 'zigbee2mqtt', 'confidence': 100})
            role = mapping.roles(include_state=True)[0]
        self.assertIs(role['presence'], False)
        self.assertEqual(role['battery_level'], 100)
        self.assertEqual(role['temperature'], 23.4)
        self.assertEqual(role['humidity'], 44.0)
        self.assertEqual(role['illuminance'], 54.0)
        self.assertTrue(role['reachable'])

    def test_presence_telemetry_uses_only_bound_mqtt_topic(self) -> None:
        source = Zigbee2MqttSensorSource(mqtt=SnapshotMqtt([FakeMessage('zigbee2mqtt/Guest WC Presence Sensor', {'battery': 90, 'humidity': 49, 'illuminance': 0, 'presence': False, 'temperature': 21.6}), FakeMessage('zigbee2mqtt/Wohnzimmer Presence', {'battery': 100, 'humidity': 44, 'illuminance': 33, 'motion_state': 'none', 'presence': False, 'temperature': 23.2})]))
        states = source.snapshot()
        role = {'role': 'living_room_presence', 'entity_id': 'zigbee2mqtt/Wohnzimmer Präsenz', 'friendly_name': 'Wohnzimmer Presence', 'source': 'zigbee2mqtt'}
        telemetry = generic_presence_telemetry_from_state(role, {'state': 'unknown'}, states)
        metrics = environmental_metrics_from_state(role, states)
        battery = find_battery_entity(role, states)
        self.assertEqual(telemetry['presence'], False)
        self.assertEqual(telemetry['motion'], 'None')
        self.assertEqual(metrics['temperature'], 23.2)
        self.assertEqual(metrics['humidity'], 44.0)
        self.assertEqual(metrics['illuminance'], 33.0)
        self.assertIsNotNone(battery)
        self.assertEqual(battery['state'], '100')

    def test_zigbee_presence_role_exposes_still_motion_state(self) -> None:
        ieee = '0xa4c1389a3e0a13e3'
        mqtt = SnapshotMqtt([FakeMessage(f'zigbee2mqtt/{ieee}', {'occupancy': True, 'motion_state': 'still', 'battery': 90})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'living_room_presence', 'room': 'living_room', 'entity_id': f'binary_sensor.{ieee}', 'device_id': ieee, 'friendly_name': 'Wohnzimmer Präsenz', 'device_class': 'occupancy', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            role = mapping.roles(include_state=True)[0]
        self.assertIs(role['presence'], True)
        self.assertEqual(role['motion'], 'Still')

    def test_environment_metrics_ignore_configuration_entities(self) -> None:
        role = {'device_id': '0xa4c1389a3e0a13e3', 'entity_id': 'binary_sensor.0xa4c1389a3e0a13e3'}
        states = [{'entity_id': 'number.0xa4c1389a3e0a13e3_temperature_calibration', 'domain': 'number', 'state': '0', 'device_id': '0xa4c1389a3e0a13e3'}, {'entity_id': 'number.0xa4c1389a3e0a13e3_humidity_calibration', 'domain': 'number', 'state': '0', 'device_id': '0xa4c1389a3e0a13e3'}, {'entity_id': 'number.0xa4c1389a3e0a13e3_illuminance_interval', 'domain': 'number', 'state': '3', 'device_id': '0xa4c1389a3e0a13e3'}, {'entity_id': 'sensor.0xa4c1389a3e0a13e3_temperature', 'domain': 'sensor', 'state': '22.9', 'device_class': 'temperature', 'device_id': '0xa4c1389a3e0a13e3'}, {'entity_id': 'sensor.0xa4c1389a3e0a13e3_humidity', 'domain': 'sensor', 'state': '56', 'device_class': 'humidity', 'device_id': '0xa4c1389a3e0a13e3'}, {'entity_id': 'sensor.0xa4c1389a3e0a13e3_illuminance', 'domain': 'sensor', 'state': '1104', 'device_class': 'illuminance', 'device_id': '0xa4c1389a3e0a13e3'}]
        metrics = environmental_metrics_from_state(role, states)
        self.assertEqual(metrics['temperature'], 22.9)
        self.assertEqual(metrics['humidity'], 56.0)
        self.assertEqual(metrics['illuminance'], 1104.0)

    def test_mqtt_discovery_stops_permit_join_when_candidate_is_found(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='entrance', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/Haustuer', {'contact': False})]
            found = manager.discovered(started['discovery_id'])
        self.assertEqual(found['status'], 'found')
        self.assertEqual(mqtt.published[-1], ('zigbee2mqtt/bridge/request/permit_join', {'value': False, 'time': 0}))

    def test_mqtt_discovery_stops_permit_join_on_timeout(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='entrance', duration=60)
            old = (datetime.now(timezone.utc) - timedelta(seconds=240)).isoformat(timespec='seconds')
            with mapping.connect() as con:
                con.execute('update sensor_discovery_sessions set started_at = ? where id = ?', (old, started['discovery_id']))
                con.commit()
            result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(mqtt.published[-1], ('zigbee2mqtt/bridge/request/permit_join', {'value': False, 'time': 0}))

    def test_cancel_discovery_stops_permit_join(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='entrance', duration=60)
            result = manager.cancel_discovery(started['discovery_id'])
        self.assertTrue(result['ok'])
        self.assertEqual(mqtt.published[-1], ('zigbee2mqtt/bridge/request/permit_join', {'value': False, 'time': 0}))

    def test_sensor_register_renames_zigbee2mqtt_device_before_saving(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='Keller', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/0xa4c13811eb64ffff', {'contact': False})]
            found = manager.discovered(started['discovery_id'])
            manager.register(found['sensor']['id'], started['discovery_id'], name='Keller Hobby Rechts', room_id='Keller')
            role = mapping.get_role('Keller_door', dev=True)
        self.assertEqual(mqtt.requests[0], ('zigbee2mqtt/bridge/request/device/rename', 'zigbee2mqtt/bridge/response/device/rename', {'from': '0xa4c13811eb64ffff', 'to': 'Keller Hobby Rechts'}))
        self.assertEqual(role['entity_id'], 'zigbee2mqtt/Keller Hobby Rechts')
        self.assertEqual(role['friendly_name'], 'Keller Hobby Rechts')

    def test_delete_zigbee2mqtt_sensor_removes_external_device_before_local_mapping(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/0xa4c13811eb64ffff', 'device_id': '0xa4c13811eb64ffff', 'friendly_name': 'Keller Hobby Rechts', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            result = mapping.delete_role('Keller_door')
            role = mapping.get_role('Keller_door', dev=True)
        self.assertTrue(result['deleted'])
        self.assertEqual(result['removal']['provider'], 'zigbee2mqtt')
        self.assertEqual(mqtt.requests[0], ('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', {'value': False, 'time': 0}))
        self.assertEqual(mqtt.requests[1], ('zigbee2mqtt/bridge/request/device/remove', 'zigbee2mqtt/bridge/response/device/remove', {'id': '0xa4c13811eb64ffff', 'force': 'true', 'block': 'false'}))
        self.assertIsNone(role)

    def test_delete_zigbee2mqtt_sensor_keeps_local_mapping_when_external_remove_fails(self) -> None:
        mqtt = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.sensor_source = NoNetworkSensorSource()
            mapping.mqtt = mqtt
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/0xa4c13811eb64ffff', 'device_id': '0xa4c13811eb64ffff', 'friendly_name': 'Keller Hobby Rechts', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            with self.assertRaises(RuntimeError):
                mapping.delete_role('Keller_door')
            role = mapping.get_role('Keller_door', dev=True)
        self.assertIsNotNone(role)

    def test_delete_zigbee2mqtt_sensor_local_only_skips_external_remove(self) -> None:
        mqtt = PermitJoinFailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/0xa4c13811eb64ffff', 'device_id': '0xa4c13811eb64ffff', 'friendly_name': 'Keller Hobby Rechts', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            result = mapping.delete_role('Keller_door', local_only=True)
            role = mapping.get_role('Keller_door', dev=True)
        self.assertTrue(result['deleted'])
        self.assertEqual(result['removal']['reason'], 'local_only')
        self.assertEqual(mqtt.requests, [])
        self.assertIsNone(role)

    def test_delete_zigbee2mqtt_sensor_does_not_remove_when_permit_join_stop_fails(self) -> None:
        mqtt = PermitJoinFailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/0xa4c13811eb64ffff', 'device_id': '0xa4c13811eb64ffff', 'friendly_name': 'Keller Hobby Rechts', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            with self.assertRaises(RuntimeError):
                mapping.delete_role('Keller_door')
            role = mapping.get_role('Keller_door', dev=True)
        self.assertIsNotNone(role)
        self.assertEqual(mqtt.requests, [('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', {'value': False, 'time': 0}), ('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', {'value': False, 'time': 0, 'device': None}), ('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', {'value': False}), ('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', False)])

    def test_delete_zigbee2mqtt_sensor_accepts_string_false_permit_join_response(self) -> None:
        mqtt = StringPermitJoinMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/0xa4c13811eb64ffff', 'device_id': '0xa4c13811eb64ffff', 'friendly_name': 'Keller Hobby Rechts', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            result = mapping.delete_role('Keller_door')
        self.assertTrue(result['deleted'])
        self.assertTrue(mqtt.assert_filter_result)
        self.assertEqual(mqtt.requests[0][0], 'zigbee2mqtt/bridge/request/permit_join')
        self.assertEqual(mqtt.requests[1][0], 'zigbee2mqtt/bridge/request/device/remove')

    def test_delete_zigbee2mqtt_sensor_retries_permit_join_stop_after_invalid_payload(self) -> None:
        mqtt = FullPermitJoinInvalidMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/0xa4c1381219fcffff', 'device_id': '0xa4c1381219fcffff', 'friendly_name': 'Keller Hobby Rechts', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            result = mapping.delete_role('Keller_door')
        self.assertTrue(result['deleted'])
        self.assertEqual(mqtt.requests[0], ('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', {'value': False, 'time': 0}))
        self.assertEqual(mqtt.requests[1], ('zigbee2mqtt/bridge/request/permit_join', 'zigbee2mqtt/bridge/response/permit_join', {'value': False, 'time': 0, 'device': None}))
        self.assertEqual(mqtt.requests[2][0], 'zigbee2mqtt/bridge/request/device/remove')

    def test_delete_zigbee2mqtt_sensor_prefers_ieee_over_stale_friendly_name(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/Keller Türkontakt', 'device_id': '0xa4c1381219fcffff', 'friendly_name': 'Keller Türkontakt2', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            result = mapping.delete_role('Keller_door')
        self.assertTrue(result['deleted'])
        self.assertEqual(mqtt.requests[1], ('zigbee2mqtt/bridge/request/device/remove', 'zigbee2mqtt/bridge/response/device/remove', {'id': '0xa4c1381219fcffff', 'force': 'true', 'block': 'false'}))

    def test_delete_zigbee2mqtt_sensor_fails_when_direct_mqtt_refused(self) -> None:
        mqtt = FailingMqtt()
        ieee = '0xa4c1389a3e0a13e3'
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.sensor_source = NoNetworkSensorSource()
            mapping.mqtt = mqtt
            mapping.snapshot = lambda: [{'entity_id': f'binary_sensor.{ieee}_presence', 'device_id': 'ha-device-id', 'friendly_name': 'Guest WC Presence Sensor Belegung', 'device_class': 'presence', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'identifiers': [['zigbee2mqtt', ieee]]}]
            mapping.upsert_role({'role': 'toilet_presence', 'room': 'toilet', 'entity_id': f'binary_sensor.{ieee}_presence', 'device_id': 'ha-device-id', 'friendly_name': 'Toilette Präsenz', 'device_class': 'presence', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            with self.assertRaises(RuntimeError):
                mapping.delete_role('toilet_presence')

    def test_delete_mqtt_mapping_with_ieee_uses_zigbee_remove_not_factory_reset(self) -> None:
        mqtt = SnapshotMqtt([])
        ieee = '0xa4c1389a3e0a13e3'
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'office_presence', 'room': 'office', 'entity_id': f'binary_sensor.{ieee}_presence', 'device_id': ieee, 'friendly_name': 'Arbeitszimmer Präsenz', 'device_class': 'presence', 'domain': 'binary_sensor', 'source': 'mqtt', 'confidence': 100})
            result = mapping.delete_role('office_presence')
        self.assertTrue(result['deleted'])
        request_topics = [request[0] for request in mqtt.requests]
        self.assertIn('zigbee2mqtt/bridge/request/device/remove', request_topics)
        self.assertNotIn(f'sentero/{ieee}/command', request_topics)

    def test_delete_zigbee2mqtt_sensor_does_not_try_child_entity_names(self) -> None:
        mqtt = RemoveFailingSnapshotMqtt([FakeMessage('zigbee2mqtt/Keller Türkontakt2', {'contact': False, 'battery': 100, 'voltage': 3000, 'tamper': False, 'linkquality': 80})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'Keller_door', 'room': 'Keller', 'entity_id': 'zigbee2mqtt/Keller Türkontakt', 'device_id': '0xa4c1381219fcffff', 'friendly_name': 'Keller Türkontakt2', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'zigbee2mqtt', 'confidence': 100})
            with self.assertRaises(RuntimeError):
                mapping.delete_role('Keller_door')
        remove_ids = [payload.get('id') for request_topic, _, payload in mqtt.requests if request_topic.endswith('/device/remove') and isinstance(payload, dict)]
        self.assertIn('0xa4c1381219fcffff', remove_ids)
        self.assertIn('Keller Türkontakt', remove_ids)
        self.assertIn('Keller Türkontakt2', remove_ids)
        self.assertNotIn('zigbee2mqtt/Keller Türkontakt', remove_ids)
        self.assertNotIn('zigbee2mqtt/Keller Türkontakt2', remove_ids)
        self.assertNotIn('keller_t_rkontakt2', remove_ids)
        self.assertNotIn('Keller Türkontakt2 Batterie', remove_ids)
        self.assertNotIn('Keller Türkontakt2 Spannung', remove_ids)
        self.assertNotIn('Keller Türkontakt2 Manipulation', remove_ids)

    def test_registered_mqtt_sensor_uses_discovery_cache_when_no_retained_state_exists(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='entrance', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/Haustuer', {'contact': False})]
            found = manager.discovered(started['discovery_id'])
            manager.register(found['sensor']['id'], started['discovery_id'], room_id='entrance')
            mqtt.messages = []
            result = mapping.test_role('main_door')
            role = mapping.roles(dev=True, include_state=True)[0]
        self.assertTrue(result['ok'])
        self.assertEqual(result['mode'], 'state_check')
        self.assertTrue(role['reachable'])

    def test_cached_mqtt_sensor_matches_topic_entity_and_ieee_identity(self) -> None:
        mqtt = SnapshotMqtt([])
        ieee = '0xa4c13811eb64ffff'
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='Keller', duration=60)
            mqtt.messages = [FakeMessage(f'zigbee2mqtt/{ieee}', {'contact': False, 'battery': 88})]
            found = manager.discovered(started['discovery_id'])
            manager.register(found['sensor']['id'], started['discovery_id'], room_id='Keller')
            mqtt.messages = []
            result = mapping.test_role('Keller_door')
            role = mapping.roles(include_state=True)[0]
            stored = mapping.get_role('Keller_door', dev=True)
        self.assertTrue(result['ok'])
        self.assertEqual(stored['entity_id'], f'zigbee2mqtt/{ieee}')
        self.assertEqual(result['entity_id'], f'binary_sensor.{ieee}')
        self.assertTrue(role['reachable'])

    def test_cached_mqtt_sensor_with_unknown_contact_but_telemetry_is_reachable(self) -> None:
        mqtt = SnapshotMqtt([])
        ieee = '0xa4c13811eb64ffff'
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='Keller', duration=60)
            mqtt.messages = [FakeMessage(f'zigbee2mqtt/{ieee}', {'contact': None, 'battery': 100, 'linkquality': 124, 'tamper': True})]
            found = manager.discovered(started['discovery_id'])
            manager.register(found['sensor']['id'], started['discovery_id'], room_id='Keller')
            mqtt.messages = []
            result = mapping.test_role('Keller_door')
            role = mapping.roles(include_state=True)[0]
        self.assertTrue(result['ok'])
        self.assertTrue(role['reachable'])
        self.assertEqual(role['battery_level'], 100)

    def test_mqtt_availability_offline_marks_presence_sensor_unreachable(self) -> None:
        device_id = 'c1001-test-01'
        mqtt = SnapshotMqtt([FakeMessage(f'sentero/{device_id}/state', {'device_id': device_id, 'firmware': '1.0.0-test', 'presence': True, 'signal_quality': 88}), FakeMessage(f'sentero/{device_id}/availability', {'device_id': device_id, 'firmware': '1.0.0-test', 'status': 'offline'})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            mapping.upsert_role({'role': 'keller_presence', 'room': 'keller', 'entity_id': f'sentero/{device_id}/state', 'device_id': device_id, 'friendly_name': 'Keller Präsenzsensor', 'device_class': 'presence', 'domain': 'binary_sensor', 'source': 'mqtt', 'confidence': 100})
            role = mapping.roles(include_state=True)[0]
        self.assertFalse(role['reachable'])
        self.assertEqual(role['state'], 'on')

    def test_mqtt_presence_sensor_exposes_usb_power_source(self) -> None:
        device_id = 'c1001-test-01'
        mqtt = SnapshotMqtt([FakeMessage(f'sentero/{device_id}/state', {'device_id': device_id, 'presence': True, 'power_source': 'usb', 'signal_quality': 88}), FakeMessage(f'sentero/{device_id}/availability', {'device_id': device_id, 'status': 'online'})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            upsert_esp32_presence_role(mapping, device_id)
            role = mapping.roles(dev=True, include_state=True)[0]
        self.assertEqual(role['power_source'], 'usb')
        self.assertIsNone(role['battery_level'])

    def test_mqtt_presence_sensor_exposes_c1001_telemetry(self) -> None:
        device_id = 'c1001-test-01'
        mqtt = SnapshotMqtt([FakeMessage(f'sentero/{device_id}/state', {'device_id': device_id, 'presence': True, 'fall_detected': False, 'motion': 'Still', 'power_source': 'usb', 'signal_quality': 88}), FakeMessage(f'sentero/{device_id}/availability', {'device_id': device_id, 'status': 'online'})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            upsert_esp32_presence_role(mapping, device_id)
            role = mapping.roles(include_state=True)[0]
        self.assertIs(role['presence'], True)
        self.assertIs(role['fall_detected'], False)
        self.assertEqual(role['motion'], 'Still')

    def test_mqtt_uuid_presence_availability_marks_sensor_reachable(self) -> None:
        device_id = '3be1ddd5-ddd6-45a2-a445-274be35449a9'
        mqtt = SnapshotMqtt(esp32_presence_messages(device_id, availability='online'))
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            upsert_esp32_presence_role(mapping, device_id)
            role = mapping.roles(include_state=True)[0]
        self.assertEqual(role['device_id'], device_id)
        self.assertTrue(role['reachable'])

    def test_mqtt_uuid_presence_ignores_old_retained_c1001_state(self) -> None:
        device_id = '3be1ddd5-ddd6-45a2-a445-274be35449a9'
        old_device_id = 'c1001-b16c33e0'
        mqtt = SnapshotMqtt([*esp32_presence_messages(old_device_id, availability='offline'), *esp32_presence_messages(device_id, availability='online')])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            upsert_esp32_presence_role(mapping, device_id)
            role = mapping.roles(dev=True, include_state=True)[0]
        self.assertEqual(role['device_id'], device_id)
        self.assertIn(device_id.replace('-', '_'), role['resolved_entity_id'])
        self.assertTrue(role['reachable'])

    def test_mqtt_discovery_uses_direct_mqtt_only(self) -> None:
        mqtt = SnapshotMqtt([])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='entrance', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/Haustuer', {'contact': False})]
            found = manager.discovered(started['discovery_id'])
        self.assertEqual(found['status'], 'found')
        self.assertEqual(found['sensor']['source_ref'], 'zigbee2mqtt/Haustuer')

    def test_mqtt_discovery_ignores_existing_sensor_state_changes(self) -> None:
        mqtt = SnapshotMqtt([FakeMessage('zigbee2mqtt/Keller', {'contact': False})])
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {'SENTERO_MQTT_BOOTSTRAP_EVENTS': ''}, clear=False):
            mapping = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            mapping.mqtt = mqtt
            mapping.sensor_source = Zigbee2MqttSensorSource(mqtt=mqtt)
            manager = SensorManager(mapping)
            started = manager.start_discovery('door_contact', room_id='Keller', duration=60)
            mqtt.messages = [FakeMessage('zigbee2mqtt/Keller', {'contact': True})]
            found = manager.discovered(started['discovery_id'])
        self.assertEqual(found['status'], 'searching')
        self.assertIsNone(found['sensor'])

    def test_delete_old_non_mqtt_mapping_only_deactivates_local_role(self) -> None:
        fake = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.mqtt = fake
            service.upsert_role({'role': 'main_door', 'room': 'entrance', 'entity_id': 'binary_sensor.alte_tuer', 'device_id': 'ha-device-1', 'friendly_name': 'Alte Tuer', 'device_class': 'opening', 'domain': 'binary_sensor', 'source': 'wizard', 'confidence': 100})
            result = service.delete_role('main_door')
            role = service.get_role('main_door', dev=True)
        self.assertTrue(result['deleted'])
        self.assertEqual(result['removal']['reason'], 'local_mapping_removed')
        self.assertIsNone(role)

    def test_mqtt_snapshot_accepts_sensor_event_rows(self) -> None:

        class MixedSource:

            def snapshot(self) -> list:
                return [SensorEvent(source='mqtt', sensor_id='binary_sensor.alte_tuer', role=None, room='entrance', state='off', changed_at='2026-06-25T13:00:00+00:00', metadata={'device_class': 'opening', 'friendly_name': 'Alte Tuer'})]
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.sensor_source = MixedSource()
            rows = service.snapshot()
        self.assertEqual(rows[0]['entity_id'], 'binary_sensor.alte_tuer')
        self.assertEqual(rows[0]['device_class'], 'opening')
        self.assertEqual(rows[0]['friendly_name'], 'Alte Tuer')

    def test_mqtt_home_status_does_not_load_sensor_snapshot(self) -> None:

        class FailingSnapshotSource:
            name = 'zigbee2mqtt'

            def configured(self) -> bool:
                return True

            def snapshot(self) -> list:
                raise RuntimeError('snapshot should not be called')
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.sensor_source = FailingSnapshotSource()
            status = service.home_status()
        self.assertEqual(status, {'connected': True, 'sensor_ready': True, 'system_ready': True})

    def test_zigbee_pairing_uses_zigbee2mqtt_permit_join_when_available(self) -> None:
        fake = FakeMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.mqtt = fake
            result = service.start_zigbee_pairing('living_room_presence', 'living_room', duration=60)
        self.assertEqual(result['status'], 'pairing_started')
        self.assertEqual(result['detail']['provider'], 'zigbee2mqtt')
        self.assertEqual(fake.published, [('zigbee2mqtt/bridge/request/permit_join', {'value': True, 'time': 60})])

    def test_zigbee_pairing_returns_mqtt_error_when_permit_join_unavailable(self) -> None:
        fake = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.mqtt = fake
            result = service.start_zigbee_pairing('living_room_presence', 'living_room', duration=60)
        self.assertEqual(result['status'], 'pairing_needs_manual_action')
        self.assertEqual(result['detail']['provider'], 'zigbee2mqtt')
        self.assertEqual(result['detail']['reason'], 'zigbee_pairing_unavailable')

    def test_zigbee_pairing_does_not_offer_fallback_when_mqtt_publish_fails(self) -> None:
        fake = FailingMqtt()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False):
            service = DeviceMappingService(database_path=Path(tmpdir) / 'sentero.db')
            service.sensor_source = NoNetworkSensorSource()
            service.mqtt = fake
            result = service.start_zigbee_pairing('living_room_presence', 'living_room', duration=60)
        self.assertEqual(result['status'], 'pairing_needs_manual_action')
        self.assertEqual(result['detail']['provider'], 'zigbee2mqtt')
        self.assertEqual(result['detail']['reason'], 'zigbee_pairing_unavailable')
if __name__ == '__main__':
    unittest.main()
