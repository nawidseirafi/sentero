from __future__ import annotations

from tests.fakes import NoNetworkSensorSource

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.network.models import CellularStatus, ConnectionType, ConnectivityCheck, NetworkStatusCode, WifiNetwork
from backend.services.network.secret_store import NetworkSecretStore
from backend.services.network.service import NetworkService
from backend.services.notification_service import NotificationProvider, NotificationService


class FakeWifi:
    def __init__(self, connect_ok: bool = True) -> None:
        self.connect_ok = connect_ok

    def available(self) -> bool:
        return True

    def ap_supported(self) -> bool:
        return True

    def scan(self) -> list[WifiNetwork]:
        return [WifiNetwork("FRITZ!Box 7590", 82, True)]

    def connect(self, ssid: str, password: str) -> dict:
        return {"ok": self.connect_ok, "message": "ok" if self.connect_ok else "bad password"}


class FakeAp:
    def __init__(self) -> None:
        self.active = False
        self.password_created = False

    def setup_ssid(self) -> str:
        return "Sentero-Setup-7F3A"

    def ensure_setup_password(self) -> None:
        self.password_created = True

    def status(self, active: bool):
        from backend.services.network.models import SetupAccessPointStatus

        return SetupAccessPointStatus(active=active, ssid=self.setup_ssid())

    def start(self) -> dict:
        self.active = True
        return {"ok": True, "active": True, "message": "started"}

    def stop(self) -> dict:
        self.active = False
        return {"ok": True, "active": False, "message": "stopped"}


class FakeCellular:
    def __init__(self, status: CellularStatus | None = None) -> None:
        self._status = status or CellularStatus(available=True, sim_present=True, registered=True, provider="Telekom", signal_percent=76, connected=True, ip_present=True)
        self.connected = False

    def status(self) -> CellularStatus:
        return self._status

    def connect(self, **kwargs) -> dict:
        self.connected = True
        return {"ok": True, "message": "Mobilfunk ist verbunden."}

    def disconnect(self) -> dict:
        self.connected = False
        return {"ok": True, "message": "Mobilfunk-Fallback beendet."}


class FakeConnectivity:
    def __init__(self, status: NetworkStatusCode = NetworkStatusCode.ONLINE_WIFI) -> None:
        self.status = status

    def check(self, connection_type: ConnectionType = ConnectionType.NONE, probes=None) -> ConnectivityCheck:
        internet = self.status in {NetworkStatusCode.ONLINE_WIFI, NetworkStatusCode.ONLINE_ETHERNET, NetworkStatusCode.ONLINE_CELLULAR}
        return ConnectivityCheck(True, True, True, internet, internet, self.status, connection_type, "" if internet else "internet_unreachable")


