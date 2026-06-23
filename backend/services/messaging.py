from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class MessagingService:
    """Small local message sink used by Sentero without RoboterSteve's message center."""

    def __init__(self) -> None:
        self._messages: list[dict[str, Any]] = []

    def create_message(
        self,
        title: str,
        message: str,
        source: str = "sentero",
        category: str = "sentero",
        severity: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = {
            "id": len(self._messages) + 1,
            "source": source,
            "category": category,
            "severity": severity,
            "title": title,
            "message": message,
            "payload": payload or {},
            "read": False,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "read_at": None,
        }
        self._messages.append(item)
        return item

    def get_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(reversed(self._messages[-limit:]))

    def get_messages_by_source(self, source: str, limit: int = 100) -> list[dict[str, Any]]:
        return [item for item in self.get_messages(limit=limit) if item.get("source") == source]

