from __future__ import annotations

from typing import Any

from backend.services.device_mapping_service import DeviceMappingService
from backend.services.network.service import NetworkService


class BoxNetworkService:
    """Compatibility facade for the original box-network setup endpoints."""

    def __init__(self, mapping: DeviceMappingService, network: NetworkService | None = None, adapter: Any | None = None) -> None:
        self.mapping = mapping
        self.network = network or NetworkService(mapping)
        self._adapter = adapter

    def status(self) -> dict[str, Any]:
        return self.network.legacy_status()

    def save_wifi(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.network.connect_wifi(payload)
        return {
            "ok": result.get("ok"),
            "applied": result.get("applied"),
            "mode": self.network.mode(),
            "message": result.get("message"),
            "status": self.status(),
        }

    def settings(self, public: bool = True) -> dict[str, Any]:
        return self.network.settings(public=public)

    def mode(self) -> str:
        return self.network.mode()

    def hostname(self) -> str:
        return self.network.hostname()
