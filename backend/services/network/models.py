from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ConnectionType(str, Enum):
    ETHERNET = "ethernet"
    WIFI = "wifi"
    CELLULAR = "cellular"
    NONE = "none"


class NetworkStatusCode(str, Enum):
    OFFLINE = "OFFLINE"
    LOCAL_ONLY = "LOCAL_ONLY"
    ONLINE_ETHERNET = "ONLINE_ETHERNET"
    ONLINE_WIFI = "ONLINE_WIFI"
    ONLINE_CELLULAR = "ONLINE_CELLULAR"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class NetworkCapabilities:
    ethernet: bool = False
    wifi: bool = False
    wifi_ap: bool = False
    cellular: bool = False

    def public(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    secured: bool

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CellularStatus:
    available: bool = False
    sim_present: bool = False
    registered: bool = False
    provider: str | None = None
    signal_percent: int | None = None
    connected: bool = False
    ip_present: bool = False
    internet_reachable: bool | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConnectivityCheck:
    link: bool
    default_route: bool
    dns: bool
    internet: bool
    sentero_mailserver: bool
    status: NetworkStatusCode
    connection_type: ConnectionType
    reason: str = ""

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["connection_type"] = self.connection_type.value
        return data


@dataclass(frozen=True)
class SetupAccessPointStatus:
    active: bool
    ssid: str
    local_url: str = "http://sentero.local:8080"
    local_ip_url: str = "http://192.168.50.1:8080"

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NetworkStatus:
    status: NetworkStatusCode
    active_connection: ConnectionType
    network_ready: bool
    internet_reachable: bool
    ethernet_active: bool
    wifi_active: bool
    cellular_active: bool
    setup_ap_active: bool
    wifi_configured: bool
    cellular: CellularStatus
    capabilities: NetworkCapabilities
    hostname: str
    local_url: str
    customer_message: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def public(self, diagnostics: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status.value,
            "active_connection": self.active_connection.value,
            "network_ready": self.network_ready,
            "internet_reachable": self.internet_reachable,
            "ethernet_active": self.ethernet_active,
            "wifi_active": self.wifi_active,
            "cellular_active": self.cellular_active,
            "setup_ap_active": self.setup_ap_active,
            "wifi_configured": self.wifi_configured,
            "cellular": self.cellular.public(),
            "capabilities": self.capabilities.public(),
            "hostname": self.hostname,
            "local_url": self.local_url,
            "message": self.customer_message,
        }
        if diagnostics:
            data["diagnostics"] = self.diagnostics
        return data


@dataclass(frozen=True)
class FailoverConfig:
    enabled: bool = True
    failure_threshold: int = 3
    recovery_threshold: int = 3
    check_interval_seconds: int = 30
    switch_cooldown_seconds: int = 120

