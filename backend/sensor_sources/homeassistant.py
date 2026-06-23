from __future__ import annotations

from .base import SensorEvent
from ..services.homeassistant_service import HomeAssistantService


class HomeAssistantSensorSource:
    name = "homeassistant"

    def __init__(self, service: HomeAssistantService | None = None) -> None:
        self.service = service or HomeAssistantService()

    def configured(self) -> bool:
        return self.service.configured()

    def snapshot(self) -> list[SensorEvent]:
        events: list[SensorEvent] = []
        for item in self.service.get_states():
            entity_id = str(item.get("entity_id") or "")
            attrs = item.get("attributes") or {}
            events.append(
                SensorEvent(
                    source=self.name,
                    sensor_id=entity_id,
                    role=None,
                    room=attrs.get("area_id"),
                    state=str(item.get("state") or ""),
                    changed_at=item.get("last_changed"),
                    metadata={"device_class": attrs.get("device_class"), "friendly_name": attrs.get("friendly_name")},
                )
            )
        return events

