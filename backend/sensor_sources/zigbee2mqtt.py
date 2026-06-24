from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from backend.services.mqtt_service import MqttService


IGNORED_TOPIC_PARTS = {"bridge", "availability"}
BINARY_DEVICE_CLASSES = {"contact", "occupancy", "motion", "presence", "opening"}
STATE_KEYS = ("contact", "occupancy", "motion", "presence", "open", "state")


class Zigbee2MqttSensorSource:
    name = "zigbee2mqtt"

    def __init__(self, mqtt: MqttService | None = None) -> None:
        self.mqtt = mqtt or MqttService()
        self.host = self.mqtt.host
        self.port = self.mqtt.port
        self.topic_prefix = os.getenv("ZIGBEE2MQTT_TOPIC_PREFIX", "zigbee2mqtt").strip() or "zigbee2mqtt"
        self.snapshot_timeout = float(os.getenv("SENTERO_MQTT_SNAPSHOT_TIMEOUT", "2.5") or "2.5")

    def configured(self) -> bool:
        return self.mqtt.configured()

    def snapshot(self) -> list[dict[str, Any]]:
        seed = os.getenv("SENTERO_MQTT_BOOTSTRAP_EVENTS", "").strip()
        if seed:
            return self._snapshot_from_seed(seed)
        messages = self.mqtt.retained_messages(f"{self.topic_prefix}/#", timeout=self.snapshot_timeout)
        rows: list[dict[str, Any]] = []
        now = utc_now()
        for message in messages:
            rows.extend(self._entities_from_message(message.topic, message.payload, now))
        return rows

    def publish(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.mqtt.publish(topic, payload)

    def _snapshot_from_seed(self, seed: str) -> list[dict[str, Any]]:
        payload = json.loads(seed)
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

    def _entities_from_message(self, topic: str, payload: Any, timestamp: str) -> list[dict[str, Any]]:
        device = self._device_from_topic(topic)
        if not device or not isinstance(payload, dict):
            return []
        rows: list[dict[str, Any]] = []
        state_key = next((key for key in STATE_KEYS if key in payload), None)
        if state_key:
            rows.append(self._entity(device, state_key, payload.get(state_key), payload, timestamp))
        for key in ("battery", "battery_low", "linkquality"):
            if key in payload:
                rows.append(self._entity(device, key, payload.get(key), payload, timestamp))
        return rows

    def _device_from_topic(self, topic: str) -> str:
        prefix = f"{self.topic_prefix}/"
        if not topic.startswith(prefix):
            return ""
        suffix = topic[len(prefix):].strip("/")
        if not suffix:
            return ""
        device = suffix.split("/", 1)[0]
        return "" if device in IGNORED_TOPIC_PARTS else device

    def _entity(self, device: str, key: str, value: Any, payload: dict[str, Any], timestamp: str) -> dict[str, Any]:
        slug = slugify(device)
        clean_key = "contact" if key == "open" else key
        is_binary = clean_key in BINARY_DEVICE_CLASSES or clean_key == "state" and str(value).lower() in {"on", "off", "true", "false"}
        domain = "binary_sensor" if is_binary else "sensor"
        suffix = "" if is_binary else f"_{slugify(clean_key)}"
        device_class = self._device_class(clean_key, is_binary)
        friendly_key = "" if is_binary else f" {clean_key.replace('_', ' ').title()}"
        return {
            "entity_id": f"{domain}.{slug}{suffix}",
            "domain": domain,
            "state": normalize_state(value),
            "friendly_name": f"{device}{friendly_key}".strip(),
            "device_class": device_class,
            "unit": "%" if clean_key == "battery" else None,
            "unit_of_measurement": "%" if clean_key == "battery" else None,
            "device_id": slug,
            "platform": "zigbee2mqtt",
            "unique_id": f"zigbee2mqtt_{slug}_{clean_key}",
            "original_name": device,
            "device_name": device,
            "manufacturer": payload.get("manufacturer") or payload.get("vendor"),
            "model": payload.get("model") or payload.get("model_id"),
            "identifiers": [["zigbee2mqtt", device]],
            "last_changed": timestamp,
            "last_updated": timestamp,
            "source": self.name,
            "attributes": {key: value, **{k: v for k, v in payload.items() if k not in {key}}},
        }

    def _device_class(self, key: str, is_binary: bool) -> str | None:
        if key == "battery":
            return "battery"
        if key in {"contact", "open"}:
            return "opening"
        if key in {"occupancy", "motion", "presence"}:
            return "motion" if key == "motion" else key
        return None if is_binary else key


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
