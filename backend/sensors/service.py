from __future__ import annotations

import os
import time
from typing import Any

from backend.logging_config import get_logger
from backend.sensors.normalizer import normalize_snapshot
from backend.services.device_mapping_service import ROOM_LABELS, DeviceMappingService, sensor_source_mode

logger = get_logger(__name__)


class SenteroSensorService:
    def __init__(self, mapping: DeviceMappingService | None = None) -> None:
        self.mapping = mapping or DeviceMappingService()

    def source_status(self) -> dict[str, Any]:
        mode = sensor_source_mode()
        detail = self.mapping.home_status()
        logger.debug(
            "Sensor source status calculated",
            extra={"component": "sensor_service", "sensor_source": mode, "connected": bool(detail.get("connected"))},
        )
        return {
            "source": mode,
            "configured": bool(detail.get("connected")),
            "connected": bool(detail.get("connected")),
            "sensor_ready": bool(detail.get("sensor_ready")),
            "mode": mode,
            "mqtt_prepared": True,
            "homeassistant_available": mode in {"homeassistant", "mixed"},
        }

    def devices(self, include_internal: bool = False) -> dict[str, Any]:
        logger.debug("Devices requested", extra={"component": "sensor_service", "include_internal": include_internal})
        devices, _events = self._snapshot_models()
        logger.debug("Devices built", extra={"component": "sensor_service", "device_count": len(devices)})
        return {"devices": [as_device(device, include_internal=include_internal) for device in devices]}

    def events(self, limit: int = 100, include_internal: bool = False) -> dict[str, Any]:
        logger.debug("Events requested", extra={"component": "sensor_service", "limit": limit, "include_internal": include_internal})
        _devices, events = self._snapshot_models()
        events = sorted(events, key=lambda item: item.occurred_at, reverse=True)[: max(1, min(limit, 500))]
        logger.debug("Events built", extra={"component": "sensor_service", "event_count": len(events)})
        return {"events": [event.public_dict() if not include_internal else event.__dict__ for event in events]}

    def rooms(self) -> dict[str, Any]:
        devices, events = self._snapshot_models()
        room_ids = sorted({device.room_id for device in devices if device.room_id} | set(ROOM_LABELS.keys()))
        rooms = []
        for room_id in room_ids:
            room_devices = [device for device in devices if device.room_id == room_id]
            rooms.append({
                "id": room_id,
                "name": ROOM_LABELS.get(room_id, str(room_id).replace("_", " ").title()),
                "device_count": len(room_devices),
                "active": any(event.room_id == room_id and event.value in {"active", "open", "suspected", "detected"} for event in events),
            })
        logger.debug("Rooms built", extra={"component": "sensor_service", "room_count": len(rooms), "device_count": len(devices), "event_count": len(events)})
        return {"rooms": rooms}

    def dashboard(self) -> dict[str, Any]:
        started = time.perf_counter()
        logger.debug("Building dashboard", extra={"component": "dashboard", "sensor_source": sensor_source_mode()})
        devices, events = self._snapshot_models()
        public_rooms = self.rooms()["rooms"]
        open_doors = sum(1 for event in events if event.event_type == "contact" and event.value == "open")
        fall_alerts = sum(1 for event in events if event.event_type == "fall_detection" and event.value == "suspected")
        low_batteries = sum(1 for device in devices if isinstance(device.battery, int) and device.battery < 30)
        active_rooms = len({event.room_id for event in events if event.room_id and event.value in {"active", "open", "suspected", "detected"}})
        last_activity = max((event.occurred_at for event in events), default=None)
        dashboard = {
            "summary": {
                "status": "learning",
                "sensor_source": sensor_source_mode(),
                "active_rooms": active_rooms,
                "last_activity": last_activity,
                "open_doors": open_doors,
                "fall_alerts": fall_alerts,
                "low_batteries": low_batteries,
            },
            "rooms": public_rooms,
            "alerts": self._alerts(devices, events),
        }
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.debug(
            "Dashboard built",
            extra={
                "component": "dashboard",
                "sensor_source": dashboard["summary"]["sensor_source"],
                "device_count": len(devices),
                "event_count": len(events),
                "room_count": len(public_rooms),
                "active_rooms": active_rooms,
                "open_doors": open_doors,
                "fall_alerts": fall_alerts,
                "low_batteries": low_batteries,
                "elapsed_ms": elapsed_ms,
            },
        )
        logger.info("Dashboard generated", extra={"component": "dashboard", "elapsed_ms": elapsed_ms})
        return dashboard

    def assign_room(self, device_id: str, room_id: str) -> dict[str, Any]:
        # Prepared API. Persistent assignment will move into the event/device store once MQTT ingestion is persistent.
        logger.info("Device room assignment prepared", extra={"component": "device_registry", "device_id": device_id, "room_id": room_id})
        return {"status": "prepared", "device_id": device_id, "room_id": room_id, "message": "Raumzuordnung ist fuer das neue Device-Modell vorbereitet."}

    def rename(self, device_id: str, name: str) -> dict[str, Any]:
        clean = str(name or "").strip()
        if not clean:
            raise ValueError("name required")
        logger.info("Device rename prepared", extra={"component": "device_registry", "device_id": device_id})
        return {"status": "prepared", "device_id": device_id, "name": clean, "message": "Umbenennung ist fuer das neue Device-Modell vorbereitet."}

    def _snapshot_models(self):  # type: ignore[no-untyped-def]
        try:
            rows = self.mapping.snapshot()
            logger.debug("Sensor snapshot rows loaded", extra={"component": "sensor_service", "row_count": len(rows), "sensor_source": sensor_source_mode()})
        except Exception:
            logger.exception("Sensor snapshot failed", extra={"component": "sensor_service", "sensor_source": sensor_source_mode()})
            rows = []
        return normalize_snapshot(rows)

    def _alerts(self, devices, events) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
        alerts: list[dict[str, Any]] = []
        for device in devices:
            if isinstance(device.battery, int) and device.battery < 30:
                alerts.append({"type": "low_battery", "severity": "warning", "title": "Batterie schwach", "device_id": device.id})
            if device.status == "offline":
                alerts.append({"type": "sensor_offline", "severity": "warning", "title": "Sensor nicht erreichbar", "device_id": device.id})
        for event in events:
            if event.event_type == "fall_detection" and event.value == "suspected":
                alerts.append({"type": "fall_suspected", "severity": "critical", "title": "Sturzverdacht", "device_id": event.device_id})
        return alerts


def as_device(device, include_internal: bool = False) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return device.__dict__ if include_internal else device.public_dict()
