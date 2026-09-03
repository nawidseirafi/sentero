from __future__ import annotations

import os
import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from backend.agents.sentero.mail.discovery import get_mail_settings, verify_mail_credentials
from backend.agents.sentero.mail.models import MailConfig
from backend.config import config_str
from backend.services.container import get_services

API_PREFIX = "/api/sentero"

TAG_AUTH = "auth"
TAG_SYSTEM = "system"
TAG_SETUP = "setup"
TAG_NOTIFICATIONS = "notifications"
TAG_CONSENTS = "consents"
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
    {"name": TAG_CONSENTS, "description": "Consent and data sharing controls."},
]

router = APIRouter(prefix=API_PREFIX)
box_setup_router = APIRouter(prefix="/api/setup")
mail_router = APIRouter(prefix="/api/mail")


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
    transport: str | None = None
    duration: int | None = None


class SensorRegisterPayload(BaseModel):
    discovery_id: int
    name: str | None = None
    room_id: str | None = None


class UnassignedSensorAssignPayload(BaseModel):
    sensor_type: str
    room_id: str | None = None
    role: str | None = None
    name: str | None = None


class SensorDiscoveryCancelPayload(BaseModel):
    discovery_id: int | None = None


class SensorNetworkPayload(BaseModel):
    wifi_ssid: str | None = None
    wifi_password: str | None = None


class EcoTrackerPayload(BaseModel):
    host: str


class SensorRoleCommandPayload(BaseModel):
    command: str
    enabled: bool | None = None
    value: Any | None = None
    settings: dict[str, Any] | None = None


class BoxNetworkWifiPayload(BaseModel):
    ssid: str
    password: str


class NetworkWifiConnectPayload(BaseModel):
    ssid: str
    password: str


class NetworkCellularConnectPayload(BaseModel):
    apn: str | None = None
    username: str | None = None
    password: str | None = None
    pin: str | None = None


class FailoverTestPayload(BaseModel):
    checks: list[dict[str, Any]] = Field(default_factory=list)


class Esp32ProvisioningStartPayload(BaseModel):
    room_id: str
    display_name: str
    device_id: str | None = None


class ConfirmPayload(BaseModel):
    entity_id: str
    name: str | None = None
    room: str | None = None


class ContactPayload(BaseModel):
    name: str
    relationship: str | None = None
    actor_role: str = "relative"
    email: str | None = None
    phone: str | None = None
    telegram_chat_id: str | None = None
    whatsapp_phone_number: str | None = None
    preferred_channels: list[str] | None = None
    notification_enabled: bool = True
    primary_contact: bool = False
    email_queries_enabled: bool | None = None
    email_permissions: list[str] | None = None


class MailContactSettingsPayload(BaseModel):
    email_queries_enabled: bool
    email_permissions: list[str] = Field(default_factory=lambda: ["STATUS", "ACTIVITY", "ROOM", "ENVIRONMENT", "NIGHT", "TECHNICAL_HEALTH"])


class NotificationPayload(BaseModel):
    anomalies: bool = True
    critical: bool = True
    daily_summary: bool = True


class SensorRoleNamePayload(BaseModel):
    name: str


class DeviceRenamePayload(BaseModel):
    name: str


class DeviceAssignRoomPayload(BaseModel):
    room_id: str


class ChannelSettingsPayload(BaseModel):
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class ConsentPayload(BaseModel):
    contact_id: int
    recipient_type: str = "relative"
    purpose: str = "behavior_notification"
    data_classes: list[str] = Field(default_factory=lambda: ["personal_behavior", "health_adjacent", "emergency"])
    valid_until: str | None = None


class ExportTokenPayload(BaseModel):
    contact_id: int
    purpose: str = "aal_partner_export"
    data_classes: list[str] = Field(default_factory=lambda: ["technical", "utility", "health_adjacent", "emergency"])
    expires_at: str | None = None


class AuditCleanupPayload(BaseModel):
    days: int = Field(default=180, ge=30, le=3650)


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


class FactoryResetRequest(BaseModel):
    confirm: str


class MailDiscoverPayload(BaseModel):
    email: str


class MailVerifyPayload(BaseModel):
    email: str
    password: str = ""
    config: MailConfig
    imap_username: str | None = None
    smtp_username: str | None = None


