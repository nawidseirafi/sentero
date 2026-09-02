from __future__ import annotations

import hashlib
import secrets
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from backend.paths import DATA_DIR
from backend.services.network.host_client import HostNetworkClient
from backend.services.network.models import SetupAccessPointStatus
from backend.services.network.secret_store import NetworkSecretStore


class AccessPointService:
    def __init__(self, secret_store: NetworkSecretStore | None = None, runner=None, host_client: HostNetworkClient | None = None) -> None:  # type: ignore[no-untyped-def]
        self.secret_store = secret_store or NetworkSecretStore()
        self.runner = runner or subprocess.run
        self.host = host_client or HostNetworkClient()

    def setup_ssid(self) -> str:
        if self.host.available():
            try:
                ssid = str(self.host.request("status").get("setup_ap_ssid") or "").strip()
                if ssid:
                    return ssid
            except Exception:
                pass
        suffix = hashlib.sha256(self._device_identifier().encode("utf-8")).hexdigest()[:4].upper()
        return f"Sentero-Setup-{suffix}"

    def ensure_setup_password(self) -> None:
        # Kept for backwards compatibility. Production onboarding uses a
        # temporary open local setup AP so customers do not need a printed key.
        if self.secret_store.get("setup_ap").get("password"):
            return
        self.secret_store.set("setup_ap", {"password": secrets.token_urlsafe(12)})

    def status(self, active: bool) -> SetupAccessPointStatus:
        return SetupAccessPointStatus(active=active, ssid=self.setup_ssid(), local_url="http://sentero.local:8080", local_ip_url="http://192.168.50.1:8080")

    def start(self) -> dict[str, Any]:
        if self.host.available():
            try:
                return self.host.request("start_setup_ap")
            except Exception as exc:
                return {"ok": False, "active": False, "message": str(exc)}
        self.ensure_setup_password()
        if shutil.which("nmcli") is None:
            return {"ok": False, "active": False, "message": "NetworkManager ist nicht verfügbar."}
        password = str(self.secret_store.get("setup_ap").get("password") or "")
        result = self.runner(
            ["nmcli", "device", "wifi", "hotspot", "ifname", self._wifi_device(), "ssid", self.setup_ssid(), "password", password],
            capture_output=True, text=True, timeout=30, check=False,
        )
        return {"ok": result.returncode == 0, "active": result.returncode == 0, "message": "Setup-WLAN gestartet." if result.returncode == 0 else "Setup-WLAN konnte nicht gestartet werden."}

    def stop(self) -> dict[str, Any]:
        if self.host.available():
            try:
                return self.host.request("stop_setup_ap")
            except Exception as exc:
                return {"ok": False, "active": False, "message": str(exc)}
        if shutil.which("nmcli") is None:
            return {"ok": True, "active": False, "message": "Setup-WLAN ist nicht aktiv."}
        result = self.runner(["nmcli", "connection", "down", "Hotspot"], capture_output=True, text=True, timeout=15, check=False)
        return {"ok": result.returncode in {0, 10}, "active": False, "message": "Setup-WLAN beendet."}

    def _device_identifier(self) -> str:
        for path in (Path("/etc/machine-id"), DATA_DIR / "system" / "device-id"):
            try:
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
            except OSError:
                continue
        return socket.gethostname() or "sentero"

    def _wifi_device(self) -> str:
        try:
            result = self.runner(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], capture_output=True, text=True, timeout=5, check=False)
            for line in result.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 2 and parts[1] == "wifi":
                    return parts[0]
        except Exception:
            pass
        return "wlan0"
