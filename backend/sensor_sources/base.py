from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from backend.services.mqtt_service import MqttService

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


def create_sensor_source(mqtt: "MqttService | None" = None) -> SensorSource:
    from .zigbee2mqtt import Zigbee2MqttSensorSource

    return Zigbee2MqttSensorSource(mqtt=mqtt)
