from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SensorEvent:
    source: str
    sensor_id: str
    role: str | None
    room: str | None
    state: str
    changed_at: str | None = None
    metadata: dict[str, object] | None = None


class SensorSource(Protocol):
    name: str

    def configured(self) -> bool:
        ...

    def snapshot(self) -> list[SensorEvent]:
        ...


def create_sensor_source() -> SensorSource:
    mode = os.getenv("SENTERO_SENSOR_SOURCE", "homeassistant").strip().lower()
    if mode == "mqtt":
        from .zigbee2mqtt import Zigbee2MqttSensorSource

        return Zigbee2MqttSensorSource()
    if mode == "mixed":
        from .mixed import MixedSensorSource

        return MixedSensorSource()
    from .homeassistant import HomeAssistantSensorSource

    return HomeAssistantSensorSource()

