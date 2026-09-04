from __future__ import annotations

from tests.fakes import NoNetworkSensorSource
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from backend.services.device_mapping_service import DeviceMappingService, SensorTransport, mqtt_identity_values, normalize_snapshot_item, role_candidate_matches, score_candidates, testable_state_entity
from backend.services.sensor_manager import SensorManager

class FakeMessage:

    def __init__(self, payload: dict) -> None:
        self.payload = payload

class FakeMqtt:

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []
        self.requests: list[tuple[str, str, object]] = []

    def publish(self, topic: str, payload: object) -> dict:
        self.published.append((topic, payload))
        return {'ok': True, 'topic': topic, 'payload': payload}

    def request_response(self, request_topic: str, response_topic: str, payload: object, timeout: float, response_filter) -> FakeMessage:
        self.requests.append((request_topic, response_topic, payload))
        response = {'status': 'ok', 'data': payload}
        return FakeMessage(response)

    def client_available(self) -> bool:
        return True

class FakeSource:
    name = 'zigbee2mqtt'

    def __init__(self, rows: list[dict] | None=None) -> None:
        self.rows = rows or []

    def configured(self) -> bool:
        return True

    def snapshot(self) -> list[dict]:
        return self.rows

class SensorOnboardingTests(unittest.TestCase):

    def test_domain_is_preserved_for_slash_entity_ids(self) -> None:
        item = {"entity_id": "zigbee2mqtt/Kueche Presence", "domain": "binary_sensor", "state": "online"}

        self.assertEqual(normalize_snapshot_item(item)["domain"], "binary_sensor")
        self.assertTrue(testable_state_entity(item))

    def test_presence_setup_uses_zigbee_discovery_by_default(self) -> None:
        manager, mapping, source, mqtt = self.manager()
        result = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        self.assertEqual(result['status'], 'searching')
        self.assertEqual(result['transport'], SensorTransport.ZIGBEE.value)
        self.assertEqual(result['expires_in_seconds'], 120)
        self.assertEqual(mqtt.published[0][0], 'zigbee2mqtt/bridge/request/permit_join')

    def test_door_setup_uses_zigbee_discovery(self) -> None:
        manager, _mapping, _source, mqtt = self.manager()
        result = manager.start_discovery('door_contact', room_id='hallway', role='main_door')
        self.assertEqual(result['status'], 'searching')
        self.assertEqual(result['transport'], SensorTransport.ZIGBEE.value)
        self.assertEqual(mqtt.published[0][0], 'zigbee2mqtt/bridge/request/permit_join')

    def test_pairing_timeout_closes_join_window(self) -> None:
        manager, mapping, _source, mqtt = self.manager()
        started = manager.start_discovery('door_contact', room_id='hallway', role='main_door', duration=10)
        old = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat(timespec='seconds')
        with mapping.connect() as con:
            con.execute('update sensor_discovery_sessions set started_at = ? where id = ?', (old, started['discovery_id']))
            con.commit()
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'not_found')
        self.assertTrue(any((payload in (False, {'value': False}) or (isinstance(payload, dict) and payload.get('value') is False) for _topic, payload in mqtt.published)))

    def test_single_presence_device_with_multiple_entities_is_assigned_to_room(self) -> None:
        manager, mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        source.rows = [zigbee_entity('binary_sensor.hallway_presence_occupancy', 'occupancy', 'on', '0xaaa'), zigbee_entity('sensor.hallway_presence_temperature', 'temperature', '21', '0xaaa'), zigbee_entity('sensor.hallway_presence_humidity', 'humidity', '45', '0xaaa')]
        found = manager.discovered(started['discovery_id'])
        registered = manager.register(found['sensor']['id'], started['discovery_id'], name='Flur Präsenz', room_id='hallway', dev=True)
        self.assertEqual(found['status'], 'found')
        self.assertEqual(registered['sensor']['room_id'], 'hallway')
        role = mapping.get_role('hallway_presence', dev=True)
        self.assertEqual(role['transport'], SensorTransport.ZIGBEE.value)
        self.assertEqual(role['sensor_type'], 'presence')
        self.assertIn('sensor.hallway_presence_temperature', json.loads(role['entity_ids_json']))
        with mapping.connect() as con:
            device = con.execute('select * from sensor_devices where room_id = ? and sensor_type = ? and enabled = 1', ('hallway', 'presence')).fetchone()
        self.assertIsNotNone(device)
        self.assertEqual(device['device_id'], '0xaaa')

    def test_multiple_found_devices_are_returned_once_per_physical_device(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        source.rows = [zigbee_entity('binary_sensor.hallway_presence_occupancy', 'occupancy', 'on', '0xaaa'), zigbee_entity('sensor.hallway_presence_temperature', 'temperature', '21', '0xaaa'), zigbee_entity('binary_sensor.kitchen_presence_occupancy', 'occupancy', 'on', '0xbbb')]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'found')
        self.assertEqual(len(result['devices']), 2)

    def test_existing_unassigned_sensor_is_not_new_mqtt_candidate(self) -> None:
        manager, mapping, source, mqtt = self.manager()
        source.rows = [zigbee_entity('binary_sensor.sensor_a_occupancy', 'occupancy', 'off', '0xAAA')]
        started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        self.assertEqual(started['status'], 'existing_device_found')
        self.assertEqual(started['devices'][0]['id'], '0xaaa')
        self.assertEqual(mqtt.published, [])
        with self.assertRaises(ValueError):
            mapping.confirm(started['discovery_id'], '0xAAA', name='Flur Präsenz', room='hallway')
        self.assertEqual(mqtt.requests, [])

    def test_existing_sensor_updates_are_not_new_mqtt_candidates(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        source.rows = [zigbee_entity('binary_sensor.sensor_a_occupancy', 'occupancy', 'off', '0xAAA')]
        started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        self.assertEqual(started['status'], 'existing_device_found')

    def test_only_new_physical_zigbee_device_is_accepted(self) -> None:
        manager, mapping, source, _mqtt = self.manager()
        source.rows = [zigbee_entity('binary_sensor.sensor_a_occupancy', 'occupancy', 'off', '0xAAA')]
        started = mapping.start_mqtt_discovery('hallway_presence', 'hallway', duration=60, sensor_type='presence')
        source.rows = [zigbee_entity('binary_sensor.sensor_a_occupancy', 'occupancy', 'on', '0xAAA'), zigbee_entity('binary_sensor.sensor_b_occupancy', 'occupancy', 'on', '0xBBB')]
        found = manager.discovered(started['session_id'], dev=True)
        registered = manager.register(found['sensor']['id'], started['session_id'], name='Flur Präsenz', room_id='hallway', dev=True)
        role = mapping.get_role('hallway_presence', dev=True)
        self.assertEqual(found['status'], 'found')
        self.assertEqual(found['sensor']['id'], '0xbbb')
        self.assertEqual(registered['status'], 'registered')
        self.assertEqual(role['device_id'], '0xBBB')

    def test_manipulated_confirm_rejects_baseline_device(self) -> None:
        _manager, mapping, source, _mqtt = self.manager()
        source.rows = [zigbee_entity('binary_sensor.sensor_a_occupancy', 'occupancy', 'off', '0xAAA')]
        started = mapping.start_mqtt_discovery('hallway_presence', 'hallway', duration=60, sensor_type='presence')
        source.rows = [zigbee_entity('binary_sensor.sensor_a_occupancy', 'occupancy', 'on', '0xAAA'), zigbee_entity('binary_sensor.sensor_b_occupancy', 'occupancy', 'on', '0xBBB')]
        mapping.candidates(started['session_id'], dev=True)
        with self.assertRaises(ValueError):
            mapping.confirm(started['session_id'], 'binary_sensor.sensor_a_occupancy', name='Flur Präsenz', room='hallway')

    def test_wrong_device_type_is_not_returned_for_presence(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        source.rows = [zigbee_entity('sensor.hallway_battery', 'battery', '100', '0xaaa')]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'unsupported_device_found')
        self.assertIsNone(result['sensor'])

    def test_door_sensor_is_detected_by_device_class(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('door_contact', room_id='hallway', role='main_door')
        source.rows = [zigbee_entity('binary_sensor.front_door_contact', 'opening', 'on', '0xdoor')]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'found')
        self.assertEqual(result['sensor']['type'], 'door_contact')

    def test_door_search_with_new_smoke_detector_returns_wrong_type(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('door_contact', room_id='kitchen', role='kitchen_door')
        source.rows = [zigbee_entity('binary_sensor.kitchen_smoke', 'smoke', 'off', '0xsmoke', payload_key='smoke')]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'wrong_type_found')
        self.assertEqual(result['detected_type'], 'smoke_detector')
        self.assertIsNone(result['sensor'])

    def test_confirm_rejects_wrong_device_type_server_side(self) -> None:
        _manager, mapping, source, _mqtt = self.manager()
        started = mapping.start_mqtt_discovery('kitchen_door', 'kitchen', duration=60, sensor_type='door')
        source.rows = [zigbee_entity('binary_sensor.kitchen_smoke', 'smoke', 'off', '0xsmoke', payload_key='smoke')]
        mapping.candidates(started['session_id'], dev=True)
        with self.assertRaises(ValueError):
            mapping.confirm(started['session_id'], '0xsmoke', name='Küche Tür', room='kitchen')

    def test_smoke_detector_is_found_for_smoke_search(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('smoke_detector', room_id='kitchen', role='kitchen_smoke')
        source.rows = [zigbee_entity('binary_sensor.kitchen_smoke', 'smoke', 'off', '0xsmoke', payload_key='smoke')]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'found')
        self.assertEqual(result['sensor']['type'], 'smoke_detector')

    def test_existing_unassigned_smoke_detector_is_offered_without_pairing(self) -> None:
        manager, _mapping, source, mqtt = self.manager()
        source.rows = [zigbee_entity('binary_sensor.kitchen_smoke', 'smoke', 'off', '0xsmoke', payload_key='smoke')]
        result = manager.start_discovery('smoke_detector', room_id='kitchen', role='kitchen_smoke')
        self.assertEqual(result['status'], 'existing_device_found')
        self.assertEqual(result['devices'][0]['id'], '0xsmoke')
        self.assertEqual(mqtt.published, [])

    def test_multiple_unassigned_smoke_detectors_are_listed(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        source.rows = [
            zigbee_entity('binary_sensor.kitchen_smoke', 'smoke', 'off', '0xsmoke1', payload_key='smoke'),
            zigbee_entity('binary_sensor.hall_smoke', 'smoke', 'off', '0xsmoke2', payload_key='smoke'),
        ]
        result = manager.start_discovery('smoke_detector', room_id='kitchen', role='kitchen_smoke')
        self.assertEqual(result['status'], 'existing_device_found')
        self.assertEqual(len(result['devices']), 2)
        self.assertIsNone(result['device'])

    def test_supported_wrong_type_is_stored_unassigned(self) -> None:
        manager, mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('door_contact', room_id='kitchen', role='kitchen_door')
        source.rows = [zigbee_entity('binary_sensor.kitchen_smoke', 'smoke', 'off', '0xsmoke', payload_key='smoke')]
        manager.discovered(started['discovery_id'])
        row = mapping.inventory_device('0xsmoke')
        self.assertEqual(row['lifecycle_status'], 'unassigned')
        self.assertEqual(row['detected_type'], 'smoke_detector')

    def test_unsupported_device_found(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('door_contact', room_id='kitchen', role='kitchen_door')
        source.rows = [zigbee_entity('sensor.kitchen_temperature', 'temperature', '22', '0xtemp', payload_key='temperature')]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'unsupported_device_found')

    def test_unsupported_remove_uses_zigbee_remove_request(self) -> None:
        manager, mapping, source, mqtt = self.manager()
        source.rows = [zigbee_entity('sensor.kitchen_temperature', 'temperature', '22', '0xtemp', payload_key='temperature')]
        mapping.sync_zigbee_inventory(source.rows)
        result = manager.remove_unassigned('0xtemp')
        self.assertTrue(result['deleted'])
        self.assertTrue(any('bridge/request/device/remove' in request[0] for request in mqtt.requests))

    def test_unsupported_keep_marks_ignored_and_is_not_warned_again(self) -> None:
        manager, mapping, source, _mqtt = self.manager()
        source.rows = [zigbee_entity('sensor.kitchen_temperature', 'temperature', '22', '0xtemp', payload_key='temperature')]
        mapping.sync_zigbee_inventory(source.rows)
        manager.ignore_unassigned('0xtemp')
        started = manager.start_discovery('door_contact', room_id='kitchen', role='kitchen_door')
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'searching')

    def test_interviewing_device_is_not_unsupported(self) -> None:
        manager, _mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('door_contact', room_id='kitchen', role='kitchen_door')
        source.rows = [zigbee_entity('sensor.new_device', '', 'unknown', '0xnew', payload_key='state', attrs={'interview_completed': False})]
        result = manager.discovered(started['discovery_id'])
        self.assertEqual(result['status'], 'searching')

    def test_restart_keeps_existing_sensor_assignment(self) -> None:
        manager, mapping, source, _mqtt = self.manager()
        started = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        source.rows = [zigbee_entity('binary_sensor.hallway_presence_occupancy', 'occupancy', 'on', '0xaaa')]
        found = manager.discovered(started['discovery_id'])
        manager.register(found['sensor']['id'], started['discovery_id'], name='Flur Präsenz', room_id='hallway', dev=True)
        restarted = DeviceMappingService(database_path=mapping.database_path)
        restarted.sensor_source = NoNetworkSensorSource()
        role = restarted.get_role('hallway_presence', dev=True)
        self.assertEqual(role['room'], 'hallway')
        self.assertEqual(role['transport'], SensorTransport.ZIGBEE.value)
        self.assertEqual(role['device_id'], '0xaaa')

    def test_sensor_replace_keeps_old_mapping_until_new_pairing_succeeds(self) -> None:
        manager, mapping, source, _mqtt = self.manager()
        first = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        source.rows = [zigbee_entity('binary_sensor.hallway_presence_occupancy', 'occupancy', 'on', '0xaaa')]
        found = manager.discovered(first['discovery_id'])
        manager.register(found['sensor']['id'], first['discovery_id'], name='Alt', room_id='hallway', dev=True)
        second = manager.start_discovery('presence', room_id='hallway', role='hallway_presence')
        role_before = mapping.get_role('hallway_presence', dev=True)
        self.assertEqual(role_before['device_id'], '0xaaa')
        manager.cancel_discovery(second['discovery_id'])
        self.assertEqual(mapping.get_role('hallway_presence', dev=True)['device_id'], '0xaaa')

    def test_presence_classes_include_target_entities(self) -> None:
        self.assertTrue(role_candidate_matches('hallway_presence', {'domain': 'binary_sensor', 'device_class': 'moving_target', 'entity_id': 'binary_sensor.mmwave_moving_target'}))
        self.assertTrue(role_candidate_matches('hallway_presence', {'domain': 'binary_sensor', 'device_class': 'static_target', 'entity_id': 'binary_sensor.mmwave_static_target'}))

    def test_active_presence_mapping_is_not_offered_for_another_room(self) -> None:
        assigned = mqtt_identity_values({'role': 'living_room_presence', 'entity_id': 'binary_sensor.guest_wc_presence_sensor_presence', 'friendly_name': 'Wohnzimmer Präsenz'})
        current = [{'entity_id': 'binary_sensor.guest_wc_presence_sensor_presence', 'domain': 'binary_sensor', 'state': 'off', 'friendly_name': 'Presence Sensor Belegung', 'device_class': 'occupancy', 'last_changed': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'last_updated': datetime.now(timezone.utc).isoformat(timespec='seconds')}]
        scored = score_candidates([], current, 'kitchen_presence', 'kitchen', datetime.now(timezone.utc), assigned_identities=assigned)
        self.assertEqual(scored, [])

    def manager(self) -> tuple[SensorManager, DeviceMappingService, FakeSource, FakeMqtt]:
        path = Path(tempfile.mkdtemp(dir='/private/tmp')) / 'sentero.db'
        mapping = DeviceMappingService(database_path=path)
        mapping.sensor_source = NoNetworkSensorSource()
        source = FakeSource()
        mqtt = FakeMqtt()
        mapping.source_mode = 'mqtt'
        mapping.sensor_source = source
        mapping.mqtt = mqtt
        manager = SensorManager(mapping)
        return (manager, mapping, source, mqtt)

def zigbee_entity(entity_id: str, device_class: str, state: str, device_id: str, payload_key: str | None = None, attrs: dict | None = None) -> dict:
    attributes = {'device_class': device_class, 'device_id': device_id, **(attrs or {})}
    if payload_key:
        attributes[payload_key] = state
    return {'entity_id': entity_id, 'domain': entity_id.split('.', 1)[0], 'state': state, 'friendly_name': entity_id.rsplit('.', 1)[-1].replace('_', ' ').title(), 'device_class': device_class, 'device_id': device_id.lower(), 'payload_key': payload_key, 'identifiers': [['zigbee2mqtt', device_id.lower()]], 'source': 'zigbee2mqtt', 'source_ref': f'zigbee2mqtt/{device_id.lower()}', 'attributes': attributes, 'last_changed': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'last_updated': datetime.now(timezone.utc).isoformat(timespec='seconds')}
if __name__ == '__main__':
    unittest.main()
