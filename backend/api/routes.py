from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from backend.services.auth_service import SenteroAuthService
from backend.services.commissioning_service import CommissioningService
from backend.services.device_mapping_service import DeviceMappingService
from backend.services.matter_service import MatterCommissioningUnavailable
from backend.services.notification_service import NotificationService
from backend.services.service import SenteroService
from backend.services.setup_service import SenteroSetupService
from backend.services.update_service import SenteroUpdateService

API_PREFIX = "/api/sentero"

TAG_AUTH = "auth"
TAG_SYSTEM = "system"
TAG_MATTER = "matter"
TAG_SETUP = "setup"
TAG_NOTIFICATIONS = "notifications"
TAG_SENSORS = "sensors"
TAG_BEHAVIOR = "behavior"
TAG_SENTERO = "sentero"

OPENAPI_TAGS = [
    {"name": TAG_AUTH, "description": "Authentication, setup account and current user session."},
    {"name": TAG_SYSTEM, "description": "System version and update lifecycle."},
    {"name": TAG_SENTERO, "description": "Core Sentero runtime status and agent execution."},
    {"name": TAG_BEHAVIOR, "description": "Behavior assessments, learning state and daily timeline."},
    {"name": TAG_SETUP, "description": "Household setup, rooms, contacts and pairing workflow."},
    {"name": TAG_MATTER, "description": "Matter commissioning and device assignment."},
    {"name": TAG_SENSORS, "description": "Sensor roles and role checks."},
    {"name": TAG_NOTIFICATIONS, "description": "Notification channels, tests and logs."},
]

router = APIRouter(prefix=API_PREFIX)
device_mapping_service = DeviceMappingService()
setup_service = SenteroSetupService(device_mapping_service)
sentero_service = SenteroService(device_mapping_service)
commissioning_service = CommissioningService(mapping=device_mapping_service)
notification_service = NotificationService(device_mapping_service)
auth_service = SenteroAuthService(device_mapping_service)
update_service = SenteroUpdateService()


class ProfilePayload(BaseModel):
    name: str | None = None
    birth_year: int | None = None
    age: int | None = None
    notes: str | None = None


class RoomsPayload(BaseModel):
    rooms: list[str]


class DiscoveryStartPayload(BaseModel):
    role: str
    room: str | None = None
    pairing_code: str | None = None


class ZigbeePairingStartPayload(BaseModel):
    role: str
    room: str | None = None
    duration: int | None = None


class ConfirmPayload(BaseModel):
    entity_id: str
    name: str | None = None
    room: str | None = None


class MatterStartPayload(BaseModel):
    setup_code: str | None = None
    qr_payload: str | None = None


class MatterAssignPayload(BaseModel):
    room: str
    role: str


class ContactPayload(BaseModel):
    name: str
    relationship: str | None = None
    email: str | None = None
    phone: str | None = None
    telegram_chat_id: str | None = None
    whatsapp_phone_number: str | None = None
    preferred_channels: list[str] | None = None
    notification_enabled: bool = True
    primary_contact: bool = False


class NotificationPayload(BaseModel):
    anomalies: bool = True
    critical: bool = True
    daily_summary: bool = False


class SensorRoleNamePayload(BaseModel):
    name: str


class ChannelSettingsPayload(BaseModel):
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class SenteroSetupPayload(BaseModel):
    name: str
    email: str
    password: str
    password_confirm: str


class SenteroLoginPayload(BaseModel):
    email: str
    password: str


class ForgotPasswordPayload(BaseModel):
    email: str


class ResetPasswordPayload(BaseModel):
    token: str
    password: str
    password_confirm: str


class UpdateMePayload(BaseModel):
    display_name: str | None = None
    name: str | None = None
    email: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str
    new_password_confirm: str


class UpdateCheckRequest(BaseModel):
    channel: str | None = None


class UpdateInstallRequest(BaseModel):
    layer: str | None = None


