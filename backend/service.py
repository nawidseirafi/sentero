from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .behavior_agent import SenteroBehaviorAgent
from .device_mapping_service import DeviceMappingService


class SenteroService:
    def __init__(self, mapping: DeviceMappingService | None = None) -> None:
        self.enabled = True
        self.mapping = mapping or DeviceMappingService()
        self.behavior = SenteroBehaviorAgent(self.mapping)

    def status(self) -> dict[str, Any]:
        latest_assessment = self.behavior.latest()
        sensor_roles = self.mapping.roles()
        configured = bool(sensor_roles)
        return {
            "status": "ready" if self.enabled and configured else "waiting_for_sensors" if self.enabled else "disabled",
            "enabled": self.enabled,
            "message": "Sentero ist bereit." if configured else "Sentero wartet auf eingerichtete Sensoren.",
            "sensor_roles": sensor_roles,
            "behavior_assessment": latest_assessment,
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

    def behavior_timeline_today(self) -> dict[str, Any]:
        return self.behavior.timeline_today()
