from __future__ import annotations

from typing import Any


def illuminance_description(value: Any) -> str | None:
    """Return a customer-friendly German description for a lux value.

    The thresholds intentionally match the Sentero frontend so all channels
    communicate the same meaning.
    """
    lux = _lux_number(value)
    if lux is None:
        return None
    if lux <= 10:
        return "Dunkel"
    if lux <= 50:
        return "Sehr gedämpft"
    if lux <= 150:
        return "Gedämpft"
    if lux <= 300:
        return "Normal hell"
    if lux <= 700:
        return "Hell"
    return "Sehr hell"


def illuminance_display(value: Any) -> str | None:
    """Return e.g. 'Sehr hell (2910 lx)' for user-facing channels."""
    lux = _lux_number(value)
    if lux is None:
        return None
    label = illuminance_description(lux)
    rounded = max(0, round(lux))
    return f"{label} ({rounded} lx)" if label else f"{rounded} lx"


def _lux_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return max(0.0, number)
