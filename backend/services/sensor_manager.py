from __future__ import annotations

import json
import os
import requests
from dotenv import dotenv_values, load_dotenv
from backend.config import config_str
from backend.paths import ENV_PATH
from typing import Any

from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, SensorTransport, sensor_source_mode
from backend.services.ecotracker_service import EcoTrackerClient, normalize_ecotracker_host
from backend.services.esp32_discovery_service import Esp32DiscoveryService
from backend.services.esp32_provisioning_service import Esp32ProvisioningService

logger = get_logger(__name__)
DEFAULT_DISCOVERY_SECONDS = 120

load_dotenv(ENV_PATH)


class SensorManager:
    """Product-facing facade for all sensor operations.

    The UI and higher-level services should call this manager instead of
    addressing MQTT or Zigbee2MQTT directly.
    """

    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
        self.esp32_discovery = Esp32DiscoveryService(mapping)
        self.esp32_provisioning = Esp32ProvisioningService(mapping, discovery=self.esp32_discovery)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists sensor_manager_network_settings (
                    id integer primary key check (id = 1),
                    wifi_ssid text,
                    wifi_password text,
                    mqtt_host text,
                    mqtt_port integer,
                    mqtt_username text,
                    mqtt_password text,
                    updated_at text not null
                )"""
            )
            con.execute(
                "insert or ignore into sensor_manager_network_settings (id, updated_at) values (1, ?)",
                (self.mapping_now(),),
            )
            con.execute(
                """create table if not exists ecotracker_settings (
                    id integer primary key check (id = 1),
                    host text,
                    enabled integer not null default 0,
                    last_payload_json text not null default '{}',
                    last_checked_at text,
                    created_at text not null,
                    updated_at text not null
                )"""
            )
            con.execute(
                "insert or ignore into ecotracker_settings (id, created_at, updated_at) values (1, ?, ?)",
                (self.mapping_now(), self.mapping_now()),
            )
            con.execute(
                """update sensor_roles
                   set entity_id = 'ecotracker.power',
                       device_class = 'power',
                       updated_at = ?
                   where active = 1
                     and source = 'ecotracker'
                     and entity_id = 'ecotracker.energyCounterIn'""",
                (self.mapping_now(),),
            )
            con.commit()

    def status(self) -> dict[str, Any]:
        source = sensor_source_mode()
        home_status = self.mapping.home_status()
        network = self.network_settings(public=True)
        return {
            "ready": bool(home_status.get("sensor_ready")) or source in {"mqtt", "zigbee2mqtt", "z2m"},
            "mode": source,
            "status_label": "Bereit" if bool(home_status.get("sensor_ready")) else "Wartet auf Sensorverbindung",
            "network": network,
            "supported_sensor_types": ["door_contact", "presence_sensor", "smoke_detector", "motion_sensor", "button", "electricity_meter"],
            "supported_meter_devices": ["everhome_ecotracker_ir"],
            "wifi_sensor_setup_enabled": wifi_sensor_setup_enabled(),
            "presence_sensor_transport": configured_presence_sensor_transport(),
        }

    def ecotracker_status(self) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from ecotracker_settings where id = 1").fetchone()
        data = dict(row) if row else {}
        reading = None
        if data.get("enabled") and data.get("host"):
            try:
                reading = public_ecotracker_reading(EcoTrackerClient(str(data["host"])).read())
            except Exception:
                logger.exception("EcoTracker status reading failed", extra={"component": "sensor_manager", "source": "ecotracker"})
        return {
            "enabled": bool(data.get("enabled")),
            "configured": bool(data.get("host")),
            "host": data.get("host") or "",
            "device": "everHome EcoTracker IR",
            "last_checked_at": data.get("last_checked_at"),
            "reading": reading,
        }

    def test_ecotracker(self, host: str) -> dict[str, Any]:
        clean_host = normalize_ecotracker_host(host)
        payload = EcoTrackerClient(clean_host).read()
        return {
            "ok": True,
            "message": "EcoTracker erreichbar.",
            "host": clean_host,
            "reading": public_ecotracker_reading(payload),
        }

    def connect_ecotracker(self, host: str) -> dict[str, Any]:
        clean_host = normalize_ecotracker_host(host)
        payload = EcoTrackerClient(clean_host).read()
        timestamp = self.mapping_now()
        with self.mapping.connect() as con:
            con.execute(
                """insert into ecotracker_settings (id, host, enabled, last_payload_json, last_checked_at, created_at, updated_at)
                   values (1, ?, 1, ?, ?, ?, ?)
                   on conflict(id) do update set
                     host = excluded.host,
                     enabled = 1,
                     last_payload_json = excluded.last_payload_json,
                     last_checked_at = excluded.last_checked_at,
                     updated_at = excluded.updated_at""",
                (clean_host, json_dumps(payload), timestamp, timestamp, timestamp),
            )
            con.execute(
                """insert into sensor_roles
                   (role, room, entity_id, device_id, friendly_name, device_class, domain, source, confidence, active, created_at, updated_at)
                   values ('home_energy', 'home', 'ecotracker.power', ?, 'everHome EcoTracker IR', 'power', 'sensor', 'ecotracker', 100, 1, ?, ?)
                   on conflict(role) where active = 1 do update set
                     room = excluded.room,
                     entity_id = excluded.entity_id,
                     device_id = excluded.device_id,
                     friendly_name = excluded.friendly_name,
                     device_class = excluded.device_class,
                     domain = excluded.domain,
                     source = excluded.source,
                     confidence = excluded.confidence,
                     updated_at = excluded.updated_at""",
                (f"ecotracker:{clean_host}", timestamp, timestamp),
            )
            con.commit()
        logger.info("EcoTracker connected", extra={"component": "sensor_manager", "host": clean_host})
        return {
            "status": "registered",
            "sensor": {
                "id": "home_energy",
                "name": "everHome EcoTracker IR",
                "room_id": "home",
                "type": "electricity_meter",
            },
            "reading": public_ecotracker_reading(payload),
        }

    def start_discovery(self, sensor_type: str, room_id: str | None = None, role: str | None = None, duration: int = DEFAULT_DISCOVERY_SECONDS, transport: str | None = None) -> dict[str, Any]:
        clean_type = normalize_sensor_type(sensor_type)
        target_role = role or role_for_sensor(clean_type, room_id)
        requested_transport = normalize_transport(transport or (configured_presence_sensor_transport() if clean_type == "presence_sensor" else SensorTransport.ZIGBEE.value))
        logger.info(
            "Sensor discovery requested",
            extra={"component": "sensor_manager", "sensor_type": clean_type, "room_id": room_id, "transport": requested_transport, "sensor_source": sensor_source_mode()},
        )
        if requested_transport == SensorTransport.WIFI_ESPHOME.value and not wifi_sensor_setup_enabled():
            raise ValueError("wifi_sensor_setup_disabled")
        source = sensor_source_mode()
        if requested_transport == SensorTransport.WIFI_ESPHOME.value:
            return {
                "discovery_id": 0,
                "status": "manual_action",
                "message": "Präsenzsensoren werden über die automatische Verbindung eingerichtet.",
                "sensor_type": clean_type,
                "room_id": room_id,
                "transport": requested_transport,
                "detail": {"reason": "wifi_esphome_requires_provisioning"},
            }
        existing = self.mapping.find_unassigned_devices(public_sensor_type(clean_type))
        if existing:
            return {
                "discovery_id": 0,
                "status": "existing_device_found",
                "message": "Bereits verbundener Sensor gefunden." if len(existing) == 1 else "Bereits verbundene Sensoren gefunden.",
                "sensor_type": clean_type,
                "room_id": room_id,
                "transport": requested_transport,
                "device": existing[0] if len(existing) == 1 else None,
                "devices": existing,
                "expires_in_seconds": 0,
            }
        result = self.mapping.start_mqtt_discovery(target_role, room_id, duration=duration, sensor_type=public_sensor_type(clean_type))
        return {
            "discovery_id": result["session_id"],
            "status": product_status(result.get("status")),
            "message": product_message(clean_type, result),
            "sensor_type": clean_type,
            "room_id": room_id,
            "transport": requested_transport,
            "expires_in_seconds": duration,
        }

    def discovered(self, discovery_id: int, dev: bool = False) -> dict[str, Any]:
        result = self.mapping.candidates(discovery_id, dev=True)
        candidate = result.get("candidate")
        public_candidate = public_candidate_from(candidate) if candidate else None
        devices = unique_public_candidates([*(result.get("candidates") or []), *([candidate] if candidate else [])])
        if result.get("status") in {"wrong_type_found", "unsupported_device_found"}:
            device = result.get("device")
            return {
                "discovery_id": discovery_id,
                "status": result.get("status"),
                "message": customer_discovery_message(str(result.get("status") or ""), result),
                "sensor": None,
                "device": device,
                "devices": [device] if device else [],
                "requested_type": result.get("requested_type"),
                "detected_type": result.get("detected_type"),
                "remaining_seconds": 0,
            }
        return {
            "discovery_id": discovery_id,
            "status": "found" if public_candidate else "searching" if result.get("remaining_seconds", 0) > 0 else "not_found",
            "message": "Sensor gefunden." if public_candidate else "Sensor wird gesucht.",
            "sensor": public_candidate,
            "devices": devices,
            "remaining_seconds": result.get("remaining_seconds"),
        }

    def register(self, sensor_id: str, discovery_id: int, name: str | None = None, room_id: str | None = None, dev: bool = False) -> dict[str, Any]:
        result = self.mapping.confirm(discovery_id, sensor_id, name=name, room=room_id, dev=dev)
        role = result.get("role") or {}
        logger.info(
            "Sensor registered",
            extra={"component": "sensor_manager", "device_id": role.get("role"), "room_id": role.get("room")},
        )
        return {
            "status": "registered",
            "sensor": {
                "id": role.get("role") or sensor_id,
                "name": role.get("label") or name or "Sensor",
                "room_id": role.get("room") or room_id,
                "type": public_type_from_role(str(role.get("role") or "")),
            },
        }

    def assign_unassigned(self, device_id: str, sensor_type: str, room_id: str | None = None, role: str | None = None, name: str | None = None, dev: bool = False) -> dict[str, Any]:
        clean_type = normalize_sensor_type(sensor_type)
        target_role = role or role_for_sensor(clean_type, room_id)
        result = self.mapping.assign_unassigned_device(device_id, target_role, room_id, public_sensor_type(clean_type), name=name, dev=True)
        role_row = result.get("role") or {}
        return {
            "status": "registered",
            "sensor": {
                "id": role_row.get("role") or target_role,
                "name": role_row.get("label") or name or "Sensor",
                "room_id": role_row.get("room") or room_id,
                "type": public_type_from_role(str(role_row.get("role") or target_role)),
            },
        }

    def unassigned_devices(self) -> dict[str, Any]:
        return {"devices": self.mapping.unassigned_devices()}

    def ignore_unassigned(self, device_id: str) -> dict[str, Any]:
        return self.mapping.ignore_unassigned_device(device_id)

    def remove_unassigned(self, device_id: str) -> dict[str, Any]:
        return self.mapping.remove_zigbee_device_by_id(device_id)

    def cancel_discovery(self, discovery_id: int | None = None) -> dict[str, Any]:
        return self.mapping.cancel_discovery(discovery_id)

    def assign_room(self, sensor_id: str, room_id: str) -> dict[str, Any]:
        return self.mapping_update_room(sensor_id, room_id)

    def network_settings(self, public: bool = True) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from sensor_manager_network_settings where id = 1").fetchone()
        data = dict(row) if row else {}
        if not public:
            return data
        return {
            "wifi_ssid": data.get("wifi_ssid") or "",
            "wifi_password_set": bool(data.get("wifi_password")),
            "configured": bool(data.get("wifi_ssid")) and bool(data.get("wifi_password")),
        }

    def save_network_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.network_settings(public=False)
        wifi_password = str(payload.get("wifi_password") or "").strip() or current.get("wifi_password")
        with self.mapping.connect() as con:
            con.execute(
                """insert into sensor_manager_network_settings
                   (id, wifi_ssid, wifi_password, mqtt_host, mqtt_port, mqtt_username, mqtt_password, updated_at)
                   values (1, ?, ?, ?, ?, ?, ?, ?)
                   on conflict(id) do update set
                     wifi_ssid = excluded.wifi_ssid,
                     wifi_password = excluded.wifi_password,
                     mqtt_host = excluded.mqtt_host,
                     mqtt_port = excluded.mqtt_port,
                     mqtt_username = excluded.mqtt_username,
                     mqtt_password = excluded.mqtt_password,
                     updated_at = excluded.updated_at""",
                (
                    clean_text(payload.get("wifi_ssid")),
                    wifi_password,
                    current.get("mqtt_host"),
                    current.get("mqtt_port"),
                    current.get("mqtt_username"),
                    current.get("mqtt_password"),
                    self.mapping_now(),
                ),
            )
            con.commit()
        logger.info("Sensor network settings saved", extra={"component": "sensor_manager"})
        return {"status": "saved", "network": self.network_settings(public=True)}

    def test_network_settings(self) -> dict[str, Any]:
        network = self.network_settings(public=True)
        if not network.get("configured"):
            return {
                "ok": False,
                "message": "Bitte zuerst die Netzwerkeinstellungen konfigurieren.",
            }
        try:
            update_url = config_str("updates.base_url").strip().rstrip("/")
            response = requests.get(update_url, timeout=5)
            response.raise_for_status()
            return {
                "ok": True,
                "message": "Netzwerkkonfiguration gültig. Update-Server erreichbar.",
                "network": network,
            }
        except Exception:
            logger.exception(
                "Update server test failed",
                extra={"component": "sensor_manager"},
            )
            return {
                "ok": False,
                "message": "Netzwerkkonfiguration vorhanden, Update-Server jedoch nicht erreichbar.",
                "network": network,
            }

    def provisioning_status(self) -> dict[str, Any]:
        return self.esp32_provisioning.status()

    def esp32_discovery_status(self) -> dict[str, Any]:
        return self.esp32_discovery.status()

    def start_esp32_discovery(self) -> dict[str, Any]:
        self.esp32_discovery.ensure_listening()
        return {
            "ok": True,
            "message": "Präsenzsensor wird gesucht.",
            "discovery": self.esp32_discovery.status(),
        }

    def start_esp32_provisioning(self, room_id: str, display_name: str, device_id: str | None = None) -> dict[str, Any]:
        return self.esp32_provisioning.provision(room_id=room_id, display_name=display_name, device_id=device_id)

    def mapping_update_room(self, sensor_id: str, room_id: str) -> dict[str, Any]:
        # Persistent device-model assignment is prepared in SenteroSensorService.
        return {"status": "prepared", "sensor_id": sensor_id, "room_id": room_id}

    @staticmethod
    def mapping_now() -> str:
        from backend.services.device_mapping_service import now

        return now()


def normalize_sensor_type(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"door", "door_contact", "contact"}:
        return "door_contact"
    if text in {"presence", "presence_sensor", "motion"}:
        return "presence_sensor"
    if text in {"smoke", "smoke_detector", "rauch", "rauchmelder", "smoke_alarm", "fire_alarm"}:
        return "smoke_detector"
    if text in {"button"}:
        return "button"
    if text in {"smart_meter", "meter", "electricity_meter", "energy_meter", "power_meter", "strom", "stromzaehler", "stromzähler"}:
        return "electricity_meter"
    if text in {"water_meter", "water", "wasser", "wasserzaehler", "wasserzähler"}:
        return "water_meter"
    if text in {"gas_meter", "gas", "gaszaehler", "gaszähler"}:
        return "gas_meter"
    return "presence_sensor"


def normalize_transport(value: str | None) -> str:
    text = str(value or SensorTransport.ZIGBEE.value).strip().lower()
    if text in {SensorTransport.WIFI_ESPHOME.value, "wifi", "esp32", "esphome"}:
        return SensorTransport.WIFI_ESPHOME.value
    return SensorTransport.ZIGBEE.value


def wifi_sensor_setup_enabled() -> bool:
    return (
        str(os.getenv("SENTERO_ENABLE_WIFI_SENSOR_SETUP") or "").strip().lower() in {"1", "true", "yes", "on"}
        or configured_presence_sensor_transport() == SensorTransport.WIFI_ESPHOME.value
    )


def configured_presence_sensor_transport() -> str:
    env_value = os.getenv("SENTERO_PRESENCE_SENSOR_TRANSPORT")
    if env_value is None:
        env_value = dotenv_values(ENV_PATH).get("SENTERO_PRESENCE_SENSOR_TRANSPORT")
    return normalize_transport(env_value or SensorTransport.ZIGBEE.value)


def public_sensor_type(value: str) -> str:
    if value == "door_contact":
        return "door"
    if value == "presence_sensor":
        return "presence"
    if value == "smoke_detector":
        return "smoke_detector"
    return value


def role_for_sensor(sensor_type: str, room_id: str | None) -> str:
    room = str(room_id or "home").strip() or "home"
    if sensor_type == "door_contact":
        return "main_door" if room in {"entrance", "hallway"} else f"{room}_door"
    if sensor_type == "smoke_detector":
        return f"{room}_smoke"
    if sensor_type == "button":
        return f"{room}_button"
    if sensor_type == "electricity_meter":
        return f"{room}_energy"
    if sensor_type == "water_meter":
        return f"{room}_water"
    if sensor_type == "gas_meter":
        return f"{room}_gas"
    return f"{room}_presence"


def product_status(status: Any) -> str:
    text = str(status or "")
    if text in {"pairing_started", "waiting_for_signal"}:
        return "searching"
    if text == "pairing_needs_manual_action":
        return "manual_action"
    return text or "searching"


def product_message(sensor_type: str, result: dict[str, Any]) -> str:
    if result.get("status") == "pairing_needs_manual_action":
        return "Bitte versetzen Sie den Sensor in den Verbindungsmodus."
    if sensor_type == "door_contact":
        return "Türsensor wird gesucht. Bitte Pairing-Taste drücken."
    if sensor_type == "smoke_detector":
        return "Rauchmelder wird gesucht. Bitte Pairing-Taste drücken."
    if sensor_type == "electricity_meter":
        return "Stromzähler wird gesucht. Bitte Zähler koppeln oder einen neuen Messwert auslösen."
    if sensor_type == "water_meter":
        return "Wasserzähler wird gesucht. Bitte Zähler koppeln oder einen neuen Messwert auslösen."
    if sensor_type == "gas_meter":
        return "Gaszähler wird gesucht. Bitte Zähler koppeln oder einen neuen Messwert auslösen."
    return "Sensor wird gesucht. Bitte einschalten oder Pairing-Taste drücken."


def customer_discovery_message(status: str, result: dict[str, Any]) -> str:
    if status == "wrong_type_found":
        detected = str(result.get("detected_type") or "sensor")
        requested = str(result.get("requested_type") or "sensor")
        return f"Es wurde ein {sensor_type_label(detected)} erkannt. Er wurde nicht als {sensor_type_label(requested)} hinzugefügt."
    if status == "unsupported_device_found":
        return "Dieses Gerät kann von Sentero derzeit nicht verwendet werden."
    return str(result.get("message") or "")


def sensor_type_label(sensor_type: str) -> str:
    return {
        "door": "Türsensor",
        "door_contact": "Türsensor",
        "presence": "Präsenzsensor",
        "presence_sensor": "Präsenzsensor",
        "smoke_detector": "Rauchmelder",
        "button": "Taster",
        "electricity_meter": "Stromzähler",
        "water_meter": "Wasserzähler",
        "gas_meter": "Gaszähler",
    }.get(str(sensor_type or ""), "Sensor")


def public_candidate_from(candidate: dict[str, Any]) -> dict[str, Any]:
    source = str(candidate.get("source") or candidate.get("platform") or "").strip()
    if source in {"zigbee2mqtt", "mqtt"} or candidate.get("source_ref") or candidate.get("topic"):
        source_ref = str(candidate.get("source_ref") or candidate.get("topic") or candidate.get("entity_id") or "").strip()
        device_id = str(candidate.get("device_id") or "").strip()
        return {
            "id": device_id or source_ref or candidate.get("entity_id"),
            "name": candidate.get("friendly_name") or candidate.get("device_name") or name_from_source_ref(source_ref) or "Sensor",
            "type": public_type_from_mqtt_candidate(candidate),
            "confidence": candidate.get("score") or candidate.get("confidence") or 0,
            "source": source or "zigbee2mqtt",
            "source_ref": source_ref or None,
            "entities": candidate.get("entity_ids") or [],
        }
    return {
        "id": candidate.get("entity_id"),
        "name": candidate.get("label") or "Sensor",
        "type": public_type_from_device_class(str(candidate.get("device_class") or "")),
        "confidence": candidate.get("score") or candidate.get("confidence") or 0,
        "entities": candidate.get("entity_ids") or [],
    }


def unique_public_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        public = public_candidate_from(candidate)
        key = str(public.get("id") or public.get("source_ref") or public.get("name"))
        existing = devices.get(key)
        if not existing or float(public.get("confidence") or 0) > float(existing.get("confidence") or 0):
            devices[key] = public
    return sorted(devices.values(), key=lambda item: float(item.get("confidence") or 0), reverse=True)


def public_type_from_device_class(device_class: str) -> str:
    if device_class in {"door", "window", "opening", "contact"}:
        return "door_contact"
    if device_class in {"motion", "occupancy", "presence"}:
        return "presence_sensor"
    if device_class == "smoke":
        return "smoke_detector"
    if device_class == "button":
        return "button"
    if device_class in {"energy", "power"}:
        return "electricity_meter"
    if device_class == "water":
        return "water_meter"
    if device_class == "gas":
        return "gas_meter"
    return "sensor"


def public_type_from_mqtt_candidate(candidate: dict[str, Any]) -> str:
    device_class = str(candidate.get("device_class") or "").lower()
    payload_key = str(candidate.get("payload_key") or "").lower()
    if device_class in {"door", "window", "opening", "contact"} or payload_key in {"contact", "open"}:
        return "door_contact"
    if device_class in {"motion", "occupancy", "presence"} or payload_key in {"occupancy", "presence", "motion"}:
        return "presence_sensor"
    if device_class == "smoke" or payload_key in {"smoke", "smoke_alarm", "smoke_state"}:
        return "smoke_detector"
    if device_class == "button" or payload_key in {"action", "button"}:
        return "button"
    if device_class in {"energy", "power"} or payload_key in {"energy", "energy_consumption", "electricity", "electricity_consumption", "power", "power_usage"}:
        return "electricity_meter"
    if device_class == "water" or payload_key in {"water", "water_consumption"}:
        return "water_meter"
    if device_class == "gas" or payload_key in {"gas", "gas_consumption"}:
        return "gas_meter"
    return "sensor"


def name_from_source_ref(source_ref: str) -> str:
    if not source_ref:
        return ""
    return source_ref.strip("/").rsplit("/", 1)[-1].replace("_", " ").strip()


def public_type_from_role(role: str) -> str:
    if "door" in role or "contact" in role:
        return "door_contact"
    if "button" in role:
        return "button"
    if "smoke" in role or "rauch" in role:
        return "smoke_detector"
    if any(term in role for term in ["energy", "power", "electricity"]):
        return "electricity_meter"
    if "water" in role:
        return "water_meter"
    if "gas" in role:
        return "gas_meter"
    return "presence_sensor"


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def public_ecotracker_reading(payload: dict[str, Any]) -> dict[str, Any]:
    meter_reading_kwh = wh_to_kwh(payload.get("energyCounterIn"))
    return {
        "power_w": payload.get("power"),
        "power_avg_w": payload.get("powerAvg"),
        "meter_reading_kwh": meter_reading_kwh,
        "energy_in_kwh": meter_reading_kwh,
        "energy_out_kwh": wh_to_kwh(payload.get("energyCounterOut")),
    }


def wh_to_kwh(value: Any) -> float | None:
    try:
        return round(float(value) / 1000, 3)
    except (TypeError, ValueError):
        return None
