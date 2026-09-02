from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from backend.config import config_float, config_str
from backend.logging_config import get_logger, is_debug_logging
from backend.services.mqtt_service import MqttService

logger = get_logger(__name__)

IGNORED_TOPIC_PARTS = {"bridge", "availability"}
BINARY_DEVICE_CLASSES = {"contact", "occupancy", "motion", "presence", "opening", "smoke"}
SMOKE_STATE_KEYS = ("smoke", "smoke_alarm", "smoke_state")
STATE_KEYS = (
    "contact",
    "occupancy",
    "motion",
    "motion_state",
    "presence",
    "open",
    "action",
    "fall_detected",
    "fall_detection",
    "breathing_detected",
    "breathing_detection",
    "respiration_rate",
    "sleep_status",
    "bed_presence",
    *SMOKE_STATE_KEYS,
    "state",
)
MEASUREMENT_KEYS = (
    "battery",
    "battery_low",
    "linkquality",
    "signal_quality",
    "temperature",
    "humidity",
    "illuminance",
    "illuminance_lux",
    "energy",
    "energy_consumption",
    "electricity",
    "electricity_consumption",
    "power",
    "power_usage",
    "water",
    "water_consumption",
    "gas",
    "gas_consumption",
)


class Zigbee2MqttSensorSource:
    name = "zigbee2mqtt"

    def __init__(self, mqtt: MqttService | None = None) -> None:
        self.mqtt = mqtt or MqttService()
        self.host = self.mqtt.host
        self.port = self.mqtt.port
        self.topic_prefix = os.getenv("SENTERO_ZIGBEE2MQTT_TOPIC_PREFIX") or os.getenv("ZIGBEE2MQTT_TOPIC_PREFIX") or config_str("mqtt.topic_prefix", "") or config_str("mqtt.zigbee2mqtt_topic_prefix", "zigbee2mqtt") or "zigbee2mqtt"
        self.topic_prefixes = self._topic_prefixes()
        self.snapshot_timeout = float(os.getenv("SENTERO_MQTT_SNAPSHOT_TIMEOUT") or config_float("mqtt.snapshot_timeout", 2.5))
        logger.debug(
            "Zigbee2MQTT source configured",
            extra={"component": "sensor_source", "sensor_source": self.name, "host": self.host, "port": self.port, "topic_prefix": self.topic_prefix, "topic_prefixes": self.topic_prefixes},
        )

    def configured(self) -> bool:
        return self.mqtt.configured()

    def subscription_topics(self) -> list[str]:
        return [f"{prefix}/#" for prefix in self.topic_prefixes]

    def snapshot(self) -> list[dict[str, Any]]:
        seed = os.getenv("SENTERO_MQTT_BOOTSTRAP_EVENTS", "").strip() or config_str("mqtt.bootstrap_events", "")
        if seed:
            logger.debug("Zigbee2MQTT snapshot uses bootstrap seed", extra={"component": "sensor_source", "sensor_source": self.name})
            return self._snapshot_from_seed(seed)

        # Keep one subscription alive for the lifetime of the backend. Zigbee2MQTT
        # device-state topics are commonly not retained, so a short polling window
        # cannot reliably represent the current sensor state.
        try:
            self.mqtt.start_listener(self.subscription_topics())
            by_topic = {}
            for prefix in self.topic_prefixes:
                for message in self.mqtt.cached_messages(f"{prefix}/#"):
                    by_topic[message.topic] = message

            # During the very first API request the asynchronous listener may not
            # yet have received retained bridge metadata. Bootstrap once from a
            # short snapshot, merge it into the same cache, then continue using the
            # persistent cache for all later requests.
            if not by_topic:
                bootstrap = []
                for prefix in self.topic_prefixes:
                    bootstrap.extend(self.mqtt.retained_messages(f"{prefix}/#", timeout=self.snapshot_timeout))
                if bootstrap:
                    self.mqtt.seed_cache(bootstrap)
                    for message in bootstrap:
                        by_topic[message.topic] = message
            messages = list(by_topic.values())
        except Exception:
            logger.exception(
                "Zigbee2MQTT snapshot failed",
                extra={"component": "sensor_source", "sensor_source": self.name, "topic_prefix": self.topic_prefix},
            )
            return []

        rows: list[dict[str, Any]] = []
        current_time = utc_now()
        device_metadata = self._bridge_device_metadata(messages)
        for message in messages:
            timestamp = str(getattr(message, "received_at", None) or current_time)
            rows.extend(self._entities_from_message(message.topic, message.payload, timestamp, device_metadata))
        logger.debug(
            "Zigbee2MQTT snapshot completed",
            extra={"component": "sensor_source", "sensor_source": self.name, "message_count": len(messages), "row_count": len(rows)},
        )
        return rows

    def publish(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.mqtt.publish(topic, payload)

    def _snapshot_from_seed(self, seed: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(seed)
        except json.JSONDecodeError:
            logger.exception("Invalid MQTT bootstrap seed", extra={"component": "sensor_source", "sensor_source": self.name})
            return []
        rows = payload if isinstance(payload, list) else [payload]
        result: list[dict[str, Any]] = []
        now = utc_now()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("entity_id"):
                result.append({**row, "source": self.name})
                continue
            topic = str(row.get("topic") or f"{self.topic_prefix}/{row.get('sensor_id') or row.get('device') or ''}")
            result.extend(self._entities_from_message(topic, row.get("payload") if "payload" in row else row, str(row.get("changed_at") or now)))
        return result

    def _entities_from_message(self, topic: str, payload: Any, timestamp: str, device_metadata: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if self._is_bridge_devices_topic(topic):
            return self._entities_from_bridge_devices(payload, topic, timestamp)
        device = self._device_from_topic(topic)
        if not device or not isinstance(payload, dict):
            logger.debug(
                "Zigbee2MQTT message skipped",
                extra={"component": "sensor_source", "sensor_source": self.name, "topic": topic, "has_device": bool(device)},
            )
            return []
        if is_debug_logging():
            logger.debug(
                "Zigbee2MQTT payload received",
                extra={"component": "sensor_source", "sensor_source": self.name, "topic": topic, "device_id": device, "payload": payload},
            )
        bridge_metadata = (device_metadata or {}).get(device) or (device_metadata or {}).get(device.lower()) or {}
        enriched_payload = {**bridge_metadata, **payload, "topic": topic, "source_ref": topic, "source": self._source_from_topic(topic)}
        if topic.strip("/").rsplit("/", 1)[-1] == "availability":
            return [self._availability_entity(device, payload, enriched_payload, timestamp)]
        rows: list[dict[str, Any]] = []
        if enriched_payload.get("source") == self.name:
            rows.append(self._device_state_entity(device, enriched_payload, timestamp))
        state_keys = [key for key in STATE_KEYS if key in payload and key != "state"]
        if "alarm" in payload and self._payload_has_smoke_capability(enriched_payload):
            state_keys.append("alarm")
        if not state_keys and "state" in payload:
            state_keys = ["state"]
        if state_keys:
            for state_key in state_keys:
                rows.append(self._entity(device, state_key, payload.get(state_key), enriched_payload, timestamp))
        for key in MEASUREMENT_KEYS:
            if key in payload:
                rows.append(self._entity(device, key, payload.get(key), enriched_payload, timestamp))
        logger.debug(
            "Zigbee2MQTT payload normalized",
            extra={"component": "sensor_source", "sensor_source": self.name, "topic": topic, "device_id": device, "row_count": len(rows)},
        )
        return rows

    def _device_state_entity(self, device: str, payload: dict[str, Any], timestamp: str) -> dict[str, Any]:
        source = payload.get("source") or self.name
        ieee = str(payload.get("ieee_address") or "").strip()
        physical_device_id = ieee if source == self.name and ieee else device if source == "mqtt" else slugify(device)
        identifier_value = ieee if source == self.name and ieee else device
        topic = str(payload.get("topic") or payload.get("source_ref") or "").strip()
        return {
            "entity_id": topic or f"{source}/{device}",
            "domain": "mqtt",
            "state": "online",
            "friendly_name": device,
            "device_class": None,
            "unit": None,
            "unit_of_measurement": None,
            "device_id": physical_device_id,
            "platform": source,
            "unique_id": f"{source}_{slugify(physical_device_id)}_state",
            "topic": topic,
            "source_ref": payload.get("source_ref") or topic,
            "payload_key": "state",
            "original_name": device,
            "device_name": device,
            "manufacturer": payload.get("manufacturer") or payload.get("vendor"),
            "model": payload.get("model") or payload.get("model_id"),
            "identifiers": [[source, identifier_value]],
            "last_changed": timestamp,
            "last_updated": timestamp,
            "source": source,
            "attributes": dict(payload),
            **{key: value for key, value in payload.items() if key not in {"topic", "source_ref", "source"}},
        }

    def _bridge_device_metadata(self, messages: list[Any]) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}
        for message in messages:
            if not self._is_bridge_devices_topic(str(getattr(message, "topic", "") or "")):
                continue
            payload = getattr(message, "payload", None)
            if not isinstance(payload, list):
                continue
            for device in payload:
                if not isinstance(device, dict):
                    continue
                ieee = str(device.get("ieee_address") or device.get("ieee") or device.get("id") or "").strip()
                friendly_name = str(device.get("friendly_name") or "").strip()
                definition = device.get("definition") if isinstance(device.get("definition"), dict) else {}
                expose_keys = self._expose_keys(definition.get("exposes"))
                item = {
                    "manufacturer": definition.get("vendor") or device.get("manufacturer"),
                "model": definition.get("model") or device.get("model_id") or device.get("model"),
                "ieee_address": ieee or None,
                "supported": device.get("supported"),
                "interview_completed": device.get("interview_completed"),
                "definition": definition,
                "exposes": definition.get("exposes"),
                "zigbee2mqtt_friendly_name": friendly_name or None,
                "smoke_capability": any(key in {*SMOKE_STATE_KEYS, "alarm"} for key in expose_keys),
            }
                for key in (friendly_name, ieee):
                    clean = str(key or "").strip()
                    if clean:
                        metadata[clean] = item
                        metadata[clean.lower()] = item
        return metadata

    def _device_from_topic(self, topic: str) -> str:
        prefix = next((value for value in self.topic_prefixes if topic.startswith(f"{value}/")), "")
        if not prefix:
            return ""
        suffix = topic[len(prefix) + 1:].strip("/")
        if not suffix:
            return ""
        device = suffix.split("/", 1)[0]
        return "" if device in IGNORED_TOPIC_PARTS else device

    def _is_bridge_devices_topic(self, topic: str) -> bool:
        return any(topic.strip("/") == f"{prefix}/bridge/devices" for prefix in self.topic_prefixes)

    def _entities_from_bridge_devices(self, payload: Any, topic: str, timestamp: str) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            return []
        rows: list[dict[str, Any]] = []
        for device in payload:
            if not isinstance(device, dict):
                continue
            ieee = str(device.get("ieee_address") or device.get("ieee") or device.get("id") or "").strip()
            friendly_name = str(device.get("friendly_name") or ieee).strip()
            if not friendly_name or friendly_name in IGNORED_TOPIC_PARTS:
                continue
            definition = device.get("definition") if isinstance(device.get("definition"), dict) else {}
            metadata = {
                "topic": topic,
                "source_ref": f"{self.topic_prefix}/{friendly_name}",
                "source": self.name,
                "manufacturer": definition.get("vendor") or device.get("manufacturer"),
                "model": definition.get("model") or device.get("model_id") or device.get("model"),
                "ieee_address": ieee or None,
                "supported": device.get("supported"),
                "interview_completed": device.get("interview_completed"),
                "definition": definition,
                "exposes": definition.get("exposes"),
                # bridge/devices describes capabilities, not live sensor values.
                # Mark these generated expose rows so state merging never mistakes
                # the placeholder value "unknown" for telemetry.
                "metadata_only": True,
            }
            expose_keys = self._expose_keys(definition.get("exposes"))
            metadata["smoke_capability"] = any(key in {*SMOKE_STATE_KEYS, "alarm"} for key in expose_keys)
            device_row = self._device_state_entity(friendly_name, metadata, timestamp)
            device_row["state"] = "unknown"
            device_row["metadata_only"] = True
            device_row["attributes"] = {**device_row.get("attributes", {}), "metadata_only": True}
            if ieee:
                device_row["device_id"] = ieee
                device_row["identifiers"] = [[self.name, ieee]]
                device_row["attributes"] = {**device_row.get("attributes", {}), "ieee_address": ieee}
            rows.append(device_row)
            for key in expose_keys:
                row = self._entity(friendly_name, key, "unknown", metadata, timestamp)
                row["metadata_only"] = True
                row["attributes"] = {**row.get("attributes", {}), "metadata_only": True}
                if ieee:
                    row["device_id"] = ieee
                    row["identifiers"] = [[self.name, ieee]]
                    row["attributes"] = {**row.get("attributes", {}), "ieee_address": ieee}
                rows.append(row)
        return rows

    def _expose_keys(self, exposes: Any) -> list[str]:
        keys: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    visit(item)
                return
            if not isinstance(value, dict):
                return
            for raw in (value.get("property"), value.get("name")):
                key = str(raw or "").strip()
                if key in STATE_KEYS or key in MEASUREMENT_KEYS:
                    keys.append(key)
                elif key == "alarm" and self._expose_is_smoke_alarm(value):
                    keys.append(key)
            visit(value.get("features"))

        visit(exposes)
        result: list[str] = []
        for key in keys:
            if key not in result:
                result.append(key)
        return result

    def _expose_is_smoke_alarm(self, value: dict[str, Any]) -> bool:
        text = " ".join(str(value.get(key) or "") for key in ("name", "property", "label", "description", "access")).lower()
        return "smoke" in text or "fire" in text or "rauch" in text

    def _payload_has_smoke_capability(self, payload: dict[str, Any]) -> bool:
        if payload.get("smoke_capability") is True:
            return True
        exposes = payload.get("exposes")
        return any(key in {*SMOKE_STATE_KEYS, "alarm"} for key in self._expose_keys(exposes))

    def _entity(self, device: str, key: str, value: Any, payload: dict[str, Any], timestamp: str) -> dict[str, Any]:
        slug = slugify(device)
        source = payload.get("source") or self.name
        ieee = str(payload.get("ieee_address") or "").strip()
        physical_device_id = ieee if source == self.name and ieee else device if source == "mqtt" else slug
        clean_key = "contact" if key == "open" else "smoke" if key in {*SMOKE_STATE_KEYS, "alarm"} else key
        is_binary = clean_key in BINARY_DEVICE_CLASSES or clean_key == "state" and str(value).lower() in {"on", "off", "true", "false"}
        domain = "button" if clean_key == "action" else "binary_sensor" if is_binary else "sensor"
        suffix = "" if is_binary else f"_{slugify(clean_key)}"
        device_class = self._device_class(clean_key, is_binary)
        friendly_key = "" if is_binary else f" {clean_key.replace('_', ' ').title()}"
        identifier_value = ieee if source == self.name and ieee else device
        return {
            "entity_id": f"{domain}.{slug}{suffix}",
            "domain": domain,
            "state": normalize_state(value),
            "friendly_name": f"{device}{friendly_key}".strip(),
            "device_class": device_class,
            "unit": "%" if clean_key == "battery" else None,
            "unit_of_measurement": "%" if clean_key == "battery" else None,
            "device_id": physical_device_id,
            "platform": source,
            "unique_id": f"{source}_{slugify(physical_device_id)}_{clean_key}",
            "topic": payload.get("topic") or payload.get("source_ref"),
            "source_ref": payload.get("source_ref") or payload.get("topic"),
            "payload_key": clean_key,
            "original_name": device,
            "device_name": device,
            "manufacturer": payload.get("manufacturer") or payload.get("vendor"),
            "model": payload.get("model") or payload.get("model_id"),
            "identifiers": [[source, identifier_value]],
            "last_changed": timestamp,
            "last_updated": timestamp,
            "source": source,
            "attributes": {key: value, **{k: v for k, v in payload.items() if k not in {key}}},
        }

    def _availability_entity(self, device: str, payload: dict[str, Any], enriched_payload: dict[str, Any], timestamp: str) -> dict[str, Any]:
        slug = slugify(device)
        source = enriched_payload.get("source") or self.name
        ieee = str(enriched_payload.get("ieee_address") or "").strip()
        device_id = device if source == "mqtt" else ieee if ieee else slug
        identifier_value = ieee if source == self.name and ieee else device
        status = str(payload.get("status") or payload.get("state") or "").strip().lower()
        online = status in {"online", "on", "true", "1", "available"}
        return {
            "entity_id": f"binary_sensor.{slug}_availability",
            "domain": "binary_sensor",
            "state": "on" if online else "unavailable",
            "friendly_name": f"{device} Verbindung",
            "device_class": "connectivity",
            "unit": None,
            "unit_of_measurement": None,
            "device_id": device_id,
            "platform": source,
            "unique_id": f"{source}_{slug}_availability",
            "topic": enriched_payload.get("topic"),
            "source_ref": enriched_payload.get("source_ref"),
            "payload_key": "availability",
            "original_name": device,
            "device_name": device,
            "manufacturer": payload.get("manufacturer") or enriched_payload.get("manufacturer"),
            "model": payload.get("model") or enriched_payload.get("model"),
            "identifiers": [[source, identifier_value]],
            "last_changed": timestamp,
            "last_updated": timestamp,
            "source": source,
            "attributes": {**payload, **{k: v for k, v in enriched_payload.items() if k not in payload}, "availability": status or None},
        }

    def _device_class(self, key: str, is_binary: bool) -> str | None:
        if key == "battery":
            return "battery"
        if key in {"linkquality", "signal_quality"}:
            return "signal_quality"
        if key in {"contact", "open"}:
            return "opening"
        if key in {*SMOKE_STATE_KEYS, "alarm"}:
            return "smoke"
        if key in {"occupancy", "motion", "presence", "bed_presence"}:
            return "motion" if key == "motion" else key
        if key in {"fall_detected", "fall_detection", "breathing_detected", "breathing_detection"}:
            return key
        if key == "action":
            return "button"
        if key in {"energy", "energy_consumption", "electricity", "electricity_consumption"}:
            return "energy"
        if key in {"power", "power_usage"}:
            return "power"
        if key in {"water", "water_consumption"}:
            return "water"
        if key in {"gas", "gas_consumption"}:
            return "gas"
        return None if is_binary else key

    def _topic_prefixes(self) -> list[str]:
        prefixes = [
            self.topic_prefix,
            os.getenv("SENTERO_ESP32_TOPIC_PREFIX") or config_str("esp32.topic_prefix", ""),
            config_str("mqtt.esp32_topic_prefix", ""),
            "sentero",
            "c1001",
        ]
        result: list[str] = []
        for value in prefixes:
            clean = str(value or "").strip().strip("/")
            if clean and clean not in result:
                result.append(clean)
        return result or ["zigbee2mqtt"]

    def _source_from_topic(self, topic: str) -> str:
        esp32_prefixes = {
            str(os.getenv("SENTERO_ESP32_TOPIC_PREFIX") or config_str("esp32.topic_prefix", "") or "").strip().strip("/"),
            str(config_str("mqtt.esp32_topic_prefix", "") or "").strip().strip("/"),
            "sentero",
            "c1001",
        }
        esp32_prefixes.discard("")
        if any(topic.startswith(f"{prefix}/") for prefix in esp32_prefixes):
            return "mqtt"
        return self.name


def normalize_state(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "sensor"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
