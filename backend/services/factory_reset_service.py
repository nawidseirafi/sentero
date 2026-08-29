from __future__ import annotations

import json
import os
import socket
from typing import Any


class FactoryResetService:
    """Request a privileged appliance factory reset through the host updater.

    The application container deliberately cannot modify NetworkManager profiles,
    persistent host directories or reboot the appliance itself.  Those steps are
    delegated to the root-owned updater over its existing Unix socket.
    """

    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = socket_path or os.getenv("SENTERO_UPDATER_SOCKET", "/run/sentero-updater/updater.sock")

    def start(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "ZURÜCKSETZEN":
            raise ValueError("Zur Bestätigung muss ZURÜCKSETZEN eingegeben werden.")
        response = self._request("factory_reset", confirm=confirmation)
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "Factory Reset konnte nicht gestartet werden."))
        return response

    def status(self) -> dict[str, Any]:
        response = self._request("factory_reset_status")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "Factory-Reset-Status ist nicht verfügbar."))
        return response

    def _request(self, action: str, *, timeout: float = 4.0, **payload: Any) -> dict[str, Any]:
        request = {"action": action, **payload}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(self.socket_path)
            client.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
        raw = b"".join(chunks).split(b"\n", 1)[0]
        if not raw:
            raise RuntimeError("Der Host-Dienst der Box hat nicht geantwortet.")
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError("Ungültige Antwort des Host-Dienstes.")
        return response
