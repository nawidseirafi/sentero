from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.paths import DATA_DIR
from backend.sensor_sources.base import create_sensor_source
from backend.services.homeassistant_service import HomeAssistantService
from backend.services.mqtt_service import MqttService

DB_PATH = DATA_DIR / 'sentero.db'
DB_TIMEOUT_SECONDS = 30
DISCOVERY_TIMEOUT_SECONDS = 180
DISCOVERY_CONFIDENCE_THRESHOLD = 50
PRESENCE_CLASSES = {'occupancy', 'motion', 'presence'}
CONTACT_CLASSES = {'door', 'window', 'opening', 'contact'}
logger = logging.getLogger(__name__)
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
    def __init__(self, database_path: Path | None = None, ha: HomeAssistantService | None = None) -> None:
        self.database_path = database_path or DB_PATH
        self.source_mode = sensor_source_mode()
        self.ha = ha or HomeAssistantService()
        self.sensor_source = create_sensor_source()
        self.mqtt = MqttService()
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.database_path, timeout=DB_TIMEOUT_SECONDS)
        con.row_factory = sqlite3.Row
        configure_sqlite_connection(con)
        return con

    def ensure_schema(self) -> None:
        with self.connect() as con:
            ensure_schema(con)
            con.commit()

    def home_status(self) -> dict[str, bool]:
        if self.uses_mqtt_source():
            if not self.sensor_source.configured():
                return {'connected': False, 'sensor_ready': False, 'system_ready': False}
            try:
                states = self.snapshot()
            except Exception:
                return {'connected': False, 'sensor_ready': False, 'system_ready': False}
            return {'connected': True, 'sensor_ready': isinstance(states, list), 'system_ready': True}
        if not self.ha.configured():
            return {'connected': False, 'sensor_ready': False, 'system_ready': False}
        try:
            states = self.ha.get_states()
        except Exception:
            return {'connected': False, 'sensor_ready': False, 'system_ready': False}
        return {'connected': True, 'sensor_ready': isinstance(states, list), 'system_ready': True}

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
        ha_url = getattr(self.ha, 'base_url', '')
        try:
            baseline = self.snapshot()
            ha_reachable = True
        except Exception:
            logger.exception("Sentero discovery baseline failed. ha_url=%s reachable=no", ha_url)
            raise
        started_at = now()
        has_pairing_code = bool(str(pairing_code or '').strip())
        status = 'waiting_for_signal'
        message = 'Bitte aktivieren Sie den Sensor jetzt einmal.'
        detail = None
        if has_pairing_code:
            status = 'pairing_started'
            message = 'Kopplung gestartet. Bitte aktivieren Sie den Sensor danach einmal.'
            detail = self._try_matter_pairing(pairing_code)
            if detail and not detail.get('ok'):
                status = 'pairing_needs_manual_action'
                message = 'Der Sensor konnte nicht verbunden werden. Bitte erneut versuchen.'
        with self.connect() as con:
            cur = con.execute(
                '''insert into sensor_discovery_sessions
                   (target_role, target_room, started_at, status, baseline_snapshot_json, pairing_code_provided, pairing_detail_json)
                   values (?, ?, ?, ?, ?, ?, ?)''',
                (role, room, started_at, status, json.dumps(baseline, ensure_ascii=False), int(has_pairing_code), json.dumps(detail, ensure_ascii=False) if detail else None),
            )
            con.commit()
            session_id = int(cur.lastrowid)
        logger.info(
            "Sentero discovery start session=%s role=%s room=%s ha_url=%s reachable=%s baseline_states=%s status=%s",
            session_id,
            role,
            room,
            ha_url,
            "yes" if ha_reachable else "no",
            len(baseline),
            status,
        )
        return {'session_id': session_id, 'status': status, 'message': message, 'detail': detail}

    def start_zigbee_pairing(self, role: str, room: str | None, duration: int = 60) -> dict[str, Any]:
        ha_url = getattr(self.ha, 'base_url', '')
        duration = min(max(int(duration or 60), 10), 300)
        try:
            baseline = self.snapshot()
            ha_reachable = True
        except Exception:
            logger.exception("Sentero pairing baseline failed. ha_url=%s reachable=no", ha_url)
            raise
        detail = self._open_zigbee_permit_join(duration)
        status = 'pairing_started' if detail.get('ok') else 'pairing_needs_manual_action'
        message = (
            'Sensor-Suche gestartet. Bitte aktivieren Sie den Sensor jetzt.'
            if detail.get('ok')
            else 'Die Sensor-Einrichtung ist noch nicht bereit.'
        )
        with self.connect() as con:
            cur = con.execute(
                '''insert into sensor_discovery_sessions
                   (target_role, target_room, started_at, status, baseline_snapshot_json, pairing_code_provided, pairing_detail_json)
                   values (?, ?, ?, ?, ?, ?, ?)''',
                (role, room, now(), status, json.dumps(baseline, ensure_ascii=False), 0, json.dumps(detail, ensure_ascii=False)),
            )
            con.commit()
            session_id = int(cur.lastrowid)
        logger.info(
            "Sentero pairing start session=%s role=%s room=%s ha_url=%s reachable=%s baseline_states=%s status=%s provider=%s",
            session_id,
            role,
            room,
            ha_url,
            "yes" if ha_reachable else "no",
            len(baseline),
            status,
            detail.get('provider'),
        )
        if not detail.get('ok'):
            logger.warning("Sentero pairing unavailable session=%s detail=%s", session_id, detail)
        return {'session_id': session_id, 'status': status, 'message': message, 'detail': detail}

    def candidates(self, session_id: int, dev: bool = False) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute('select * from sensor_discovery_sessions where id = ?', (session_id,)).fetchone()
        if not row:
            raise ValueError('session not found')
        started_at = parse_time(row['started_at'])
        elapsed_seconds = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0)
        if row['status'] == 'pairing_needs_manual_action':
            logger.info(
                "Sentero discovery poll session=%s skipped status=pairing_needs_manual_action ha_url=%s",
                session_id,
                getattr(self.ha, 'base_url', ''),
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
        baseline = json.loads(row['baseline_snapshot_json'] or '[]')
        current = self.snapshot()
        scored = score_candidates(baseline, current, row['target_role'], row['target_room'], row['started_at'])
        raw_changed_count = count_changed_entities(baseline, current, row['started_at'])
        changed_count = len(scored)
        best_scored = scored[0] if scored else None
        best = best_scored if best_scored and best_scored['confidence'] >= DISCOVERY_CONFIDENCE_THRESHOLD else None
        timed_out = elapsed_seconds >= DISCOVERY_TIMEOUT_SECONDS
        status = 'signal_detected' if best else 'no_signal_detected' if timed_out else 'waiting_for_signal'
        message = (
            'Sensor-Signal erkannt.'
            if best
            else 'Wir konnten den Sensor nicht eindeutig erkennen. Bitte erneut versuchen.'
            if timed_out
            else 'Wir warten noch auf ein eindeutiges Sensorsignal.'
        )
        with self.connect() as con:
            con.execute(
                '''update sensor_discovery_sessions set ended_at = ?, status = ?, candidate_snapshot_json = ? where id = ?''',
                (now() if best or timed_out else None, status, json.dumps(current, ensure_ascii=False), session_id),
            )
            con.commit()
        logger.info(
            "Sentero discovery poll session=%s ha_url=%s baseline_states=%s current_states=%s raw_changed=%s changed_entities=%s best=%s best_score=%s status=%s elapsed=%.1f",
            session_id,
            getattr(self.ha, 'base_url', ''),
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
            'remaining_seconds': max(DISCOVERY_TIMEOUT_SECONDS - elapsed_seconds, 0),
            'changed_count': changed_count if dev else None,
            'current_state_count': len(current) if dev else None,
            'baseline_state_count': len(baseline) if dev else None,
        }

    def confirm(self, session_id: int, entity_id: str, name: str | None = None, room: str | None = None, dev: bool = False) -> dict[str, Any]:
        with self.connect() as con:
            session = con.execute('select * from sensor_discovery_sessions where id = ?', (session_id,)).fetchone()
        if not session:
            raise ValueError('session not found')
        baseline = json.loads(session['baseline_snapshot_json'] or '[]')
        current = json.loads(session['candidate_snapshot_json'] or '[]') or self.snapshot()
        scored = score_candidates(baseline, current, session['target_role'], session['target_room'], session['started_at'])
        entity = next(
            (
                item for item in scored
                if item.get('entity_id') == entity_id
                and item.get('confidence', 0) >= DISCOVERY_CONFIDENCE_THRESHOLD
            ),
            None,
        )
        if not entity:
            raise ValueError('entity does not match this pairing session')
        attrs = entity.get('attributes') or {}
        target_room = str(room or session['target_room'] or '').strip() or None
        desired_name = str(name or '').strip() or attrs.get('friendly_name') or entity.get('friendly_name') or 'Sensor'
        metadata_detail = self._apply_home_assistant_metadata(entity, desired_name, target_room)
        payload = {
            'role': session['target_role'],
            'room': target_room,
            'entity_id': entity_id,
            'device_id': attrs.get('device_id') or entity.get('device_id'),
            'friendly_name': desired_name,
            'device_class': attrs.get('device_class') or entity.get('device_class'),
            'domain': entity_id.split('.')[0],
            'source': 'wizard',
            'confidence': 100,
        }
        role = self.upsert_role(payload)
        with self.connect() as con:
            con.execute('update sensor_discovery_sessions set status = ?, selected_entity_id = ?, ended_at = ? where id = ?', ('confirmed', entity_id, now(), session_id))
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

    def upsert_role(self, data: dict[str, Any]) -> dict[str, Any]:
        role = str(data.get('role') or '').strip()
        entity_id = str(data.get('entity_id') or '').strip()
        if not role or not entity_id:
            raise ValueError('role and entity_id required')
        domain = str(data.get('domain') or entity_id.split('.')[0] if '.' in entity_id else '').strip()
        data = {**data, 'domain': domain}
        if not role_candidate_matches(role, data, allow_missing_device_class=True):
            raise ValueError('entity does not match expected sensor class for role')
        timestamp = now()
        with self.connect() as con:
            con.execute('update sensor_roles set active = 0, updated_at = ? where role = ?', (timestamp, role))
            con.execute(
                '''insert into sensor_roles
                   (role, room, entity_id, device_id, friendly_name, device_class, domain, source, confidence, active, created_at, updated_at)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                (role, data.get('room'), entity_id, data.get('device_id'), data.get('friendly_name'), data.get('device_class'), data.get('domain'), data.get('source'), float(data.get('confidence') or 0), timestamp, timestamp),
            )
            con.commit()
        return self.get_role(role, dev=True) or {}

    def get_role(self, role: str, dev: bool = False) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute('select * from sensor_roles where role = ? and active = 1 limit 1', (role,)).fetchone()
        if not row:
            return None
        data = dict(row)
        return data if dev else public_role(data)

    def delete_role(self, role: str) -> dict[str, Any]:
        mapped = self.get_role(role, dev=True)
        if not mapped:
            raise ValueError('sensor role not found')
        removal = self._remove_zigbee_device(mapped)
        if not removal.get('ok'):
            raise RuntimeError(removal.get('message') or 'Geraet konnte nicht entfernt werden.')
        with self.connect() as con:
            timestamp = now()
            device_id = str(mapped.get('device_id') or '').strip()
            if device_id:
                con.execute('update sensor_roles set active = 0, updated_at = ? where device_id = ? and active = 1', (timestamp, device_id))
            else:
                con.execute('update sensor_roles set active = 0, updated_at = ? where role = ?', (timestamp, role))
            con.commit()
        logger.info("Sentero sensor deleted role=%s entity=%s device=%s removal=%s", role, mapped.get('entity_id'), mapped.get('device_id'), removal)
        return {'deleted': True, 'role': role, 'removal': removal}

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
        metadata = self._apply_home_assistant_metadata(entity, clean_name, mapped.get('room'))
        timestamp = now()
        with self.connect() as con:
            con.execute(
                'update sensor_roles set friendly_name = ?, updated_at = ? where role = ? and active = 1',
                (clean_name, timestamp, role),
            )
            con.commit()
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
        identify = find_identify_entity(states, device_id, entity_id) if not self.uses_mqtt_source() else None
        if identify:
            try:
                response = self.ha.call_service('button', 'press', {'entity_id': identify['entity_id']})
                logger.info(
                    "Sentero sensor test identify role=%s entity=%s identify_entity=%s device=%s",
                    role,
                    entity_id,
                    identify.get('entity_id'),
                    device_id,
                )
                return {
                    'ok': True,
                    'mode': 'identify',
                    'message': 'Sensor wurde identifiziert.',
                    'entity_id': identify.get('entity_id'),
                    'response': response,
                }
            except Exception as exc:
                logger.info(
                    "Sentero sensor test identify failed role=%s entity=%s identify_entity=%s device=%s error=%s",
                    role,
                    entity_id,
                    identify.get('entity_id'),
                    device_id,
                    exc,
                )
        device_entities = [item for item in states if device_id and str(item.get('device_id') or '') == device_id]
        if not device_entities:
            device_entities = [item for item in states if str(item.get('entity_id') or '') == entity_id]
        usable_entities = [item for item in device_entities if testable_state_entity(item)]
        reachable = [item for item in usable_entities if state_is_reachable(item.get('state'))]
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
                'message': 'Sensor ist aktuell nicht erreichbar.',
                'entity_id': entity_id,
                'entity_count': len(device_entities),
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
        }

    def _remove_zigbee_device(self, mapped: dict[str, Any]) -> dict[str, Any]:
        entity_id = str(mapped.get('entity_id') or '').strip()
        device_id = str(mapped.get('device_id') or '').strip()
        states = self.snapshot()
        device_entities = [item for item in states if device_id and str(item.get('device_id') or '') == device_id]
        if not device_entities:
            device_entities = [item for item in states if str(item.get('entity_id') or '') == entity_id]
        if not device_entities:
            return {
                'ok': True,
                'provider': 'zigbee2mqtt' if self.uses_mqtt_source() else 'home_assistant',
                'reason': 'already_missing',
                'message': 'Geraet war bereits nicht mehr vorhanden.',
                'entity_id': entity_id,
                'device_id': device_id or None,
            }
        identifiers = []
        for item in device_entities:
            identifiers.extend(parse_identifiers(item.get('identifiers')))
        ieee = first_identifier_value(identifiers, {'zha'})
        mqtt_ids = zigbee2mqtt_identifiers(identifiers, device_entities)
        attempts: list[dict[str, Any]] = []
        for provider in zigbee_provider_order():
            if self.uses_mqtt_source() and provider == 'zha':
                continue
            if provider == 'zha':
                if not ieee:
                    continue
                try:
                    response = self.ha.call_service('zha', 'remove', {'ieee': ieee})
                    return {'ok': True, 'provider': 'zha', 'ieee': ieee, 'response': response, 'attempts': attempts}
                except Exception as exc:
                    attempts.append({'provider': 'zha', 'ieee': ieee, 'error': str(exc)})
                    logger.info("Sentero device remove failed provider=zha entity=%s device=%s ieee=%s error=%s", entity_id, device_id, ieee, exc)
                continue
            if provider == 'zigbee2mqtt':
                for mqtt_id in mqtt_ids:
                    try:
                        response = self._mqtt_publish(
                            'zigbee2mqtt/bridge/request/device/remove',
                            {'id': mqtt_id, 'force': True},
                        )
                        return {'ok': True, 'provider': 'zigbee2mqtt', 'id': mqtt_id, 'response': response, 'attempts': attempts}
                    except Exception as exc:
                        attempts.append({'provider': 'zigbee2mqtt', 'id': mqtt_id, 'error': str(exc)})
                        logger.info("Sentero device remove failed provider=zigbee2mqtt entity=%s device=%s id=%s error=%s", entity_id, device_id, mqtt_id, exc)
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
            logger.exception("Sentero sensor state refresh failed. ha_url=%s", getattr(self.ha, 'base_url', ''))
            return [{**row, 'reachable': False, 'state': None, 'last_changed': None, 'last_updated': None, 'battery_level': None} for row in rows]
        by_entity = {str(item.get('entity_id') or ''): item for item in states}
        result = []
        for row in rows:
            entity_id = str(row.get('entity_id') or '')
            state = resolve_role_state(dict(row), states, by_entity)
            value = state.get('state') if state else None
            reachable = sensor_reachable_status(state)
            battery_entity = find_battery_entity({**row, **(state or {})}, states)
            battery_level = parse_battery(battery_entity.get('state')) if battery_entity else None
            logger.info(
                "Sentero sensor health role=%s stored_entity=%s resolved_entity=%s reachable=%s battery_entity=%s battery_level=%s",
                row.get('role'),
                entity_id,
                state.get('entity_id') if state else None,
                reachable,
                battery_entity.get('entity_id') if battery_entity else None,
                battery_level,
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
            })
        return result

    def snapshot(self) -> list[dict[str, Any]]:
        if self.uses_mqtt_source():
            return [normalize_snapshot_item(item) for item in self.sensor_source.snapshot()]
        states = self.ha.get_states()
        entity_registry = self._entity_registry_by_entity_id()
        device_registry = self._device_registry_by_id()
        result = []
        for item in states:
            entity_id = str(item.get('entity_id') or '')
            attrs = item.get('attributes') or {}
            registry = entity_registry.get(entity_id, {})
            device_id = registry.get('device_id') or attrs.get('device_id')
            device = device_registry.get(str(device_id or ''), {})
            result.append({
                'entity_id': entity_id,
                'domain': entity_id.split('.')[0] if '.' in entity_id else '',
                'state': item.get('state'),
                'friendly_name': attrs.get('friendly_name'),
                'device_class': attrs.get('device_class'),
                'unit': attrs.get('unit_of_measurement'),
                'unit_of_measurement': attrs.get('unit_of_measurement'),
                'device_id': device_id,
                'area_id': registry.get('area_id') or device.get('area_id'),
                'platform': registry.get('platform'),
                'unique_id': registry.get('unique_id'),
                'original_name': registry.get('original_name'),
                'device_name': device.get('name_by_user') or device.get('name'),
                'manufacturer': device.get('manufacturer'),
                'model': device.get('model'),
                'identifiers': device.get('identifiers'),
                'last_changed': item.get('last_changed'),
                'last_updated': item.get('last_updated'),
            })
        return result

    def uses_mqtt_source(self) -> bool:
        return self.source_mode in {'mqtt', 'zigbee2mqtt', 'z2m'}

    def _mqtt_publish(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.mqtt.publish(topic, payload)

    def _entity_registry_by_entity_id(self) -> dict[str, dict[str, Any]]:
        try:
            response = self.ha.websocket_command({'type': 'config/entity_registry/list'}, timeout=12)
            rows = registry_result_list(response)
        except Exception as exc:
            logger.info("Sentero HA entity registry unavailable ha_url=%s error=%s", getattr(self.ha, 'base_url', ''), exc)
            return {}
        return {str(item.get('entity_id') or ''): item for item in rows if item.get('entity_id')}

    def _device_registry_by_id(self) -> dict[str, dict[str, Any]]:
        try:
            response = self.ha.websocket_command({'type': 'config/device_registry/list'}, timeout=12)
            rows = registry_result_list(response)
        except Exception as exc:
            logger.info("Sentero HA device registry unavailable ha_url=%s error=%s", getattr(self.ha, 'base_url', ''), exc)
            return {}
        return {str(item.get('id') or ''): item for item in rows if item.get('id')}

    def _area_registry(self) -> list[dict[str, Any]]:
        try:
            response = self.ha.websocket_command({'type': 'config/area_registry/list'}, timeout=12)
            return registry_result_list(response)
        except Exception as exc:
            logger.info("Sentero HA area registry unavailable ha_url=%s error=%s", getattr(self.ha, 'base_url', ''), exc)
            return []

    def _ensure_home_assistant_area(self, room: str | None) -> str | None:
        if not room:
            return None
        wanted = normalize(room)
        terms = {wanted, *[normalize(term) for term in ROOM_TERMS.get(room, [room])]}
        for area in self._area_registry():
            area_id = str(area.get('area_id') or area.get('id') or '')
            name = str(area.get('name') or '')
            if normalize(area_id) in terms or normalize(name) in terms:
                return area_id
        label = ROOM_LABELS.get(room) or str(room).replace('_', ' ').strip().title()
        try:
            response = assert_ha_success(self.ha.websocket_command({'type': 'config/area_registry/create', 'name': label}, timeout=12))
            result = response.get('result') if isinstance(response, dict) else None
            if isinstance(result, dict):
                return result.get('area_id') or result.get('id')
        except Exception as exc:
            logger.info("Sentero HA area create failed room=%s label=%s error=%s", room, label, exc)
        return None

    def _apply_home_assistant_metadata(self, entity: dict[str, Any], name: str, room: str | None) -> dict[str, Any]:
        entity_id = str(entity.get('entity_id') or '').strip()
        device_id = str(entity.get('device_id') or '').strip()
        if self.uses_mqtt_source():
            rename = self._rename_zigbee2mqtt_device(entity, name)
            return {
                'entity_id': entity_id,
                'device_id': device_id or None,
                'name': name,
                'room': room,
                'updated': ['zigbee2mqtt'] if rename.get('ok') else [],
                'ok': bool(rename.get('ok')),
                'zigbee2mqtt': rename,
            }
        area_id = self._ensure_home_assistant_area(room)
        detail: dict[str, Any] = {'entity_id': entity_id, 'device_id': device_id or None, 'name': name, 'room': room, 'area_id': area_id, 'updated': []}
        if not entity_id:
            detail['ok'] = False
            detail['reason'] = 'missing_entity_id'
            return detail
        if device_id and (area_id or name):
            payload: dict[str, Any] = {'type': 'config/device_registry/update', 'device_id': device_id}
            if area_id:
                payload['area_id'] = area_id
            if name:
                payload['name_by_user'] = name
            try:
                assert_ha_success(self.ha.websocket_command(payload, timeout=12))
                detail['updated'].append('device_registry')
            except Exception as exc:
                detail.setdefault('errors', []).append({'target': 'device_registry', 'error': str(exc)})
                logger.info("Sentero HA device metadata update failed entity=%s device=%s area=%s error=%s", entity_id, device_id, area_id, exc)
        payload = {'type': 'config/entity_registry/update', 'entity_id': entity_id}
        if name:
            payload['name'] = name
        if area_id:
            payload['area_id'] = area_id
        try:
            assert_ha_success(self.ha.websocket_command(payload, timeout=12))
            detail['updated'].append('entity_registry')
        except Exception as exc:
            detail.setdefault('errors', []).append({'target': 'entity_registry', 'error': str(exc)})
            logger.info("Sentero HA entity metadata update failed entity=%s area=%s error=%s", entity_id, area_id, exc)
        zigbee2mqtt_rename = self._rename_zigbee2mqtt_device(entity, name)
        if zigbee2mqtt_rename.get('ok'):
            detail['updated'].append('zigbee2mqtt')
        elif zigbee2mqtt_rename.get('reason') != 'no_zigbee2mqtt_id':
            detail.setdefault('errors', []).append({'target': 'zigbee2mqtt', 'error': zigbee2mqtt_rename})
        detail['ok'] = bool(detail['updated'])
        return detail

    def _rename_zigbee2mqtt_device(self, entity: dict[str, Any], name: str) -> dict[str, Any]:
        clean_name = str(name or '').strip()
        if not clean_name:
            return {'ok': False, 'reason': 'missing_name'}
        identifiers = parse_identifiers(entity.get('identifiers'))
        candidates = zigbee2mqtt_identifiers(identifiers, [entity])
        source_id = next((value for value in candidates if re.fullmatch(r'0x[0-9a-fA-F]{12,16}', value)), None)
        if not source_id:
            return {'ok': False, 'reason': 'no_zigbee2mqtt_id', 'candidates': candidates}
        try:
            response = self._mqtt_publish(
                'zigbee2mqtt/bridge/request/device/rename',
                {'from': source_id, 'to': clean_name, 'homeassistant_rename': not self.uses_mqtt_source()},
            )
            logger.info("Sentero Zigbee2MQTT rename sent from=%s to=%s entity=%s", source_id, clean_name, entity.get('entity_id'))
            return {'ok': True, 'provider': 'zigbee2mqtt', 'from': source_id, 'to': clean_name, 'response': response}
        except Exception as exc:
            logger.info("Sentero Zigbee2MQTT rename failed from=%s to=%s entity=%s error=%s", source_id, clean_name, entity.get('entity_id'), exc)
            return {'ok': False, 'reason': 'rename_failed', 'from': source_id, 'to': clean_name, 'error': str(exc)}

    def _try_matter_pairing(self, pairing_code: str) -> dict[str, Any]:
        code = str(pairing_code or '').strip().replace(' ', '')
        if not code:
            return {'ok': False, 'reason': 'missing_code'}
        try:
            response = self.ha.websocket_command({'type': 'matter/commission_with_code', 'code': code}, timeout=90)
        except Exception as exc:
            return {'ok': False, 'reason': 'pairing_call_failed', 'error': str(exc)}
        return {'ok': bool(response.get('success', True)), 'response': response}

    def _open_zigbee_permit_join(self, duration: int) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        for provider in zigbee_provider_order():
            if self.uses_mqtt_source() and provider == 'zha':
                continue
            if provider == 'zha':
                try:
                    response = self.ha.call_service('zha', 'permit', {'duration': duration})
                    logger.info("Sentero Zigbee permit_join sent provider=zha duration=%s", duration)
                    return {'ok': True, 'provider': 'zha', 'duration': duration, 'response': response, 'attempts': attempts}
                except Exception as exc:
                    attempts.append({'provider': 'zha', 'error': str(exc)})
                    logger.info("Sentero Zigbee permit_join failed provider=zha error=%s", exc)
                continue
            if provider == 'zigbee2mqtt':
                try:
                    response = self._mqtt_publish(
                        'zigbee2mqtt/bridge/request/permit_join',
                        {'value': True, 'time': duration},
                    )
                    logger.info("Sentero Zigbee permit_join sent provider=zigbee2mqtt duration=%s", duration)
                    return {'ok': True, 'provider': 'zigbee2mqtt', 'duration': duration, 'response': response, 'attempts': attempts}
                except Exception as exc:
                    attempts.append({'provider': 'zigbee2mqtt', 'error': str(exc)})
                    logger.info("Sentero Zigbee permit_join failed provider=zigbee2mqtt error=%s", exc)
        return {'ok': False, 'reason': 'zigbee_pairing_unavailable', 'message': 'Zigbee-Anlernen nicht verfuegbar', 'attempts': attempts}


def sensor_source_mode() -> str:
    return os.getenv('SENTERO_SENSOR_SOURCE', 'mqtt').strip().lower()


def normalize_snapshot_item(item: dict[str, Any]) -> dict[str, Any]:
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
        "alter table trusted_contacts add column whatsapp_phone_number text",
        "alter table trusted_contacts add column preferred_channels text not null default '[\"email\"]'",
        "alter table trusted_contacts add column notification_enabled integer not null default 1",
        "alter table trusted_contacts add column primary_contact integer not null default 0",
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
        contact_id integer,
        channel text not null,
        severity text not null,
        status text not null,
        message_title text,
        error_message text,
        created_at text not null
    )''')
    con.execute('''create table if not exists system_warning_state (
        warning_key text primary key,
        status text not null,
        first_seen_at text not null,
        last_seen_at text not null,
        last_sent_at text,
        resolved_at text,
        payload_json text not null default '{}'
    )''')
    con.execute('''create table if not exists sentero_users (
        id integer primary key autoincrement,
        email text not null unique,
        password_hash text not null,
        display_name text,
        role text not null default 'viewer',
        is_active integer not null default 1,
        created_at text not null,
        updated_at text not null,
        last_login_at text
    )''')
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
    con.execute('create unique index if not exists idx_sensor_roles_active_role on sensor_roles(role) where active = 1')
    con.execute('''create table if not exists sensor_discovery_sessions (id integer primary key autoincrement, target_role text not null, target_room text, started_at text not null, ended_at text, status text not null, baseline_snapshot_json text, candidate_snapshot_json text, selected_entity_id text)''')
    for statement in [
        "alter table sensor_discovery_sessions add column pairing_code_provided integer not null default 0",
        "alter table sensor_discovery_sessions add column pairing_detail_json text",
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


def score_candidates(baseline: list[dict[str, Any]], current: list[dict[str, Any]], role: str, room: str | None, started_at: str | datetime) -> list[dict[str, Any]]:
    before = {item.get('entity_id'): item for item in baseline}
    baseline_device_ids = {str(item.get('device_id') or '') for item in baseline if item.get('device_id')}
    started = parse_time(started_at)
    scored = []
    for item in current:
        entity_id = str(item.get('entity_id') or '')
        if not entity_id:
            continue
        device_id = str(item.get('device_id') or '')
        old = before.get(entity_id, {})
        is_new = entity_id not in before
        is_new_device = bool(device_id and device_id not in baseline_device_ids)
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
            scored.append({**item, 'confidence': confidence, 'reasons': reasons, 'is_new': is_new, 'is_new_device': is_new_device, 'entity_priority': priority})
    return sorted(scored, key=lambda x: (bool(x.get('is_new_device')), role_state_priority(role, x), bool(x.get('is_new')), x['confidence'], parse_time(x.get('last_updated')).timestamp()), reverse=True)


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
    if role_is_presence(role):
        return (
            (domain == 'binary_sensor' and (allow_device_class_mismatch or class_matches(role, device_class) or (allow_missing_device_class and not has_device_class)))
            or (domain == 'sensor' and role_keyword_matches(role, item, include_model=allow_device_class_mismatch))
        )
    if role_is_contact(role):
        return (
            (domain == 'binary_sensor' and (allow_device_class_mismatch or class_matches(role, device_class) or (allow_missing_device_class and not has_device_class)))
            or (domain == 'sensor' and contact_sensor_candidate_matches(item, include_model=allow_device_class_mismatch))
            or (domain in {'lock', 'switch'} and role_keyword_matches(role, item, include_model=True))
        )
    return domain == 'binary_sensor'


def class_matches(role: str, device_class: Any) -> bool:
    dc = str(device_class or '').lower()
    if role_is_presence(role):
        return dc in PRESENCE_CLASSES
    if role_is_contact(role):
        return dc in CONTACT_CLASSES
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
    if domain in {'button', 'update'}:
        return -80
    if device_class in {'battery', 'signal_strength'} or any(term in haystack for term in ['batterie', 'battery', 'rssi', 'lqi', 'firmware', 'identifizieren']):
        return -50
    if role_is_presence(role):
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
    return 0


def resolve_role_state(row: dict[str, Any], states: list[dict[str, Any]], by_entity: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    entity_id = str(row.get('entity_id') or '')
    direct = by_entity.get(entity_id)
    if direct and state_is_reachable(direct.get('state')) and role_state_matches(str(row.get('role') or ''), direct):
        return direct
    device_id = str(row.get('device_id') or '').strip()
    candidates = []
    if device_id:
        candidates = [item for item in states if str(item.get('device_id') or '') == device_id and role_state_matches(str(row.get('role') or ''), item)]
    if not candidates and entity_id:
        prefix = entity_id.rsplit('_', 1)[0] if '_' in entity_id else entity_id.rsplit('.', 1)[-1]
        candidates = [item for item in states if prefix and str(item.get('entity_id') or '').startswith(prefix) and role_state_matches(str(row.get('role') or ''), item)]
    if not candidates:
        room = str(row.get('room') or '')
        label = str(row.get('friendly_name') or row.get('role') or '')
        candidates = [
            item for item in states
            if role_state_matches(str(row.get('role') or ''), item)
            and (
                room_matches(room, str(item.get('entity_id') or ''), item.get('friendly_name'))
                or (label and normalize(label).split('_')[0] in normalize(f"{item.get('entity_id') or ''} {item.get('friendly_name') or ''}"))
            )
        ]
    reachable = [item for item in candidates if state_is_reachable(item.get('state'))]
    selected = sorted(reachable or candidates, key=lambda item: role_state_priority(str(row.get('role') or ''), item), reverse=True)
    if selected:
        return selected[0]
    return direct


def role_state_matches(role: str, item: dict[str, Any]) -> bool:
    domain = str(item.get('domain') or str(item.get('entity_id') or '').split('.', 1)[0])
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
    return score


def testable_state_entity(item: dict[str, Any]) -> bool:
    entity_id = str(item.get('entity_id') or '')
    domain = str(item.get('domain') or entity_id.split('.', 1)[0] if '.' in entity_id else '')
    if domain in {'button', 'update'}:
        return False
    haystack = normalize(f"{entity_id} {item.get('friendly_name') or ''} {item.get('original_name') or ''}")
    if any(term in haystack for term in ['identifizieren', 'identify', 'firmware']):
        return False
    return domain in {'binary_sensor', 'sensor', 'lock', 'switch'}


def sensor_reachable_status(state: dict[str, Any] | None) -> bool | None:
    if not state:
        return False
    value = str(state.get('state') or '').strip().lower()
    if value == 'unavailable':
        return False
    if value in {'', 'unknown', 'none'}:
        return None
    return True


def state_is_reachable(value: Any) -> bool:
    return str(value or '').strip().lower() not in {'', 'unknown', 'unavailable', 'none'}


def role_is_presence(role: str) -> bool:
    return str(role or '').endswith(('presence', '_motion'))


def role_is_contact(role: str) -> bool:
    value = str(role or '')
    return value in {'main_door', 'window_contact'} or value.endswith(('_door', '_contact'))


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
    data = {'label': item.get('friendly_name') or 'Sensor erkannt', 'confidence': item.get('confidence', 0), 'score': item.get('confidence', 0), 'entity_id': item.get('entity_id')}
    if dev:
        data.update(item)
    return data


def find_identify_entity(states: list[dict[str, Any]], device_id: str, entity_id: str) -> dict[str, Any] | None:
    entity_prefix = entity_id.rsplit('_', 1)[0] if '_' in entity_id else entity_id.rsplit('.', 1)[-1]
    candidates = []
    for item in states:
        current_entity = str(item.get('entity_id') or '')
        if not current_entity.startswith('button.'):
            continue
        haystack = normalize(f"{current_entity} {item.get('friendly_name') or ''} {item.get('device_class') or ''}")
        same_device = bool(device_id and str(item.get('device_id') or '') == device_id)
        same_prefix = bool(entity_prefix and normalize(entity_prefix) in normalize(current_entity))
        if (same_device or same_prefix) and any(term in haystack for term in ['identify', 'identifizieren']):
            candidates.append(item)
    return candidates[0] if candidates else None


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


def first_identifier_value(identifiers: list[tuple[str, str]], domains: set[str]) -> str | None:
    wanted = {normalize(domain) for domain in domains}
    for domain, value in identifiers:
        if normalize(domain) in wanted and value:
            return value
    return None


def zigbee_provider_order() -> list[str]:
    configured = normalize(os.getenv('SENTERO_ZIGBEE_PROVIDER') or os.getenv('ZIGBEE_PROVIDER') or 'auto')
    if configured in {'zigbee2mqtt', 'z2m', 'mqtt'}:
        return ['zigbee2mqtt', 'zha']
    if configured == 'zha':
        return ['zha', 'zigbee2mqtt']
    return ['zigbee2mqtt', 'zha']


def zigbee2mqtt_identifiers(identifiers: list[tuple[str, str]], entities: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for domain, value in identifiers:
        if normalize(domain) not in {'mqtt', 'zigbee2mqtt'}:
            continue
        values.extend(expand_zigbee2mqtt_id(value))
    for item in entities:
        for field in ('unique_id', 'entity_id', 'device_name', 'friendly_name'):
            values.extend(expand_zigbee2mqtt_id(item.get(field)))
    return dedupe([value for value in values if value])


def expand_zigbee2mqtt_id(value: Any) -> list[str]:
    text = str(value or '').strip()
    if not text:
        return []
    ieee_match = re.search(r'0x[0-9a-fA-F]{12,16}', text)
    if ieee_match:
        return [ieee_match.group(0)]
    normalized = text
    for prefix in ('zigbee2mqtt_', 'mqtt_'):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            return [normalized] if normalized else []
    result = [text]
    if '.' in text:
        result.append(text.rsplit('.', 1)[-1])
    return result


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


def registry_result_list(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response.get('result') if isinstance(response, dict) else None
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ('entities', 'devices', 'areas', 'items'):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def assert_ha_success(response: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response, dict) and response.get('success') is False:
        raise RuntimeError(str(response.get('error') or response))
    return response


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
        'device_class': data.get('device_class'),
        'domain': data.get('domain'),
    }


def find_battery_level(role: dict[str, Any], states: list[dict[str, Any]]) -> int | None:
    match = find_battery_entity(role, states)
    if not match:
        return None
    return parse_battery(match.get('state'))


def find_battery_entity(role: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any] | None:
    device_id = str(role.get('device_id') or '').strip()
    role_entity = str(role.get('entity_id') or '')
    role_prefix = role_entity.rsplit('_', 1)[0] if '_' in role_entity else role_entity
    for state in states:
        entity_id = str(state.get('entity_id') or '')
        if not is_battery_entity(state):
            continue
        if device_id and str(state.get('device_id') or '') == device_id:
            if parse_battery(state.get('state')) is not None:
                return state
        if role_prefix and entity_id.startswith(role_prefix):
            if parse_battery(state.get('state')) is not None:
                return state
    return None


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
