from __future__ import annotations

from .base import SensorEvent
from .homeassistant import HomeAssistantSensorSource
from .zigbee2mqtt import Zigbee2MqttSensorSource


class MixedSensorSource:
    name = "mixed"

    def __init__(self) -> None:
        self.sources = [Zigbee2MqttSensorSource(), HomeAssistantSensorSource()]

    def configured(self) -> bool:
        return any(source.configured() for source in self.sources)

    def snapshot(self) -> list[SensorEvent]:
        events: list[SensorEvent] = []
        for source in self.sources:
            if not source.configured():
                continue
            try:
                events.extend(source.snapshot())
            except Exception:
                continue
        return events