def model_data(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def api_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def is_dev_mode(dev: bool = False) -> bool:
    return dev or os.getenv("SENTERO_DEV_MODE", "").lower() in {"1", "true", "yes", "on"}


@router.get("/auth/status", tags=[TAG_AUTH])
def sentero_auth_status(request: Request):
    return auth_service.status(request)


@router.post("/auth/setup", tags=[TAG_AUTH])
def sentero_auth_setup(payload: SenteroSetupPayload, request: Request, response: Response):
    return auth_service.setup(model_data(payload), response, request)


@router.post("/auth/login", tags=[TAG_AUTH])
def sentero_auth_login(payload: SenteroLoginPayload, request: Request, response: Response):
    return auth_service.login(model_data(payload), response, request)


@router.post("/auth/logout", tags=[TAG_AUTH])
def sentero_auth_logout(request: Request, response: Response):
    return auth_service.logout(request, response)


@router.get("/auth/me", tags=[TAG_AUTH])
def sentero_auth_me(request: Request):
    return auth_service.me(request)


@router.put("/auth/me", tags=[TAG_AUTH])
def sentero_auth_update_me(payload: UpdateMePayload, request: Request):
    return auth_service.update_me(model_data(payload), request)


@router.post("/auth/change-password", tags=[TAG_AUTH])
def sentero_auth_change_password(payload: ChangePasswordPayload, request: Request):
    return auth_service.change_password(model_data(payload), request)


@router.post("/auth/forgot-password", tags=[TAG_AUTH])
def sentero_auth_forgot_password(payload: ForgotPasswordPayload, request: Request):
    return auth_service.forgot_password(model_data(payload), request)


@router.post("/auth/reset-password", tags=[TAG_AUTH])
def sentero_auth_reset_password(payload: ResetPasswordPayload):
    return auth_service.reset_password(model_data(payload))


@router.get("/system/update/status", tags=[TAG_SYSTEM])
def sentero_update_status():
    return update_service.status()


@router.get("/system/update/check", tags=[TAG_SYSTEM])
def sentero_update_check(channel: str | None = None):
    return update_service.check_for_updates(channel=channel)


@router.post("/system/update/check", tags=[TAG_SYSTEM])
def sentero_update_check_post(payload: UpdateCheckRequest):
    return update_service.check_for_updates(channel=payload.channel)


@router.post("/system/update/install", tags=[TAG_SYSTEM])
def sentero_update_install(payload: UpdateInstallRequest, request: Request):
    user = auth_service.user_from_request(request, required=True)
    if str(user.get("role") or "") not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Nur Inhaber und Administratoren duerfen Updates installieren.")
    return update_service.install_update(username=str(user.get("email") or "sentero"), layer=payload.layer or "auto")


@router.get("/status", tags=[TAG_SENTERO])
def sentero_status():
    return sentero_service.status()


@router.post("/run", tags=[TAG_SENTERO])
def run_sentero_agent():
    return sentero_service.run(dry_run=False)


@router.get("/behavior/latest", tags=[TAG_BEHAVIOR])
def sentero_behavior_latest():
    return {"assessment": sentero_service.latest_behavior(), "learning": sentero_service.behavior_learning_status()}


@router.get("/behavior/history", tags=[TAG_BEHAVIOR])
def sentero_behavior_history(limit: int = Query(20, ge=1, le=100)):
    return {"assessments": sentero_service.behavior_history(limit=limit)}


@router.get("/behavior/timeline", tags=[TAG_BEHAVIOR])
def sentero_behavior_timeline():
    return sentero_service.behavior_timeline_today()


@router.get("/setup/status", tags=[TAG_SETUP])
def setup_status():
    return setup_service.status()


@router.post("/setup/start", tags=[TAG_SETUP])
def setup_start():
    return setup_service.set_step("profile", "welcome", complete=False)


@router.post("/setup/profile", tags=[TAG_SETUP])
def setup_profile(payload: ProfilePayload):
    return setup_service.profile(model_data(payload))


@router.get("/setup/rooms", tags=[TAG_SETUP])
def setup_rooms():
    return {"rooms": ["living_room", "kitchen", "bathroom", "bedroom", "hallway", "entrance"]}


@router.post("/setup/rooms", tags=[TAG_SETUP])
def setup_rooms_save(payload: RoomsPayload):
    return setup_service.rooms(payload.rooms)


@router.post("/setup/discovery/start", tags=[TAG_SETUP])
def discovery_start(payload: DiscoveryStartPayload):
    try:
        return device_mapping_service.start_pairing(payload.role, payload.room, payload.pairing_code)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/pairing/matter/start", tags=[TAG_MATTER])
def matter_pairing_start(payload: DiscoveryStartPayload):
    try:
        return device_mapping_service.start_pairing(payload.role, payload.room, payload.pairing_code)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/pairing/zigbee/start", tags=[TAG_SETUP])
def zigbee_pairing_start(payload: ZigbeePairingStartPayload):
    try:
        return device_mapping_service.start_zigbee_pairing(payload.role, payload.room, duration=payload.duration or 60)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/matter/start", tags=[TAG_MATTER])
def matter_start(payload: MatterStartPayload):
    try:
        return commissioning_service.start(setup_code=payload.setup_code, qr_payload=payload.qr_payload)
    except MatterCommissioningUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/matter/capabilities", tags=[TAG_MATTER])
def matter_capabilities(dev: bool = Query(False)):
    try:
        return commissioning_service.capabilities(dev=is_dev_mode(dev))
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/matter/status/{commissioning_id}", tags=[TAG_MATTER])
def matter_status(commissioning_id: str, dev: bool = Query(False)):
    try:
        return commissioning_service.status(commissioning_id, dev=is_dev_mode(dev))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/matter/device/{commissioning_id}", tags=[TAG_MATTER])
def matter_device(commissioning_id: str, dev: bool = Query(False)):
    try:
        return commissioning_service.device(commissioning_id, dev=is_dev_mode(dev))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/matter/device/{commissioning_id}/assign", tags=[TAG_MATTER])
def matter_assign(commissioning_id: str, payload: MatterAssignPayload):
    try:
        return commissioning_service.assign(commissioning_id, room=payload.room, role=payload.role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/setup/discovery/{session_id}/candidates", tags=[TAG_SETUP])
def discovery_candidates(session_id: int, dev: bool = Query(False)):
    try:
        return device_mapping_service.candidates(session_id, dev=dev)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/discovery/{session_id}/confirm", tags=[TAG_SETUP])
def discovery_confirm(session_id: int, payload: ConfirmPayload, dev: bool = Query(False)):
    try:
        return device_mapping_service.confirm(session_id, payload.entity_id, name=payload.name, room=payload.room, dev=dev)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/sensors", tags=[TAG_SENSORS])
def setup_sensors():
    return setup_service.sensors()


@router.post("/setup/contact", tags=[TAG_SETUP])
def setup_contact(payload: ContactPayload):
    try:
        return setup_service.contact(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/setup/contact/{contact_id}", tags=[TAG_SETUP])
def setup_contact_update(contact_id: int, payload: ContactPayload):
    try:
        return setup_service.update_contact(contact_id, model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/setup/contact/{contact_id}", tags=[TAG_SETUP])
def setup_contact_delete(contact_id: int):
    return setup_service.delete_contact(contact_id)


@router.post("/setup/notifications", tags=[TAG_SETUP])
def setup_notifications(payload: NotificationPayload):
    return setup_service.notifications(model_data(payload))


@router.get("/notifications/channels", tags=[TAG_NOTIFICATIONS])
def notification_channels():
    return notification_service.channels()


@router.post("/notifications/channels/email", tags=[TAG_NOTIFICATIONS])
def notification_channel_email(payload: ChannelSettingsPayload):
    return notification_service.save_channel("email", True, payload.config)


@router.post("/notifications/channels/telegram", tags=[TAG_NOTIFICATIONS])
def notification_channel_telegram(payload: ChannelSettingsPayload):
    return notification_service.save_channel("telegram", payload.enabled, payload.config)


@router.post("/notifications/channels/whatsapp", tags=[TAG_NOTIFICATIONS])
def notification_channel_whatsapp(payload: ChannelSettingsPayload):
    return notification_service.save_channel("whatsapp", payload.enabled, payload.config)


@router.post("/notifications/test/email", tags=[TAG_NOTIFICATIONS])
def notification_test_email(dev: bool = Query(False)):
    return notification_service.test("email", dev=is_dev_mode(dev))


@router.post("/notifications/test/telegram", tags=[TAG_NOTIFICATIONS])
def notification_test_telegram(dev: bool = Query(False)):
    return notification_service.test("telegram", dev=is_dev_mode(dev))


@router.post("/notifications/test/whatsapp", tags=[TAG_NOTIFICATIONS])
def notification_test_whatsapp(dev: bool = Query(False)):
    return notification_service.test("whatsapp", dev=is_dev_mode(dev))


@router.get("/notifications/logs", tags=[TAG_NOTIFICATIONS])
def notification_logs(limit: int = Query(100, ge=1, le=500)):
    return notification_service.logs(limit=limit)


@router.post("/notifications/system/check", tags=[TAG_NOTIFICATIONS])
def notification_system_check():
    return notification_service.notify_system_warnings()


@router.post("/setup/complete", tags=[TAG_SETUP])
def setup_complete():
    try:
        return setup_service.complete()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sensor-roles", tags=[TAG_SENSORS])
def sensor_roles(dev: bool = Query(False), include_state: bool = Query(False)):
    return {"sensor_roles": device_mapping_service.roles(dev=dev, include_state=include_state)}


@router.post("/sensor-roles", tags=[TAG_SENSORS])
def sensor_role_save(payload: dict[str, Any]):
    try:
        return device_mapping_service.upsert_role(payload)
    except Exception as exc:
        raise api_error(exc) from exc


@router.delete("/sensor-roles/{role}", tags=[TAG_SENSORS])
def sensor_role_delete(role: str):
    try:
        return device_mapping_service.delete_role(role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensor-roles/{role}/test", tags=[TAG_SENSORS])
def sensor_role_test(role: str):
    try:
        return device_mapping_service.test_role(role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.put("/sensor-roles/{role}/name", tags=[TAG_SENSORS])
def sensor_role_rename(role: str, payload: SensorRoleNamePayload):
    try:
        return device_mapping_service.rename_role(role, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc
