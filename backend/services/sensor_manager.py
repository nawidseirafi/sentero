from __future__ import annotations

import json
import os
import requests
from backend.config import config_str
from typing import Any

from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, sensor_source_mode

logger = get_logger(__name__)


class SensorManager:
    """Product-facing facade for all sensor operations.

    The UI and higher-level services should call this manager instead of
    addressing Home Assistant, MQTT or Zigbee2MQTT directly.
    """

    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
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
            con.commit()

    def status(self) -> dict[str, Any]:
        source = sensor_source_mode()
        home_status = self.mapping.home_status()
        network = self.network_settings(public=True)
        return {
            "ready": bool(home_status.get("sensor_ready")) or source in {"mqtt", "zigbee2mqtt", "z2m", "mixed"},
            "mode": source,
            "status_label": "Bereit" if bool(home_status.get("sensor_ready")) else "Wartet auf Sensorverbindung",
            "network": network,
            "supported_sensor_types": ["door_contact", "presence_sensor", "motion_sensor", "button"],
        }

    def start_discovery(self, sensor_type: str, room_id: str | None = None, role: str | None = None, duration: int = 180) -> dict[str, Any]:
        clean_type = normalize_sensor_type(sensor_type)
        target_role = role or role_for_sensor(clean_type, room_id)
        logger.info(
            "Sensor discovery requested",
            extra={"component": "sensor_manager", "sensor_type": clean_type, "room_id": room_id, "sensor_source": sensor_source_mode()},
        )
        source = sensor_source_mode()
        if source in {"mqtt", "zigbee2mqtt", "z2m", "mixed"}:
            result = self.mapping.start_mqtt_discovery(target_role, room_id, duration=duration)
        else:
            result = self.mapping.start_zigbee_pairing(target_role, room_id, duration=duration)
        return {
            "discovery_id": result["session_id"],
            "status": product_status(result.get("status")),
            "message": product_message(clean_type, result),
            "sensor_type": clean_type,
            "room_id": room_id,
        }

    def discovered(self, discovery_id: int, dev: bool = False) -> dict[str, Any]:
        result = self.mapping.candidates(discovery_id, dev=dev)
        candidate = result.get("candidate")
        public_candidate = public_candidate_from(candidate) if candidate else None
        return {
            "discovery_id": discovery_id,
            "status": "found" if public_candidate else "searching" if result.get("remaining_seconds", 0) > 0 else "not_found",
            "message": "Sensor gefunden." if public_candidate else "Sensor wird gesucht.",
            "sensor": public_candidate,
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
    if text in {"button"}:
        return "button"
    return "presence_sensor"


def role_for_sensor(sensor_type: str, room_id: str | None) -> str:
    room = str(room_id or "home").strip() or "home"
    if sensor_type == "door_contact":
        return "main_door" if room in {"entrance", "hallway"} else f"{room}_door"
    if sensor_type == "button":
        return f"{room}_button"
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
    return "Sensor wird gesucht. Bitte einschalten oder Pairing-Taste drücken."


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
        }
    return {
        "id": candidate.get("entity_id"),
        "name": candidate.get("label") or "Sensor",
        "type": public_type_from_device_class(str(candidate.get("device_class") or "")),
        "confidence": candidate.get("score") or candidate.get("confidence") or 0,
    }


def public_type_from_device_class(device_class: str) -> str:
    if device_class in {"door", "window", "opening", "contact"}:
        return "door_contact"
    if device_class in {"motion", "occupancy", "presence"}:
        return "presence_sensor"
    if device_class == "button":
        return "button"
    return "sensor"


def public_type_from_mqtt_candidate(candidate: dict[str, Any]) -> str:
    device_class = str(candidate.get("device_class") or "").lower()
    payload_key = str(candidate.get("payload_key") or "").lower()
    if device_class in {"door", "window", "opening", "contact"} or payload_key in {"contact", "open"}:
        return "door_contact"
    if device_class in {"motion", "occupancy", "presence"} or payload_key in {"occupancy", "presence", "motion"}:
        return "presence_sensor"
    if device_class == "button" or payload_key in {"action", "button"}:
        return "button"
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
    return "presence_sensor"


def clean_text(value: Any) -> str:
    return str(value or "").strip()