class NetworkServiceTests(unittest.TestCase):
    def test_first_boot_without_network_starts_protected_setup_ap(self) -> None:
        service, ap, _ = self.service(connectivity=FakeConnectivity(NetworkStatusCode.OFFLINE))
        with patch.dict(os.environ, {"SENTERO_BOX_SETUP_MODE": "auto"}, clear=False):
            result = service.ensure_first_boot_setup()
            status = service.status()

        self.assertTrue(result["ok"])
        self.assertTrue(ap.password_created)
        self.assertTrue(status["setup_ap_active"])
        self.assertEqual(status["status"], "OFFLINE")
        self.assertNotIn("password", str(result).lower())

    def test_wifi_scan_and_successful_connect_stops_setup_ap_without_leaking_secret(self) -> None:
        service, _, db_path = self.service()
        with patch.dict(os.environ, {"SENTERO_BOX_SETUP_MODE": "disabled"}, clear=False):
            service.start_setup_ap()
            scan = service.wifi_networks()
            result = service.connect_wifi({"ssid": "FRITZ!Box 7590", "password": "wifi-secret"})

        self.assertEqual(scan["networks"][0]["ssid"], "FRITZ!Box 7590")
        self.assertTrue(result["ok"])
        self.assertFalse(result["status"]["setup_ap_active"])
        self.assertNotIn("wifi-secret", str(result))
        self.assertNotIn("wifi-secret", db_path.read_text(encoding="utf-8", errors="ignore"))

    def test_wrong_wifi_password_keeps_setup_ap_active_and_does_not_persist(self) -> None:
        service, _, _ = self.service(wifi=FakeWifi(connect_ok=False))
        with patch.dict(os.environ, {"SENTERO_BOX_SETUP_MODE": "auto"}, clear=False):
            service.start_setup_ap()
            result = service.connect_wifi({"ssid": "FRITZ!Box 7590", "password": "wrong"})
            settings = service.settings()

        self.assertFalse(result["ok"])
        self.assertTrue(result["status"]["setup_ap_active"])
        self.assertFalse(settings["configured"])

    def test_cellular_status_models_missing_sim(self) -> None:
        service, _, _ = self.service(cellular=FakeCellular(CellularStatus(available=True, sim_present=False, registered=False)))

        status = service.cellular_status()

        self.assertTrue(status["available"])
        self.assertFalse(status["sim_present"])
        self.assertFalse(status["registered"])

    def test_failover_uses_thresholds_and_recovers_without_flapping(self) -> None:
        service, _, _ = self.service()
        checks = [{"connection": "wifi", "ok": False} for _ in range(2)]
        checks.extend([{"connection": "wifi", "ok": False}, {"connection": "wifi", "ok": True, "preferred_ok": True}, {"connection": "wifi", "ok": True, "preferred_ok": True}, {"connection": "wifi", "ok": True, "preferred_ok": True}])

        result = service.failover_test(checks)

        self.assertEqual(result["switches"], ["cellular_fallback_started", "cellular_fallback_stopped"])
        self.assertEqual(result["final_connection"], "wifi")

    def service(self, wifi=None, cellular=None, connectivity=None):
        tmp = Path(tempfile.mkdtemp(dir="/private/tmp"))
        db_path = tmp / "sentero.db"
        mapping = DeviceMappingService(database_path=db_path)
        mapping.sensor_source = NoNetworkSensorSource()
        store = NetworkSecretStore(tmp / "network-secrets.json")
        ap = FakeAp()
        service = NetworkService(mapping, wifi=wifi or FakeWifi(), access_point=ap, cellular=cellular or FakeCellular(), connectivity=connectivity or FakeConnectivity(), secret_store=store)
        return service, ap, db_path


class CapturingProvider(NotificationProvider):
    channel = "email"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, contact: dict, title: str, text: str, config: dict) -> None:
        self.sent.append(text)


class OfflineQueueTests(unittest.TestCase):
    def test_offline_notifications_are_persisted_and_sent_after_recovery(self) -> None:
        tmp = Path(tempfile.mkdtemp(dir="/private/tmp"))
        mapping = DeviceMappingService(database_path=tmp / "sentero.db")
        mapping.sensor_source = NoNetworkSensorSource()
        provider = CapturingProvider()
        offline = FakeConnectivity(NetworkStatusCode.OFFLINE)
        service = NotificationService(mapping, connectivity=offline)
        service.providers["email"] = provider
        self._seed_email(mapping)

        service._send_with_log({"id": 1, "email": "a@example.test"}, "email", "red", "Warnung", "Text", fallback=False)
        self.assertEqual(provider.sent, [])
        self.assertEqual(service.queue_status()["queue"]["pending"], 1)

        service.connectivity = FakeConnectivity(NetworkStatusCode.ONLINE_WIFI)
        result = service.process_pending_queue()

        self.assertEqual(result["sent"], 1)
        self.assertIn("Ursprünglicher Zeitpunkt", provider.sent[0])

    def _seed_email(self, mapping: DeviceMappingService) -> None:
        with mapping.connect() as con:
            con.execute(
                """insert into notification_channel_settings (channel, enabled, config_json, created_at, updated_at)
                   values ('email', 1, '{"smtp_host":"smtp.example.test","smtp_user":"user","smtp_password":"secret"}', ?, ?)
                   on conflict(channel) do update set enabled = excluded.enabled, config_json = excluded.config_json, updated_at = excluded.updated_at""",
                (now(), now()),
            )
            con.commit()


if __name__ == "__main__":
    unittest.main()
