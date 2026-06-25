from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests

from backend.config import config_float, config_str
from backend.logging_config import get_logger, is_debug_logging
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.mqtt_service import MqttMessage, MqttService

logger = get_logger(__name__)

DEFAULT_PROVISIONING_URL = "http://192.168.4.1/api/provision"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MQTT_WAIT_SECONDS = 30.0
C1001_CAPABILITY_KEYS = {
    "presence": "presence",
    "fall_detected": "fall_detection",
    "fall_detection": "fall_detection",
    "breathing_detected": "breathing_detection",
    "breathing_detection": "breathing_detection",
    "respiration_rate": "respiration_rate",
    "battery": "battery",
    "signal_quality": "signal_quality",
    "linkquality": "signal_quality",
}


@dataclass(frozen=True)
class ProvisionedSensor:
    device_id: str
    name: str
    room_id: str
    model: str | None
    firmware: str | None
    source_ref: str
    capabilities: list[str]


class Esp32ProvisioningService:
    def __init__(
        self,
        mapping: DeviceMappingService,
        mqtt: MqttService | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.mapping = mapping
        self.mqtt = mqtt or mapping.mqtt or MqttService()
        self.http = http_client or requests

    def status(self) -> dict[str, Any]:
        network = self._network_settings(public=True)
        return {
            "implemented": True,
            "status": "ready" if network.get("configured") and self.mqtt.configured() else "needs_configuration",
            "message": (
                "WLAN-Sensor-Provisioning ist bereit."
                if network.get("configured") and self.mqtt.configured()
                else "Bitte zuerst die Netzwerkeinstellungen speichern."
            ),
            "network_configured": bool(network.get("configured")),
            "mqtt_configured": bool(self.mqtt.configured()),
            "available_steps": [
                "Netzwerkdaten speichern",
                "Sensor im Einrichtungsmodus per HTTP konfigurieren",
                "Auf MQTT-Verfügbarkeit warten",
                "Sensor als Sentero-Präsenzsensor registrieren",
            ],
            "missing_steps": [],
        }

    def provision(self, room_id: str, display_name: str) -> dict[str, Any]:
        clean_room = str(room_id or "").strip()
        clean_name = str(display_name or "").strip()
        if not clean_room:
            raise ValueError("Bitte wählen Sie einen Raum aus.")
        if not clean_name:
            raise ValueError("Bitte geben Sie einen Sensornamen an.")

        started = time.perf_counter()
        logger.info("Provisioning started", extra={"component": "esp32_provisioning", "room_id": clean_room})
        payload = self.build_payload()
        if is_debug_logging():
            logger.debug("Provisioning payload prepared", extra={"component": "esp32_provisioning", "payload": masked_payload(payload)})

        response = self._post_provisioning_payload(payload)
        device_id = str(response.get("device_id") or "").strip()
        if not device_id:
            raise RuntimeError("Sensor hat keine Geräte-ID zurückgegeben.")
        logger.info(
            "Provisioning confirmed by sensor",
            extra={"component": "esp32_provisioning", "device_id": device_id, "model": response.get("model")},
        )

        state = self._wait_for_mqtt_state(device_id)
        source_ref = str(state.get("topic") or f"{self.topic_prefix()}/{device_id}/state")
        capabilities = capabilities_from_state_payload(state.get("payload"))
        sensor = ProvisionedSensor(
            device_id=device_id,
            name=clean_name,
            room_id=clean_room,
            model=clean_text(response.get("model")),
            firmware=clean_text(response.get("firmware")),
            source_ref=source_ref,
            capabilities=capabilities,
        )
        role = self._register_sensor(sensor)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "Device registered",
            extra={
                "component": "esp32_provisioning",
                "device_id": device_id,
                "room_id": clean_room,
                "source_ref": source_ref,
                "elapsed_ms": elapsed_ms,
            },
        )
        return {
            "ok": True,
            "device": {
                "id": device_id,
                "name": clean_name,
                "type": "presence_radar",
                "room_id": clean_room,
                "source": "mqtt",
                "capabilities": capabilities,
            },
            "message": "Präsenzsensor erfolgreich eingerichtet.",
            "role": role,
        }

    def build_payload(self) -> dict[str, Any]:
        network = self._network_settings(public=False)
        ssid = str(network.get("wifi_ssid") or "").strip()
        password = str(network.get("wifi_password") or "")
        if not ssid or not password:
            raise ValueError("Bitte speichern Sie zuerst WLAN-Name und WLAN-Passwort.")
        if not self.mqtt.configured():
            raise ValueError("Die Sensornetzwerk-Verbindung ist noch nicht konfiguriert.")
        return {
            "protocol": 1,
            "wifi": {
                "ssid": ssid,
                "password": password,
            },
            "mqtt": {
                "host": self.mqtt.host,
                "port": self.mqtt.port,
                "username": self.mqtt.username,
                "password": self.mqtt.password,
                "topic_prefix": self.topic_prefix(),
            },
            "device": {
                "timezone": self.timezone(),
                **({"token": self.device_token()} if self.device_token() else {}),
            },
        }

    def topic_prefix(self) -> str:
        return (
            os.getenv("SENTERO_ESP32_TOPIC_PREFIX")
            or config_str("esp32.topic_prefix", "")
            or config_str("mqtt.esp32_topic_prefix", "")
            or "sentero"
        ).strip().strip("/") or "sentero"

    def provisioning_url(self) -> str:
        return (
            os.getenv("SENTERO_ESP32_PROVISIONING_URL")
            or config_str("esp32.provisioning_url", "")
            or DEFAULT_PROVISIONING_URL
        ).strip()

    def provisioning_timeout(self) -> float:
        return float(os.getenv("SENTERO_ESP32_PROVISIONING_TIMEOUT") or config_float("esp32.provisioning_timeout", DEFAULT_TIMEOUT_SECONDS))

    def mqtt_wait_timeout(self) -> float:
        return float(os.getenv("SENTERO_ESP32_MQTT_WAIT_TIMEOUT") or config_float("esp32.mqtt_wait_timeout", DEFAULT_MQTT_WAIT_SECONDS))

    def timezone(self) -> str:
        return (os.getenv("SENTERO_TIMEZONE") or config_str("app.timezone", "Europe/Berlin") or "Europe/Berlin").strip()

    def device_token(self) -> str:
        return (os.getenv("SENTERO_ESP32_DEVICE_TOKEN") or "").strip()

    def _post_provisioning_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.provisioning_url()
        try:
            response = self.http.post(url, json=payload, timeout=self.provisioning_timeout())
            logger.debug(
                "Provisioning HTTP response",
                extra={"component": "esp32_provisioning", "status_code": getattr(response, "status_code", None), "url": url},
            )
            response.raise_for_status()
            body = response.json()
        except requests.Timeout as exc:
            logger.warning("Provisioning timeout", extra={"component": "esp32_provisioning", "url": url})
            raise RuntimeError("Präsenzsensor ist nicht erreichbar. Bitte Sensor einschalten und erneut versuchen.") from exc
        except Exception as exc:
            logger.exception("HTTP provisioning failed", extra={"component": "esp32_provisioning", "url": url})
            raise RuntimeError("Präsenzsensor konnte nicht verbunden werden.") from exc
        if not isinstance(body, dict) or body.get("success") is not True:
            logger.error("Sensor rejected provisioning", extra={"component": "esp32_provisioning", "response": safe_response(body)})
            raise RuntimeError("Präsenzsensor hat die Einrichtung abgelehnt.")
        return body

    def _wait_for_mqtt_state(self, device_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.mqtt_wait_timeout()
        state_topics = [f"{self.topic_prefix()}/{device_id}/state", f"c1001/{device_id}/state"]
        availability_topic = f"{self.topic_prefix()}/{device_id}/availability"
        availability_seen = False
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                availability = self.mqtt.retained_messages(availability_topic, timeout=0.8)
                if availability:
                    availability_seen = True
                    logger.info("MQTT availability received", extra={"component": "esp32_provisioning", "device_id": device_id, "topic": availability_topic})
                for topic in state_topics:
                    messages = self.mqtt.retained_messages(topic, timeout=0.8)
                    for message in messages:
                        if self._valid_state_message(message):
                            logger.debug("MQTT state received", extra={"component": "esp32_provisioning", "device_id": device_id, "topic": message.topic})
                            return {"topic": message.topic, "payload": message.payload}
                time.sleep(0.2)
            except RuntimeError as exc:
                if "Präsenzsensor sendet" in str(exc):
                    raise
                last_error = exc
                logger.warning("MQTT wait attempt failed", extra={"component": "esp32_provisioning", "device_id": device_id})
                time.sleep(0.5)
            except Exception as exc:
                last_error = exc
                logger.warning("MQTT wait attempt failed", extra={"component": "esp32_provisioning", "device_id": device_id})
                time.sleep(0.5)
        if availability_seen:
            logger.warning("Sensor confirmed but MQTT state missing", extra={"component": "esp32_provisioning", "device_id": device_id})
            raise RuntimeError("Präsenzsensor ist verbunden, sendet aber noch keine Messwerte.")
        logger.error("MQTT wait failed", extra={"component": "esp32_provisioning", "device_id": device_id})
        raise RuntimeError("Präsenzsensor hat sich nicht im Sensornetzwerk gemeldet.") from last_error

    def _valid_state_message(self, message: MqttMessage) -> bool:
        if not isinstance(message.payload, dict):
            logger.error("Invalid sensor state payload", extra={"component": "esp32_provisioning", "topic": message.topic})
            raise RuntimeError("Präsenzsensor sendet ungültige Daten.")
        if not capabilities_from_state_payload(message.payload):
            logger.error("Invalid sensor state payload", extra={"component": "esp32_provisioning", "topic": message.topic})
            raise RuntimeError("Präsenzsensor sendet noch keine nutzbaren Messwerte.")
        return True

    def _register_sensor(self, sensor: ProvisionedSensor) -> dict[str, Any]:
        try:
            role = f"{sensor.room_id}_presence"
            return self.mapping.upsert_role({
                "role": role,
                "room": sensor.room_id,
                "entity_id": sensor.source_ref,
                "device_id": sensor.device_id,
                "friendly_name": sensor.name,
                "device_class": "presence",
                "domain": "binary_sensor",
                "source": "mqtt",
                "confidence": 100,
                "model": sensor.model,
                "updated_at": now(),
            })
        except Exception as exc:
            logger.exception("Registration failed", extra={"component": "esp32_provisioning", "device_id": sensor.device_id, "room_id": sensor.room_id})
            raise RuntimeError("Präsenzsensor konnte nicht gespeichert werden.") from exc

    def _network_settings(self, public: bool = True) -> dict[str, Any]:
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


def capabilities_from_state_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    capabilities = []
    for key, capability in C1001_CAPABILITY_KEYS.items():
        if key in payload and payload.get(key) is not None:
            capabilities.append(capability)
    return sorted(set(capabilities))


def masked_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def mask(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: "***" if key in {"password", "token"} and item else mask(item) for key, item in value.items()}
        return value

    return mask(payload)


def safe_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "***" if "token" in key.lower() or "password" in key.lower() else safe_response(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_response(item) for item in value]
    return value


def clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
