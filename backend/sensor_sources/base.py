from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from backend.config import config_str


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
    import os

    mode = (os.getenv("SENTERO_SENSOR_SOURCE") or config_str("sensor_sources.source", "homeassistant") or "homeassistant").strip().lower()
    if mode in {"mqtt", "zigbee2mqtt", "z2m"}:
        from .zigbee2mqtt import Zigbee2MqttSensorSource

        return Zigbee2MqttSensorSource()
    if mode == "mixed":
        from .mixed import MixedSensorSource

        return MixedSensorSource()
    from .homeassistant import HomeAssistantSensorSource

    return HomeAssistantSensorSource()
