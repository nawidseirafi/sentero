from __future__ import annotations

from typing import Any


class NoNetworkSensorSource:
    """Deterministic sensor source for unit tests.

    It deliberately performs no MQTT/network I/O.
    """
    name = "mqtt"

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    def configured(self) -> bool:
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def discover(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.rows)
