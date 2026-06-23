from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .base import SensorEvent


class Zigbee2MqttSensorSource:
    name = "zigbee2mqtt"

    def __init__(self) -> None:
        self.host = os.getenv("MQTT_HOST", "mosquitto")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.topic_prefix = os.getenv("ZIGBEE2MQTT_TOPIC_PREFIX", "zigbee2mqtt")

    def configured(self) -> bool:
        return bool(self.host)

    def snapshot(self) -> list[SensorEvent]:
        # Production integration point: subscribe to Zigbee2MQTT topics and persist events.
        seed = os.getenv("SENTERO_MQTT_BOOTSTRAP_EVENTS", "").strip()
        if not seed:
            return []
        payload = json.loads(seed)
        rows = payload if isinstance(payload, list) else [payload]
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return [
            SensorEvent(
                source=self.name,
                sensor_id=str(row.get("sensor_id") or row.get("topic") or ""),
                role=row.get("role"),
                room=row.get("room"),
                state=str(row.get("state") or ""),
                changed_at=str(row.get("changed_at") or now),
                metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
            )
            for row in rows
            if isinstance(row, dict)
        ]

