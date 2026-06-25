from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from backend.config import config_str
from backend.services.container import get_services

API_PREFIX = "/api/sentero"

TAG_AUTH = "auth"
TAG_SYSTEM = "system"
TAG_SETUP = "setup"
TAG_NOTIFICATIONS = "notifications"
TAG_SENSORS = "sensors"
TAG_DEVICES = "devices"
TAG_EVENTS = "events"
TAG_BEHAVIOR = "behavior"
TAG_SENTERO = "sentero"

OPENAPI_TAGS = [
    {"name": TAG_AUTH, "description": "Authentication, setup account and current user session."},
    {"name": TAG_SYSTEM, "description": "System version and update lifecycle."},
    {"name": TAG_SENTERO, "description": "Core Sentero runtime status and agent execution."},
    {"name": TAG_BEHAVIOR, "description": "Behavior assessments, learning state and daily timeline."},
    {"name": TAG_SETUP, "description": "Household setup, rooms, contacts and pairing workflow."},
    {"name": TAG_SENSORS, "description": "Sensor roles and role checks."},
    {"name": TAG_DEVICES, "description": "Normalized Sentero devices independent of sensor source."},
    {"name": TAG_EVENTS, "description": "Normalized Sentero sensor events independent of sensor source."},
    {"name": TAG_NOTIFICATIONS, "description": "Notification channels, tests and logs."},
]

router = APIRouter(prefix=API_PREFIX)


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


class SensorDiscoveryPayload(BaseModel):
    sensor_type: str = "presence_sensor"
    room_id: str | None = None
    role: str | None = None
    duration: int | None = None


class SensorRegisterPayload(BaseModel):
    discovery_id: int
    name: str | None = None
    room_id: str | None = None


class SensorNetworkPayload(BaseModel):
    wifi_ssid: str | None = None
    wifi_password: str | None = None


class ConfirmPayload(BaseModel):
    entity_id: str
    name: str | None = None
    room: str | None = None


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


class DeviceRenamePayload(BaseModel):
    name: str


class DeviceAssignRoomPayload(BaseModel):
    room_id: str


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
    return dev or (config_str("app.dev_mode", "") or os.getenv("SENTERO_DEV_MODE", "")).lower() in {"1", "true", "yes", "on"}


@router.get("/auth/status", tags=[TAG_AUTH])
def sentero_auth_status(request: Request):
    return get_services().auth.status(request)


@router.post("/auth/setup", tags=[TAG_AUTH])
def sentero_auth_setup(payload: SenteroSetupPayload, request: Request, response: Response):
    return get_services().auth.setup(model_data(payload), response, request)


@router.post("/auth/login", tags=[TAG_AUTH])
def sentero_auth_login(payload: SenteroLoginPayload, request: Request, response: Response):
    return get_services().auth.login(model_data(payload), response, request)


@router.post("/auth/logout", tags=[TAG_AUTH])
def sentero_auth_logout(request: Request, response: Response):
    return get_services().auth.logout(request, response)


@router.get("/auth/me", tags=[TAG_AUTH])
def sentero_auth_me(request: Request):
    return get_services().auth.me(request)


@router.put("/auth/me", tags=[TAG_AUTH])
def sentero_auth_update_me(payload: UpdateMePayload, request: Request):
    return get_services().auth.update_me(model_data(payload), request)


@router.post("/auth/change-password", tags=[TAG_AUTH])
def sentero_auth_change_password(payload: ChangePasswordPayload, request: Request):
    return get_services().auth.change_password(model_data(payload), request)


@router.post("/auth/forgot-password", tags=[TAG_AUTH])
def sentero_auth_forgot_password(payload: ForgotPasswordPayload, request: Request):
    return get_services().auth.forgot_password(model_data(payload), request)


@router.post("/auth/reset-password", tags=[TAG_AUTH])
def sentero_auth_reset_password(payload: ResetPasswordPayload):
    return get_services().auth.reset_password(model_data(payload))


@router.get("/system/update/status", tags=[TAG_SYSTEM])
def sentero_update_status():
    return get_services().update.status()


@router.get("/system/update/check", tags=[TAG_SYSTEM])
def sentero_update_check(channel: str | None = None):
    return get_services().update.check_for_updates(channel=channel)


@router.post("/system/update/check", tags=[TAG_SYSTEM])
def sentero_update_check_post(payload: UpdateCheckRequest):
    return get_services().update.check_for_updates(channel=payload.channel)


@router.post("/system/update/install", tags=[TAG_SYSTEM])
def sentero_update_install(payload: UpdateInstallRequest, request: Request):
    user = get_services().auth.user_from_request(request, required=True)
    if str(user.get("role") or "") not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Nur Inhaber und Administratoren duerfen Updates installieren.")
    return get_services().update.install_update(username=str(user.get("email") or "sentero"), layer=payload.layer or "auto")


@router.get("/status", tags=[TAG_SENTERO])
def sentero_status():
    return get_services().sentero.status()


