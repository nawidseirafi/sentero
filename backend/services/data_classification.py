from __future__ import annotations

from typing import Any

DATA_CLASSES = {"technical", "environmental", "utility", "personal_behavior", "health_adjacent", "emergency"}
AGGREGATION_LEVELS = {"raw", "summary", "aggregate", "metadata"}

EVENT_DATA_CLASS = {
    "battery": "technical",
    "signal_quality": "technical",
    "availability": "technical",
    "temperature": "environmental",
    "humidity": "environmental",
    "illuminance": "environmental",
    "energy_consumption": "utility",
    "power_usage": "utility",
    "water_consumption": "utility",
    "gas_consumption": "utility",
    "contact": "personal_behavior",
    "presence": "personal_behavior",
    "motion": "personal_behavior",
    "occupancy": "personal_behavior",
    "fall_detection": "emergency",
    "button": "emergency",
    "breathing_detection": "health_adjacent",
    "respiration_rate": "health_adjacent",
}


def classify_sensor_event(event_type: Any, device_class: Any = None, role: Any = None) -> str:
    for value in (event_type, device_class, role):
        text = str(value or "").strip().lower()
        if not text:
            continue
        if text in EVENT_DATA_CLASS:
            return EVENT_DATA_CLASS[text]
        if text.endswith(("_presence", "_motion", "_door", "_contact")):
            return "personal_behavior"
        if text.endswith(("_energy", "_power", "_water", "_gas")):
            return "utility"
        if "fall" in text or "emergency" in text or "notfall" in text:
            return "emergency"
        if "battery" in text or "linkquality" in text or "signal" in text:
            return "technical"
    return "technical"


def classify_assessment(status: Any) -> str:
    return "emergency" if str(status or "").lower() == "red" else "health_adjacent"


def classify_notification(severity: Any, channel: Any = None) -> str:
    if str(channel or "") == "consent":
        return "metadata"
    return "emergency" if str(severity or "").lower() == "red" else "health_adjacent"


def aggregation_for_data_class(data_class: str, raw: bool = False) -> str:
    if raw:
        return "raw"
    if data_class in {"personal_behavior", "health_adjacent", "emergency"}:
        return "summary"
    if data_class in {"utility", "environmental"}:
        return "aggregate"
    return "metadata"


def with_classification(data: dict[str, Any], data_class: str, aggregation_level: str | None = None) -> dict[str, Any]:
    clean_class = data_class if data_class in DATA_CLASSES or data_class == "metadata" else "technical"
    return {
        **data,
        "data_class": clean_class,
        "aggregation_level": aggregation_level or aggregation_for_data_class(clean_class),
    }
