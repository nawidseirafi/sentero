from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from backend.config import config_int, config_str
from backend.paths import DATA_DIR
from backend.logging_config import get_logger
from backend.services.ecotracker_service import EcoTrackerClient, ecotracker_snapshot_rows
from backend.sensor_sources.base import create_sensor_source
from backend.services.mqtt_service import MqttService

DB_PATH = DATA_DIR / 'sentero.db'
DB_TIMEOUT_SECONDS = 30
DISCOVERY_TIMEOUT_SECONDS = 180
DISCOVERY_CONFIDENCE_THRESHOLD = 50
PRESENCE_CLASSES = {'occupancy', 'motion', 'presence', 'moving_target', 'static_target'}
CONTACT_CLASSES = {'door', 'window', 'opening', 'contact'}
SMART_METER_CLASSES = {'energy', 'power', 'water', 'gas'}
DEFAULT_PRESENCE_LIVE_STATE_TTL_SECONDS = 300
DEFAULT_PRESENCE_UNREACHABLE_GRACE_SECONDS = 900
DEFAULT_SENSOR_HEALTH_STALE_SECONDS = 172800
DEFAULT_SENSOR_UNREACHABLE_GRACE_SECONDS = 604800
SMART_METER_KEYS = {
    'energy',
    'energy_consumption',
    'electricity',
    'electricity_consumption',
    'power',
    'power_usage',
    'water',
    'water_consumption',
    'gas',
    'gas_consumption',
}
logger = get_logger(__name__)


class SensorTransport(str, Enum):
    ZIGBEE = "zigbee"
    WIFI_ESPHOME = "wifi_esphome"
