from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from backend.logging_config import get_logger
from backend.sensors.models import Capability, SenteroDevice, SenteroEvent

logger = get_logger(__name__)

CONTACT_CLASSES = {"contact", "door", "window", "opening"}
PRESENCE_CLASSES = {"occupancy", "presence"}
MOTION_CLASSES = {"motion"}
ENVIRONMENTAL_CLASSES = {"temperature", "humidity", "illuminance", "illuminance_lux"}
BUTTON_CLASSES = {"action", "button"}
C1001_KEYS = {"presence", "fall_detected", "breathing_detected", "respiration_rate", "sleep_status", "bed_presence"}


def normalize_snapshot(rows: list[dict[str, Any]]) -> tuple[list[SenteroDevice], list[SenteroEvent]]:
    logger.debug("Sensor snapshot normalization start", extra={"component": "sensor_normalizer", "row_count": len(rows)})
    devices: dict[str, dict[str, Any]] = {}
    events: list[SenteroEvent] = []
    for row in rows:
        try:
            normalized = normalize_row(row)
        except Exception:
            logger.exception("Failed to normalize sensor row", extra={"component": "sensor_normalizer", "row": row})
            continue
        if not normalized:
            logger.debug("Sensor row skipped", extra={"component": "sensor_normalizer", "row": row})
            continue
        device_id, device_data, event = normalized
        existing = devices.setdefault(device_id, device_data)
        existing["capabilities"] = sorted(set(existing.get("capabilities", [])) | set(device_data.get("capabilities", [])))
        existing["battery"] = coalesce_int(existing.get("battery"), device_data.get("battery"))
        existing["signal_quality"] = coalesce_int(existing.get("signal_quality"), device_data.get("signal_quality"))
        existing["last_seen"] = newest(existing.get("last_seen"), device_data.get("last_seen"))
        existing["status"] = best_status(existing.get("status"), device_data.get("status"))
        events.append(event)
    result = [SenteroDevice(**data) for data in devices.values()]
    logger.debug(
        "Sensor snapshot normalization completed",
        extra={"component": "sensor_normalizer", "device_count": len(result), "event_count": len(events)},
    )
    return result, events


def normalize_row(row: dict[str, Any]) -> tuple[str, dict[str, Any], SenteroEvent] | None:
    source = str(row.get("source") or row.get("platform") or "homeassistant").strip() or "homeassistant"
    source_ref = str(row.get("entity_id") or row.get("unique_id") or row.get("sensor_id") or "").strip()
    device_id_raw = str(row.get("device_id") or row.get("device_name") or row.get("friendly_name") or source_ref).strip()
    if not source_ref and not device_id_raw:
        logger.warning("Sensor row missing source reference", extra={"component": "sensor_normalizer", "sensor_source": source})
        return None
    device_id = stable_id(source, device_id_raw or source_ref)
    attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    device_class = str(row.get("device_class") or attrs.get("device_class") or "").strip().lower()
    state_key = mqtt_state_key(row, attrs)
    event_type, value, capabilities = event_from_value(device_class, state_key, row.get("state"), attrs)
    device_type = device_type_for(device_class, state_key, capabilities)
    occurred_at = str(row.get("last_updated") or row.get("last_changed") or row.get("changed_at") or utc_now())
    battery = parse_int(row.get("battery_level") if row.get("battery_level") is not None else attrs.get("battery"))
    if "battery" in capabilities and battery is None:
        battery = parse_int(row.get("state"))
    signal_quality = parse_int(row.get("signal_quality") or attrs.get("linkquality") or attrs.get("signal_quality"))
    if "signal_quality" in capabilities and signal_quality is None:
        signal_quality = parse_int(row.get("state"))
    device_data = {
        "id": device_id,
        "name": clean_name(row.get("friendly_name") or row.get("device_name") or source_ref or "Sensor"),
        "room_id": clean_room(row.get("room") or row.get("area_id")),
        "type": device_type,
        "capabilities": capabilities,
        "manufacturer": clean_optional(row.get("manufacturer") or attrs.get("manufacturer")),
        "model": clean_optional(row.get("model") or attrs.get("model")),
        "battery": battery,
        "signal_quality": signal_quality,
        "last_seen": occurred_at,
        "status": status_from(row.get("reachable"), row.get("state")),
        "source": source,
        "source_ref": source_ref or None,
    }
    event = SenteroEvent(
        id=stable_id("event", f"{device_id}:{event_type}:{occurred_at}:{value}"),
        device_id=device_id,
        room_id=device_data["room_id"],
        event_type=event_type,
        value=value,
        occurred_at=occurred_at,
        source=source,
        raw_payload=row,
    )
    logger.debug(
        "Sensor row normalized",
        extra={
            "component": "sensor_normalizer",
            "sensor_source": source,
            "device_id": device_id,
            "room_id": device_data["room_id"],
            "event_type": event_type,
            "value": value,
            "device_type": device_type,
            "capabilities": capabilities,
        },
    )
    return device_id, device_data, event


