from __future__ import annotations

import json
import os
import socket
from typing import Any


class HostNetworkClient:
    """Small JSON client for the privileged host-side Sentero network agent."""

    def __init__(self, socket_path: str | None = None) -> None:
        self.socket_path = socket_path or os.getenv("SENTERO_NETWORK_SOCKET", "/run/sentero-network/network.sock")

    def available(self) -> bool:
        return os.path.exists(self.socket_path)

    def request(self, action: str, **payload: Any) -> dict[str, Any]:
        request = {"action": action, **payload}
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(60)
            client.connect(self.socket_path)
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
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
            raise RuntimeError("Der Netzwerkdienst der Box hat nicht geantwortet.")
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError("Ungültige Antwort des Netzwerkdienstes.")
        return response
