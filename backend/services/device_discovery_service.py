from __future__ import annotations

from typing import Any

from .device_mapping_service import CONTACT_CLASSES, PRESENCE_CLASSES, room_matches


def compact_state(item: dict[str, Any]) -> dict[str, Any]:
    entity_id = str(item.get("entity_id") or "")
    attrs = item.get("attributes") or {}
    return {
        "entity_id": entity_id,
        "domain": entity_id.split(".")[0] if "." in entity_id else "",
        "state": item.get("state"),
        "friendly_name": attrs.get("friendly_name"),
        "device_class": attrs.get("device_class"),
        "device_id": attrs.get("device_id"),
        "last_changed": item.get("last_changed"),
        "last_updated": item.get("last_updated"),
    }


class DeviceDiscoveryService:
    def __init__(self, ha: Any) -> None:
        self.ha = ha

    def snapshot(self) -> list[dict[str, Any]]:
        return [compact_state(item) for item in self.ha.get_states()]

    def detect_new_entities(self, baseline: list[dict[str, Any]]) -> dict[str, Any]:
        before = {item.get("entity_id") for item in baseline}
        current = self.snapshot()
        registry_by_entity = {item.get("entity_id"): item for item in self.entity_registry()}
        devices_by_id = {item.get("id"): item for item in self.device_registry()}
        entities = [item for item in current if item.get("entity_id") not in before]
        enriched = []
        for entity in entities:
            registry_item = registry_by_entity.get(entity.get("entity_id"), {})
            enriched.append({**entity, "device_id": entity.get("device_id") or registry_item.get("device_id")})
        device_ids = sorted({str(item.get("device_id")) for item in enriched if item.get("device_id")})
        return {
            "current": current,
            "entities": enriched,
            "device_ids": device_ids,
            "devices": [devices_by_id[item] for item in device_ids if item in devices_by_id],
            "suggestions": suggest_roles(enriched),
        }

    def entity_registry(self) -> list[dict[str, Any]]:
        try:
            response = self.ha.websocket_command({"type": "config/entity_registry/list"}, timeout=20)
        except Exception:
            return []
        result = response.get("result")
        return result if isinstance(result, list) else []

    def device_registry(self) -> list[dict[str, Any]]:
        try:
            response = self.ha.websocket_command({"type": "config/device_registry/list"}, timeout=20)
        except Exception:
            return []
        result = response.get("result")
        return result if isinstance(result, list) else []


def suggest_roles(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for entity in entities:
        entity_id = str(entity.get("entity_id") or "")
        domain = str(entity.get("domain") or "")
        device_class = str(entity.get("device_class") or "").lower()
        if domain != "binary_sensor":
            continue
        if device_class in PRESENCE_CLASSES:
            suggestions.append({
                "role": "presence",
                "kind": "presence",
                "label": "Raumsensor",
                "entity_id": entity_id,
                "score": 80,
            })
        if device_class in CONTACT_CLASSES:
            suggestions.append({
                "role": "main_door" if room_matches("entrance", entity_id, entity.get("friendly_name")) else "contact",
                "kind": "contact",
                "label": "Tuer- oder Fenstersensor",
                "entity_id": entity_id,
                "score": 80,
            })
    return sorted(suggestions, key=lambda item: item["score"], reverse=True)
