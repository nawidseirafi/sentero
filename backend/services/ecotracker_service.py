from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

ECOTRACKER_SOURCE = "ecotracker"
ECOTRACKER_MODEL = "EcoTracker IR"
ECOTRACKER_MANUFACTURER = "everHome"


class EcoTrackerClient:
    def __init__(self, host: str, timeout: float = 5.0) -> None:
        self.host = normalize_ecotracker_host(host)
        self.timeout = timeout

    @property
    def url(self) -> str:
        return f"http://{self.host}/v1/json"

    def read(self) -> dict[str, Any]:
        response = requests.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("ecotracker_invalid_response")
        if "power" not in payload and "energyCounterIn" not in payload:
            raise ValueError("ecotracker_missing_meter_values")
        return payload


def normalize_ecotracker_host(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("ecotracker_host_required")
    parsed = urlparse(text if "://" in text else f"http://{text}")
    host = parsed.netloc or parsed.path
    host = host.strip().strip("/")
    if not host:
        raise ValueError("ecotracker_host_required")
    if "/" in host or "?" in host or "#" in host:
        raise ValueError("ecotracker_host_invalid")
    return host


def ecotracker_snapshot_rows(host: str, payload: dict[str, Any], timestamp: str | None = None) -> list[dict[str, Any]]:
    occurred_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for key, device_class, unit, label, state in [
        ("power", "power", "W", "EcoTracker Leistung", payload.get("power")),
        ("powerAvg", "power", "W", "EcoTracker Leistung Durchschnitt", payload.get("powerAvg")),
        ("powerPhase1", "power", "W", "EcoTracker Leistung Phase 1", payload.get("powerPhase1")),
        ("powerPhase2", "power", "W", "EcoTracker Leistung Phase 2", payload.get("powerPhase2")),
        ("powerPhase3", "power", "W", "EcoTracker Leistung Phase 3", payload.get("powerPhase3")),
        ("energyCounterIn", "energy", "Wh", "EcoTracker Netzbezug", payload.get("energyCounterIn")),
        ("energyCounterInT1", "energy", "Wh", "EcoTracker Netzbezug T1", payload.get("energyCounterInT1")),
        ("energyCounterInT2", "energy", "Wh", "EcoTracker Netzbezug T2", payload.get("energyCounterInT2")),
        ("energyCounterOut", "energy", "Wh", "EcoTracker Einspeisung", payload.get("energyCounterOut")),
    ]:
        if state is None:
            continue
        rows.append(
            {
                "entity_id": f"ecotracker.{key}",
                "domain": "sensor",
                "state": state,
                "friendly_name": label,
                "device_class": device_class,
                "payload_key": key,
                "unit": unit,
                "unit_of_measurement": unit,
                "device_id": f"ecotracker:{host}",
                "device_name": "everHome EcoTracker IR",
                "manufacturer": ECOTRACKER_MANUFACTURER,
                "model": ECOTRACKER_MODEL,
                "source": ECOTRACKER_SOURCE,
                "source_ref": f"http://{host}/v1/json#{key}",
                "last_changed": occurred_at,
                "last_updated": occurred_at,
                "reachable": True,
                "attributes": {"payload_key": key, "unit_of_measurement": unit},
            }
        )
    return rows
