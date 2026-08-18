from __future__ import annotations

import json
import os
import socket
from typing import Any

from backend.config import config_int, config_str, config_value
from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.network.access_point_service import AccessPointService
from backend.services.network.cellular_service import CellularService
from backend.services.network.connectivity_service import ConnectivityService
from backend.services.network.models import (
    CellularStatus,
    ConnectionType,
    FailoverConfig,
    NetworkCapabilities,
    NetworkStatus,
    NetworkStatusCode,
)
from backend.services.network.secret_store import NetworkSecretStore
from backend.services.network.wifi_service import WifiService

logger = get_logger(__name__)

VALID_MODES = {"disabled", "auto", "force"}


class NetworkService:
    def __init__(
        self,
        mapping: DeviceMappingService,
        wifi: WifiService | None = None,
        access_point: AccessPointService | None = None,
        cellular: CellularService | None = None,
        connectivity: ConnectivityService | None = None,
        secret_store: NetworkSecretStore | None = None,
    ) -> None:
        self.mapping = mapping
        self.secret_store = secret_store or NetworkSecretStore()
        self.wifi = wifi or WifiService()
        self.access_point = access_point or AccessPointService(self.secret_store)
        self.cellular = cellular or CellularService()
        self.connectivity = connectivity or ConnectivityService()
        self._failure_count = 0
        self._recovery_count = 0
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists network_settings (
                    id integer primary key check (id = 1),
                    wifi_ssid text,
                    wifi_configured integer not null default 0,
                    cellular_configured integer not null default 0,
                    setup_ap_active integer not null default 0,
                    setup_completed integer not null default 0,
                    active_connection text,
                    last_switch_at text,
                    updated_at text not null
                )"""
            )
            con.execute(
                """create table if not exists network_event_history (
                    id integer primary key autoincrement,
                    event text not null,
                    connection_type text,
                    success integer not null,
                    signal_quality integer,
                    reason text,
                    created_at text not null
                )"""
            )
            con.execute(
                "insert or ignore into network_settings (id, updated_at) values (1, ?)",
                (now(),),
            )
            con.commit()

    def mode(self) -> str:
        raw = (os.getenv("SENTERO_BOX_SETUP_MODE") or config_str("box_setup.mode", "disabled") or "disabled").strip().lower()
        return raw if raw in VALID_MODES else "disabled"

    def hostname(self) -> str:
        return (os.getenv("SENTERO_BOX_HOSTNAME") or config_str("box_setup.hostname", "sentero") or "sentero").strip() or "sentero"

    def status(self, diagnostics: bool = False) -> dict[str, Any]:
        return self._status().public(diagnostics=diagnostics)

    def capabilities(self) -> dict[str, bool]:
        return self._capabilities().public()

    def wifi_networks(self) -> dict[str, Any]:
        return {"networks": [network.public() for network in self.wifi.scan()]}

    def connect_wifi(self, payload: dict[str, Any]) -> dict[str, Any]:
        ssid = str(payload.get("ssid") or "").strip()
        password = str(payload.get("password") or "")
        if not ssid:
            raise ValueError("Bitte wählen Sie ein WLAN aus.")
        if not password:
            raise ValueError("Bitte geben Sie das WLAN-Passwort ein.")
        if self.mode() == "disabled":
            self.secret_store.set("wifi", {"ssid": ssid, "password": password})
            self._persist_wifi(ssid, configured=True)
            self._set_active_connection(ConnectionType.WIFI)
            self.stop_setup_ap(mark_setup_complete=True)
            self._event("wifi_connected", ConnectionType.WIFI, True, reason="development_mode")
            return {"ok": True, "applied": False, "message": "Development-Modus: WLAN-Daten gespeichert, keine Netzwerkänderung ausgeführt.", "status": self.status()}
        result = self.wifi.connect(ssid, password)
        if not result.get("ok"):
            self._event("wifi_lost", ConnectionType.WIFI, False, reason="connect_failed")
            return {"ok": False, "applied": False, "message": result.get("message") or "WLAN konnte nicht verbunden werden.", "status": self.status()}
        check = self.connectivity.check(ConnectionType.WIFI)
        if check.status != NetworkStatusCode.ONLINE_WIFI:
            self._event("wifi_lost", ConnectionType.WIFI, False, reason=check.reason or "internet_unreachable")
            return {"ok": False, "applied": True, "message": "WLAN ist verbunden, aber das Internet ist nicht erreichbar. Das Setup-WLAN bleibt aktiv.", "status": self.status()}
        self.secret_store.set("wifi", {"ssid": ssid, "password": password})
        self._persist_wifi(ssid, configured=True)
        self._set_active_connection(ConnectionType.WIFI)
        self.stop_setup_ap(mark_setup_complete=True)
        self._event("wifi_connected", ConnectionType.WIFI, True)
        return {"ok": True, "applied": True, "message": "Sentero ist mit dem Internet verbunden.", "status": self.status()}

    def test_wifi(self) -> dict[str, Any]:
        check = self.connectivity.check(ConnectionType.WIFI)
        return {"ok": check.status == NetworkStatusCode.ONLINE_WIFI, "connectivity": check.public(), "message": "Internetverbindung erfolgreich geprüft." if check.internet else "WLAN hat aktuell keine Internetverbindung."}

    def cellular_status(self) -> dict[str, Any]:
        return self.cellular.status().public()

    def connect_cellular(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        self._persist_cellular(configured=True)
        if self.mode() == "disabled":
            return {"ok": True, "applied": False, "message": "Development-Modus: Mobilfunk als Fallback vorgemerkt.", "status": self.status()}
        result = self.cellular.connect(
            apn=str(payload.get("apn") or "").strip() or None,
            username=str(payload.get("username") or "").strip() or None,
            password=str(payload.get("password") or "") or None,
            pin=str(payload.get("pin") or "") or None,
        )
        if result.get("ok"):
            self._set_active_connection(ConnectionType.CELLULAR)
            self.stop_setup_ap(mark_setup_complete=True)
            self._event("cellular_fallback_started", ConnectionType.CELLULAR, True, signal_quality=self.cellular.status().signal_percent)
        return {"ok": bool(result.get("ok")), "applied": True, "message": result.get("message") or "", "status": self.status()}

    def disconnect_cellular(self) -> dict[str, Any]:
        result = self.cellular.disconnect()
        self._event("cellular_fallback_stopped", ConnectionType.CELLULAR, bool(result.get("ok")))
        return {"ok": bool(result.get("ok")), "message": result.get("message") or "", "status": self.status()}

    def start_setup_ap(self, reason: str = "manual") -> dict[str, Any]:
        self.access_point.ensure_setup_password()
        if self.mode() == "disabled":
            self._set_setup_ap(True)
            self._event("setup_ap_started", ConnectionType.NONE, True, reason=reason)
            return {"ok": True, "message": "Development-Modus: Setup-WLAN als aktiv markiert.", "access_point": self.access_point.status(True).public()}
        result = self.access_point.start()
        self._set_setup_ap(bool(result.get("active")))
        self._event("setup_ap_started", ConnectionType.NONE, bool(result.get("ok")), reason=reason)
        return {"ok": bool(result.get("ok")), "message": result.get("message") or "", "access_point": self.access_point.status(bool(result.get("active"))).public()}

    def stop_setup_ap(self, mark_setup_complete: bool = False) -> dict[str, Any]:
        if self.mode() == "disabled":
            result = {"ok": True, "message": "Development-Modus: Setup-WLAN als inaktiv markiert."}
        else:
            result = self.access_point.stop()
        self._set_setup_ap(False, setup_completed=mark_setup_complete)
        self._event("setup_ap_stopped", ConnectionType.NONE, bool(result.get("ok")))
        return {"ok": bool(result.get("ok")), "message": result.get("message") or "", "access_point": self.access_point.status(False).public()}

    def ensure_first_boot_setup(self) -> dict[str, Any]:
        status = self._status()
        if self.mode() == "force":
            return self.start_setup_ap(reason="force")
        if self.mode() == "auto" and not status.network_ready and not status.setup_ap_active:
            return self.start_setup_ap(reason="first_boot")
        return {"ok": True, "message": "Keine Setup-Änderung nötig.", "status": status.public()}

    def failover_test(self, checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        config = self.failover_config()
        state = {"current": self._active_connection(), "failures": 0, "recoveries": 0, "switches": []}
        for item in checks or []:
            connection = ConnectionType(str(item.get("connection") or state["current"] or "none"))
            if state["current"] == ConnectionType.NONE and connection != ConnectionType.NONE:
                state["current"] = connection
            ok = bool(item.get("ok"))
            preferred_ok = bool(item.get("preferred_ok", ok))
            if not ok:
                state["failures"] += 1
                state["recoveries"] = 0
            else:
                state["recoveries"] += 1
                state["failures"] = 0
            if state["current"] in {ConnectionType.WIFI, ConnectionType.ETHERNET} and state["failures"] >= config.failure_threshold and self.cellular.status().available:
                state["current"] = ConnectionType.CELLULAR
                state["switches"].append("cellular_fallback_started")
                state["failures"] = 0
            if state["current"] == ConnectionType.CELLULAR and preferred_ok and state["recoveries"] >= config.recovery_threshold:
                state["current"] = connection if connection in {ConnectionType.WIFI, ConnectionType.ETHERNET} else ConnectionType.WIFI
                state["switches"].append("cellular_fallback_stopped")
                state["recoveries"] = 0
        return {"ok": True, "final_connection": state["current"].value if isinstance(state["current"], ConnectionType) else state["current"], "switches": state["switches"], "config": config.__dict__}

    def maintain_once(self) -> dict[str, Any]:
        config = self.failover_config()
        status = self._status()
        actions: list[str] = []
        if not config.enabled:
            return {"ok": True, "actions": actions, "status": status.public()}

        if status.active_connection in {ConnectionType.WIFI, ConnectionType.ETHERNET}:
            if not status.internet_reachable:
                self._failure_count += 1
                self._recovery_count = 0
            else:
                self._failure_count = 0
            if self._failure_count >= config.failure_threshold and self.cellular.status().available:
                result = self.connect_cellular({})
                if result.get("ok"):
                    actions.append("cellular_fallback_started")
                    self._failure_count = 0

        if status.active_connection == ConnectionType.CELLULAR:
            preferred = self._preferred_available_connection()
            if preferred != ConnectionType.NONE:
                self._recovery_count += 1
            else:
                self._recovery_count = 0
            if preferred != ConnectionType.NONE and self._recovery_count >= config.recovery_threshold:
                self._set_active_connection(preferred)
                self.disconnect_cellular()
                self._event("cellular_fallback_stopped", ConnectionType.CELLULAR, True, reason=f"recovered_{preferred.value}")
                actions.append("cellular_fallback_stopped")
                self._recovery_count = 0
        return {"ok": True, "actions": actions, "status": self.status()}

    def failover_config(self) -> FailoverConfig:
        return FailoverConfig(
            enabled=as_bool(config_value("network.failover.enabled", True)),
            failure_threshold=max(1, config_int("network.failover.failure_threshold", 3)),
            recovery_threshold=max(1, config_int("network.failover.recovery_threshold", 3)),
            check_interval_seconds=max(5, config_int("network.failover.check_interval_seconds", 30)),
            switch_cooldown_seconds=max(30, config_int("network.failover.switch_cooldown_seconds", 120)),
        )

    def legacy_status(self) -> dict[str, Any]:
        status = self._status()
        data = status.public()
        data.update({
            "mode": self.mode(),
            "ip_address": local_ip_address(),
        })
        return data

    def settings(self, public: bool = True) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from network_settings where id = 1").fetchone()
        data = dict(row) if row else {}
        if public:
            configured = bool(data.get("wifi_configured"))
            return {"wifi_ssid": data.get("wifi_ssid") or "", "wifi_password_set": configured and bool(self.secret_store.get("wifi").get("password")), "configured": configured}
        return data

    def _status(self) -> NetworkStatus:
        settings = self.settings(public=False)
        active = self._active_connection()
        cellular = self.cellular.status()
        capabilities = self._capabilities(cellular=cellular)
        check = self.connectivity.check(active)
        setup_ap = bool(settings.get("setup_ap_active"))
        if self.mode() == "force":
            setup_ap = True
        ethernet_active = active == ConnectionType.ETHERNET and check.internet
        wifi_active = active == ConnectionType.WIFI and check.internet
        cellular_active = active == ConnectionType.CELLULAR and (cellular.connected or check.internet)
        network_ready = check.internet and active != ConnectionType.NONE
        status = check.status if network_ready or check.status in {NetworkStatusCode.LOCAL_ONLY, NetworkStatusCode.DEGRADED} else NetworkStatusCode.OFFLINE
        return NetworkStatus(
            status=status,
            active_connection=active,
            network_ready=network_ready,
            internet_reachable=check.internet,
            ethernet_active=ethernet_active,
            wifi_active=wifi_active,
            cellular_active=cellular_active,
            setup_ap_active=setup_ap,
            wifi_configured=bool(settings.get("wifi_configured")),
            cellular=cellular,
            capabilities=capabilities,
            hostname=self.hostname(),
            local_url=f"http://{self.hostname()}.local",
            customer_message=customer_message(status, active, cellular),
            diagnostics={
                "connectivity": check.public(),
                "failover": self.failover_config().__dict__,
                "default_route": active.value,
            },
        )

    def _capabilities(self, cellular: CellularStatus | None = None) -> NetworkCapabilities:
        return NetworkCapabilities(
            ethernet=True,
            wifi=self.wifi.available() or self.mode() == "disabled",
            wifi_ap=self.wifi.ap_supported() or self.mode() == "disabled",
            cellular=(cellular or self.cellular.status()).available,
        )

    def _preferred_available_connection(self) -> ConnectionType:
        ethernet = self.connectivity.check(ConnectionType.ETHERNET)
        if ethernet.status == NetworkStatusCode.ONLINE_ETHERNET:
            return ConnectionType.ETHERNET
        if self.settings(public=True).get("configured"):
            wifi = self.connectivity.check(ConnectionType.WIFI)
            if wifi.status == NetworkStatusCode.ONLINE_WIFI:
                return ConnectionType.WIFI
        return ConnectionType.NONE

    def _active_connection(self) -> ConnectionType:
        with self.mapping.connect() as con:
            row = con.execute("select active_connection from network_settings where id = 1").fetchone()
        value = str(row["active_connection"] if row else "" or "")
        try:
            return ConnectionType(value)
        except ValueError:
            return ConnectionType.NONE

    def _set_active_connection(self, connection: ConnectionType) -> None:
        with self.mapping.connect() as con:
            con.execute("update network_settings set active_connection = ?, last_switch_at = ?, updated_at = ? where id = 1", (connection.value, now(), now()))
            con.commit()

    def _persist_wifi(self, ssid: str, configured: bool) -> None:
        with self.mapping.connect() as con:
            con.execute("update network_settings set wifi_ssid = ?, wifi_configured = ?, updated_at = ? where id = 1", (ssid, int(configured), now()))
            con.commit()

    def _persist_cellular(self, configured: bool) -> None:
        with self.mapping.connect() as con:
            con.execute("update network_settings set cellular_configured = ?, updated_at = ? where id = 1", (int(configured), now()))
            con.commit()

    def _set_setup_ap(self, active: bool, setup_completed: bool | None = None) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "update network_settings set setup_ap_active = ?, setup_completed = coalesce(?, setup_completed), updated_at = ? where id = 1",
                (int(active), None if setup_completed is None else int(setup_completed), now()),
            )
            con.commit()

    def _event(self, event: str, connection_type: ConnectionType, success: bool, signal_quality: int | None = None, reason: str | None = None) -> None:
        with self.mapping.connect() as con:
            last = con.execute("select event, success, reason from network_event_history order by id desc limit 1").fetchone()
            if last and last["event"] == event and bool(last["success"]) == success and (last["reason"] or None) == reason:
                return
            con.execute(
                """insert into network_event_history (event, connection_type, success, signal_quality, reason, created_at)
                   values (?, ?, ?, ?, ?, ?)""",
                (event, connection_type.value, int(success), signal_quality, reason, now()),
            )
            con.commit()
        logger.info("Network state changed", extra={"component": "network", "event": event, "connection_type": connection_type.value, "success": success, "reason": reason or ""})


def customer_message(status: NetworkStatusCode, connection: ConnectionType, cellular: CellularStatus) -> str:
    if status == NetworkStatusCode.ONLINE_CELLULAR or connection == ConnectionType.CELLULAR:
        signal = ""
        if cellular.signal_percent is not None:
            signal = " Signal: Gut." if cellular.signal_percent >= 60 else " Signal: Schwach."
        return f"Internet verbunden. Sentero verwendet momentan Mobilfunk.{signal}".strip()
    if status in {NetworkStatusCode.ONLINE_WIFI, NetworkStatusCode.ONLINE_ETHERNET}:
        via = "WLAN" if connection == ConnectionType.WIFI else "Ethernet"
        return f"Internet verbunden. Über {via}."
    if status == NetworkStatusCode.DEGRADED:
        return "Internet teilweise verbunden. Benachrichtigungen können verzögert sein."
    return "Internet nicht verbunden. Sentero überwacht weiterhin lokal. Benachrichtigungen werden später versendet."


def local_ip_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 443))
            ip_address = sock.getsockname()[0]
        if ip_address and not ip_address.startswith("127."):
            return ip_address
    except OSError:
        return None
    return None


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", "nein"}
    return bool(value)
