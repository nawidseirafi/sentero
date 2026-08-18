from __future__ import annotations

import csv
import io
import shutil
import subprocess
from typing import Any

from backend.logging_config import get_logger
from backend.services.network.models import WifiNetwork

logger = get_logger(__name__)


class WifiService:
    def __init__(self, runner=None) -> None:  # type: ignore[no-untyped-def]
        self.runner = runner or subprocess.run

    def available(self) -> bool:
        return shutil.which("nmcli") is not None and bool(self._wifi_devices())

    def ap_supported(self) -> bool:
        devices = self._wifi_devices()
        if not devices:
            return False
        if shutil.which("iw") is None:
            return True
        try:
            result = self.runner(["iw", "list"], capture_output=True, text=True, timeout=5, check=False)
            return "* AP" in result.stdout or "\n\t\t * AP" in result.stdout
        except Exception:
            return True

    def scan(self) -> list[WifiNetwork]:
        if shutil.which("nmcli") is None:
            return []
        try:
            self.runner(["nmcli", "device", "wifi", "rescan"], capture_output=True, text=True, timeout=15, check=False)
            result = self.runner(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception:
            logger.exception("WiFi scan failed", extra={"component": "network"})
            return []
        networks: dict[str, WifiNetwork] = {}
        for row in csv.reader(io.StringIO(result.stdout), delimiter=":", escapechar="\\"):
            if not row:
                continue
            ssid = (row[0] if len(row) > 0 else "").strip()
            if not ssid:
                continue
            try:
                signal = max(0, min(100, int(row[1] if len(row) > 1 else 0)))
            except ValueError:
                signal = 0
            secured = bool((row[2] if len(row) > 2 else "").strip())
            existing = networks.get(ssid)
            if not existing or signal > existing.signal:
                networks[ssid] = WifiNetwork(ssid=ssid, signal=signal, secured=secured)
        return sorted(networks.values(), key=lambda item: item.signal, reverse=True)

    def connect(self, ssid: str, password: str) -> dict[str, Any]:
        if shutil.which("nmcli") is None:
            return {"ok": False, "message": "NetworkManager ist nicht verfügbar."}
        try:
            result = self.runner(
                ["nmcli", "device", "wifi", "connect", ssid, "password", password],
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except Exception as exc:
            logger.exception("WiFi connect failed", extra={"component": "network", "ssid": ssid})
            return {"ok": False, "message": exc.__class__.__name__}
        if result.returncode == 0:
            return {"ok": True, "message": "WLAN ist verbunden."}
        logger.warning("WiFi connect rejected", extra={"component": "network", "ssid": ssid, "returncode": result.returncode})
        return {"ok": False, "message": "WLAN konnte nicht verbunden werden. Bitte prüfen Sie das Passwort."}

    def _wifi_devices(self) -> list[str]:
        if shutil.which("nmcli") is None:
            return []
        try:
            result = self.runner(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"], capture_output=True, text=True, timeout=5, check=False)
        except Exception:
            return []
        devices = []
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1] == "wifi":
                devices.append(parts[0])
        return devices