def event_from_value(device_class: str, key: str, state: Any, attrs: dict[str, Any]) -> tuple[str, Any, list[Capability]]:
    raw = state
    lowered = str(raw).strip().lower()
    if device_class in CONTACT_CLASSES or key in {"contact", "open"}:
        is_open = lowered in {"on", "open", "true", "1"}
        return "contact", "open" if is_open else "closed", ["contact"]
    if device_class in PRESENCE_CLASSES or key in {"occupancy", "presence", "bed_presence"}:
        return "presence", "active" if truthy(raw) else "inactive", ["presence"]
    if device_class in MOTION_CLASSES or key == "motion":
        return "motion", "active" if truthy(raw) else "inactive", ["motion"]
    if key in {"fall_detected", "fall_detection"}:
        return "fall_detection", "suspected" if truthy(raw) else "clear", ["fall_detection"]
    if key in {"breathing_detected", "breathing_detection"}:
        return "breathing_detection", "detected" if truthy(raw) else "not_detected", ["breathing_detection"]
    if key == "respiration_rate":
        return "respiration_rate", parse_number(raw), ["respiration_rate"]
    if device_class in {"temperature"} or key == "temperature":
        return "temperature", parse_number(raw), ["temperature"]
    if device_class in {"humidity"} or key == "humidity":
        return "humidity", parse_number(raw), ["humidity"]
    if device_class in {"illuminance", "illuminance_lux"} or key in {"illuminance", "illuminance_lux"}:
        return "illuminance", parse_number(raw), ["illuminance"]
    if device_class == "battery" or key in {"battery", "battery_low"}:
        return "battery", parse_int(raw), ["battery"]
    if key in {"linkquality", "signal_quality"}:
        return "signal_quality", parse_int(raw), ["signal_quality"]
    if key in BUTTON_CLASSES:
        return "button", str(raw), ["button"]
    if key == "sleep_status":
        return "activity_hint", str(raw), ["presence"]
    logger.debug(
        "Sensor value mapped to generic state",
        extra={"component": "sensor_normalizer", "device_class": device_class, "state_key": key},
    )
    return "state", str(raw), []


def device_type_for(device_class: str, key: str, capabilities: list[Capability]) -> str:
    if "contact" in capabilities:
        return "door_contact"
    if any(cap in capabilities for cap in ["presence", "fall_detection", "breathing_detection", "respiration_rate"]):
        return "presence_radar"
    if "motion" in capabilities:
        return "motion_sensor"
    if "button" in capabilities:
        return "button"
    if any(cap in capabilities for cap in ["temperature", "humidity", "illuminance"]):
        return "environmental_sensor"
    if key in C1001_KEYS:
        return "presence_radar"
    return "environmental_sensor" if device_class in ENVIRONMENTAL_CLASSES else "motion_sensor"


def mqtt_state_key(row: dict[str, Any], attrs: dict[str, Any]) -> str:
    device_class = str(row.get("device_class") or attrs.get("device_class") or "").lower()
    if device_class in {"battery", "temperature", "humidity", "illuminance", "illuminance_lux", "motion", "occupancy", "presence", "linkquality", "signal_quality"}:
        return device_class
    for key in [
        "contact", "open", "occupancy", "presence", "motion", "fall_detected",
        "breathing_detected", "respiration_rate", "sleep_status", "bed_presence",
        "battery", "linkquality", "action", "temperature", "humidity", "illuminance", "illuminance_lux",
    ]:
        if key in attrs:
            return key
    unique = str(row.get("unique_id") or row.get("entity_id") or "").lower()
    for key in ["battery", "linkquality", "temperature", "humidity", "illuminance"]:
        if key in unique:
            return key
    return str(row.get("device_class") or attrs.get("device_class") or "state").lower()


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:32] or "device"
    return f"{prefix}_{slug}_{digest}"


def clean_name(value: Any) -> str:
    return str(value or "Sensor").replace("_", " ").strip() or "Sensor"


def clean_room(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def clean_optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def parse_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def parse_number(value: Any) -> int | float | str | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return str(value) if value is not None else None
    return int(number) if number.is_integer() else number


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "on", "open", "active", "detected", "yes", "home"}


def status_from(reachable: Any, state: Any) -> str:
    if reachable is False or str(state).lower() == "unavailable":
        return "offline"
    if reachable is True or str(state).lower() not in {"", "unknown", "none"}:
        return "online"
    return "unknown"


def coalesce_int(current: Any, incoming: Any) -> int | None:
    return parse_int(current) if current is not None else parse_int(incoming)


def newest(current: Any, incoming: Any) -> str | None:
    if not current:
        return str(incoming) if incoming else None
    if not incoming:
        return str(current)
    return max(str(current), str(incoming))


def best_status(current: Any, incoming: Any) -> str:
    order = {"offline": 0, "unknown": 1, "online": 2}
    return str(current) if order.get(str(current), 1) >= order.get(str(incoming), 1) else str(incoming)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
