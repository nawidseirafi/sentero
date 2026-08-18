from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from backend.logging_config import get_logger
from backend.services.network.models import CellularStatus

logger = get_logger(__name__)


class CellularService:
    def __init__(self, runner=None) -> None:  # type: ignore[no-untyped-def]
        self.runner = runner or subprocess.run

    def status(self) -> CellularStatus:
        modem = self._first_modem()
        if not modem:
            return CellularStatus()
        details = self._modem_details(modem)
        sim_present = "sim-missing" not in details.lower() and "sim" in details.lower()
        registered = any(term in details.lower() for term in ("registered", "home", "roaming"))
        provider = self._match(details, r"operator name:\s*'([^']+)'") or self._match(details, r"operator name:\s*(.+)")
        signal = self._signal_percent(modem)
        connected = "connected" in details.lower()
        ip_present = any(term in details.lower() for term in ("ip4 config", "address:"))
        return CellularStatus(
            available=True,
            sim_present=sim_present,
            registered=registered,
            provider=provider.strip() if provider else None,
            signal_percent=signal,
            connected=connected,
            ip_present=ip_present,
        )

    def connect(self, apn: str | None = None, username: str | None = None, password: str | None = None, pin: str | None = None) -> dict[str, Any]:
        if shutil.which("nmcli") is None:
            return {"ok": False, "message": "NetworkManager ist nicht verfügbar."}
        args = ["nmcli", "connection", "up", "type", "gsm"]
        if apn:
            args.extend(["apn", apn])
        if username:
            args.extend(["user", username])
        if password:
            args.extend(["password", password])
        if pin:
            logger.info("SIM PIN supplied for cellular connect", extra={"component": "network", "pin_supplied": True})
        result = self.runner(args, capture_output=True, text=True, timeout=45, check=False)
        return {"ok": result.returncode == 0, "message": "Mobilfunk ist verbunden." if result.returncode == 0 else "Mobilfunk konnte nicht verbunden werden."}

    def disconnect(self) -> dict[str, Any]:
        if shutil.which("nmcli") is None:
            return {"ok": True, "message": "Mobilfunk ist nicht aktiv."}
        result = self.runner(["nmcli", "radio", "wwan", "off"], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode == 0:
            self.runner(["nmcli", "radio", "wwan", "on"], capture_output=True, text=True, timeout=15, check=False)
        return {"ok": result.returncode == 0, "message": "Mobilfunk-Fallback beendet."}

    def _first_modem(self) -> str | None:
        if shutil.which("mmcli") is None:
            return None
        try:
            result = self.runner(["mmcli", "-L"], capture_output=True, text=True, timeout=5, check=False)
        except Exception:
            return None
        match = re.search(r"/Modem/(\d+)", result.stdout)
        return match.group(1) if match else None

    def _modem_details(self, modem: str) -> str:
        try:
            result = self.runner(["mmcli", "-m", modem], capture_output=True, text=True, timeout=10, check=False)
            return result.stdout
        except Exception:
            logger.exception("ModemManager status failed", extra={"component": "network"})
            return ""

    def _signal_percent(self, modem: str) -> int | None:
        try:
            result = self.runner(["mmcli", "-m", modem, "--signal-get"], capture_output=True, text=True, timeout=10, check=False)
        except Exception:
            return None
        match = re.search(r"(\d+)%", result.stdout)
        if not match:
            return None
        return max(0, min(100, int(match.group(1))))

    def _match(self, text: str, pattern: str) -> str | None:
        match = re.search(pattern, text)
        return match.group(1).strip() if match else None

