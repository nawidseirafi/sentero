from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NETWORK_SOCKET = Path('/run/sentero-network/network.sock')


class SystemStatusService:
    """Read appliance health from the privileged host agent.

    The application container intentionally has no Docker socket. The host
    network agent already runs privileged and exposes a tiny local Unix-socket
    API, so service health stays read-only and isolated from Docker control.
    """

    def _host_request(self, action: str, timeout: float = 3.0) -> dict[str, Any]:
        if not NETWORK_SOCKET.exists():
            return {"ok": False, "message": "Hostdienst ist nicht erreichbar."}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(str(NETWORK_SOCKET))
                client.sendall((json.dumps({"action": action}) + "\n").encode("utf-8"))
                data = b""
                while b"\n" not in data and len(data) < 262144:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            if not data:
                return {"ok": False, "message": "Hostdienst antwortet nicht."}
            payload = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
            return payload if isinstance(payload, dict) else {"ok": False}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"ok": False, "message": "Hoststatus konnte nicht gelesen werden."}

    def status(self) -> dict[str, Any]:
        payload = self._host_request("system_status")
        if payload.get("ok"):
            return payload
        # A compact, stable response lets the UI stay calm even during a host
        # agent restart. It will refresh automatically on the next poll.
        return {
            "ok": False,
            "overall": "warning",
            "summary": "Systemstatus wird gerade aktualisiert.",
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "services": [],
            "network": {},
        }
