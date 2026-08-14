from __future__ import annotations

from typing import Any

AAL_ROLES = {"resident", "relative", "care_service", "emergency_service", "housing_provider", "admin"}
DEFAULT_CONTACT_AAL_ROLE = "relative"
DEFAULT_USER_AAL_ROLE = "admin"

ROLE_LABELS = {
    "resident": "Bewohner",
    "relative": "Angehoerige",
    "care_service": "Pflegedienst",
    "emergency_service": "Notfalldienst",
    "housing_provider": "Wohnungsanbieter",
    "admin": "Administrator",
}

ROLE_DATA_CLASS_PERMISSIONS = {
    "resident": {"technical", "environmental", "utility", "personal_behavior", "health_adjacent", "emergency"},
    "admin": {"technical", "environmental", "utility", "personal_behavior", "health_adjacent", "emergency"},
    "relative": {"personal_behavior", "health_adjacent", "emergency"},
    "care_service": {"personal_behavior", "health_adjacent", "emergency"},
    "emergency_service": {"emergency"},
    "housing_provider": {"technical", "environmental", "utility"},
}


def normalize_aal_role(value: Any, default: str = DEFAULT_CONTACT_AAL_ROLE) -> str:
    role = str(value or "").strip().lower()
    return role if role in AAL_ROLES else default


def can_access_data_classes(
    actor_role: Any,
    data_classes: list[str] | set[str],
    aggregation_level: str = "summary",
    emergency_context: bool = False,
) -> bool:
    role = normalize_aal_role(actor_role)
    requested = {str(item or "").strip() for item in data_classes if str(item or "").strip()}
    if not requested:
        return False
    allowed = set(ROLE_DATA_CLASS_PERMISSIONS.get(role, set()))
    if role == "emergency_service" and emergency_context:
        allowed.update({"health_adjacent"})
    if aggregation_level == "raw" and role not in {"resident", "admin"}:
        return False
    return requested.issubset(allowed)