@router.post("/run", tags=[TAG_SENTERO])
def run_sentero_agent():
    return get_services().sentero.run(dry_run=False)


@router.get("/behavior/latest", tags=[TAG_BEHAVIOR])
def sentero_behavior_latest():
    return {"assessment": get_services().sentero.latest_behavior(), "learning": get_services().sentero.behavior_learning_status()}


@router.get("/behavior/history", tags=[TAG_BEHAVIOR])
def sentero_behavior_history(limit: int = Query(20, ge=1, le=100)):
    return {"assessments": get_services().sentero.behavior_history(limit=limit)}


@router.get("/behavior/timeline", tags=[TAG_BEHAVIOR])
def sentero_behavior_timeline():
    return get_services().sentero.behavior_timeline_today()


@router.get("/devices", tags=[TAG_DEVICES])
def sentero_devices(dev: bool = Query(False)):
    return get_services().sensors.devices(include_internal=is_dev_mode(dev))


@router.get("/events", tags=[TAG_EVENTS])
def sentero_events(limit: int = Query(100, ge=1, le=500), dev: bool = Query(False)):
    return get_services().sensors.events(limit=limit, include_internal=is_dev_mode(dev))


@router.get("/rooms", tags=[TAG_DEVICES])
def sentero_rooms():
    return get_services().sensors.rooms()


@router.get("/dashboard", tags=[TAG_SENTERO])
def sentero_dashboard():
    return get_services().sensors.dashboard()


@router.get("/sensor-source/status", tags=[TAG_SENSORS])
def sentero_sensor_source_status():
    return get_services().sensors.source_status()


@router.get("/sensors/status", tags=[TAG_SENSORS])
def sentero_sensor_manager_status():
    return get_services().sensor_manager.status()


@router.post("/sensors/start-discovery", tags=[TAG_SENSORS])
def sentero_sensor_manager_start_discovery(payload: SensorDiscoveryPayload):
    try:
        return get_services().sensor_manager.start_discovery(
            payload.sensor_type,
            room_id=payload.room_id,
            role=payload.role,
            duration=payload.duration or 180,
        )
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/sensors/discovered", tags=[TAG_SENSORS])
def sentero_sensor_manager_discovered(discovery_id: int = Query(...), dev: bool = Query(False)):
    try:
        return get_services().sensor_manager.discovered(discovery_id, dev=is_dev_mode(dev))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensors/{sensor_id}/register", tags=[TAG_SENSORS])
