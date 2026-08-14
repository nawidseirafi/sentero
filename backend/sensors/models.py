from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.services.data_classification import aggregation_for_data_class, classify_sensor_event

DeviceType = Literal[
    "door_contact",
    "presence_radar",
    "motion_sensor",
    "button",
    "environmental_sensor",
    "smart_meter",
]
DeviceStatus = Literal["online", "offline", "unknown"]
Capability = Literal[
    "contact",
    "presence",
    "motion",
    "fall_detection",
    "breathing_detection",
    "respiration_rate",
    "temperature",
    "humidity",
    "illuminance",
    "battery",
    "signal_quality",
    "button",
    "energy_consumption",
    "power_usage",
    "water_consumption",
    "gas_consumption",
]


@dataclass(frozen=True)
class SenteroDevice:
    id: str
    name: str
    room_id: str | None
    type: DeviceType
    capabilities: list[Capability] = field(default_factory=list)
    manufacturer: str | None = None
    model: str | None = None
    battery: int | None = None
    signal_quality: int | None = None
    last_seen: str | None = None
    status: DeviceStatus = "unknown"
    source: str = "homeassistant"
    source_ref: str | None = None

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("source_ref", None)
        data["data_class"] = device_data_class(self.type, self.capabilities)
        data["aggregation_level"] = aggregation_for_data_class(data["data_class"])
        return data


@dataclass(frozen=True)
class SenteroEvent:
    id: str
    device_id: str
    room_id: str | None
    event_type: str
    value: Any
    occurred_at: str
    source: str
    raw_payload: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw_payload", None)
        data_class = classify_sensor_event(self.event_type)
        data["data_class"] = data_class
        data["aggregation_level"] = aggregation_for_data_class(data_class, raw=True)
        return data


def device_data_class(device_type: str, capabilities: list[str]) -> str:
    if device_type == "smart_meter":
        return "utility"
    if device_type == "environmental_sensor":
        return "environmental"
    classes = {classify_sensor_event(capability) for capability in capabilities}
    if "emergency" in classes:
        return "emergency"
    if "health_adjacent" in classes:
        return "health_adjacent"
    if "personal_behavior" in classes:
        return "personal_behavior"
    if "utility" in classes:
        return "utility"
    if "environmental" in classes:
        return "environmental"
    return "technical"