def model_data(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def api_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def is_dev_mode(dev: bool = False) -> bool:
    return dev or (config_str("app.dev_mode", "") or os.getenv("SENTERO_DEV_MODE", "")).lower() in {"1", "true", "yes", "on"}


@mail_router.post("/discover", response_model=MailConfig, tags=[TAG_SETUP])
async def discover_mail(payload: MailDiscoverPayload):
    config = await get_mail_settings(payload.email)
    if config is None:
        raise HTTPException(status_code=404, detail="Für diese E-Mail-Domain wurden keine Mailserver-Einstellungen gefunden.")
    return config


@mail_router.post("/verify", tags=[TAG_SETUP])
async def verify_mail(payload: MailVerifyPayload):
    password = str(payload.password or "")
    if not password or _looks_masked_secret(password):
        stored = get_services().notification.stored_channel_config("email")
        password = str(stored.get("smtp_password") or stored.get("imap_password") or "")

    if not password:
        return {
            "ok": False,
            "message": "Kein gespeichertes Passwort vorhanden. Bitte geben Sie das Passwort oder App-Passwort erneut ein.",
        }

    ok, message = await asyncio.to_thread(
        verify_mail_credentials,
        payload.config,
        payload.email,
        password,
        payload.imap_username,
        payload.smtp_username,
    )
    return {"ok": ok, "message": message or "Senden und Empfangen funktioniert."}


def _looks_masked_secret(value: Any) -> bool:
    text = str(value or "")
    return "•" in text or text.startswith("***")


@box_setup_router.get("/box-network/status", tags=[TAG_SETUP])
def box_network_status():
    return get_services().box_network.status()


@box_setup_router.post("/box-network/wifi", tags=[TAG_SETUP])
def box_network_wifi(payload: BoxNetworkWifiPayload):
    try:
        return get_services().box_network.save_wifi(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@box_setup_router.get("/network/status", tags=[TAG_SETUP])
def local_setup_network_status():
    return get_services().network.status()


@box_setup_router.get("/network/wifi/networks", tags=[TAG_SETUP])
def local_setup_wifi_networks():
    return get_services().network.wifi_networks()


@box_setup_router.post("/network/wifi/connect", tags=[TAG_SETUP])
def local_setup_wifi_connect(payload: NetworkWifiConnectPayload):
    try:
        return get_services().network.connect_wifi(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@box_setup_router.post("/network/cellular/connect", tags=[TAG_SETUP])
def local_setup_cellular_connect(payload: NetworkCellularConnectPayload):
    return get_services().network.connect_cellular(model_data(payload))


@router.get("/network/status", tags=[TAG_SYSTEM])
def network_status(diagnostics: bool = False):
    return get_services().network.status(diagnostics=diagnostics)


@router.get("/network/capabilities", tags=[TAG_SYSTEM])
def network_capabilities():
    return get_services().network.capabilities()


@router.get("/network/wifi/networks", tags=[TAG_SYSTEM])
def network_wifi_networks():
    return get_services().network.wifi_networks()


@router.post("/network/wifi/connect", tags=[TAG_SYSTEM])
def network_wifi_connect(payload: NetworkWifiConnectPayload):
    try:
        return get_services().network.connect_wifi(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/network/wifi/test", tags=[TAG_SYSTEM])
def network_wifi_test():
    return get_services().network.test_wifi()


@router.get("/network/cellular/status", tags=[TAG_SYSTEM])
def network_cellular_status():
    return get_services().network.cellular_status()


@router.post("/network/cellular/connect", tags=[TAG_SYSTEM])
def network_cellular_connect(payload: NetworkCellularConnectPayload):
    return get_services().network.connect_cellular(model_data(payload))


@router.post("/network/cellular/disconnect", tags=[TAG_SYSTEM])
def network_cellular_disconnect():
    return get_services().network.disconnect_cellular()


@router.post("/network/setup-ap/start", tags=[TAG_SYSTEM])
def network_setup_ap_start():
    return get_services().network.start_setup_ap(reason="manual")


@router.post("/network/setup-ap/stop", tags=[TAG_SYSTEM])
def network_setup_ap_stop():
    return get_services().network.stop_setup_ap()


@router.post("/network/failover/test", tags=[TAG_SYSTEM])
def network_failover_test(payload: FailoverTestPayload):
    return get_services().network.failover_test(model_data(payload).get("checks") or [])


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


@router.get("/system/status", tags=[TAG_SYSTEM])
def sentero_system_status():
    return get_services().system_status.status()


@router.get("/system/factory-reset/status", tags=[TAG_SYSTEM])
def sentero_factory_reset_status(request: Request):
    user = get_services().auth.user_from_request(request, required=True)
    if str(user.get("role") or "") not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Nur Inhaber und Administratoren dürfen die Werkseinstellungen verwalten.")
    try:
        return get_services().factory_reset.status()
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/system/factory-reset", tags=[TAG_SYSTEM])
def sentero_factory_reset(payload: FactoryResetRequest, request: Request):
    user = get_services().auth.user_from_request(request, required=True)
    if str(user.get("role") or "") not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Nur Inhaber und Administratoren dürfen die Box zurücksetzen.")
    try:
        return get_services().factory_reset.start(payload.confirm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


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
def sentero_behavior_timeline(live: bool = Query(False), date: str | None = Query(None)):
    return get_services().sentero.behavior_timeline_today(live_snapshot=live, day=date)


@router.get("/behavior/day", tags=[TAG_BEHAVIOR])
def sentero_behavior_day(date: str | None = Query(None), live: bool = Query(False)):
    return get_services().sentero.behavior_day(day=date, live_snapshot=live)


@router.get("/behavior/trends", tags=[TAG_BEHAVIOR])
def sentero_behavior_trends(days: int = Query(14, ge=7, le=30)):
    return get_services().sentero.behavior_trends(days=days)


@router.get("/behavior/hints", tags=[TAG_BEHAVIOR])
def sentero_behavior_hints(days: int = Query(14, ge=1, le=30)):
    return get_services().sentero.behavior_hints(days=days)


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
            duration=payload.duration or 120,
            transport=payload.transport,
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


@router.post("/sensors/discovery/cancel", tags=[TAG_SENSORS])
def sentero_sensor_manager_cancel_discovery(payload: SensorDiscoveryCancelPayload):
    try:
        return get_services().sensor_manager.cancel_discovery(payload.discovery_id)
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


@router.get("/sensors/unassigned", tags=[TAG_SENSORS])
def sentero_sensor_manager_unassigned():
    try:
        return get_services().sensor_manager.unassigned_devices()
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensors/unassigned/{device_id}/assign", tags=[TAG_SENSORS])
def sentero_sensor_manager_assign_unassigned(device_id: str, payload: UnassignedSensorAssignPayload, dev: bool = Query(False)):
    try:
        return get_services().sensor_manager.assign_unassigned(
            device_id,
            payload.sensor_type,
            room_id=payload.room_id,
            role=payload.role,
            name=payload.name,
            dev=is_dev_mode(dev),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensors/unassigned/{device_id}/ignore", tags=[TAG_SENSORS])
def sentero_sensor_manager_ignore_unassigned(device_id: str):
    try:
        return get_services().sensor_manager.ignore_unassigned(device_id)
    except Exception as exc:
        raise api_error(exc) from exc


@router.delete("/sensors/unassigned/{device_id}", tags=[TAG_SENSORS])
def sentero_sensor_manager_remove_unassigned(device_id: str):
    try:
        return get_services().sensor_manager.remove_unassigned(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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


@router.get("/sensors/ecotracker", tags=[TAG_SENSORS])
def sentero_ecotracker_status():
    return get_services().sensor_manager.ecotracker_status()


@router.post("/sensors/ecotracker/test", tags=[TAG_SENSORS])
def sentero_ecotracker_test(payload: EcoTrackerPayload):
    try:
        return get_services().sensor_manager.test_ecotracker(payload.host)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensors/ecotracker/connect", tags=[TAG_SENSORS])
def sentero_ecotracker_connect(payload: EcoTrackerPayload):
    try:
        return get_services().sensor_manager.connect_ecotracker(payload.host)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/sensors/provisioning/status", tags=[TAG_SENSORS])
def sentero_sensor_manager_provisioning_status():
    return get_services().sensor_manager.provisioning_status()


@router.post("/sensors/provisioning/esp32/discovery/start", tags=[TAG_SENSORS])
def sentero_sensor_manager_start_esp32_discovery():
    try:
        return get_services().sensor_manager.start_esp32_discovery()
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/sensors/provisioning/esp32/discovered", tags=[TAG_SENSORS])
def sentero_sensor_manager_esp32_discovered():
    try:
        return get_services().sensor_manager.esp32_discovery_status()
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/sensors/provisioning/esp32/start", tags=[TAG_SENSORS])
def sentero_sensor_manager_start_esp32_provisioning(payload: Esp32ProvisioningStartPayload):
    try:
        return get_services().sensor_manager.start_esp32_provisioning(payload.room_id, payload.display_name, device_id=payload.device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise api_error(exc) from exc


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


@router.get("/setup/email-queries", tags=[TAG_SETUP])
def setup_email_queries():
    return get_services().mail_assistant_settings.status()


@router.put("/setup/contact/{contact_id}/email-queries", tags=[TAG_SETUP])
def setup_contact_email_queries(contact_id: int, payload: MailContactSettingsPayload):
    try:
        return get_services().mail_assistant_settings.update_contact(contact_id, model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/setup/notifications", tags=[TAG_SETUP])
def setup_notifications(payload: NotificationPayload):
    return get_services().setup.notifications(model_data(payload))


@router.get("/notifications/channels", tags=[TAG_NOTIFICATIONS])
def notification_channels():
    return get_services().notification.channels()


@router.get("/notifications/telegram/bot", tags=[TAG_NOTIFICATIONS])
def notification_telegram_bot():
    try:
        return get_services().notification.telegram_bot_info()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@router.get("/consents", tags=[TAG_CONSENTS])
def consents():
    return get_services().consent.list()


@router.post("/consents", tags=[TAG_CONSENTS])
def grant_consent(payload: ConsentPayload):
    try:
        return get_services().consent.grant(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/consents/{consent_id}/revoke", tags=[TAG_CONSENTS])
def revoke_consent(consent_id: int):
    return get_services().consent.revoke(consent_id)


@router.get("/exports/tokens", tags=[TAG_CONSENTS])
def export_tokens():
    return get_services().exports.list_tokens()


@router.post("/exports/tokens", tags=[TAG_CONSENTS])
def create_export_token(payload: ExportTokenPayload):
    try:
        return get_services().exports.create_token(model_data(payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/exports/tokens/{token_id}/revoke", tags=[TAG_CONSENTS])
def revoke_export_token(token_id: int):
    return get_services().exports.revoke_token(token_id)


@router.get("/transparency", tags=[TAG_CONSENTS])
def transparency(limit: int = Query(100, ge=1, le=500)):
    return get_services().audit.transparency(limit=limit)


@router.get("/transparency/retention", tags=[TAG_CONSENTS])
def transparency_retention():
    return get_services().audit.retention_status()


@router.post("/transparency/retention/cleanup", tags=[TAG_CONSENTS])
def transparency_retention_cleanup(payload: AuditCleanupPayload):
    return get_services().audit.cleanup(days=payload.days)


def export_token_from_request(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return str(request.query_params.get("token") or "").strip()


def exchange_export(request: Request, export_type: str, period_start: str | None = None, period_end: str | None = None):
    try:
        return get_services().exports.export(export_token_from_request(request), export_type, period_start=period_start, period_end=period_end)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/exchange/v1/daily-status", tags=[TAG_CONSENTS])
def exchange_daily_status(request: Request, period_start: str | None = None, period_end: str | None = None):
    return exchange_export(request, "daily-status", period_start=period_start, period_end=period_end)


@router.get("/exchange/v1/event-summary", tags=[TAG_CONSENTS])
def exchange_event_summary(request: Request, period_start: str | None = None, period_end: str | None = None):
    return exchange_export(request, "event-summary", period_start=period_start, period_end=period_end)


@router.get("/exchange/v1/system-status", tags=[TAG_CONSENTS])
def exchange_system_status(request: Request, period_start: str | None = None, period_end: str | None = None):
    return exchange_export(request, "system-status", period_start=period_start, period_end=period_end)


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
def sensor_role_delete(role: str, local_only: bool = Query(False)):
    try:
        return get_services().mapping.delete_role(role, local_only=local_only)
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


@router.post("/sensor-roles/{role}/command", tags=[TAG_SENSORS])
def sensor_role_command(role: str, payload: SensorRoleCommandPayload):
    try:
        return get_services().mapping.send_role_command(role, payload.model_dump(exclude_none=True))
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