ROOM_TERMS = {
    'living_room': ['wohnzimmer', 'living', 'living_room'],
    'kitchen': ['kueche', 'küche', 'kitchen'],
    'bathroom': ['bad', 'bathroom', 'wc'],
    'bedroom': ['schlafzimmer', 'bedroom'],
    'hallway': ['flur', 'hallway', 'diele'],
    'entrance': ['eingang', 'tuer', 'tür', 'door', 'front'],
}
ROOM_LABELS = {
    'living_room': 'Wohnzimmer',
    'kitchen': 'Küche',
    'bathroom': 'Bad',
    'bedroom': 'Schlafzimmer',
    'hallway': 'Flur',
    'entrance': 'Eingang',
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def configure_sqlite_connection(con: sqlite3.Connection) -> None:
    con.execute('pragma busy_timeout = 30000')
    con.execute('pragma journal_mode = WAL')
    con.execute('pragma foreign_keys = ON')


class DeviceMappingService:
    def __init__(self, database_path: Path | None = None, **_: Any) -> None:
        self.database_path = database_path or DB_PATH
        self.source_mode = sensor_source_mode()
        # One shared MQTT service is important: the sensor source owns the live
        # subscription/cache while mapping commands use the same broker settings.
        self.mqtt = MqttService(database_path=self.database_path)
        self.sensor_source = create_sensor_source(self.mqtt)
        self.ensure_schema()
        logger.debug(
            "Device mapping service initialized",
            extra={"component": "device_mapping", "sensor_source": self.source_mode, "database_path": str(self.database_path)},
        )

    @contextmanager
    def connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("Database connection opening", extra={"component": "database", "database_path": str(self.database_path)})
        con = sqlite3.connect(self.database_path, timeout=DB_TIMEOUT_SECONDS)
        try:
            con.row_factory = sqlite3.Row
            configure_sqlite_connection(con)
            yield con
        finally:
            con.close()

    def ensure_schema(self) -> None:
        with self.connect() as con:
            ensure_schema(con)
            con.commit()

    def home_status(self) -> dict[str, bool]:
        if not self.sensor_source.configured() or not self.mqtt.client_available():
            return {'connected': False, 'sensor_ready': False, 'system_ready': False}
        return {'connected': True, 'sensor_ready': True, 'system_ready': True}

    def start_mqtt_listener(self) -> None:
        topics_fn = getattr(self.sensor_source, 'subscription_topics', None)
        topics = topics_fn() if callable(topics_fn) else [f"{self._zigbee2mqtt_topic('')}#"]
        self.mqtt.start_listener(topics)

    def stop_mqtt_listener(self) -> None:
        self.mqtt.stop_listener()

    def roles(self, dev: bool = False, include_state: bool = False) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute('select * from sensor_roles where active = 1 order by room, role').fetchall()
        valid_rows = [dict(row) for row in rows if role_candidate_matches(str(row['role'] or ''), dict(row), allow_missing_device_class=True)]
        if include_state:
            valid_rows = self._attach_state(valid_rows)
        return valid_rows if dev else [public_role(row) for row in valid_rows]

    def get_entity_for_role(self, role: str) -> str | None:
        with self.connect() as con:
            rows = con.execute('select * from sensor_roles where role = ? and active = 1 order by id desc', (role,)).fetchall()
        for row in rows:
            data = dict(row)
            if role_candidate_matches(role, data, allow_missing_device_class=True):
                return data['entity_id']
        return None

    def start_pairing(self, role: str, room: str | None, pairing_code: str | None = None) -> dict[str, Any]:
        try:
            baseline = self.snapshot()
        except Exception:
            logger.exception("Sentero discovery baseline failed")
            raise
        started_at = now()
        status = 'waiting_for_signal'
        message = 'Bitte aktivieren Sie den Sensor jetzt einmal.'
        detail = None
        with self.connect() as con:
            cur = con.execute(
                '''insert into sensor_discovery_sessions
                   (target_role, target_room, started_at, status, baseline_snapshot_json, pairing_code_provided, pairing_detail_json)
                   values (?, ?, ?, ?, ?, ?, ?)''',
                (role, room, started_at, status, json.dumps(baseline, ensure_ascii=False), 0, json.dumps(detail, ensure_ascii=False) if detail else None),
            )
            con.commit()
            session_id = int(cur.lastrowid)
        logger.info(
            "Sentero discovery start session=%s role=%s room=%s baseline_states=%s status=%s",
            session_id,
            role,
            room,
            len(baseline),
            status,
        )
        return {'session_id': session_id, 'status': status, 'message': message, 'detail': detail}

    def start_zigbee_pairing(self, role: str, room: str | None, duration: int = 60, sensor_type: str | None = None) -> dict[str, Any]:
        duration = min(max(int(duration or 60), 10), 300)
        try:
            baseline = self.snapshot()
        except Exception:
            logger.exception("Sentero pairing baseline failed")
            raise
        baseline_device_ids = sorted(stable_physical_device_ids(baseline))
        detail = self._open_zigbee_permit_join(duration)
        status = 'pairing_started' if detail.get('ok') else 'pairing_needs_manual_action'
        message = (
            'Sensor-Suche gestartet. Bitte aktivieren Sie den Sensor jetzt.'
            if detail.get('ok')
            else str(detail.get('message') or 'Zigbee2MQTT Permit Join ist nicht verfügbar.')
        )
        with self.connect() as con:
            cur = con.execute(
                '''insert into sensor_discovery_sessions
                   (target_role, target_room, target_sensor_type, target_transport, timeout_seconds,
                    started_at, status, baseline_snapshot_json, baseline_device_ids_json, pairing_code_provided, pairing_detail_json)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    role,
                    room,
                    sensor_type,
                    SensorTransport.ZIGBEE.value,
                    duration,
                    now(),
                    status,
                    json.dumps(baseline, ensure_ascii=False),
                    json.dumps(baseline_device_ids, ensure_ascii=False),
                    0,
                    json.dumps(detail, ensure_ascii=False),
                ),
            )
            con.commit()
            session_id = int(cur.lastrowid)
        logger.info(
            "Sentero pairing start session=%s role=%s room=%s baseline_states=%s status=%s provider=%s",
            session_id,
            role,
            room,
            len(baseline),
            status,
            detail.get('provider'),
        )
        if not detail.get('ok'):
            logger.warning("Sentero pairing unavailable session=%s detail=%s", session_id, detail)
        return {'session_id': session_id, 'status': status, 'message': message, 'detail': detail}

    def start_mqtt_discovery(self, role: str, room: str | None, duration: int = 180, sensor_type: str | None = None) -> dict[str, Any]:
        duration = min(max(int(duration or DISCOVERY_TIMEOUT_SECONDS), 10), 300)
        try:
            baseline = self._mqtt_snapshot()
        except Exception:
            logger.exception("MQTT discovery baseline failed", extra={"component": "wizard", "sensor_source": self.source_mode})
            baseline = []
        baseline_device_ids = sorted(stable_physical_device_ids(baseline))
        permit_join = self._open_zigbee_permit_join(duration)
        detail = {
            **permit_join,
            'provider': permit_join.get('provider') or 'zigbee2mqtt',
            'mode': 'mqtt_discovery',
            'duration': duration,
        }
        status = 'pairing_started' if permit_join.get('ok') else 'waiting_for_signal'
        with self.connect() as con:
            cur = con.execute(
                '''insert into sensor_discovery_sessions
                   (target_role, target_room, target_sensor_type, target_transport, timeout_seconds,
                    started_at, status, baseline_snapshot_json, pairing_code_provided, pairing_detail_json)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    role,
                    room,
                    sensor_type,
                    SensorTransport.ZIGBEE.value,
                    duration,
                    now(),
                    status,
                    json.dumps(baseline, ensure_ascii=False),
                    0,
                    json.dumps(detail, ensure_ascii=False),
                ),
            )
            con.commit()
            session_id = int(cur.lastrowid)
        logger.info(
            "MQTT sensor discovery started",
            extra={
                "component": "wizard",
                "session_id": session_id,
                "role": role,
                "room_id": room,
                "sensor_source": self.source_mode,
                "baseline_states": len(baseline),
                "baseline_device_ids": baseline_device_ids,
                "permit_join": bool(permit_join.get('ok')),
            },
        )
        return {
            'session_id': session_id,
            'status': status,
            'message': 'Sensor-Suche gestartet. Bitte aktivieren Sie den Sensor jetzt.',
            'detail': detail,
        }

    def candidates(self, session_id: int, dev: bool = False) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute('select * from sensor_discovery_sessions where id = ?', (session_id,)).fetchone()
        if not row:
            raise ValueError('session not found')
        started_at = parse_time(row['started_at'])
        elapsed_seconds = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0)
        timeout_seconds = int(row['timeout_seconds'] or DISCOVERY_TIMEOUT_SECONDS)
        if row['status'] == 'pairing_needs_manual_action':
            logger.info(
                "Sentero discovery poll session=%s skipped status=pairing_needs_manual_action",
                session_id,
            )
            return {
                'session_id': session_id,
                'status': 'no_signal_detected',
                'message': 'Der Sensor konnte nicht verbunden werden. Bitte erneut versuchen.',
                'candidate': None,
                'candidates': [],
                'elapsed_seconds': elapsed_seconds,
                'remaining_seconds': 0,
            }
        detail = discovery_detail(row)
        mqtt_discovery = detail.get('mode') == 'mqtt_discovery'
        require_new_device = discovery_requires_new_physical_device(row, detail)
        baseline = json.loads(row['baseline_snapshot_json'] or '[]')
        cached_candidate_snapshot = json.loads(row['candidate_snapshot_json'] or '[]')
        if row['status'] in {'found', 'signal_detected', 'completed', 'confirmed'} and cached_candidate_snapshot:
            current = cached_candidate_snapshot
        else:
            current = self._mqtt_snapshot() if mqtt_discovery else self.snapshot()
        current_device_ids = stable_physical_device_ids(current)
        baseline_device_ids = stable_physical_device_ids(baseline)
        new_device_ids = current_device_ids - baseline_device_ids
        assigned_identities = self._assigned_sensor_identities()
        if mqtt_discovery:
            self._log_discovery_device_sets(int(session_id), baseline, current)
        scored = score_candidates(
            baseline,
            current,
            row['target_role'],
            row['target_room'],
            row['started_at'],
            require_new=require_new_device,
            assigned_identities=assigned_identities,
            discovery_session=int(session_id),
        )
        raw_changed_count = count_changed_entities(baseline, current, row['started_at'])
        changed_count = len(scored)
        best_scored = scored[0] if scored else None
        best = best_scored if best_scored and best_scored['confidence'] >= DISCOVERY_CONFIDENCE_THRESHOLD else None
        timed_out = elapsed_seconds >= timeout_seconds
        status = 'found' if best else 'no_signal_detected' if timed_out else 'waiting_for_signal'
        message = (
            'Sensor-Signal erkannt.'
            if best
            else 'Wir konnten den Sensor nicht eindeutig erkennen. Bitte erneut versuchen.'
            if timed_out
            else 'Wir warten noch auf ein eindeutiges Sensorsignal.'
        )
        with self.connect() as con:
            con.execute(
                '''update sensor_discovery_sessions
                   set ended_at = ?, status = ?, candidate_snapshot_json = ?,
                       current_device_ids_json = ?, new_device_ids_json = ?
                   where id = ?''',
                (
                    now() if best or timed_out else None,
                    status,
                    json.dumps(current, ensure_ascii=False),
                    json.dumps(sorted(current_device_ids), ensure_ascii=False),
                    json.dumps(sorted(new_device_ids), ensure_ascii=False),
                    session_id,
                ),
            )
            con.commit()
        stop_detail = None
        if best or timed_out:
            stop_detail = self.stop_zigbee_pairing(session_id=session_id, reason='found' if best else 'timeout')
        logger.info(
            "Sentero discovery poll session=%s baseline_states=%s current_states=%s raw_changed=%s changed_entities=%s best=%s best_score=%s status=%s elapsed=%.1f",
            session_id,
            len(baseline),
            len(current),
            raw_changed_count,
            changed_count,
            best_scored.get('entity_id') if best_scored else None,
            best_scored.get('confidence') if best_scored else None,
            status,
            elapsed_seconds,
        )
        if scored:
            logger.info(
                "Sentero discovery candidates session=%s candidates=%s",
                session_id,
                [
                    {
                        'entity_id': item.get('entity_id'),
                        'score': item.get('confidence'),
                        'reasons': item.get('reasons', []),
                        'new_device': item.get('is_new_device'),
                        'new_entity': item.get('is_new'),
                        'device_id': item.get('device_id'),
                        'device_class': item.get('device_class'),
                        'model': item.get('model'),
                        'domain': item.get('domain'),
                    }
                    for item in scored[:5]
                ],
            )
        public_candidates = [candidate_public(item, dev) for item in scored[:5]] if dev else []
        return {
            'session_id': session_id,
            'status': status,
            'message': message,
            'candidate': candidate_public(best, dev) if best else None,
            'candidates': public_candidates,
            'elapsed_seconds': elapsed_seconds,
            'remaining_seconds': max(timeout_seconds - elapsed_seconds, 0),
            'changed_count': changed_count if dev else None,
            'current_state_count': len(current) if dev else None,
            'baseline_state_count': len(baseline) if dev else None,
            'pairing_stopped': stop_detail if dev else None,
        }

    def confirm(self, session_id: int, entity_id: str, name: str | None = None, room: str | None = None, dev: bool = False) -> dict[str, Any]:
        with self.connect() as con:
            session = con.execute('select * from sensor_discovery_sessions where id = ?', (session_id,)).fetchone()
        if not session:
            raise ValueError('session not found')
        self.stop_zigbee_pairing(session_id=session_id, reason='register')
        baseline = json.loads(session['baseline_snapshot_json'] or '[]')
        detail = discovery_detail(session)
        mqtt_discovery = detail.get('mode') == 'mqtt_discovery'
        require_new_device = discovery_requires_new_physical_device(session, detail)
        current = json.loads(session['candidate_snapshot_json'] or '[]') or (self._mqtt_snapshot() if mqtt_discovery else self.snapshot())
        assigned_identities = self._assigned_sensor_identities()
        if mqtt_discovery:
            self._log_discovery_device_sets(int(session_id), baseline, current)
        scored = score_candidates(
            baseline,
            current,
            session['target_role'],
            session['target_room'],
            session['started_at'],
            require_new=require_new_device,
            assigned_identities=assigned_identities,
            discovery_session=int(session_id),
        )
        entity = next(
            (
                item for item in scored
                if candidate_id_matches(item, entity_id)
                and item.get('confidence', 0) >= DISCOVERY_CONFIDENCE_THRESHOLD
            ),
            None,
        )
        if not entity:
            raise ValueError('entity does not match this pairing session')
        if require_new_device:
            selected_device_id = stable_physical_device_id(entity)
            baseline_device_ids = stable_physical_device_ids(baseline)
            if not selected_device_id or selected_device_id in baseline_device_ids:
                logger.warning(
                    "Discovery confirm rejected baseline device",
                    extra={
                        "component": "wizard",
                        "discovery_session": int(session_id),
                        "selected_device_id": selected_device_id,
                        "baseline_device_ids": sorted(baseline_device_ids),
                    },
                )
                raise ValueError("Device existed before this discovery session and cannot be registered as a newly paired sensor.")
        attrs = entity.get('attributes') or {}
        target_room = str(room or session['target_room'] or '').strip() or None
        desired_name = str(name or '').strip() or attrs.get('friendly_name') or entity.get('friendly_name') or 'Sensor'
        source = str(entity.get('source') or entity.get('platform') or '').strip()
        mqtt_candidate = source in {'zigbee2mqtt', 'mqtt'} or bool(entity.get('source_ref') or entity.get('topic'))
        metadata_detail = self._apply_sensor_metadata(entity, desired_name, target_room)
        if mqtt_candidate and not metadata_detail.get('ok'):
            raise RuntimeError(str(metadata_detail.get('message') or metadata_detail.get('reason') or 'Sensor konnte nicht umbenannt werden.'))
        source_ref = str(metadata_detail.get('source_ref') or entity.get('source_ref') or entity.get('topic') or entity.get('entity_id') or entity_id).strip()
        transport = str(session['target_transport'] or SensorTransport.ZIGBEE.value)
        sensor_type = str(session['target_sensor_type'] or sensor_type_from_role(session['target_role']))
        device_id = attrs.get('device_id') or entity.get('device_id')
        primary_entity_id = str(entity.get('entity_id') or entity_id)
        entity_ids = entity_ids_for_physical_device(current, entity)
        payload = {
            'role': session['target_role'],
            'room': target_room,
            'entity_id': source_ref if mqtt_candidate else str(entity.get('entity_id') or entity_id),
            'device_id': device_id,
            'friendly_name': desired_name,
            'device_class': attrs.get('device_class') or entity.get('device_class'),
            'domain': entity.get('domain') or (entity_id.split('.')[0] if '.' in entity_id else ''),
            'source': source or 'wizard',
            'confidence': 100,
            'sensor_type': sensor_type,
            'transport': transport,
            'primary_entity_id': primary_entity_id,
            'entity_ids': entity_ids,
            'last_seen': latest_seen_for_entities(current, entity_ids),
        }
        role = self.upsert_role(payload)
        self.upsert_sensor_device(payload)
        with self.connect() as con:
            con.execute(
                'update sensor_discovery_sessions set status = ?, selected_entity_id = ?, selected_device_id = ?, ended_at = ? where id = ?',
                ('confirmed', entity_id, stable_physical_device_id(entity) or entity.get('device_id'), now(), session_id),
            )
            con.commit()
        logger.info(
            "Sentero discovery confirmed session=%s role=%s room=%s entity=%s device=%s name=%s metadata=%s",
            session_id,
            session['target_role'],
            target_room,
            entity_id,
            payload.get('device_id'),
            desired_name,
            metadata_detail,
        )
        response = {'status': 'confirmed', 'role': role if dev else public_role(role)}
        if dev:
            response['metadata'] = metadata_detail
        return response

    def _log_discovery_device_sets(self, session_id: int, baseline: list[dict[str, Any]], current: list[dict[str, Any]]) -> None:
        baseline_device_ids = stable_physical_device_ids(baseline)
        current_device_ids = stable_physical_device_ids(current)
        logger.info(
            "Discovery physical device sets",
            extra={
                "component": "wizard",
                "discovery_session": session_id,
                "baseline_device_ids": sorted(baseline_device_ids),
                "current_device_ids": sorted(current_device_ids),
                "new_device_ids": sorted(current_device_ids - baseline_device_ids),
            },
        )

    def cancel_discovery(self, session_id: int | None = None) -> dict[str, Any]:
        return self.stop_zigbee_pairing(session_id=session_id, reason='cancel')

    def upsert_role(self, data: dict[str, Any]) -> dict[str, Any]:
        role = str(data.get('role') or '').strip()
        entity_id = str(data.get('entity_id') or '').strip()
        if not role or not entity_id:
            raise ValueError('role and entity_id required')
        domain = str(data.get('domain') or (entity_id.split('.')[0] if '.' in entity_id else '')).strip()
        data = {**data, 'domain': domain}
        if not role_candidate_matches(role, data, allow_missing_device_class=True):
            raise ValueError('entity does not match expected sensor class for role')
        timestamp = now()
        entity_ids = data.get('entity_ids')
        if not isinstance(entity_ids, list):
            entity_ids = [data.get('primary_entity_id') or entity_id]
        entity_ids_json = json.dumps([str(item) for item in entity_ids if item], ensure_ascii=False)
        sensor_type = str(data.get('sensor_type') or sensor_type_from_role(role))
        transport = normalize_transport(data.get('transport'), data.get('source'))
        with self.connect() as con:
            con.execute('update sensor_roles set active = 0, updated_at = ? where role = ?', (timestamp, role))
            con.execute(
                '''insert into sensor_roles
                   (role, room, entity_id, device_id, friendly_name, device_class, domain, source, confidence,
                    sensor_type, transport, primary_entity_id, entity_ids_json, last_seen, enabled, active, created_at, updated_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)''',
                (
                    role,
                    data.get('room'),
                    entity_id,
                    data.get('device_id'),
                    data.get('friendly_name'),
                    data.get('device_class'),
                    data.get('domain'),
                    data.get('source'),
                    float(data.get('confidence') or 0),
                    sensor_type,
                    transport,
                    data.get('primary_entity_id') or entity_id,
                    entity_ids_json,
                    data.get('last_seen'),
                    timestamp,
                    timestamp,
                ),
            )
            con.commit()
        return self.get_role(role, dev=True) or {}

    def upsert_sensor_device(self, data: dict[str, Any]) -> dict[str, Any]:
        primary_entity_id = str(data.get('primary_entity_id') or data.get('entity_id') or '').strip()
        if not primary_entity_id:
            raise ValueError('primary_entity_id required')
        sensor_type = str(data.get('sensor_type') or sensor_type_from_role(str(data.get('role') or ''))).strip()
        transport = normalize_transport(data.get('transport'), data.get('source'))
        entity_ids = data.get('entity_ids')
        if not isinstance(entity_ids, list):
            entity_ids = [primary_entity_id]
        entity_ids_json = json.dumps([str(item) for item in entity_ids if item], ensure_ascii=False)
        timestamp = now()
        device_id = str(data.get('device_id') or '').strip()
        room_id = data.get('room')
        with self.connect() as con:
            if device_id:
                con.execute('update sensor_devices set enabled = 0, updated_at = ? where room_id is ? and sensor_type = ? and enabled = 1', (timestamp, room_id, sensor_type))
            else:
                con.execute('update sensor_devices set enabled = 0, updated_at = ? where room_id is ? and sensor_type = ? and primary_entity_id = ? and enabled = 1', (timestamp, room_id, sensor_type, primary_entity_id))
            cur = con.execute(
                '''insert into sensor_devices
                   (room_id, sensor_type, transport, device_id, primary_entity_id, entity_ids_json,
                    friendly_name, connected_at, last_seen, enabled, created_at, updated_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                (
                    room_id,
                    sensor_type,
                    transport,
                    device_id or None,
                    primary_entity_id,
                    entity_ids_json,
                    data.get('friendly_name'),
                    timestamp,
                    data.get('last_seen'),
                    timestamp,
                    timestamp,
                ),
            )
            con.commit()
            device_row = con.execute('select * from sensor_devices where id = ?', (int(cur.lastrowid),)).fetchone()
        return dict(device_row) if device_row else {}

    def get_role(self, role: str, dev: bool = False) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute('select * from sensor_roles where role = ? and active = 1 limit 1', (role,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return data if dev else public_role(data)

    def delete_role(self, role: str, local_only: bool = False) -> dict[str, Any]:
        mapped = self.get_role(role, dev=True)
        if not mapped:
            raise ValueError('sensor role not found')
        source = str(mapped.get('source') or '').strip()
        zigbee_sensor = is_zigbee2mqtt_mapping(mapped)
        esp32_sensor = is_esp32_mqtt_mapping(mapped) and not zigbee_sensor
        if local_only:
            removal = {
                'ok': True,
                'provider': source or 'sentero',
                'reason': 'local_only',
                'message': 'Sensor wurde nur aus Sentero entfernt.',
                'external_remove': 'skipped',
                'entity_id': mapped.get('entity_id'),
                'device_id': mapped.get('device_id'),
            }
        elif zigbee_sensor:
            removal = self._remove_zigbee_device(mapped)
        elif esp32_sensor:
            removal = self._remove_esp32_sensor(mapped)
        else:
            removal = {
                'ok': True,
                'provider': source or 'sentero',
                'reason': 'local_mapping_removed',
                'message': 'Sensor wurde aus Sentero entfernt.',
                'external_remove': 'not_applicable',
                'entity_id': mapped.get('entity_id'),
                'device_id': mapped.get('device_id'),
            }
        if not removal.get('ok'):
            logger.warning(
                "Sentero sensor delete blocked because external removal failed",
                extra={"component": "device_mapping", "role": role, "provider": removal.get('provider'), "reason": removal.get('reason')},
            )
            raise RuntimeError(str(removal.get('message') or 'Sensor konnte nicht aus dem Sensornetzwerk entfernt werden.'))
        with self.connect() as con:
            timestamp = now()
            device_id = str(mapped.get('device_id') or '').strip()
            if device_id:
                con.execute('update sensor_roles set active = 0, updated_at = ? where device_id = ? and active = 1', (timestamp, device_id))
            else:
                con.execute('update sensor_roles set active = 0, updated_at = ? where role = ?', (timestamp, role))
            con.commit()
        logger.info(
            "Sentero sensor mapping deleted role=%s entity=%s device=%s",
            role,
            mapped.get('entity_id'),
            mapped.get('device_id'),
        )
        logger.info("Sensor lokal gelöscht", extra={"component": "device_mapping", "role": role, "device_id": mapped.get('device_id'), "provider": removal.get('provider')})
        return {'deleted': True, 'role': role, 'removal': removal}

    def _remove_esp32_sensor(self, mapped: dict[str, Any]) -> dict[str, Any]:
        device_id = str(mapped.get('device_id') or '').strip()
        if not device_id:
            return {'ok': False, 'provider': 'mqtt', 'reason': 'missing_device_id', 'message': 'Sensor konnte nicht eindeutig identifiziert werden.'}
        state = self._attach_state([mapped])[0]
        if state.get('reachable') is False:
            logger.warning("Sensor offline", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id})
            return {
                'ok': False,
                'provider': 'mqtt',
                'reason': 'sensor_offline',
                'message': 'Der Sensor ist derzeit nicht erreichbar. Er kann deshalb nicht auf Werkseinstellungen zurückgesetzt werden.',
                'device_id': device_id,
            }
        try:
            response = self.factory_reset_sensor(device_id)
            return {'ok': True, 'provider': 'mqtt', 'reason': 'factory_reset_confirmed', 'message': 'Präsenzsensor wurde zurückgesetzt.', 'device_id': device_id, 'response': response}
        except TimeoutError as exc:
            logger.warning("Factory Reset Timeout", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id})
            return {'ok': False, 'provider': 'mqtt', 'reason': 'factory_reset_timeout', 'message': 'Sensor hat den Factory Reset nicht bestätigt.', 'device_id': device_id}
        except Exception as exc:
            logger.exception("Sensor konnte nicht gelöscht werden", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id})
            return {'ok': False, 'provider': 'mqtt', 'reason': 'factory_reset_failed', 'message': str(exc) or 'Sensor konnte nicht gelöscht werden.', 'device_id': device_id}

    def factory_reset_sensor(self, device_id: str) -> dict[str, Any]:
        clean_id = str(device_id or '').strip()
        if not clean_id:
            raise RuntimeError('Sensor konnte nicht eindeutig identifiziert werden.')
        logger.info("Factory Reset angefordert", extra={"component": "device_mapping", "provider": "mqtt", "device_id": clean_id})
        message = self.send_factory_reset_command(clean_id)
        response = self.wait_for_factory_reset_ack(clean_id, message.payload)
        logger.info("Factory Reset bestätigt", extra={"component": "device_mapping", "provider": "mqtt", "device_id": clean_id})
        return response

    def send_factory_reset_command(self, device_id: str):
        command_topic = esp32_command_topic(device_id)
        status_topic = esp32_status_topic(device_id)
        payload = {'command': 'factory_reset', 'enabled': 'true'}
        try:
            return self.mqtt.request_response(
                command_topic,
                status_topic,
                payload,
                timeout=10.0,
                response_filter=lambda response, wanted=device_id: esp32_factory_reset_ack_matches(response, wanted),
            )
        except TimeoutError:
            logger.warning("Factory Reset nicht bestätigt", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id})
            raise
        except Exception:
            logger.exception("MQTT Publish fehlgeschlagen", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id, "topic": command_topic})
            raise

    def wait_for_factory_reset_ack(self, device_id: str, payload: Any) -> dict[str, Any]:
        if not esp32_factory_reset_ack_matches(payload, device_id):
            logger.warning("Factory Reset nicht bestätigt", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id})
            raise TimeoutError('factory_reset_not_confirmed')
        return payload if isinstance(payload, dict) else {'status': str(payload)}

    def send_role_command(self, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        mapped = self.get_role(role, dev=True)
        if not mapped:
            raise ValueError('sensor role not found')
        device_id = str(mapped.get('device_id') or '').strip()
        if not device_id:
            raise RuntimeError('Sensor konnte nicht eindeutig identifiziert werden.')
        source = str(mapped.get('source') or '').strip()
        if source != 'mqtt':
            raise RuntimeError('Dieser Sensor unterstützt keine direkten MQTT-Kommandos.')
        command = str(payload.get('command') or '').strip()
        if not command:
            raise RuntimeError('Sensor-Kommando fehlt.')
        state = self._attach_state([mapped])[0]
        if state.get('reachable') is False:
            raise RuntimeError('Sensor ist derzeit nicht erreichbar.')

        command_topic = esp32_command_topic(device_id)
        status_topic = esp32_status_topic(device_id)
        try:
            message = self.mqtt.request_response(
                command_topic,
                status_topic,
                payload,
                timeout=5.0,
                response_filter=lambda response, wanted=device_id, wanted_command=command: esp32_command_ack_matches(response, wanted, wanted_command),
            )
        except TimeoutError as exc:
            logger.warning("Sensor-Kommando nicht bestätigt", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id, "command": command})
            raise RuntimeError('Sensor hat das Kommando nicht bestätigt.') from exc
        except Exception:
            logger.exception("Sensor-Kommando fehlgeschlagen", extra={"component": "device_mapping", "provider": "mqtt", "device_id": device_id, "topic": command_topic, "command": command})
            raise

        response = message.payload if isinstance(message.payload, dict) else {'status': str(message.payload)}
        ok = bool(response.get('ok', response.get('status') == 'command_accepted'))
        return {
            'ok': ok,
            'role': role,
            'device_id': device_id,
            'topic': command_topic,
            'response': response,
            'hp_led': response.get('hp_led'),
            'fall_led': response.get('fall_led'),
            'led_status': response.get('led_status') if isinstance(response.get('led_status'), dict) else None,
            'message': response.get('message') or ('Kommando ausgeführt' if ok else 'Kommando abgelehnt'),
        }

    def _sync_esp32_role_name(self, role: str, mapped: dict[str, Any], name: str) -> dict[str, Any] | None:
        source = str(mapped.get('source') or '').strip()
        if source != 'mqtt':
            return None
        device_id = str(mapped.get('device_id') or '').strip()
        if not device_id:
            return None
        return self.send_role_command(role, {
            'command': 'configure',
            'friendly_name': name,
            'device': {
                'friendly_name': name,
            },
        })

    def rename_role(self, role: str, name: str) -> dict[str, Any]:
        clean_name = str(name or '').strip()
        if not clean_name:
            raise ValueError('name required')
        mapped = self.get_role(role, dev=True)
        if not mapped:
            raise ValueError('sensor role not found')
        entity_id = str(mapped.get('entity_id') or '').strip()
        current = self.snapshot()
        entity = next((item for item in current if item.get('entity_id') == entity_id), None) or {
            'entity_id': entity_id,
            'device_id': mapped.get('device_id'),
            'domain': mapped.get('domain') or entity_id.split('.')[0],
        }
        metadata = self._apply_sensor_metadata(entity, clean_name, mapped.get('room'))
        timestamp = now()
        with self.connect() as con:
            con.execute(
                'update sensor_roles set friendly_name = ?, updated_at = ? where role = ? and active = 1',
                (clean_name, timestamp, role),
            )
            con.commit()
        device_sync = None
        try:
            device_sync = self._sync_esp32_role_name(role, mapped, clean_name)
        except Exception as exc:
            device_sync = {'ok': False, 'message': str(exc)}
            logger.warning(
                "ESP32 sensor name sync failed role=%s entity=%s name=%s error=%s",
                role,
                entity_id,
                clean_name,
                exc,
            )
        if device_sync is not None:
            metadata = {**(metadata or {}), 'device_sync': device_sync}
        logger.info(
            "Sentero sensor renamed role=%s entity=%s name=%s metadata=%s",
            role,
            entity_id,
            clean_name,
            metadata,
        )
        return {'status': 'renamed', 'role': public_role(self.get_role(role, dev=True) or {}), 'metadata': metadata}

    def test_role(self, role: str) -> dict[str, Any]:
        mapped = self.get_role(role, dev=True)
        if not mapped:
            raise ValueError('sensor role not found')
        entity_id = str(mapped.get('entity_id') or '').strip()
        device_id = str(mapped.get('device_id') or '').strip()
        states = self.snapshot()
        by_entity = {str(item.get('entity_id') or ''): item for item in states}
        resolved_state = resolve_role_state(mapped, states, by_entity)
        if not resolved_state and self.uses_mqtt_source():
            resolved_state = self._cached_discovery_state(mapped)
        device_entities = [item for item in states if device_id and str(item.get('device_id') or '') == device_id]
        if not device_entities:
            device_entities = [item for item in states if str(item.get('entity_id') or '') == entity_id]
        if resolved_state and resolved_state not in device_entities:
            device_entities.append(resolved_state)
        usable_entities = [item for item in device_entities if testable_state_entity(item)]
        stale_entities = [item for item in device_entities if presence_live_state_is_stale(mapped, item)]
        unreachable_stale_entities = [item for item in device_entities if presence_live_state_is_unreachable(mapped, item)]
        reachable = [
            item for item in usable_entities
            if not presence_live_state_is_unreachable(mapped, item)
            and (state_is_reachable(item.get('state')) or sensor_reachable_status(item) is True)
        ]
        if not reachable:
            logger.info(
                "Sentero sensor test unreachable role=%s entity=%s device=%s device_entities=%s usable_entities=%s",
                role,
                entity_id,
                device_id,
                len(device_entities),
                len(usable_entities),
            )
            return {
                'ok': False,
                'mode': 'state_check',
                'message': 'Sensor meldet seit längerer Zeit keine neuen Daten.' if unreachable_stale_entities else 'Sensor ist aktuell nicht erreichbar.',
                'entity_id': entity_id,
                'entity_count': len(device_entities),
                'stale': bool(stale_entities),
            }
        primary = next((item for item in reachable if str(item.get('entity_id') or '') == entity_id), reachable[0])
        logger.info(
            "Sentero sensor test state_check role=%s entity=%s state=%s device=%s reachable_entities=%s",
            role,
            primary.get('entity_id'),
            primary.get('state'),
            device_id,
            len(reachable),
        )
        return {
            'ok': True,
            'mode': 'state_check',
            'message': 'Sensor ist erreichbar.',
            'entity_id': primary.get('entity_id'),
            'state': primary.get('state'),
            'entity_count': len(device_entities),
            'stale': presence_live_state_is_stale(mapped, primary),
        }

    def _remove_zigbee_device(self, mapped: dict[str, Any]) -> dict[str, Any]:
        entity_id = str(mapped.get('entity_id') or '').strip()
        device_id = str(mapped.get('device_id') or '').strip()
        try:
            states = self.snapshot()
        except Exception:
            logger.exception("Zigbee2MQTT remove snapshot failed", extra={"component": "device_mapping", "source_ref": entity_id, "device_id": device_id})
            states = []
        mapped_identities = mqtt_identity_values(mapped)
        device_entities = []
        for item in states:
            item_entity_id = str(item.get('entity_id') or '').strip()
            same_device = bool(device_id and str(item.get('device_id') or '').strip() == device_id)
            same_entity = bool(entity_id and item_entity_id == entity_id)
            same_identity = bool(mapped_identities and mapped_identities.intersection(mqtt_identity_values(item)))
            if same_device or same_entity or same_identity:
                device_entities.append(item)
        identifiers = parse_identifiers(mapped.get('identifiers'))
        for item in device_entities:
            identifiers.extend(parse_identifiers(item.get('identifiers')))
        mqtt_ids = zigbee2mqtt_identifiers(identifiers, [mapped, *device_entities])
        attempts: list[dict[str, Any]] = []
        for provider in zigbee_provider_order():
            if provider == 'zigbee2mqtt':
                try:
                    permit_join = self._disable_zigbee2mqtt_permit_join_confirmed(reason='remove', device_id=device_id or None)
                    self._close_discovery_sessions_for_mapping(mapped)
                except Exception as exc:
                    attempts.append({'provider': 'zigbee2mqtt', 'step': 'permit_join_disable', 'error': str(exc)})
                    return {
                        'ok': False,
                        'reason': 'permit_join_stop_failed',
                        'message': 'Permit Join konnte nicht deaktiviert werden.',
                        'entity_id': entity_id,
                        'device_id': device_id or None,
                        'identifiers': identifiers,
                        'mqtt_ids': mqtt_ids,
                        'attempts': attempts,
                    }
                for mqtt_id in mqtt_ids:
                    try:
                        response = self._zigbee2mqtt_request(
                            'device/remove',
                            {'id': mqtt_id, 'force': 'true', 'block': 'false'},
                            lambda payload, wanted=mqtt_id: z2m_response_matches_id(payload, wanted),
                        )
                        logger.info("Device erfolgreich entfernt", extra={"component": "device_mapping", "device_id": mqtt_id, "source_ref": entity_id})
                        return {'ok': True, 'provider': 'zigbee2mqtt', 'id': mqtt_id, 'permit_join': permit_join, 'response': response, 'attempts': attempts}
                    except Exception as exc:
                        attempts.append({'provider': 'zigbee2mqtt', 'id': mqtt_id, 'error': str(exc)})
                        logger.exception("Remove fehlgeschlagen", extra={"component": "device_mapping", "provider": "zigbee2mqtt", "device_id": device_id, "source_ref": entity_id})
        return {
            'ok': False,
            'reason': 'zigbee_remove_unavailable',
            'message': 'Geraet konnte nicht entfernt werden.',
            'entity_id': entity_id,
            'device_id': device_id or None,
            'identifiers': identifiers,
            'mqtt_ids': mqtt_ids,
            'attempts': attempts,
        }

    def _attach_state(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            states = self.snapshot()
        except Exception:
            logger.exception("Sentero sensor state refresh failed")
            return [{**row, 'reachable': False, 'state': None, 'last_changed': None, 'last_updated': None, 'battery_level': None} for row in rows]
        by_entity = {str(item.get('entity_id') or ''): item for item in states}
        result = []
        for row in rows:
            entity_id = str(row.get('entity_id') or '')
            state = resolve_role_state(dict(row), states, by_entity)
            if not state and self.uses_mqtt_source():
                state = self._cached_discovery_state(dict(row))
            value = state.get('state') if state else None
            reachable = sensor_reachable_status(state)
            availability = find_mqtt_availability_state({**row, **(state or {})}, states)
            if availability is not None:
                reachable = availability
            telemetry_state = combined_mqtt_telemetry_state(dict(row), state, states)
            if reachable is None and mqtt_item_has_telemetry(telemetry_state):
                reachable = True
            stale = mqtt_state_is_health_stale(dict(row), telemetry_state)
            stale_seconds = sensor_state_age_seconds(telemetry_state) if stale else None
            stale_unreachable = mqtt_state_is_health_unreachable(dict(row), telemetry_state)
            if stale_unreachable:
                reachable = False
            direct_battery_level = battery_level_from_state(telemetry_state)
            battery_entity = find_battery_entity(dict(row), states) if direct_battery_level is None else None
            battery_level = direct_battery_level if direct_battery_level is not None else parse_battery(battery_entity.get('state')) if battery_entity else None
            power_source = power_source_from_state(telemetry_state)
            environmental = environmental_metrics_from_state({**dict(row), **(telemetry_state or {})}, states)
            live_telemetry_state = None if stale else telemetry_state
            c1001_telemetry = c1001_telemetry_from_state(live_telemetry_state)
            generic_presence = generic_presence_telemetry_from_state(dict(row), live_telemetry_state, states if not stale else [])
            motion = c1001_telemetry.get('motion') if c1001_telemetry.get('motion') is not None else generic_presence.get('motion')
            motion_state = motion_state_from_state(live_telemetry_state)
            explicit_presence = c1001_telemetry.get('presence')
            inferred_presence = generic_presence.get('presence')
            presence = effective_presence_value(explicit_presence, inferred_presence, motion_state, motion)
            logger.debug(
                "Sensor health resolved",
                extra={
                    "component": "device_mapping",
                    "role": row.get('role'),
                    "source_ref": entity_id,
                    "resolved_entity": state.get('entity_id') if state else None,
                    "reachable": reachable,
                    "availability": availability,
                    "battery_entity": battery_entity.get('entity_id') if battery_entity else None,
                    "battery_level": battery_level,
                    "power_source": power_source,
                    "temperature": environmental.get('temperature'),
                    "humidity": environmental.get('humidity'),
                    "illuminance": environmental.get('illuminance'),
                    "presence": presence,
                    "fall_detected": c1001_telemetry.get('fall_detected'),
                    "motion": motion,
                    "motion_state": motion_state,
                    "stale": stale,
                    "stale_seconds": stale_seconds,
                },
            )
            result.append({
                **row,
                'device_id': row.get('device_id') or (state.get('device_id') if state else None),
                'area_id': state.get('area_id') if state else None,
                'platform': state.get('platform') if state else None,
                'unique_id': state.get('unique_id') if state else None,
                'original_name': state.get('original_name') if state else None,
                'device_name': state.get('device_name') if state else None,
                'manufacturer': state.get('manufacturer') if state else None,
                'model': state.get('model') if state else None,
                'identifiers': state.get('identifiers') if state else None,
                'resolved_entity_id': state.get('entity_id') if state else None,
                'state': value,
                'reachable': reachable,
                'last_changed': state.get('last_changed') if state else None,
                'last_updated': state.get('last_updated') if state else None,
                'battery_level': battery_level,
                'power_source': power_source,
                'temperature': environmental.get('temperature'),
                'humidity': environmental.get('humidity'),
                'illuminance': environmental.get('illuminance'),
                'presence': presence,
                'fall_detected': c1001_telemetry.get('fall_detected'),
                'motion': motion,
                'motion_state': motion_state,
                'stale': stale,
                'stale_seconds': stale_seconds,
                'hp_led': c1001_telemetry.get('hp_led'),
                'fall_led': c1001_telemetry.get('fall_led'),
                'led_status': c1001_telemetry.get('led_status'),
                'writable_settings': c1001_telemetry.get('writable_settings'),
            })
        return result

    def _cached_discovery_state(self, mapped: dict[str, Any]) -> dict[str, Any] | None:
        role = str(mapped.get('role') or '').strip()
        if not role:
            return None
        with self.connect() as con:
            rows = con.execute(
                """select candidate_snapshot_json
                   from sensor_discovery_sessions
                   where target_role = ? and status = 'confirmed'
                   order by id desc
                   limit 5""",
                (role,),
            ).fetchall()
        for row in rows:
            try:
                snapshot = json.loads(row['candidate_snapshot_json'] or '[]')
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(snapshot, list):
                continue
            states = [normalize_snapshot_item(item) for item in snapshot]
            state = resolve_role_state(mapped, states, {str(item.get('entity_id') or ''): item for item in states})
            if state and (state_is_reachable(state.get('state')) or mqtt_item_has_telemetry(state)):
                logger.debug(
                    "Sensor state resolved from discovery cache",
                    extra={"component": "device_mapping", "role": role, "device_id": mapped.get('device_id'), "source_ref": mapped.get('entity_id')},
                )
                return state
        return None

    def snapshot(self) -> list[dict[str, Any]]:
        local_rows = self._local_ecotracker_snapshot()
        mqtt_rows = [normalize_snapshot_item(item) for item in self.sensor_source.snapshot()]
        return [*local_rows, *mqtt_rows]

    def _local_ecotracker_snapshot(self) -> list[dict[str, Any]]:
        try:
            with self.connect() as con:
                row = con.execute("select host, enabled from ecotracker_settings where id = 1").fetchone()
        except sqlite3.OperationalError:
            return []
        if not row or not bool(row["enabled"]) or not str(row["host"] or "").strip():
            return []
        try:
            payload = EcoTrackerClient(str(row["host"])).read()
        except Exception:
            logger.exception("EcoTracker local snapshot failed", extra={"component": "device_mapping", "source": "ecotracker"})
            return []
        return [normalize_snapshot_item(item) for item in ecotracker_snapshot_rows(str(row["host"]), payload)]

    def _mqtt_snapshot(self) -> list[dict[str, Any]]:
        sources = getattr(self.sensor_source, 'sources', None)
        if isinstance(sources, list):
            rows: list[dict[str, Any]] = []
            for source in sources:
                if str(getattr(source, 'name', '')) not in {'zigbee2mqtt', 'mqtt'}:
                    continue
                if hasattr(source, 'configured') and not source.configured():
                    continue
                rows.extend(normalize_snapshot_item(item) for item in source.snapshot())
            return rows
        return [normalize_snapshot_item(item) for item in self.sensor_source.snapshot()]

    def _assigned_sensor_identities(self) -> set[str]:
        with self.connect() as con:
            rows = con.execute('select * from sensor_roles where active = 1').fetchall()
        identities: set[str] = set()
        for row in rows:
            identities.update(mqtt_identity_values(dict(row)))
        return identities

    def uses_mqtt_source(self) -> bool:
        return True

    def _mqtt_publish(self, topic: str, payload: Any) -> dict[str, Any]:
        return self.mqtt.publish(topic, payload)

    def _zigbee2mqtt_topic(self, suffix: str) -> str:
        prefix = os.getenv('SENTERO_ZIGBEE2MQTT_TOPIC_PREFIX') or os.getenv('ZIGBEE2MQTT_TOPIC_PREFIX') or config_str('mqtt.topic_prefix', '') or config_str('mqtt.zigbee2mqtt_topic_prefix', 'zigbee2mqtt') or 'zigbee2mqtt'
        clean_prefix = str(prefix or 'zigbee2mqtt').strip().strip('/') or 'zigbee2mqtt'
        clean_suffix = str(suffix or '').strip().strip('/')
        return f'{clean_prefix}/{clean_suffix}' if clean_suffix else clean_prefix

    def _apply_sensor_metadata(self, entity: dict[str, Any], name: str, room: str | None) -> dict[str, Any]:
        entity_id = str(entity.get('entity_id') or '').strip()
        device_id = str(entity.get('device_id') or '').strip()
        rename = self._rename_zigbee2mqtt_device(entity, name)
        return {
            'entity_id': entity_id,
            'device_id': device_id or None,
            'name': name,
            'room': room,
            'updated': ['zigbee2mqtt'] if rename.get('ok') else [],
            'ok': True,
            'rename_optional': True,
            'source_ref': rename.get('source_ref') or entity.get('source_ref') or entity.get('topic') or entity_id,
            'zigbee2mqtt': rename,
        }

    def _rename_zigbee2mqtt_device(self, entity: dict[str, Any], name: str) -> dict[str, Any]:
        clean_name = str(name or '').strip()
        if not clean_name:
            return {'ok': False, 'reason': 'missing_name'}
        identifiers = parse_identifiers(entity.get('identifiers'))
        candidates = zigbee2mqtt_identifiers(identifiers, [entity])
        source_id = next((value for value in candidates if re.fullmatch(r'0x[0-9a-fA-F]{12,16}', value)), None)
        source_id = source_id or (candidates[0] if candidates else None)
        if not source_id:
            return {'ok': False, 'reason': 'no_zigbee2mqtt_id', 'candidates': candidates}
        try:
            response = self._zigbee2mqtt_request(
                'device/rename',
                {'from': source_id, 'to': clean_name},
                lambda payload: z2m_rename_response_matches(payload, source_id, clean_name),
            )
            source_ref = self._zigbee2mqtt_topic(clean_name)
            logger.info("Sensor renamed in Zigbee2MQTT", extra={"component": "device_mapping", "device_id": source_id, "source_ref": source_ref})
            return {'ok': True, 'provider': 'zigbee2mqtt', 'from': source_id, 'to': clean_name, 'source_ref': source_ref, 'response': response}
        except Exception as exc:
            logger.warning("Zigbee2MQTT rename failed", extra={"component": "device_mapping", "device_id": source_id, "source_ref": entity.get('entity_id')})
            return {'ok': False, 'reason': 'rename_failed', 'message': 'Sensor konnte im Sensornetzwerk nicht umbenannt werden.', 'from': source_id, 'to': clean_name, 'error': str(exc)}

    def _zigbee2mqtt_request(self, action: str, payload: Any, response_filter) -> dict[str, Any]:
        request_topic = self._zigbee2mqtt_topic(f'bridge/request/{action}')
        response_topic = self._zigbee2mqtt_topic(f'bridge/response/{action}')
        logger.debug(
            "Zigbee2MQTT request",
            extra={"component": "device_mapping", "topic": request_topic, "action": action, "payload": payload},
        )
        try:
            response = self.mqtt.request_response(request_topic, response_topic, payload, timeout=8.0, response_filter=response_filter)
            response_payload = response.payload if isinstance(response.payload, dict) else {}
        except Exception:
            logger.exception("Zigbee2MQTT request failed", extra={"component": "device_mapping", "action": action, "topic": request_topic})
            raise
        if response_payload.get('status') != 'ok':
            message = str(response_payload.get('error') or response_payload or 'Zigbee2MQTT request failed')
            logger.warning("Zigbee2MQTT request not confirmed", extra={"component": "device_mapping", "action": action, "response": response_payload})
            raise RuntimeError(message)
        logger.debug("Zigbee2MQTT response", extra={"component": "device_mapping", "topic": response_topic, "action": action, "response": response_payload})
        return response_payload

    def _disable_zigbee2mqtt_permit_join_confirmed(self, reason: str = 'stop', device_id: str | None = None) -> dict[str, Any]:
        errors: list[str] = []
        for payload in zigbee2mqtt_permit_join_payloads(False):
            try:
                response = self._zigbee2mqtt_request(
                    'permit_join',
                    payload,
                    lambda payload: z2m_permit_join_response_matches(payload, False),
                )
                logger.info(
                    "Permit Join deaktiviert",
                    extra={"component": "device_mapping", "provider": "zigbee2mqtt", "reason": reason, "device_id": device_id},
                )
                return response
            except Exception as exc:
                errors.append(str(exc))
                logger.debug(
                    "Zigbee2MQTT permit_join stop payload failed",
                    extra={"component": "device_mapping", "provider": "zigbee2mqtt", "reason": reason, "device_id": device_id, "payload": payload},
                    exc_info=True,
                )
        logger.warning(
            "Permit Join konnte nicht deaktiviert werden",
            extra={"component": "device_mapping", "provider": "zigbee2mqtt", "reason": reason, "device_id": device_id, "errors": errors},
        )
        raise RuntimeError("Permit Join konnte nicht deaktiviert werden.")

    def _close_discovery_sessions_for_mapping(self, mapped: dict[str, Any]) -> None:
        role = str(mapped.get('role') or '').strip()
        device_id = str(mapped.get('device_id') or '').strip()
        source_ref = str(mapped.get('entity_id') or '').strip()
        with self.connect() as con:
            con.execute(
                """update sensor_discovery_sessions
                   set status = case when status in ('waiting_for_signal', 'pairing_started', 'found', 'signal_detected') then 'completed' else status end,
                       ended_at = coalesce(ended_at, ?)
                   where (? != '' and target_role = ?)
                      or (? != '' and selected_entity_id = ?)
                      or (? != '' and selected_entity_id = ?)""",
                (now(), role, role, device_id, device_id, source_ref, source_ref),
            )
            con.commit()

    def _open_zigbee_permit_join(self, duration: int) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        for provider in zigbee_provider_order():
            if provider == 'zigbee2mqtt':
                try:
                    response = self._mqtt_publish(
                        self._zigbee2mqtt_topic('bridge/request/permit_join'),
                        zigbee2mqtt_permit_join_payloads(True, duration)[0],
                    )
                    logger.info("Zigbee pairing started", extra={"component": "wizard", "provider": "zigbee2mqtt", "duration": duration})
                    return {'ok': True, 'provider': 'zigbee2mqtt', 'duration': duration, 'response': response, 'attempts': attempts}
                except Exception as exc:
                    attempts.append({'provider': 'zigbee2mqtt', 'error': str(exc)})
                    logger.warning("Zigbee permit_join failed", extra={"component": "wizard", "provider": "zigbee2mqtt", "duration": duration})
        return {'ok': False, 'provider': 'zigbee2mqtt', 'reason': 'zigbee_pairing_unavailable', 'message': 'Zigbee-Anlernen nicht verfuegbar', 'attempts': attempts}

    def stop_zigbee_pairing(self, session_id: int | None = None, reason: str = 'stop') -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        detail: dict[str, Any] = {}
        if session_id is not None:
            with self.connect() as con:
                row = con.execute('select * from sensor_discovery_sessions where id = ?', (session_id,)).fetchone()
                if not row:
                    raise ValueError('session not found')
                detail = discovery_detail(row)
        provider = str(detail.get('provider') or '').strip()
        providers = [provider] if provider == 'zigbee2mqtt' else zigbee_provider_order()
        for candidate_provider in providers:
            if candidate_provider == 'zigbee2mqtt':
                try:
                    payload = zigbee2mqtt_permit_join_payloads(False)[0]
                    logger.debug(
                        "Zigbee2MQTT permit_join stop request",
                        extra={"component": "wizard", "provider": "zigbee2mqtt", "payload": payload, "session_id": session_id, "reason": reason},
                    )
                    response = self._mqtt_publish(self._zigbee2mqtt_topic('bridge/request/permit_join'), payload)
                    if session_id is not None:
                        with self.connect() as con:
                            con.execute(
                                "update sensor_discovery_sessions set status = case when status in ('waiting_for_signal', 'pairing_started') then ? else status end, ended_at = coalesce(ended_at, ?) where id = ?",
                                ('cancelled' if reason == 'cancel' else 'completed', now(), session_id),
                            )
                            con.commit()
                    logger.info(
                        "Zigbee pairing stopped",
                        extra={"component": "wizard", "provider": "zigbee2mqtt", "session_id": session_id, "reason": reason},
                    )
                    return {'ok': True, 'provider': 'zigbee2mqtt', 'reason': reason, 'response': response, 'attempts': attempts}
                except Exception as exc:
                    attempts.append({'provider': 'zigbee2mqtt', 'error': str(exc)})
                    logger.warning(
                        "Permit Join konnte nicht deaktiviert werden",
                        extra={"component": "wizard", "provider": "zigbee2mqtt", "session_id": session_id, "reason": reason},
                    )
                continue
        return {'ok': False, 'reason': 'permit_join_stop_failed', 'attempts': attempts}


def sensor_source_mode() -> str:
    return 'mqtt'


def normalize_snapshot_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        metadata = getattr(item, 'metadata', None)
        attrs = metadata if isinstance(metadata, dict) else {}
        sensor_id = str(getattr(item, 'sensor_id', '') or '')
        source = str(getattr(item, 'source', '') or '')
        return normalize_snapshot_item({
            'entity_id': sensor_id,
            'domain': sensor_id.split('.')[0] if '.' in sensor_id else '',
            'state': getattr(item, 'state', None),
            'friendly_name': attrs.get('friendly_name') or sensor_id,
            'device_class': attrs.get('device_class'),
            'room': getattr(item, 'room', None),
            'area_id': getattr(item, 'room', None),
            'source': source,
            'source_ref': sensor_id,
            'last_changed': getattr(item, 'changed_at', None),
            'last_updated': getattr(item, 'changed_at', None),
            'attributes': attrs,
        })
    entity_id = str(item.get('entity_id') or '')
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    return {
        **item,
        'entity_id': entity_id,
        'domain': str(item.get('domain') or entity_id.split('.')[0] if '.' in entity_id else ''),
        'state': item.get('state'),
        'friendly_name': item.get('friendly_name') or attrs.get('friendly_name') or entity_id,
        'device_class': item.get('device_class') or attrs.get('device_class'),
        'unit': item.get('unit') or item.get('unit_of_measurement') or attrs.get('unit_of_measurement'),
        'unit_of_measurement': item.get('unit_of_measurement') or item.get('unit') or attrs.get('unit_of_measurement'),
        'device_id': item.get('device_id') or attrs.get('device_id'),
        'identifiers': item.get('identifiers') or attrs.get('identifiers'),
        'topic': item.get('topic') or attrs.get('topic'),
        'source_ref': item.get('source_ref') or attrs.get('source_ref') or item.get('topic') or attrs.get('topic'),
        'payload_key': item.get('payload_key') or attrs.get('payload_key'),
        'last_changed': item.get('last_changed') or item.get('changed_at'),
        'last_updated': item.get('last_updated') or item.get('changed_at'),
    }


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute('''create table if not exists setup_state (id integer primary key check (id = 1), current_step text not null default 'welcome', completed_steps text not null default '[]', is_complete integer not null default 0, updated_at text not null)''')
    try:
        con.execute("alter table setup_state add column selected_rooms_json text not null default '[]'")
    except sqlite3.OperationalError:
        pass
    con.execute('''create table if not exists sentero_profile (id integer primary key check (id = 1), name text, age integer, notes text, created_at text not null, updated_at text not null)''')
    try:
        con.execute("alter table sentero_profile add column birth_year integer")
    except sqlite3.OperationalError:
        pass
    con.execute('''create table if not exists trusted_contacts (id integer primary key autoincrement, name text not null, relationship text, email text, active integer not null default 1, created_at text not null, updated_at text not null)''')
    for statement in [
        "alter table trusted_contacts add column phone text",
        "alter table trusted_contacts add column telegram_chat_id text",
        "alter table trusted_contacts add column telegram_invite_code text",
        "alter table trusted_contacts add column telegram_linked_at text",
        "alter table trusted_contacts add column whatsapp_phone_number text",
        "alter table trusted_contacts add column preferred_channels text not null default '[\"email\"]'",
        "alter table trusted_contacts add column notification_enabled integer not null default 1",
        "alter table trusted_contacts add column primary_contact integer not null default 0",
        "alter table trusted_contacts add column actor_role text not null default 'relative'",
        "alter table trusted_contacts add column email_queries_enabled integer not null default 0",
        "alter table trusted_contacts add column email_permissions text not null default '[]'",
    ]:
        try:
            con.execute(statement)
        except sqlite3.OperationalError:
            pass
    con.execute('''create table if not exists notification_preferences (id integer primary key check (id = 1), anomalies integer not null default 1, critical integer not null default 1, daily_summary integer not null default 0, updated_at text not null)''')
    con.execute('''create table if not exists notification_channel_settings (
        id integer primary key autoincrement,
        channel text not null unique,
        enabled integer not null default 0,
        config_json text not null default '{}',
        created_at text not null,
        updated_at text not null
    )''')
    con.execute('''create table if not exists notification_logs (
        id integer primary key autoincrement,
        incident_key text,
        contact_id integer,
        channel text not null,
        severity text not null,
        status text not null,
        message_title text,
        error_message text,
        data_class text not null default 'health_adjacent',
        aggregation_level text not null default 'summary',
        outgoing_message_id text,
        created_at text not null
    )''')
    try:
        con.execute("alter table notification_logs add column data_class text not null default 'health_adjacent'")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("alter table notification_logs add column aggregation_level text not null default 'summary'")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("alter table notification_logs add column outgoing_message_id text")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("alter table notification_logs add column incident_key text")
    except sqlite3.OperationalError:
        pass
    con.execute('''create table if not exists data_consents (
        id integer primary key autoincrement,
        contact_id integer not null,
        recipient_type text not null,
        purpose text not null,
        data_classes_json text not null default '[]',
        valid_until text,
        revoked_at text,
        created_at text not null,
        updated_at text not null,
        foreign key(contact_id) references trusted_contacts(id)
    )''')
    con.execute('create index if not exists idx_data_consents_contact_purpose on data_consents(contact_id, purpose, revoked_at)')
    con.execute('''create table if not exists system_warning_state (
        warning_key text primary key,
        incident_key text,
        category text,
        subject_id text,
        status text not null,
        severity text,
        first_seen_at text not null,
        last_seen_at text not null,
        last_sent_at text,
        last_notified_severity text,
        resolved_at text,
        consecutive_healthy_checks integer not null default 0,
        reminder_count integer not null default 0,
        payload_json text not null default '{}'
    )''')
    for statement in [
        "alter table system_warning_state add column incident_key text",
        "alter table system_warning_state add column category text",
        "alter table system_warning_state add column subject_id text",
        "alter table system_warning_state add column severity text",
        "alter table system_warning_state add column last_notified_severity text",
        "alter table system_warning_state add column consecutive_healthy_checks integer not null default 0",
        "alter table system_warning_state add column reminder_count integer not null default 0",
    ]:
        try:
            con.execute(statement)
        except sqlite3.OperationalError:
            pass
    con.execute("update system_warning_state set incident_key = warning_key where incident_key is null or trim(incident_key) = ''")
    con.execute('''create table if not exists sentero_users (
        id integer primary key autoincrement,
        email text not null unique,
        password_hash text not null,
        display_name text,
        role text not null default 'viewer',
        aal_role text not null default 'admin',
        is_active integer not null default 1,
        created_at text not null,
        updated_at text not null,
        last_login_at text
    )''')
    try:
        con.execute("alter table sentero_users add column aal_role text not null default 'admin'")
    except sqlite3.OperationalError:
        pass
    con.execute('''create table if not exists sentero_sessions (
        id integer primary key autoincrement,
        user_id integer not null,
        token_hash text not null unique,
        expires_at text not null,
        created_at text not null,
        foreign key(user_id) references sentero_users(id)
    )''')
    con.execute('create index if not exists idx_sentero_sessions_token_hash on sentero_sessions(token_hash)')
    con.execute('create index if not exists idx_sentero_sessions_user_id on sentero_sessions(user_id)')
    con.execute('''create table if not exists sentero_password_reset_tokens (
        id integer primary key autoincrement,
        user_id integer not null,
        token_hash text not null unique,
        expires_at text not null,
        used_at text,
        created_at text not null,
        foreign key(user_id) references sentero_users(id)
    )''')
    con.execute('create index if not exists idx_sentero_password_reset_tokens_hash on sentero_password_reset_tokens(token_hash)')
    con.execute('''create table if not exists sensor_roles (id integer primary key autoincrement, role text not null, room text, entity_id text not null, device_id text, friendly_name text, device_class text, domain text, source text, confidence real, active integer not null default 1, created_at text not null, updated_at text not null)''')
    for statement in [
        "alter table sensor_roles add column sensor_type text",
        "alter table sensor_roles add column transport text",
        "alter table sensor_roles add column primary_entity_id text",
        "alter table sensor_roles add column entity_ids_json text",
        "alter table sensor_roles add column last_seen text",
        "alter table sensor_roles add column enabled integer not null default 1",
    ]:
        try:
            con.execute(statement)
        except sqlite3.OperationalError:
            pass
    con.execute('create unique index if not exists idx_sensor_roles_active_role on sensor_roles(role) where active = 1')
    con.execute('''create table if not exists sensor_devices (
        id integer primary key autoincrement,
        room_id text,
        sensor_type text not null,
        transport text not null,
        device_id text,
        primary_entity_id text not null,
        entity_ids_json text not null,
        friendly_name text,
        connected_at text not null,
        last_seen text,
        enabled integer not null default 1,
        created_at text not null,
        updated_at text not null
    )''')
    con.execute('create index if not exists idx_sensor_devices_room_type on sensor_devices(room_id, sensor_type)')
    con.execute('create index if not exists idx_sensor_devices_device_id on sensor_devices(device_id)')
    con.execute('''create table if not exists sensor_discovery_sessions (id integer primary key autoincrement, target_role text not null, target_room text, started_at text not null, ended_at text, status text not null, baseline_snapshot_json text, candidate_snapshot_json text, selected_entity_id text)''')
    for statement in [
        "alter table sensor_discovery_sessions add column pairing_code_provided integer not null default 0",
        "alter table sensor_discovery_sessions add column pairing_detail_json text",
        "alter table sensor_discovery_sessions add column target_sensor_type text",
        "alter table sensor_discovery_sessions add column target_transport text",
        "alter table sensor_discovery_sessions add column timeout_seconds integer",
        "alter table sensor_discovery_sessions add column baseline_device_ids_json text",
        "alter table sensor_discovery_sessions add column current_device_ids_json text",
        "alter table sensor_discovery_sessions add column new_device_ids_json text",
        "alter table sensor_discovery_sessions add column selected_device_id text",
    ]:
        try:
            con.execute(statement)
        except sqlite3.OperationalError:
            pass
    con.execute('insert or ignore into setup_state (id, updated_at) values (1, ?)', (now(),))
    con.execute('insert or ignore into notification_preferences (id, updated_at) values (1, ?)', (now(),))
    con.execute(
        "insert or ignore into notification_channel_settings (channel, enabled, config_json, created_at, updated_at) values ('email', 1, '{}', ?, ?)",
        (now(), now()),
    )


def discovery_detail(row: Any) -> dict[str, Any]:
    try:
        raw = row['pairing_detail_json'] if hasattr(row, '__getitem__') else None
    except (KeyError, IndexError, TypeError):
        raw = None
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def discovery_requires_new_physical_device(row: Any, detail: dict[str, Any] | None = None) -> bool:
    detail = detail or discovery_detail(row)
    if detail.get('mode') == 'mqtt_discovery':
        return True
    try:
        transport = str(row['target_transport'] or '').strip()
    except (KeyError, IndexError, TypeError):
        transport = ''
    return transport == SensorTransport.ZIGBEE.value


def score_candidates(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
    role: str,
    room: str | None,
    started_at: str | datetime,
    require_new: bool = False,
    assigned_identities: set[str] | None = None,
    discovery_session: int | None = None,
) -> list[dict[str, Any]]:
    before = {item.get('entity_id'): item for item in baseline}
    baseline_device_ids = stable_physical_device_ids(baseline)
    current_device_ids = stable_physical_device_ids(current)
    new_device_ids = current_device_ids - baseline_device_ids
    assigned_identities = assigned_identities or set()
    started = parse_time(started_at)
    scored = []
    for item in current:
        entity_id = str(item.get('entity_id') or '')
        if not entity_id:
            continue
        stable_device_id = stable_physical_device_id(item)
        physical_identities = physical_device_identity_values(item)
        identities = mqtt_identity_values(item)
        if identities and identities.intersection(assigned_identities):
            continue
        old = before.get(entity_id, {})
        is_new = entity_id not in before
        is_new_device = bool(stable_device_id and stable_device_id in new_device_ids)
        if require_new:
            if not stable_device_id:
                logger.info(
                    "Discovery candidate rejected",
                    extra={
                        "component": "wizard",
                        "discovery_session": discovery_session,
                        "candidate_device_id": None,
                        "candidate_entity_id": entity_id,
                        "candidate_is_new": False,
                        "candidate_rejected_reason": "missing_stable_device_id",
                    },
                )
                continue
            if stable_device_id in baseline_device_ids:
                logger.info(
                    "Discovery candidate rejected",
                    extra={
                        "component": "wizard",
                        "discovery_session": discovery_session,
                        "candidate_device_id": stable_device_id,
                        "candidate_entity_id": entity_id,
                        "candidate_is_new": False,
                        "candidate_rejected_reason": "device_existed_before_discovery",
                    },
                )
                continue
            if stable_device_id not in new_device_ids:
                logger.info(
                    "Discovery candidate rejected",
                    extra={
                        "component": "wizard",
                        "discovery_session": discovery_session,
                        "candidate_device_id": stable_device_id,
                        "candidate_entity_id": entity_id,
                        "candidate_is_new": False,
                        "candidate_rejected_reason": "device_not_in_current_new_device_set",
                    },
                )
                continue
            logger.info(
                "Discovery candidate accepted for scoring",
                extra={
                    "component": "wizard",
                    "discovery_session": discovery_session,
                    "candidate_device_id": stable_device_id,
                    "candidate_entity_id": entity_id,
                    "candidate_is_new": True,
                },
            )
        state_changed = bool(old) and item.get('state') != old.get('state')
        last_changed_updated = is_after(item.get('last_changed'), started)
        last_updated_updated = is_after(item.get('last_updated'), started)
        changed = is_new or is_new_device or state_changed or last_changed_updated or last_updated_updated
        if not changed:
            continue

        priority = candidate_entity_priority(role, item)
        if priority <= -50:
            continue
        discovery_match = role_candidate_matches(role, item, allow_missing_device_class=True, allow_device_class_mismatch=is_new_device or is_new)
        state_match = role_state_matches(role, item)
        if not discovery_match and not state_match:
            continue

        confidence = 0
        reasons = []
        if is_new_device:
            confidence += 65
            reasons.append('new_device')
        if is_new:
            confidence += 45
            reasons.append('new_entity')
        if state_changed:
            confidence += 35
            reasons.append('state_changed')
        if last_changed_updated or last_updated_updated:
            confidence += 25
            reasons.append('timestamp_updated')
        if class_matches(role, item.get('device_class')):
            confidence += 30
            reasons.append('device_class_match')
        elif role_keyword_matches(role, item, include_model=True):
            confidence += 20
            reasons.append('role_keyword_match')
        if state_match:
            confidence += 20
            reasons.append('state_entity_match')
        if room_matches(room, entity_id, item.get('friendly_name')):
            confidence += 20
            reasons.append('room_match')
        if domain_matches(role, item.get('domain')):
            confidence += 10
            reasons.append('domain_match')
        confidence += priority
        if priority:
            reasons.append(f'entity_priority_{priority}')
        state_value = str(item.get('state') or '').lower()
        if state_value in {'unknown', 'unavailable'}:
            confidence -= 10
            reasons.append(f'state_{state_value}')
        if confidence >= 40:
            scored.append({**item, 'confidence': confidence, 'reasons': reasons, 'is_new': is_new, 'is_new_device': is_new_device, 'stable_device_id': stable_device_id, 'entity_priority': priority})
    sorted_scored = sorted(scored, key=lambda x: (bool(x.get('is_new_device')), role_state_priority(role, x), bool(x.get('is_new')), x['confidence'], parse_time(x.get('last_updated')).timestamp()), reverse=True)
    if require_new:
        return best_candidate_per_physical_device(sorted_scored, current)
    return sorted_scored


def count_changed_entities(baseline: list[dict[str, Any]], current: list[dict[str, Any]], started_at: str | datetime) -> int:
    before = {item.get('entity_id'): item for item in baseline}
    started = parse_time(started_at)
    count = 0
    for item in current:
        entity_id = item.get('entity_id')
        old = before.get(entity_id, {})
        if (
            entity_id not in before
            or (old and item.get('state') != old.get('state'))
            or is_after(item.get('last_changed'), started)
            or is_after(item.get('last_updated'), started)
        ):
            count += 1
    return count


def domain_matches(role: str, domain: Any) -> bool:
    if role_is_presence(role) or role_is_contact(role):
        return str(domain or '') in {'binary_sensor', 'sensor', 'lock', 'switch'}
    if role_is_button(role):
        return str(domain or '') in {'button', 'sensor'}
    if role_is_smart_meter(role):
        return str(domain or '') == 'sensor'
    return bool(domain)


def parse_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or '').strip()
        if text.endswith('Z'):
            text = f'{text[:-1]}+00:00'
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_after(value: Any, threshold: datetime) -> bool:
    if not value:
        return False
    return parse_time(value) > threshold


def role_candidate_matches(role: str, item: dict[str, Any], allow_missing_device_class: bool = False, allow_device_class_mismatch: bool = False) -> bool:
    domain = str(item.get('domain') or '')
    device_class = item.get('device_class')
    has_device_class = bool(str(device_class or '').strip())
    if role_is_presence(role) and has_presence_telemetry(item):
        return True
    source = str(item.get('source') or item.get('platform') or '').strip().lower()
    if (
        role_is_presence(role)
        and allow_missing_device_class
        and source in {'zigbee2mqtt', 'mqtt'}
        and (item.get('source_ref') or item.get('topic') or '/' in str(item.get('entity_id') or ''))
    ):
        return True
    if role_is_smart_meter(role):
        return (
            domain == 'sensor'
            and (allow_device_class_mismatch or class_matches(role, device_class) or smart_meter_candidate_matches(role, item))
            and (allow_missing_device_class or has_device_class or smart_meter_candidate_matches(role, item))
        )
    if role_is_button(role):
        return domain in {'button', 'sensor'} and (
            str(device_class or '').lower() == 'button'
            or str(item.get('payload_key') or '').lower() in {'action', 'button'}
            or role_keyword_matches(role, item, include_model=True)
        )
    if role_is_presence(role):
        return (
            (domain == 'binary_sensor' and (allow_device_class_mismatch or class_matches(role, device_class) or (allow_missing_device_class and not has_device_class)))
            or (domain == 'sensor' and role_keyword_matches(role, item, include_model=allow_device_class_mismatch))
        )
    if role_is_contact(role):
        return (
            (domain == 'binary_sensor' and (allow_device_class_mismatch or class_matches(role, device_class) or (allow_missing_device_class and not has_device_class)))
            or (domain == 'sensor' and contact_sensor_candidate_matches(item, include_model=allow_device_class_mismatch))
            or (domain == 'mqtt' and source in {'zigbee2mqtt', 'mqtt'} and (class_matches(role, device_class) or has_contact_telemetry(item)))
            or (domain in {'lock', 'switch'} and role_keyword_matches(role, item, include_model=True))
        )
    return domain == 'binary_sensor'


def class_matches(role: str, device_class: Any) -> bool:
    dc = str(device_class or '').lower()
    if role_is_presence(role):
        return dc in PRESENCE_CLASSES
    if role_is_contact(role):
        return dc in CONTACT_CLASSES
    if role_is_button(role):
        return dc == 'button'
    if role_is_smart_meter(role):
        if role_is_electricity_meter(role):
            return dc in {'energy', 'power'}
        if 'water' in normalize(role):
            return dc == 'water'
        if 'gas' in normalize(role):
            return dc == 'gas'
        return dc in SMART_METER_CLASSES
    return False


def role_keyword_matches(role: str, item: dict[str, Any], include_model: bool = True) -> bool:
    values = [
        item.get('entity_id'),
        item.get('friendly_name'),
        item.get('original_name'),
        item.get('device_name'),
    ]
    if include_model:
        values.extend([item.get('model'), item.get('manufacturer'), item.get('unique_id'), item.get('identifiers')])
    haystack = normalize(' '.join(str(value or '') for value in values))
    if role_is_presence(role):
        return any(term in haystack for term in ['occupy', 'occupancy', 'motion', 'presence', 'bewegung', 'praesenz', 'präsenz'])
    if role_is_contact(role):
        return any(term in haystack for term in ['contact', 'door', 'window', 'opening', 'tuer', 'tür', 'tuerschloss', 'türschloss', 'fenster'])
    if role_is_button(role):
        return any(term in haystack for term in ['button', 'action', 'knopf', 'taster'])
    return False


def contact_sensor_candidate_matches(item: dict[str, Any], include_model: bool = False) -> bool:
    domain = str(item.get('domain') or '')
    device_class = str(item.get('device_class') or '').lower()
    if domain == 'binary_sensor' and device_class in CONTACT_CLASSES:
        return True
    if not include_model:
        return False
    haystack = normalize(' '.join(str(value or '') for value in [
        item.get('entity_id'),
        item.get('friendly_name'),
        item.get('original_name'),
        item.get('device_name'),
    ]))
    haystack = f"{haystack} {normalize(str(item.get('model') or ''))}"
    return any(term in haystack for term in ['contact', 'door', 'window', 'opening', 'tuer', 'tuerschloss', 'fenster'])


def candidate_entity_priority(role: str, item: dict[str, Any]) -> int:
    domain = str(item.get('domain') or '')
    device_class = str(item.get('device_class') or '').lower()
    haystack = normalize(' '.join(str(value or '') for value in [
        item.get('entity_id'),
        item.get('friendly_name'),
        item.get('original_name'),
        item.get('device_name'),
        item.get('model'),
    ]))
    if domain in {'button', 'update'} and not role_is_button(role):
        return -80
    if device_class in {'battery', 'signal_strength'} or any(term in haystack for term in ['batterie', 'battery', 'rssi', 'lqi', 'firmware', 'identifizieren']):
        return -50
    if role_is_presence(role):
        if has_presence_telemetry(item):
            return 60
        if domain == 'binary_sensor' and class_matches(role, device_class):
            return 40
        if any(term in haystack for term in ['occupy', 'occupancy', 'presence', 'praesenz', 'präsenz', 'motion', 'bewegung']):
            return 25
        if device_class in {'illuminance'}:
            return 5
    if role_is_contact(role):
        if domain == 'binary_sensor' and class_matches(role, device_class):
            return 40
        if domain == 'lock' and any(term in haystack for term in ['turschloss', 'tuerschloss', 'türschloss', 'door', 'lock']):
            return 35
        if domain == 'switch' and any(term in haystack for term in ['door', 'tuer', 'tür']):
            return 20
    if role_is_button(role):
        if domain == 'button' or device_class == 'button':
            return 40
        if any(term in haystack for term in ['button', 'action', 'knopf', 'taster']):
            return 25
    if role_is_smart_meter(role):
        if domain != 'sensor':
            return -80
        if class_matches(role, device_class):
            return 45
        if smart_meter_candidate_matches(role, item):
            return 35
    return 0


def resolve_role_state(row: dict[str, Any], states: list[dict[str, Any]], by_entity: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    entity_id = str(row.get('entity_id') or '').strip()
    role = str(row.get('role') or '')

    # MQTT device state: the exact physical topic is authoritative.  Do this
    # before the generic identity matching because bridge/devices expose rows use
    # the device topic as source_ref even though their actual topic is
    # zigbee2mqtt/bridge/devices.  Those rows are metadata, not live telemetry.
    exact_device_state = next(
        (
            item
            for item in states
            if is_live_mqtt_state(item)
            and exact_mqtt_device_topic_match(row, item)
            and role_state_matches(role, item)
        ),
        None,
    )
    if exact_device_state is not None:
        return exact_device_state

    direct = by_entity.get(entity_id)
    if (
        direct
        and not is_metadata_only_state(direct)
        and sensor_reachable_status(direct) is not False
        and role_state_matches(role, direct)
    ):
        return direct

    wanted_ids = mqtt_identity_values(row)
    if wanted_ids:
        candidates = [
            item
            for item in states
            if not is_metadata_only_state(item)
            and wanted_ids.intersection(mqtt_identity_values(item))
            and role_state_matches(role, item)
        ]
        selected = sorted(
            candidates,
            key=lambda item: (
                exact_mqtt_device_topic_match(row, item),
                is_live_mqtt_state(item),
                sensor_reachable_status(item) is True,
                has_presence_telemetry(item) if role_is_presence(role) else False,
                role_state_priority(role, item),
            ),
            reverse=True,
        )
        if selected:
            return selected[0]

    device_id = str(row.get('device_id') or '').strip()
    candidates: list[dict[str, Any]] = []
    if device_id:
        candidates = [
            item
            for item in states
            if not is_metadata_only_state(item)
            and str(item.get('device_id') or '') == device_id
            and role_state_matches(role, item)
        ]
    if not candidates and entity_id:
        prefix = entity_id.rsplit('_', 1)[0] if '_' in entity_id else entity_id.rsplit('.', 1)[-1]
        candidates = [
            item
            for item in states
            if not is_metadata_only_state(item)
            and prefix
            and str(item.get('entity_id') or '').startswith(prefix)
            and role_state_matches(role, item)
        ]
    if not candidates:
        room = str(row.get('room') or '')
        label = str(row.get('friendly_name') or row.get('role') or '')
        candidates = [
            item
            for item in states
            if not is_metadata_only_state(item)
            and role_state_matches(role, item)
            and (
                room_matches(room, str(item.get('entity_id') or ''), item.get('friendly_name'))
                or (label and normalize(label).split('_')[0] in normalize(f"{item.get('entity_id') or ''} {item.get('friendly_name') or ''}"))
            )
        ]
    reachable = [item for item in candidates if state_is_reachable(item.get('state'))]
    selected = sorted(
        reachable or candidates,
        key=lambda item: (
            is_live_mqtt_state(item),
            role_state_priority(role, item),
        ),
        reverse=True,
    )
    if selected:
        return selected[0]
    return direct if direct and not is_metadata_only_state(direct) else None


def role_state_matches(role: str, item: dict[str, Any]) -> bool:
    domain = str(item.get('domain') or str(item.get('entity_id') or '').split('.', 1)[0])
    if role_is_presence(role) and has_presence_telemetry(item):
        return True
    if role_is_button(role):
        return domain == 'button' or str(item.get('device_class') or '').lower() == 'button' or str(item.get('payload_key') or '').lower() in {'action', 'button'}
    if role_is_smart_meter(role):
        return role_candidate_matches(role, item, allow_missing_device_class=True, allow_device_class_mismatch=False)
    if domain in {'button', 'update', 'number', 'select'}:
        return False
    haystack = normalize(' '.join(str(item.get(key) or '') for key in ['entity_id', 'friendly_name', 'original_name', 'device_name']))
    if any(term in haystack for term in ['battery', 'batterie', 'voltage', 'spannung', 'illuminance', 'beleuchtungsstaerke', 'humidity', 'luftfeuchtigkeit', 'temperature', 'temperatur', 'identify', 'identifizieren', 'firmware']):
        return False
    return role_candidate_matches(role, item, allow_missing_device_class=True, allow_device_class_mismatch=False)


def role_state_priority(role: str, item: dict[str, Any]) -> int:
    entity_id = normalize(str(item.get('entity_id') or ''))
    device_class = str(item.get('device_class') or '').lower()
    score = candidate_entity_priority(role, item)
    if role_is_presence(role):
        if any(term in entity_id for term in ['presence', 'praesenz', 'occupancy', 'occupy']):
            score += 25
        if 'pir' in entity_id or 'motion' in entity_id or 'bewegung' in entity_id:
            score += 10
        if device_class in {'occupancy', 'presence'}:
            score += 20
    if role_is_contact(role):
        if any(term in entity_id for term in ['contact', 'door', 'tuer', 'window', 'fenster']):
            score += 25
        if device_class in CONTACT_CLASSES:
            score += 20
    if role_is_smart_meter(role):
        if any(term in entity_id for term in ['energy', 'electricity', 'strom', 'power', 'water', 'wasser', 'gas']):
            score += 25
        if device_class in SMART_METER_CLASSES:
            score += 25
    return score


def normalize_transport(value: Any, source: Any = None) -> str:
    text = str(value or '').strip().lower()
    if text in {SensorTransport.ZIGBEE.value, 'zigbee2mqtt'}:
        return SensorTransport.ZIGBEE.value
    if text in {SensorTransport.WIFI_ESPHOME.value, 'wifi', 'esp32', 'mqtt'}:
        return SensorTransport.WIFI_ESPHOME.value if str(source or '').strip() == 'mqtt' else SensorTransport.ZIGBEE.value
    source_text = str(source or '').strip().lower()
    if source_text == 'mqtt':
        return SensorTransport.WIFI_ESPHOME.value
    return SensorTransport.ZIGBEE.value


def sensor_type_from_role(role: str) -> str:
    if role_is_contact(role):
        return 'door'
    if role_is_presence(role):
        return 'presence'
    if role_is_electricity_meter(role):
        return 'electricity_meter'
    if 'water' in normalize(role):
        return 'water_meter'
    if 'gas' in normalize(role):
        return 'gas_meter'
    if role_is_button(role):
        return 'button'
    return 'sensor'


def entity_ids_for_physical_device(states: list[dict[str, Any]], selected: dict[str, Any]) -> list[str]:
    selected_entity_id = str(selected.get('entity_id') or '').strip()
    device_id = stable_physical_device_id(selected) or str(selected.get('device_id') or '').strip()
    identities = mqtt_identity_values(selected)
    grouped: list[str] = []
    for item in states:
        entity_id = str(item.get('entity_id') or '').strip()
        if not entity_id:
            continue
        if str(item.get('domain') or '') == 'mqtt' and str(item.get('payload_key') or '') == 'state':
            continue
        item_device_id = stable_physical_device_id(item) or str(item.get('device_id') or '').strip()
        same_device = bool(device_id and item_device_id == device_id)
        same_mqtt_device = bool(identities and identities.intersection(mqtt_identity_values(item)))
        if same_device or same_mqtt_device or entity_id == selected_entity_id:
            grouped.append(entity_id)
    if selected_entity_id and selected_entity_id not in grouped:
        grouped.insert(0, selected_entity_id)
    return sorted(set(grouped), key=lambda value: (value != selected_entity_id, value))


def latest_seen_for_entities(states: list[dict[str, Any]], entity_ids: list[str]) -> str | None:
    wanted = set(entity_ids)
    timestamps = [
        parse_time(item.get('last_updated') or item.get('last_changed'))
        for item in states
        if str(item.get('entity_id') or '') in wanted and (item.get('last_updated') or item.get('last_changed'))
    ]
    if not timestamps:
        return None
    return max(timestamps).isoformat(timespec='seconds')


def testable_state_entity(item: dict[str, Any]) -> bool:
    entity_id = str(item.get('entity_id') or '')
    domain = str(item.get('domain') or entity_id.split('.', 1)[0] if '.' in entity_id else '')
    source = str(item.get('source') or item.get('platform') or '').strip().lower()
    payload_key = str(item.get('payload_key') or '').strip().lower()
    if domain in {'button', 'update'}:
        return False
    haystack = normalize(f"{entity_id} {item.get('friendly_name') or ''} {item.get('original_name') or ''}")
    if any(term in haystack for term in ['identifizieren', 'identify', 'firmware']):
        return False
    if payload_key == 'state' and source in {'zigbee2mqtt', 'mqtt'} and (item.get('topic') or item.get('source_ref')):
        return True
    return domain in {'binary_sensor', 'sensor', 'lock', 'switch', 'mqtt'}


def sensor_reachable_status(state: dict[str, Any] | None) -> bool | None:
    if not state:
        return False
    value = str(state.get('state') or '').strip().lower()
    if value == 'unavailable':
        return False
    if value in {'', 'unknown', 'none'}:
        if mqtt_item_has_telemetry(state):
            return True
        return None
    return True


def presence_live_state_ttl_seconds() -> int:
    raw = os.getenv('SENTERO_PRESENCE_LIVE_STATE_TTL_SECONDS')
    try:
        value = int(raw) if raw is not None else config_int('sensors.presence_live_state_ttl_seconds', DEFAULT_PRESENCE_LIVE_STATE_TTL_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_PRESENCE_LIVE_STATE_TTL_SECONDS
    return max(value, 30)


def presence_unreachable_grace_seconds() -> int:
    raw = os.getenv('SENTERO_PRESENCE_UNREACHABLE_GRACE_SECONDS')
    try:
        value = int(raw) if raw is not None else config_int('sensors.presence_unreachable_grace_seconds', DEFAULT_PRESENCE_UNREACHABLE_GRACE_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_PRESENCE_UNREACHABLE_GRACE_SECONDS
    return max(value, presence_live_state_ttl_seconds())


def sensor_health_stale_seconds() -> int:
    raw = os.getenv('SENTERO_SENSOR_HEALTH_STALE_SECONDS')
    try:
        value = int(raw) if raw is not None else config_int('sensors.health_stale_seconds', DEFAULT_SENSOR_HEALTH_STALE_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_SENSOR_HEALTH_STALE_SECONDS
    return max(value, presence_live_state_ttl_seconds())


def sensor_unreachable_grace_seconds() -> int:
    raw = os.getenv('SENTERO_SENSOR_UNREACHABLE_GRACE_SECONDS')
    try:
        value = int(raw) if raw is not None else config_int('sensors.unreachable_grace_seconds', DEFAULT_SENSOR_UNREACHABLE_GRACE_SECONDS)
    except (TypeError, ValueError):
        value = DEFAULT_SENSOR_UNREACHABLE_GRACE_SECONDS
    return max(value, sensor_health_stale_seconds())


def sensor_state_age_seconds(state: dict[str, Any] | None) -> int | None:
    if not state:
        return None
    timestamp = state.get('last_updated') or state.get('last_changed')
    if not timestamp:
        return None
    return max(0, int((datetime.now(timezone.utc) - parse_time(timestamp)).total_seconds()))


def presence_live_state_is_stale(role: dict[str, Any], state: dict[str, Any] | None) -> bool:
    if not state or not role_is_presence(str(role.get('role') or '')):
        return False
    source = str(state.get('source') or state.get('platform') or role.get('source') or '').strip().lower()
    if source not in {'zigbee2mqtt', 'mqtt'} and not (state.get('topic') or state.get('source_ref') or role.get('entity_id')):
        return False
    age = sensor_state_age_seconds(state)
    return age is not None and age > presence_live_state_ttl_seconds()


def presence_live_state_is_unreachable(role: dict[str, Any], state: dict[str, Any] | None) -> bool:
    if not presence_live_state_is_stale(role, state):
        return False
    age = sensor_state_age_seconds(state)
    return age is not None and age > presence_unreachable_grace_seconds()


def mqtt_state_is_health_stale(role: dict[str, Any], state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if role_is_presence(str(role.get('role') or '')):
        return presence_live_state_is_stale(role, state)
    source = str(state.get('source') or state.get('platform') or role.get('source') or '').strip().lower()
    if source not in {'zigbee2mqtt', 'mqtt'} and not (state.get('topic') or state.get('source_ref') or role.get('entity_id')):
        return False
    age = sensor_state_age_seconds(state)
    return age is not None and age > sensor_health_stale_seconds()


def mqtt_state_is_health_unreachable(role: dict[str, Any], state: dict[str, Any] | None) -> bool:
    if not state:
        return False
    if role_is_presence(str(role.get('role') or '')):
        return presence_live_state_is_unreachable(role, state)
    if not mqtt_state_is_health_stale(role, state):
        return False
    age = sensor_state_age_seconds(state)
    return age is not None and age > sensor_unreachable_grace_seconds()


def state_is_reachable(value: Any) -> bool:
    return str(value or '').strip().lower() not in {'', 'unknown', 'unavailable', 'none'}


def is_metadata_only_state(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    if item.get('metadata_only') is True:
        return True
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    return attrs.get('metadata_only') is True


def is_live_mqtt_state(item: dict[str, Any] | None) -> bool:
    if not item or is_metadata_only_state(item):
        return False
    return (
        str(item.get('domain') or '').strip().lower() == 'mqtt'
        and str(item.get('payload_key') or '').strip().lower() == 'state'
        and bool(str(item.get('topic') or '').strip())
    )


def exact_mqtt_device_topic_match(role: dict[str, Any], state: dict[str, Any]) -> bool:
    """Match the registered MQTT device topic against the message's real topic.

    Deliberately do not use state.source_ref here. bridge/devices metadata rows
    carry source_ref=zigbee2mqtt/<friendly_name> and would otherwise look like
    exact live-state matches even though their real topic is bridge/devices.
    """
    if is_metadata_only_state(state):
        return False
    wanted = mqtt_topic_values(role)
    actual_topic = str(state.get('topic') or '').strip().strip('/').lower()
    return bool(actual_topic and actual_topic in wanted)


def mqtt_telemetry_value_is_valid(value: Any) -> bool:
    # False and numeric zero are real sensor values and must never be rejected.
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {'', 'unknown', 'unavailable', 'none', 'null'}
    return True


def mqtt_item_has_telemetry(item: dict[str, Any] | None) -> bool:
    if not item or item.get('metadata_only') is True:
        return False
    source = str(item.get('source') or item.get('platform') or '').strip().lower()
    if source not in {'zigbee2mqtt', 'mqtt'} and not (item.get('topic') or item.get('source_ref')):
        return False
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    if attrs.get('metadata_only') is True:
        return False
    telemetry_keys = {
        'battery',
        'battery_low',
        'humidity',
        'illuminance',
        'illuminance_lux',
        'linkquality',
        'motion',
        'motion_state',
        'occupancy',
        'presence',
        'signal_quality',
        'temperature',
        'voltage',
        'tamper',
        'last_seen',
    }
    return any(key in attrs and mqtt_telemetry_value_is_valid(attrs.get(key)) for key in telemetry_keys) or any(
        key in item and mqtt_telemetry_value_is_valid(item.get(key)) for key in telemetry_keys
    )


def has_presence_telemetry(item: dict[str, Any] | None) -> bool:
    if not item or item.get('metadata_only') is True:
        return False
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    if attrs.get('metadata_only') is True:
        return False
    return any(
        (key in item and mqtt_telemetry_value_is_valid(item.get(key)))
        or (key in attrs and mqtt_telemetry_value_is_valid(attrs.get(key)))
        for key in (
            'presence',
            'occupancy',
            'motion',
            'motion_state',
            'moving_target',
            'static_target',
        )
    )


def has_contact_telemetry(item: dict[str, Any] | None) -> bool:
    if not item or item.get('metadata_only') is True:
        return False
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    if attrs.get('metadata_only') is True:
        return False
    return any(
        (key in item and mqtt_telemetry_value_is_valid(item.get(key)))
        or (key in attrs and mqtt_telemetry_value_is_valid(attrs.get(key)))
        for key in ('contact', 'open')
    )


def battery_level_from_state(state: dict[str, Any] | None) -> int | None:
    if not state:
        return None
    attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
    for value in (state.get('battery'), attrs.get('battery')):
        parsed = parse_battery(value)
        if parsed is not None:
            return parsed
    return None


def combined_mqtt_telemetry_state(role: dict[str, Any], state: dict[str, Any] | None, states: list[dict[str, Any]]) -> dict[str, Any] | None:
    raw_candidates = [item for item in [state, *bound_mqtt_sensor_states(role, states)] if isinstance(item, dict)]
    candidates = [candidate for candidate in unique_state_candidates(raw_candidates) if not is_metadata_only_state(candidate)]
    if not candidates:
        return state if state and not is_metadata_only_state(state) else None

    # The exact device-topic JSON state is the primary source.  Individual
    # telemetry rows may supplement it but must never replace it or overwrite a
    # valid False/0 value with unknown metadata.
    primary = next(
        (
            candidate
            for candidate in candidates
            if is_live_mqtt_state(candidate)
            and exact_mqtt_device_topic_match(role, candidate)
        ),
        None,
    )
    primary = primary or next((candidate for candidate in candidates if is_live_mqtt_state(candidate)), None)
    primary = primary or candidates[0]

    # Merge the primary first, then supplemental rows.  Invalid telemetry values
    # (unknown/unavailable/none/null) are ignored. False and 0 remain valid.
    ordered = [primary, *[candidate for candidate in candidates if candidate is not primary]]
    merged_attrs: dict[str, Any] = {}
    merged: dict[str, Any] = {}
    telemetry_keys = (
        'battery',
        'battery_low',
        'humidity',
        'illuminance',
        'illuminance_lux',
        'linkquality',
        'motion',
        'motion_state',
        'occupancy',
        'presence',
        'signal_quality',
        'temperature',
        'voltage',
        'last_seen',
    )

    for candidate in ordered:
        attrs = candidate.get('attributes') if isinstance(candidate.get('attributes'), dict) else {}
        for key, value in attrs.items():
            if key == 'metadata_only':
                continue
            if key in telemetry_keys and not mqtt_telemetry_value_is_valid(value):
                continue
            if value is not None:
                merged_attrs[key] = value

        for key in telemetry_keys:
            if key in candidate and mqtt_telemetry_value_is_valid(candidate.get(key)):
                merged[key] = candidate.get(key)

        payload_key = str(candidate.get('payload_key') or '').strip()
        payload_value = candidate.get('state')
        if payload_key and payload_key not in {'state', 'availability'} and mqtt_telemetry_value_is_valid(payload_value):
            merged[payload_key] = payload_value

    # Re-apply values from the authoritative exact-topic state last, so a
    # supplemental entity cannot overwrite current device JSON telemetry.
    primary_attrs = primary.get('attributes') if isinstance(primary.get('attributes'), dict) else {}
    for key in telemetry_keys:
        primary_value = primary.get(key)
        if mqtt_telemetry_value_is_valid(primary_value):
            merged[key] = primary_value
        elif key in primary_attrs and mqtt_telemetry_value_is_valid(primary_attrs.get(key)):
            merged[key] = primary_attrs.get(key)

    return {
        **primary,
        **merged,
        'attributes': {
            **primary_attrs,
            **merged_attrs,
            **merged,
        },
    }


def environmental_metrics_from_state(role: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        'temperature': find_numeric_metric(role, states, {'temperature'}, {'temperature', 'temperatur'}),
        'humidity': find_numeric_metric(role, states, {'humidity'}, {'humidity', 'luftfeuchtigkeit'}),
        'illuminance': find_numeric_metric(role, states, {'illuminance', 'illuminance_lux'}, {'illuminance', 'illuminance_lux', 'beleuchtungsstaerke', 'beleuchtungsstarke', 'helligkeit'}),
    }


def find_numeric_metric(role: dict[str, Any], states: list[dict[str, Any]], keys: set[str], name_terms: set[str]) -> float | None:
    direct = metric_from_item(role, keys)
    if direct is not None:
        return direct
    candidates = bound_mqtt_sensor_states(role, states)
    for state in candidates:
        if not metric_entity_allowed(state):
            continue
        payload_key = normalize(str(state.get('payload_key') or ''))
        device_class = normalize(str(state.get('device_class') or ''))
        entity_id = normalize(str(state.get('entity_id') or ''))
        friendly_name = normalize(str(state.get('friendly_name') or state.get('original_name') or ''))
        if payload_key not in keys and device_class not in keys and not any(term in entity_id or term in friendly_name for term in name_terms):
            continue
        value = metric_from_item(state, keys)
        if value is None:
            value = parse_float(state.get('state'))
        if value is not None:
            return value
    return None


def metric_entity_allowed(state: dict[str, Any]) -> bool:
    domain = normalize(str(state.get('domain') or ''))
    if domain and domain != 'sensor':
        return False
    haystack = normalize(' '.join(str(state.get(key) or '') for key in ('entity_id', 'friendly_name', 'original_name', 'device_name', 'payload_key')))
    return not any(term in haystack for term in {
        'calibration',
        'kalibrierung',
        'interval',
        'sensitivity',
        'sensitivitaet',
        'sensitivity',
        'distance',
        'distanz',
        'mode',
    })


def metric_from_item(item: dict[str, Any], keys: set[str]) -> float | None:
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    for key in keys:
        value = parse_float(item.get(key))
        if value is not None:
            return value
        value = parse_float(attrs.get(key))
        if value is not None:
            return value
    return None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(',', '.')
    if not text or text.lower() in {'none', 'unknown', 'unavailable', 'nan'}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def power_source_from_state(state: dict[str, Any] | None) -> str | None:
    if not state:
        return None
    attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
    value = str(state.get('power_source') or attrs.get('power_source') or attrs.get('powerSource') or '').strip().lower()
    if value in {'usb', 'mains', 'wired', 'external', 'power_adapter', 'netzteil', 'netzbetrieb'}:
        return 'usb' if value == 'usb' else 'mains'
    if value in {'battery', 'battery_powered', 'akku', 'batterie'}:
        return 'battery'
    return None


def c1001_telemetry_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not state:
        return {'presence': None, 'fall_detected': None, 'motion': None, 'hp_led': None, 'fall_led': None, 'led_status': None, 'writable_settings': None}
    attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
    presence = parse_bool_value(first_present(state, attrs, 'presence'))
    if presence is None and str(state.get('payload_key') or '').strip().lower() == 'presence':
        presence = parse_bool_value(state.get('state'))
    fall_detected = parse_bool_value(first_present(state, attrs, 'fall_detected'))
    motion = first_present(state, attrs, 'motion')
    if motion is None:
        motion = first_present(state, attrs, 'motion_state')
    if motion is not None:
        motion = normalize_motion_state(motion) or str(motion)
    hp_led = parse_bool_value(first_present(state, attrs, 'hp_led'))
    fall_led = parse_bool_value(first_present(state, attrs, 'fall_led'))
    raw_led_status = first_present(state, attrs, 'led_status')
    raw_writable_settings = first_present(state, attrs, 'writable_settings')
    writable_settings = [str(item) for item in raw_writable_settings] if isinstance(raw_writable_settings, list) else None
    led_status = raw_led_status if isinstance(raw_led_status, dict) else None
    if led_status:
        if hp_led is None:
            hp_led = parse_bool_value(led_status.get('hp_led'))
        if fall_led is None:
            fall_led = parse_bool_value(led_status.get('fall_led'))
    if hp_led is not None or fall_led is not None:
        led_status = {
            **(led_status or {}),
            'hp_led': hp_led,
            'fall_led': fall_led,
            'all_on': bool(hp_led and fall_led),
            'any_on': bool(hp_led or fall_led),
        }
    return {'presence': presence, 'fall_detected': fall_detected, 'motion': motion, 'hp_led': hp_led, 'fall_led': fall_led, 'led_status': led_status, 'writable_settings': writable_settings}


def generic_presence_telemetry_from_state(role: dict[str, Any], state: dict[str, Any] | None, states: list[dict[str, Any]]) -> dict[str, Any]:
    if not role_is_presence(str(role.get('role') or '')):
        return {'presence': None, 'motion': None}
    presence = generic_presence_value(state)
    if presence is None:
        presence_state = find_presence_state(role, states)
        presence = generic_presence_value(presence_state)
    motion_state = find_motion_state(role, states)
    if presence is False:
        return {'presence': False, 'motion': 'None'}
    if presence is True:
        return {'presence': True, 'motion': normalize_motion_state(motion_state, default='Still')}
    return {'presence': None, 'motion': normalize_motion_state(motion_state)}


def motion_state_implies_presence(value: Any) -> bool:
    text = normalize(str(value or ''))
    return text in {
        'move', 'moving', 'movement', 'active', 'motion', 'detected', 'moving_target',
        'static', 'static_target', 'presence', 'present', 'still', 'stationary', 'standstill',
    }


def effective_presence_value(
    explicit_presence: Any,
    inferred_presence: Any = None,
    motion_state: Any = None,
    motion: Any = None,
) -> bool | None:
    """Resolve room presence conservatively for combined radar/PIR sensors.

    A sensor can briefly publish presence=false while its motion_state still says
    moving/static/still. Those states are direct evidence that somebody is in the
    room, so they must win over a contradictory false presence bit. A true
    presence bit always remains true. Only when no positive motion/presence hint
    exists do we accept false/None.
    """
    explicit = parse_bool_value(explicit_presence)
    inferred = parse_bool_value(inferred_presence)
    if explicit is True or inferred is True:
        return True
    if motion_state_implies_presence(motion_state) or motion_state_implies_presence(motion):
        return True
    if explicit is False or inferred is False:
        return False
    return None


def generic_presence_value(state: dict[str, Any] | None) -> bool | None:
    if not state:
        return None
    attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}

    explicit = None
    for key in ('presence', 'occupancy'):
        value = parse_bool_value(first_present(state, attrs, key))
        if value is not None:
            explicit = value
            if value is True:
                return True
            break

    motion_state = first_present(state, attrs, 'motion_state')
    motion = first_present(state, attrs, 'motion')
    if motion_state_implies_presence(motion_state) or motion_state_implies_presence(motion):
        return True

    if explicit is not None:
        return explicit

    motion_bool = parse_bool_value(motion)
    if motion_bool is not None:
        return motion_bool

    normalized_motion_state = normalize(str(motion_state or ''))
    if normalized_motion_state in {'none', 'clear', 'off', 'false', '0', 'no_motion', 'no motion'}:
        return False

    payload_key = normalize(str(state.get('payload_key') or ''))
    device_class = normalize(str(state.get('device_class') or ''))
    if payload_key in {'occupancy', 'presence'} or device_class in {'occupancy', 'presence'}:
        return parse_bool_value(state.get('state'))
    return None


def find_presence_state(role: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for state in bound_mqtt_sensor_states(role, states):
        payload_key = normalize(str(state.get('payload_key') or ''))
        device_class = normalize(str(state.get('device_class') or ''))
        if payload_key in {'occupancy', 'presence'} or device_class in {'occupancy', 'presence'}:
            candidates.append(state)
    return sorted(candidates, key=lambda item: role_state_priority(str(role.get('role') or ''), item), reverse=True)[0] if candidates else None


def find_motion_state(role: dict[str, Any], states: list[dict[str, Any]]) -> str | None:
    for state in bound_mqtt_sensor_states(role, states):
        attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        direct = first_present(state, attrs, 'motion_state')
        if direct is None:
            direct = first_present(state, attrs, 'motion')
        if direct is not None:
            return str(direct).strip()
        payload_key = normalize(str(state.get('payload_key') or ''))
        device_class = normalize(str(state.get('device_class') or ''))
        haystack = normalize(' '.join(str(state.get(key) or '') for key in ('entity_id', 'friendly_name', 'original_name', 'device_name')))
        if payload_key not in {'motion_state', 'motion'} and device_class not in {'motion_state', 'motion'} and 'motion_state' not in haystack:
            continue
        return str(state.get('state') or '').strip()
    return None


def bound_mqtt_sensor_states(role: dict[str, Any], states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # bridge/devices expose rows are useful for discovery but must never
    # participate in runtime telemetry resolution.
    live_states = [state for state in states if not is_metadata_only_state(state)]

    topic_values = mqtt_topic_values(role)
    if topic_values:
        exact_device_matches = [
            state
            for state in live_states
            if exact_mqtt_device_topic_match(role, state)
        ]
        # Prefer real device JSON state before any related per-property MQTT rows.
        exact_device_matches = sorted(
            exact_device_matches,
            key=lambda state: is_live_mqtt_state(state),
            reverse=True,
        )

        identities = physical_device_identity_values(role)
        identity_matches = [
            state
            for state in live_states
            if identities and identities.intersection(hard_mqtt_identity_values(state))
        ]
        return unique_state_candidates([*exact_device_matches, *identity_matches])

    identities = physical_device_identity_values(role)
    if not identities:
        identities = hard_mqtt_identity_values(role)
    if not identities:
        return []
    return [
        state
        for state in live_states
        if identities.intersection(hard_mqtt_identity_values(state))
    ]


def unique_state_candidates(states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for state in states:
        key = "|".join(str(state.get(item) or '') for item in ('entity_id', 'topic', 'source_ref', 'payload_key'))
        if key in seen:
            continue
        seen.add(key)
        result.append(state)
    return result


def exact_mqtt_topic_match(role: dict[str, Any], state: dict[str, Any]) -> bool:
    wanted = mqtt_topic_values(role)
    current = mqtt_topic_values(state)
    return bool(wanted and current and wanted.intersection(current))


def mqtt_topic_values(item: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    has_topic_binding = False
    for key in ('topic', 'source_ref', 'entity_id'):
        raw = str(item.get(key) or '').strip().strip('/')
        if '/' in raw:
            has_topic_binding = True
            values.add(raw.lower())
    source = str(item.get('source') or item.get('platform') or '').strip().lower()
    friendly_name = str(item.get('friendly_name') or '').strip()
    if has_topic_binding and friendly_name and source in {'zigbee2mqtt', 'mqtt'}:
        values.add(f"zigbee2mqtt/{friendly_name}".strip('/').lower())
    return values


def hard_mqtt_identity_values(item: dict[str, Any]) -> set[str]:
    values = physical_device_identity_values(item)
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    for raw in (
        item.get('device_id'),
        attrs.get('device_id'),
        item.get('entity_id'),
        item.get('source_ref'),
        item.get('topic'),
        item.get('unique_id'),
    ):
        add_physical_identity(values, raw)
    return {value for value in values if value}


def normalize_motion_state(value: Any, default: str | None = None) -> str | None:
    text = normalize(str(value or ''))
    if not text:
        return default
    if text in {'move', 'moving', 'movement', 'active', 'motion', 'detected', 'large', 'small'}:
        return 'Active'
    if text in {'still', 'static', 'stationary', 'standstill', 'presence'}:
        return 'Still'
    if text in {'none', 'clear', 'off', 'false', '0', 'no_motion', 'no motion'}:
        return 'None'
    return default


def first_present(item: dict[str, Any], attrs: dict[str, Any], key: str) -> Any:
    if key in item and item.get(key) is not None:
        return item.get(key)
    if key in attrs and attrs.get(key) is not None:
        return attrs.get(key)
    return None


def motion_state_from_state(state: dict[str, Any] | None) -> str | None:
    if not state:
        return None
    attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
    value = first_present(state, attrs, 'motion_state')
    if value is None:
        value = first_present(state, attrs, 'motion')
    return str(value).strip() if value is not None else None


def find_mqtt_availability_state(role: dict[str, Any], states: list[dict[str, Any]]) -> bool | None:
    source = str(role.get('source') or role.get('platform') or '').strip().lower()
    if source not in {'zigbee2mqtt', 'mqtt'} and not (role.get('topic') or role.get('source_ref') or role.get('entity_id')):
        return None
    wanted_ids = mqtt_identity_values(role)
    for state in states:
        payload_key = str(state.get('payload_key') or '').strip().lower()
        device_class = str(state.get('device_class') or '').strip().lower()
        entity_id = str(state.get('entity_id') or '').strip().lower()
        topic = str(state.get('topic') or state.get('source_ref') or '').strip().lower()
        if payload_key != 'availability' and device_class != 'connectivity' and not topic.endswith('/availability') and not entity_id.endswith('_availability'):
            continue
        if not wanted_ids.intersection(mqtt_identity_values(state)):
            continue
        attrs = state.get('attributes') if isinstance(state.get('attributes'), dict) else {}
        value = str(attrs.get('status') or attrs.get('availability') or state.get('state') or '').strip().lower()
        if value in {'offline', 'unavailable', 'off', 'false', '0', 'lost', 'disconnected'}:
            return False
        if value in {'online', 'available', 'on', 'true', '1', 'connected'}:
            return True
    return None


def esp32_topic_prefix() -> str:
    return (
        os.getenv('SENTERO_ESP32_TOPIC_PREFIX')
        or config_str('esp32.topic_prefix', '')
        or config_str('mqtt.esp32_topic_prefix', '')
        or 'sentero'
    ).strip().strip('/') or 'sentero'


def esp32_command_topic(device_id: str) -> str:
    return f"{esp32_topic_prefix()}/{str(device_id).strip()}/command"


def esp32_status_topic(device_id: str) -> str:
    return f"{esp32_topic_prefix()}/{str(device_id).strip()}/status"


def esp32_factory_reset_ack_matches(payload: Any, device_id: str) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get('status') or '').strip().lower()
    ack_device = str(payload.get('device_id') or payload.get('deviceId') or '').strip()
    return status == 'factory_resetting' and (not ack_device or ack_device == str(device_id).strip())


def esp32_command_ack_matches(payload: Any, device_id: str, command: str) -> bool:
    if not isinstance(payload, dict):
        return False
    status = str(payload.get('status') or '').strip().lower()
    if status not in {'command_accepted', 'command_rejected'}:
        return False
    ack_device = str(payload.get('device_id') or payload.get('deviceId') or '').strip()
    if ack_device and ack_device != str(device_id).strip():
        return False
    ack_command = str(payload.get('command') or '').strip().lower().replace('-', '_')
    wanted_command = str(command or '').strip().lower().replace('-', '_')
    return not ack_command or ack_command == wanted_command


def mqtt_identity_values(item: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ('entity_id', 'source_ref', 'topic', 'device_id', 'unique_id', 'original_name', 'device_name', 'friendly_name'):
        raw = str(item.get(key) or '').strip()
        if not raw:
            continue
        values.add(raw)
        values.add(raw.lower())
        values.add(slug_identity(raw))
        if '/' in raw:
            tail = raw.rsplit('/', 1)[-1].strip()
            if tail and tail.lower() not in {'state', 'availability', 'status', 'command'}:
                values.add(tail)
                values.add(tail.lower())
                values.add(slug_identity(tail))
        if '.' in raw:
            tail = raw.rsplit('.', 1)[-1].strip()
            if tail:
                values.add(tail)
                values.add(tail.lower())
                values.add(slug_identity(tail))
        match = re.search(r'0x[0-9a-fA-F]{8,16}', raw)
        if match:
            values.add(match.group(0).lower())
    return {value for value in values if value}


def physical_device_identity_values(item: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    for raw in (
        item.get('device_id'),
        attrs.get('device_id'),
        item.get('ieee_address'),
        attrs.get('ieee_address'),
    ):
        add_physical_identity(values, raw)
    for domain, value in parse_identifiers(item.get('identifiers')):
        if normalize(domain) in {'mqtt', 'zigbee2mqtt'}:
            add_physical_identity(values, value)
    return {value for value in values if value}


def is_zigbee2mqtt_mapping(item: dict[str, Any]) -> bool:
    source = str(item.get('source') or '').strip().lower()
    if source == 'zigbee2mqtt':
        return True
    for field in ('device_id', 'entity_id', 'primary_entity_id', 'source_ref', 'topic'):
        value = str(item.get(field) or '').strip()
        if value.startswith('zigbee2mqtt/'):
            return True
        if is_ieee_address(value) or ieee_address_from_value(value):
            return True
    entity_ids = item.get('entity_ids')
    if isinstance(entity_ids, list) and any(is_ieee_address(value) or ieee_address_from_value(value) or str(value).startswith('zigbee2mqtt/') for value in entity_ids):
        return True
    try:
        entity_ids_json = json.loads(str(item.get('entity_ids_json') or '[]'))
    except (TypeError, json.JSONDecodeError):
        entity_ids_json = []
    if isinstance(entity_ids_json, list) and any(is_ieee_address(value) or ieee_address_from_value(value) or str(value).startswith('zigbee2mqtt/') for value in entity_ids_json):
        return True
    for _domain, value in parse_identifiers(item.get('identifiers')):
        if is_ieee_address(value) or ieee_address_from_value(value):
            return True
    return False


def is_esp32_mqtt_mapping(item: dict[str, Any]) -> bool:
    source = str(item.get('source') or '').strip().lower()
    if source != 'mqtt':
        return False
    prefix = esp32_topic_prefix().strip('/')
    for field in ('entity_id', 'primary_entity_id', 'source_ref', 'topic'):
        value = str(item.get(field) or '').strip().strip('/')
        if value.startswith(f'{prefix}/') or value.startswith('sentero/'):
            return True
    return False


def stable_physical_device_ids(items: list[dict[str, Any]]) -> set[str]:
    return {device_id for item in items if (device_id := stable_physical_device_id(item))}


def stable_physical_device_id(item: dict[str, Any]) -> str:
    attrs = item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
    for raw in (
        item.get('ieee_address'),
        attrs.get('ieee_address'),
        item.get('ieee'),
        attrs.get('ieee'),
        item.get('device_id'),
        attrs.get('device_id'),
    ):
        ieee = ieee_address_from_value(raw)
        if ieee:
            return ieee
    for _domain, value in parse_identifiers(item.get('identifiers')):
        ieee = ieee_address_from_value(value)
        if ieee:
            return ieee
    device_id = str(item.get('device_id') or attrs.get('device_id') or '').strip()
    return device_id.lower() if device_id else ""


def ieee_address_from_value(value: Any) -> str:
    match = re.search(r'0x[0-9a-fA-F]{8,16}', str(value or ''))
    return match.group(0).lower() if match else ""


def best_candidate_per_physical_device(candidates: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_entities: dict[str, list[str]] = {}
    for item in current:
        device_id = stable_physical_device_id(item)
        entity_id = str(item.get('entity_id') or '').strip()
        if device_id and entity_id:
            grouped_entities.setdefault(device_id, []).append(entity_id)
    result: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        device_id = stable_physical_device_id(candidate)
        if not device_id:
            continue
        display_device_id = str(candidate.get('device_id') or device_id).strip() or device_id
        enriched = {**candidate, 'device_id': display_device_id, 'stable_device_id': device_id, 'entity_ids': sorted(set(grouped_entities.get(device_id, [])))}
        existing = result.get(device_id)
        if not existing or float(enriched.get('confidence') or 0) > float(existing.get('confidence') or 0):
            result[device_id] = enriched
    return sorted(result.values(), key=lambda x: (x['confidence'], role_state_priority(str(x.get('role') or ''), x), parse_time(x.get('last_updated')).timestamp()), reverse=True)


def add_physical_identity(values: set[str], raw: Any) -> None:
    text = str(raw or '').strip()
    if not text:
        return
    values.add(text)
    values.add(text.lower())
    match = re.search(r'0x[0-9a-fA-F]{8,16}', text)
    if match:
        values.add(match.group(0).lower())


def slug_identity(value: str) -> str:
    return re.sub(r'[^a-z0-9_]+', '_', value.lower()).strip('_')


def role_is_presence(role: str) -> bool:
    return str(role or '').endswith(('presence', '_motion'))


def role_is_contact(role: str) -> bool:
    value = str(role or '')
    return value in {'main_door', 'window_contact'} or value.endswith(('_door', '_contact'))


def role_is_button(role: str) -> bool:
    value = str(role or '')
    return value.endswith('_button') or value == 'button'


def role_is_smart_meter(role: str) -> bool:
    value = normalize(str(role or ''))
    return value.endswith(('_energy', '_power', '_water', '_gas', '_meter')) or any(term in value for term in ['electricity_meter', 'smart_meter', 'stromzaehler', 'wasserzaehler', 'gaszaehler'])


def role_is_electricity_meter(role: str) -> bool:
    value = normalize(str(role or ''))
    return any(term in value for term in ['energy', 'power', 'electricity', 'smart_meter', 'strom'])


def smart_meter_candidate_matches(role: str, item: dict[str, Any]) -> bool:
    haystack = normalize(' '.join(str(item.get(key) or '') for key in ['entity_id', 'friendly_name', 'original_name', 'device_name', 'model', 'payload_key']))
    device_class = str(item.get('device_class') or '').lower()
    payload_key = str(item.get('payload_key') or '').lower()
    keys = {device_class, payload_key}
    if role_is_electricity_meter(role):
        return bool(keys.intersection({'energy', 'power', 'energy_consumption', 'electricity', 'electricity_consumption', 'power_usage'})) or any(term in haystack for term in ['energy', 'electricity', 'strom', 'power', 'kwh', 'watt'])
    normalized_role = normalize(role)
    if 'water' in normalized_role or 'wasser' in normalized_role:
        return 'water' in keys or 'water_consumption' in keys or any(term in haystack for term in ['water', 'wasser'])
    if 'gas' in normalized_role:
        return 'gas' in keys or 'gas_consumption' in keys or 'gas' in haystack
    return bool(keys.intersection(SMART_METER_CLASSES | SMART_METER_KEYS)) or any(term in haystack for term in ['energy', 'electricity', 'strom', 'power', 'water', 'wasser', 'gas'])


def candidate_id_matches(item: dict[str, Any], selected_id: str) -> bool:
    wanted = str(selected_id or '').strip()
    if not wanted:
        return False
    values = {
        str(item.get('entity_id') or '').strip(),
        str(item.get('source_ref') or '').strip(),
        str(item.get('topic') or '').strip(),
        str(item.get('device_id') or '').strip(),
    }
    return wanted in values


def room_matches(room: str | None, entity_id: str, friendly_name: Any) -> bool:
    if not room:
        return False
    haystack = normalize(f'{entity_id} {friendly_name or ""}')
    return any(normalize(term) in haystack for term in ROOM_TERMS.get(room, [room]))


def normalize(value: str) -> str:
    return re.sub(r'[^a-z0-9_]+', '_', value.lower().replace('ü', 'ue').replace('ä', 'ae').replace('ö', 'oe').replace('ß', 'ss'))


def candidate_public(item: dict[str, Any] | None, dev: bool) -> dict[str, Any] | None:
    if not item:
        return None
    data = {
        'label': item.get('friendly_name') or 'Sensor erkannt',
        'confidence': item.get('confidence', 0),
        'score': item.get('confidence', 0),
        'entity_id': item.get('entity_id'),
        'device_id': item.get('device_id'),
        'device_class': item.get('device_class'),
        'domain': item.get('domain'),
        'source': item.get('source') or item.get('platform'),
        'source_ref': item.get('source_ref') or item.get('topic'),
        'entity_ids': item.get('entity_ids'),
        'topic': item.get('topic'),
        'payload_key': item.get('payload_key'),
    }
    if dev:
        data.update(item)
    return data


def parse_identifiers(value: Any) -> list[tuple[str, str]]:
    raw = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except ValueError:
            return []
    result: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            domain = str(item[0] or '').strip()
            identifier = str(item[1] or '').strip()
            if domain and identifier:
                result.append((domain, identifier))
    return result


def zigbee_provider_order() -> list[str]:
    return ['zigbee2mqtt']


def zigbee2mqtt_identifiers(identifiers: list[tuple[str, str]], entities: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for domain, value in identifiers:
        if normalize(domain) not in {'mqtt', 'zigbee2mqtt'}:
            continue
        values.extend(expand_zigbee2mqtt_id(value))
    for index, item in enumerate(entities):
        fields = ('device_id', 'source_ref', 'topic', 'original_name', 'device_name')
        if index == 0:
            fields = (*fields, 'entity_id', 'friendly_name')
        for field in fields:
            raw_value = item.get(field)
            if index > 0 and field == 'device_id' and not is_ieee_address(raw_value):
                continue
            values.extend(expand_zigbee2mqtt_id(raw_value))
    deduped = dedupe([value for value in values if value])
    return sorted(deduped, key=lambda value: 0 if is_ieee_address(value) else 1)


def expand_zigbee2mqtt_id(value: Any) -> list[str]:
    text = str(value or '').strip()
    if not text:
        return []
    ieee_matches = [match.group(0).lower() for match in re.finditer(r'0x[0-9a-fA-F]{12,16}', text)]
    if ieee_matches:
        return dedupe(ieee_matches)
    normalized = text
    for prefix in ('zigbee2mqtt_', 'mqtt_'):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            return [normalized] if normalized else []
    if '.' in text:
        return [text.rsplit('.', 1)[-1]]
    if '/' in text:
        return [text.rsplit('/', 1)[-1]]
    return [text]


def is_ieee_address(value: Any) -> bool:
    return bool(re.fullmatch(r'0x[0-9a-fA-F]{12,16}', str(value or '').strip()))


def z2m_response_matches_id(payload: Any, wanted: str) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get('status') == 'error':
        return True
    wanted_values = {value.lower() for value in expand_zigbee2mqtt_id(wanted)}
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    response_id = str(data.get('id') or '').strip()
    if not response_id:
        return bool(payload.get('status'))
    return bool(wanted_values.intersection({value.lower() for value in expand_zigbee2mqtt_id(response_id)}))


def z2m_rename_response_matches(payload: Any, source_id: str, clean_name: str) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get('status') == 'error':
        return True
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    response_to = str(data.get('to') or '').strip()
    if response_to:
        return response_to == clean_name
    return bool(payload.get('status'))


def zigbee2mqtt_permit_join_payloads(enabled: bool, duration: int | None = None) -> list[Any]:
    if enabled:
        time_value = int(duration or 180)
        return [
            {'value': True, 'time': time_value},
            {'value': True, 'time': time_value, 'device': None},
            True,
        ]
    return [
        {'value': False, 'time': 0},
        {'value': False, 'time': 0, 'device': None},
        {'value': False},
        False,
    ]


def z2m_permit_join_response_matches(payload: Any, wanted: bool) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get('status') == 'error':
        return True
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    value = data.get('value', payload.get('value'))
    if value is None:
        return bool(payload.get('status'))
    parsed = parse_bool_value(value)
    if parsed is None:
        return bool(payload.get('status'))
    return parsed is wanted


def parse_bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {'true', '1', 'on', 'yes', 'enabled'}:
        return True
    if text in {'false', '0', 'off', 'no', 'disabled'}:
        return False
    return None


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def public_role(data: dict[str, Any]) -> dict[str, Any]:
    return {
        'role': data.get('role'),
        'room': data.get('room'),
        'label': data.get('friendly_name') or data.get('role'),
        'configured': bool(data.get('active')),
        'updated_at': data.get('updated_at'),
        'state': data.get('state'),
        'reachable': data.get('reachable'),
        'last_changed': data.get('last_changed'),
        'last_updated': data.get('last_updated'),
        'battery_level': data.get('battery_level'),
        'power_source': data.get('power_source'),
        'temperature': data.get('temperature'),
        'humidity': data.get('humidity'),
        'illuminance': data.get('illuminance'),
        'presence': data.get('presence'),
        'fall_detected': data.get('fall_detected'),
        'motion': data.get('motion'),
        'motion_state': data.get('motion_state'),
        'stale': data.get('stale'),
        'stale_seconds': data.get('stale_seconds'),
        'hp_led': data.get('hp_led'),
        'fall_led': data.get('fall_led'),
        'led_status': data.get('led_status'),
        'writable_settings': data.get('writable_settings'),
        'device_class': data.get('device_class'),
        'domain': data.get('domain'),
        'source': data.get('source'),
        'device_id': data.get('device_id'),
        'source_ref': data.get('entity_id'),
    }


def find_battery_level(role: dict[str, Any], states: list[dict[str, Any]]) -> int | None:
    match = find_battery_entity(role, states)
    if not match:
        return None
    return parse_battery(match.get('state'))


def find_battery_entity(role: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any] | None:
    role_entity = str(role.get('entity_id') or '')
    role_prefixes = battery_lookup_prefixes(role_entity)
    candidates = bound_mqtt_sensor_states(role, states)
    if not candidates and role_prefixes:
        candidates = states
    for state in candidates:
        entity_id = str(state.get('entity_id') or '')
        if not is_battery_entity(state):
            continue
        if any(entity_id.startswith(prefix) for prefix in role_prefixes):
            if parse_battery(state.get('state')) is not None:
                return state
        if parse_battery(state.get('state')) is not None:
            return state
    return None


def battery_lookup_prefixes(entity_id: str) -> list[str]:
    text = str(entity_id or '').strip()
    if not text:
        return []
    domain, _, object_id = text.partition('.')
    base = object_id or text
    for suffix in (
        '_contact',
        '_door',
        '_opening',
        '_presence',
        '_occupancy',
        '_motion',
        '_bewegung',
        '_praesenz',
        '_präsenz',
    ):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    prefixes = []
    for candidate_domain in ['sensor', domain]:
        if candidate_domain:
            prefixes.append(f'{candidate_domain}.{base}')
    return unique_ordered([item for item in prefixes if item])


def unique_ordered(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def is_battery_entity(state: dict[str, Any]) -> bool:
    entity_id = str(state.get('entity_id') or '')
    if not entity_id.startswith('sensor.'):
        return False
    object_id = normalize(entity_id.split('.', 1)[1] if '.' in entity_id else entity_id)
    names = [
        object_id,
        normalize(str(state.get('friendly_name') or '')),
        normalize(str(state.get('original_name') or '')),
    ]
    return any(name.endswith(('_battery', '_batterie')) or name in {'battery', 'batterie'} for name in names if name)


def parse_battery(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace('%', '').strip())
    except ValueError:
        return None
    if number < 0 or number > 100:
        return None
    return int(round(number))
