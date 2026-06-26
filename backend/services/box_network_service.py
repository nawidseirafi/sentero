from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from typing import Any

from backend.config import config_str
from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, now

logger = get_logger(__name__)

VALID_MODES = {"disabled", "auto", "force"}
DEFAULT_HOSTNAME = "sentero"


@dataclass(frozen=True)
class BoxNetworkStatus:
    mode: str
    network_ready: bool
    ethernet_active: bool
    wifi_active: bool
    ip_address: str | None
    setup_ap_active: bool
    hostname: str
    local_url: str
    message: str
    wifi_configured: bool
    internet_reachable: bool | None = None


class BoxNetworkAdapter:
    """Boundary for OS-level network operations.

    The default adapter is intentionally non-destructive. Real NetworkManager,
    hotspot and mDNS integration belongs behind this interface.
    """

    def status(self, mode: str, hostname: str, wifi_configured: bool) -> BoxNetworkStatus:
        ip_address = local_ip_address()
        network_ready = bool(ip_address) or mode == "disabled"
        setup_ap_active = mode == "force" or (mode == "auto" and not network_ready)
        return BoxNetworkStatus(
            mode=mode,
            network_ready=network_ready,
            ethernet_active=bool(ip_address),
            wifi_active=False,
            ip_address=ip_address,
            setup_ap_active=setup_ap_active,
            hostname=hostname,
            local_url=f"http://{hostname}.local",
            message="Netzwerk ist verbunden." if network_ready else "Sentero wartet auf eine Netzwerkverbindung.",
            wifi_configured=wifi_configured,
            internet_reachable=None,
        )

    def apply_wifi(self, ssid: str, password: str) -> dict[str, Any]:
        raise NotImplementedError("OS-Netzwerkadapter ist noch nicht aktiviert.")

    def disable_setup_ap(self) -> None:
        return None


class DisabledBoxNetworkAdapter(BoxNetworkAdapter):
    def apply_wifi(self, ssid: str, password: str) -> dict[str, Any]:
        return {
            "ok": True,
            "applied": False,
            "message": "Development-Modus: WLAN-Daten gespeichert, keine Netzwerkänderung ausgeführt.",
        }


class PreparedNetworkManagerAdapter(BoxNetworkAdapter):
    def apply_wifi(self, ssid: str, password: str) -> dict[str, Any]:
        logger.warning("NetworkManager adapter not implemented", extra={"component": "box_network"})
        return {
            "ok": False,
            "applied": False,
            "message": "Produktive WLAN-Verbindung ist vorbereitet, aber noch nicht aktiviert.",
        }


class BoxNetworkService:
    def __init__(self, mapping: DeviceMappingService, adapter: BoxNetworkAdapter | None = None) -> None:
        self.mapping = mapping
        self._adapter = adapter
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists box_network_settings (
                    id integer primary key check (id = 1),
                    wifi_ssid text,
                    wifi_password text,
                    updated_at text not null
                )"""
            )
            con.execute(
                "insert or ignore into box_network_settings (id, updated_at) values (1, ?)",
                (now(),),
            )
            con.commit()

    def status(self) -> dict[str, Any]:
        settings = self.settings(public=False)
        status = self.adapter().status(
            mode=self.mode(),
            hostname=self.hostname(),
            wifi_configured=bool(settings.get("wifi_ssid")) and bool(settings.get("wifi_password")),
        )
        return status_to_public(status)

    def save_wifi(self, payload: dict[str, Any]) -> dict[str, Any]:
        ssid = str(payload.get("ssid") or payload.get("wifi_ssid") or "").strip()
        password = str(payload.get("password") or payload.get("wifi_password") or "")
        if not ssid:
            raise ValueError("Bitte geben Sie den WLAN-Namen ein.")
        if not password:
            raise ValueError("Bitte geben Sie das WLAN-Passwort ein.")

        mode = self.mode()
        if mode == "disabled":
            self._persist_wifi(ssid, password)
            logger.info("Box WiFi settings saved in development mode", extra={"component": "box_network", "mode": mode})
            return {
                "ok": True,
                "applied": False,
                "mode": mode,
                "message": "Development-Modus: WLAN-Daten gespeichert, keine Netzwerkänderung ausgeführt.",
                "status": self.status(),
            }

        result = self.adapter().apply_wifi(ssid, password)
        if result.get("ok"):
            self._persist_wifi(ssid, password)
            self.adapter().disable_setup_ap()
            logger.info("Box WiFi configured", extra={"component": "box_network", "mode": mode})
            return {
                "ok": True,
                "applied": True,
                "mode": mode,
                "message": "Sentero verbindet sich jetzt mit Ihrem WLAN.",
                "status": self.status(),
            }

        logger.warning("Box WiFi configuration was not applied", extra={"component": "box_network", "mode": mode})
        return {
            "ok": False,
            "applied": False,
            "mode": mode,
            "message": result.get("message") or "WLAN konnte nicht verbunden werden. Die bisherige Verbindung bleibt erhalten.",
            "status": self.status(),
        }

    def settings(self, public: bool = True) -> dict[str, Any]:
        with self.mapping.connect() as con:
            row = con.execute("select * from box_network_settings where id = 1").fetchone()
        data = dict(row) if row else {}
        if not public:
            return data
        return {
            "wifi_ssid": data.get("wifi_ssid") or "",
            "wifi_password_set": bool(data.get("wifi_password")),
        }

    def mode(self) -> str:
        raw = (os.getenv("SENTERO_BOX_SETUP_MODE") or config_str("box_setup.mode", "disabled") or "disabled").strip().lower()
        return raw if raw in VALID_MODES else "disabled"

    def hostname(self) -> str:
        return (os.getenv("SENTERO_BOX_HOSTNAME") or config_str("box_setup.hostname", DEFAULT_HOSTNAME) or DEFAULT_HOSTNAME).strip() or DEFAULT_HOSTNAME

    def adapter(self) -> BoxNetworkAdapter:
        if self._adapter:
            return self._adapter
        if self.mode() == "disabled":
            return DisabledBoxNetworkAdapter()
        return PreparedNetworkManagerAdapter()

    def _persist_wifi(self, ssid: str, password: str) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """insert into box_network_settings (id, wifi_ssid, wifi_password, updated_at)
                   values (1, ?, ?, ?)
                   on conflict(id) do update set
                     wifi_ssid = excluded.wifi_ssid,
                     wifi_password = excluded.wifi_password,
                     updated_at = excluded.updated_at""",
                (ssid, password, now()),
            )
            con.commit()


def status_to_public(status: BoxNetworkStatus) -> dict[str, Any]:
    return {
        "mode": status.mode,
        "network_ready": status.network_ready,
        "ethernet_active": status.ethernet_active,
        "wifi_active": status.wifi_active,
        "ip_address": status.ip_address,
        "setup_ap_active": status.setup_ap_active,
        "hostname": status.hostname,
        "local_url": status.local_url,
        "message": status.message,
        "wifi_configured": status.wifi_configured,
        "internet_reachable": status.internet_reachable,
    }


def local_ip_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip_address = sock.getsockname()[0]
        if ip_address and not ip_address.startswith("127."):
            return ip_address
    except OSError:
        return None
    return None