def sentero_sensor_manager_register(sensor_id: str, payload: SensorRegisterPayload, dev: bool = Query(False)):
    try:
        return get_services().sensor_manager.register(
            sensor_id,
            payload.discovery_id,
            name=payload.name,
            room_id=payload.room_id,
            dev=is_dev_mode(dev),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensors/{sensor_id}/assign-room", tags=[TAG_SENSORS])
def sentero_sensor_manager_assign_room(sensor_id: str, payload: DeviceAssignRoomPayload):
    return get_services().sensor_manager.assign_room(sensor_id, payload.room_id)


@router.get("/sensors/network", tags=[TAG_SENSORS])
def sentero_sensor_manager_network():
    return get_services().sensor_manager.network_settings(public=True)


@router.post("/sensors/network", tags=[TAG_SENSORS])
def sentero_sensor_manager_save_network(payload: SensorNetworkPayload):
    return get_services().sensor_manager.save_network_settings(model_data(payload))


@router.post("/sensors/network/test", tags=[TAG_SENSORS])
def sentero_sensor_manager_test_network():
    return get_services().sensor_manager.test_network_settings()


@router.get("/sensors/provisioning/status", tags=[TAG_SENSORS])
def sentero_sensor_manager_provisioning_status():
    return get_services().sensor_manager.provisioning_status()


@router.post("/devices/{device_id}/assign-room", tags=[TAG_DEVICES])
def sentero_device_assign_room(device_id: str, payload: DeviceAssignRoomPayload):
    return get_services().sensors.assign_room(device_id, payload.room_id)


@router.post("/devices/{device_id}/rename", tags=[TAG_DEVICES])
def sentero_device_rename(device_id: str, payload: DeviceRenamePayload):
    try:
        return get_services().sensors.rename(device_id, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/setup/status", tags=[TAG_SETUP])
def setup_status():
    return get_services().setup.status()


@router.post("/setup/start", tags=[TAG_SETUP])
def setup_start():
    return get_services().setup.set_step("profile", "welcome", complete=False)


@router.post("/setup/profile", tags=[TAG_SETUP])
def setup_profile(payload: ProfilePayload):
    return get_services().setup.profile(model_data(payload))


@router.get("/setup/rooms", tags=[TAG_SETUP])
def setup_rooms():
    return {"rooms": ["living_room", "kitchen", "bathroom", "bedroom", "hallway", "entrance"]}


@router.post("/setup/rooms", tags=[TAG_SETUP])
def setup_rooms_save(payload: RoomsPayload):
    return get_services().setup.rooms(payload.rooms)


@router.post("/setup/discovery/start", tags=[TAG_SETUP])
def discovery_start(payload: DiscoveryStartPayload):
    try:
        return get_services().mapping.start_pairing(payload.role, payload.room, payload.pairing_code)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/pairing/zigbee/start", tags=[TAG_SETUP])
def zigbee_pairing_start(payload: ZigbeePairingStartPayload):
    try:
        return get_services().mapping.start_zigbee_pairing(payload.role, payload.room, duration=payload.duration or 60)
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/setup/discovery/{session_id}/candidates", tags=[TAG_SETUP])
def discovery_candidates(session_id: int, dev: bool = Query(False)):
    try:
        return get_services().mapping.candidates(session_id, dev=dev)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/discovery/{session_id}/confirm", tags=[TAG_SETUP])
def discovery_confirm(session_id: int, payload: ConfirmPayload, dev: bool = Query(False)):
    try:
        return get_services().mapping.confirm(session_id, payload.entity_id, name=payload.name, room=payload.room, dev=dev)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/setup/sensors", tags=[TAG_SENSORS])
def setup_sensors():
    return get_services().setup.sensors()


@router.post("/setup/contact", tags=[TAG_SETUP])
def setup_contact(payload: ContactPayload):
    try:
        return get_services().setup.contact(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/setup/contact/{contact_id}", tags=[TAG_SETUP])
def setup_contact_update(contact_id: int, payload: ContactPayload):
    try:
        return get_services().setup.update_contact(contact_id, model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/setup/contact/{contact_id}", tags=[TAG_SETUP])
def setup_contact_delete(contact_id: int):
    return get_services().setup.delete_contact(contact_id)


@router.post("/setup/notifications", tags=[TAG_SETUP])
def setup_notifications(payload: NotificationPayload):
    return get_services().setup.notifications(model_data(payload))


@router.get("/notifications/channels", tags=[TAG_NOTIFICATIONS])
def notification_channels():
    return get_services().notification.channels()


@router.post("/notifications/channels/email", tags=[TAG_NOTIFICATIONS])
def notification_channel_email(payload: ChannelSettingsPayload):
    return get_services().notification.save_channel("email", True, payload.config)


@router.post("/notifications/channels/telegram", tags=[TAG_NOTIFICATIONS])
def notification_channel_telegram(payload: ChannelSettingsPayload):
    return get_services().notification.save_channel("telegram", payload.enabled, payload.config)


@router.post("/notifications/channels/whatsapp", tags=[TAG_NOTIFICATIONS])
def notification_channel_whatsapp(payload: ChannelSettingsPayload):
    return get_services().notification.save_channel("whatsapp", payload.enabled, payload.config)


@router.post("/notifications/test/email", tags=[TAG_NOTIFICATIONS])
def notification_test_email(dev: bool = Query(False)):
    return get_services().notification.test("email", dev=is_dev_mode(dev))


@router.post("/notifications/test/telegram", tags=[TAG_NOTIFICATIONS])
def notification_test_telegram(dev: bool = Query(False)):
    return get_services().notification.test("telegram", dev=is_dev_mode(dev))


@router.post("/notifications/test/whatsapp", tags=[TAG_NOTIFICATIONS])
def notification_test_whatsapp(dev: bool = Query(False)):
    return get_services().notification.test("whatsapp", dev=is_dev_mode(dev))


@router.get("/notifications/logs", tags=[TAG_NOTIFICATIONS])
def notification_logs(limit: int = Query(100, ge=1, le=500)):
    return get_services().notification.logs(limit=limit)


@router.post("/notifications/system/check", tags=[TAG_NOTIFICATIONS])
def notification_system_check():
    return get_services().notification.notify_system_warnings()


@router.post("/setup/complete", tags=[TAG_SETUP])
def setup_complete():
    try:
        return get_services().setup.complete()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sensor-roles", tags=[TAG_SENSORS])
def sensor_roles(dev: bool = Query(False), include_state: bool = Query(False)):
    return {"sensor_roles": get_services().mapping.roles(dev=dev, include_state=include_state)}


@router.post("/sensor-roles", tags=[TAG_SENSORS])
def sensor_role_save(payload: dict[str, Any]):
    try:
        return get_services().mapping.upsert_role(payload)
    except Exception as exc:
        raise api_error(exc) from exc


@router.delete("/sensor-roles/{role}", tags=[TAG_SENSORS])
def sensor_role_delete(role: str):
    try:
        return get_services().mapping.delete_role(role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensor-roles/{role}/test", tags=[TAG_SENSORS])
def sensor_role_test(role: str):
    try:
        return get_services().mapping.test_role(role)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.put("/sensor-roles/{role}/name", tags=[TAG_SENSORS])
def sensor_role_rename(role: str, payload: SensorRoleNamePayload):
    try:
        return get_services().mapping.rename_role(role, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc
