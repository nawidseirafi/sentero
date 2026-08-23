from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.behavior_agent import SenteroBehaviorAgent
from backend.services.device_mapping_service import DeviceMappingService, ROOM_LABELS


class SenteroService:
    def __init__(self, mapping: DeviceMappingService | None = None) -> None:
        self.enabled = True
        self.mapping = mapping or DeviceMappingService()
        self.behavior = SenteroBehaviorAgent(self.mapping)

    def status(self) -> dict[str, Any]:
        latest_assessment = self.behavior.latest()
        sensor_roles = self.mapping.roles()
        live_roles = self.mapping.roles(dev=True, include_state=True)
        configured = bool(sensor_roles)
        present_roles = [role for role in live_roles if role.get("active", True) and role.get("enabled", True) and role.get("presence") is True]
        present_roles.sort(key=lambda role: str(role.get("last_updated") or role.get("last_changed") or role.get("updated_at") or ""), reverse=True)
        current_presence = present_roles[0] if present_roles else None
        current_room = current_presence.get("room") if current_presence else None
        current_location = f"Im {ROOM_LABELS.get(str(current_room), str(current_room).replace('_', ' ').title())}" if current_room else "Nicht im Haus"
        return {
            "status": "ready" if self.enabled and configured else "waiting_for_sensors" if self.enabled else "disabled",
            "enabled": self.enabled,
            "message": "Sentero ist bereit." if configured else "Sentero wartet auf eingerichtete Sensoren.",
            "sensor_roles": sensor_roles,
            "behavior_assessment": latest_assessment,
            "current_presence": {
                "present": bool(current_presence),
                "room": current_room,
                "entity_id": current_presence.get("entity_id") if current_presence else None,
                "motion_state": current_presence.get("motion_state") if current_presence else None,
                "last_updated": (current_presence.get("last_updated") or current_presence.get("last_changed") or current_presence.get("updated_at")) if current_presence else None,
            },
            "current_location": current_location,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def enable(self) -> dict[str, Any]:
        self.enabled = True
        return self.status()

    def disable(self) -> dict[str, Any]:
        self.enabled = False
        return self.status()

    def toggle(self) -> dict[str, Any]:
        self.enabled = not self.enabled
        return self.status()

    def run(self, dry_run: bool = True, action: str | None = None) -> dict[str, Any]:
        result = self.behavior.run(dry_run=dry_run)
        return {
            **self.status(),
            "action": action or "behavior_assessment",
            "dry_run": dry_run,
            "result": result,
        }

    def latest_behavior(self) -> dict[str, Any] | None:
        return self.behavior.latest()

    def behavior_learning_status(self) -> dict[str, Any]:
        return self.behavior.learning_status()

    def behavior_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.behavior.history(limit=limit)

    def behavior_timeline_today(self, live_snapshot: bool = False) -> dict[str, Any]:
        return self.behavior.timeline_today(live_snapshot=live_snapshot)

    def record_behavior_snapshot(self) -> int:
        if not self.mapping.roles(dev=True, include_state=False):
            return 0
        return self.behavior.record_current_snapshot()
