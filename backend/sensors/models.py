from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DeviceType = Literal[
    "door_contact",
    "presence_radar",
    "motion_sensor",
    "button",
    "environmental_sensor",
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
        return data
