from __future__ import annotations

import socket
from typing import Any

from backend.config import config_str
from backend.services.network.models import ConnectionType, ConnectivityCheck, NetworkStatusCode


class ConnectivityService:
    def check(self, connection_type: ConnectionType = ConnectionType.NONE, probes: dict[str, Any] | None = None) -> ConnectivityCheck:
        probes = probes or {}
        link = bool(probes.get("link", True))
        default_route = bool(probes.get("default_route", self._has_default_route()))
        dns = bool(probes.get("dns", self._dns_resolves()))
        internet = bool(probes.get("internet", self._tcp_connect("1.1.1.1", 443)))
        mail = bool(probes.get("sentero_mailserver", self._mailserver_reachable()))

        if not link:
            status = NetworkStatusCode.OFFLINE
            reason = "network_link_down"
        elif not default_route or not dns:
            status = NetworkStatusCode.LOCAL_ONLY
            reason = "local_network_only"
        elif not internet:
            status = NetworkStatusCode.LOCAL_ONLY
            reason = "internet_unreachable"
        elif not mail:
            status = NetworkStatusCode.DEGRADED
            reason = "sentero_mailserver_unreachable"
        else:
            status = {
                ConnectionType.ETHERNET: NetworkStatusCode.ONLINE_ETHERNET,
                ConnectionType.WIFI: NetworkStatusCode.ONLINE_WIFI,
                ConnectionType.CELLULAR: NetworkStatusCode.ONLINE_CELLULAR,
            }.get(connection_type, NetworkStatusCode.DEGRADED)
            reason = ""

        return ConnectivityCheck(
            link=link,
            default_route=default_route,
            dns=dns,
            internet=internet,
            sentero_mailserver=mail,
            status=status,
            connection_type=connection_type,
            reason=reason,
        )

    def _has_default_route(self) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("1.1.1.1", 443))
                return bool(sock.getsockname()[0])
        except OSError:
            return False

    def _dns_resolves(self) -> bool:
        try:
            socket.getaddrinfo("sentero.de", 443)
            return True
        except OSError:
            return False

    def _mailserver_reachable(self) -> bool:
        host = config_str("network.mail_probe_host", "") or "sentero.de"
        try:
            return self._tcp_connect(host, int(config_str("network.mail_probe_port", "443") or "443"))
        except ValueError:
            return self._tcp_connect(host, 443)

    def _tcp_connect(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            return False

