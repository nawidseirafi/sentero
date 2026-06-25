from __future__ import annotations

from backend.logging_config import get_logger

from .base import SensorEvent
from ..services.homeassistant_service import HomeAssistantService

logger = get_logger(__name__)


class HomeAssistantSensorSource:
    name = "homeassistant"

    def __init__(self, service: HomeAssistantService | None = None) -> None:
        self.service = service or HomeAssistantService()

    def configured(self) -> bool:
        return self.service.configured()

    def snapshot(self) -> list[SensorEvent]:
        events: list[SensorEvent] = []
        states = self.service.get_states()
        logger.debug("Home Assistant sensor snapshot start", extra={"component": "sensor_source", "sensor_source": self.name, "state_count": len(states)})
        for item in states:
            entity_id = str(item.get("entity_id") or "")
            attrs = item.get("attributes") or {}
            logger.debug(
                "Home Assistant entity mapped",
                extra={
                    "component": "sensor_source",
                    "sensor_source": self.name,
                    "source_ref": entity_id,
                    "device_class": attrs.get("device_class"),
                    "room_id": attrs.get("area_id"),
                },
            )
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
        logger.debug("Home Assistant sensor snapshot completed", extra={"component": "sensor_source", "sensor_source": self.name, "event_count": len(events)})
        return events
