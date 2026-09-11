"""Host recovery regressions; no actual radio, Docker or elapsed-time waits."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch


def load_agent():
    path = Path(__file__).resolve().parents[1] / "box/sentero-network/sentero_network.py"
    spec = importlib.util.spec_from_file_location("host_network_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.agent = load_agent()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent._RECOVERY_MARKER = Path(self.tmp.name) / "automatic"
        self.now = 0
        self.snapshot = dict(ready=None, pending=False, ap_device=None,
                             device="wlan0", profiles=["home-uuid"])
        self.agent.time = Mock(monotonic=lambda: self.now)
        self.agent.recovery_snapshot = Mock(side_effect=lambda **kwargs: dict(self.snapshot))
        self.agent.start_setup_ap = Mock(return_value={"ok": True})
        self.agent.stop_setup_ap = Mock(return_value={"ok": True})
        self.agent.post_connect_stack = Mock()
        self.agent.run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.recovery = self.agent._RECOVERY
        self.recovery.enable()

    def step(self, seconds):
        self.now = seconds
        self.recovery.step()

    def test_a_lan_ready_immediately(self):
        self.snapshot["ready"] = ("ethernet", "eth0", "192.168.1.2")
        self.step(0)
        self.agent.start_setup_ap.assert_not_called()
        self.agent.stop_setup_ap.assert_called_once()
        self.assertIn("compose", self.agent.run.call_args.args[0])
        self.assertFalse(self.recovery.enabled)

    def test_b_wifi_ready_immediately(self):
        self.snapshot["ready"] = ("wifi", "wlan0", "192.168.1.2")
        self.step(0)
        self.agent.start_setup_ap.assert_not_called()
        self.assertIn("compose", self.agent.run.call_args.args[0])

    def test_c_dhcp_after_sixty_seconds_never_disconnected(self):
        self.snapshot["pending"] = True
        for second in range(60):
            self.step(second)
        self.agent.start_setup_ap.assert_not_called()
        self.agent.stop_setup_ap.assert_not_called()
        self.agent.run.assert_not_called()
        self.snapshot["ready"] = ("wifi", "wlan0", "192.168.1.2")
        self.step(60)
        self.agent.start_setup_ap.assert_not_called()
        self.assertIn("compose", self.agent.run.call_args.args[0])

    def test_deadline_never_turns_pending_into_failure(self):
        self.snapshot["pending"] = True
        for second in (0, 120, 600, 3600):
            self.step(second)
        self.agent.start_setup_ap.assert_not_called()
        self.agent.run.assert_not_called()

    def fail_to_ap(self):
        for second in (0, 29, 30, 35, 64, 65):
            self.step(second)

    def test_d_failure_has_grace_and_one_nonblocking_retry(self):
        self.fail_to_ap()
        self.agent.run.assert_called_once_with(
            ["nmcli", "--wait", "0", "connection", "up", "uuid", "home-uuid", "ifname", "wlan0"], 5)
        self.agent.start_setup_ap.assert_called_once_with(automatic=True)
        self.assertEqual(self.recovery.next_attempt, 185)

    def test_e_no_saved_network_starts_ap_after_grace(self):
        self.snapshot["profiles"] = []
        self.step(0)
        self.step(29)
        self.agent.start_setup_ap.assert_not_called()
        self.step(30)
        self.agent.start_setup_ap.assert_called_once()
        self.agent.run.assert_not_called()

    def test_f_recovery_and_backoff(self):
        self.fail_to_ap()
        self.snapshot["ap_device"] = "wlan0"
        self.step(184)
        self.agent.stop_setup_ap.assert_not_called()
        self.step(185)
        self.agent.stop_setup_ap.assert_called_once()
        self.snapshot.update(ap_device=None, pending=True)
        self.step(240)
        self.snapshot["ready"] = ("wifi", "wlan0", "192.168.1.2")
        self.step(245)
        self.agent.start_setup_ap.assert_called_once()
        self.assertFalse(self.recovery.enabled)

    def test_repeated_failures_increase_bounded_backoff(self):
        self.fail_to_ap()
        self.snapshot["ap_device"] = "wlan0"
        self.step(185)
        self.snapshot["ap_device"] = None
        self.step(190)
        self.step(220)
        self.assertEqual(self.recovery.next_attempt, 460)
        self.assertLessEqual(self.recovery.backoff, self.recovery.MAX_BACKOFF)

    def test_setup_station_prevents_radio_switch(self):
        self.snapshot["ap_device"] = "wlan0"
        self.agent.run.return_value.stdout = "Station aa:bb:cc:dd:ee:ff"
        self.step(600)
        self.agent.stop_setup_ap.assert_not_called()
        self.assertEqual(self.recovery.next_attempt, 720)

    def test_failed_station_query_preserves_setup(self):
        self.snapshot["ap_device"] = "wlan0"
        self.agent.run.return_value.returncode = 1
        self.step(600)
        self.agent.stop_setup_ap.assert_not_called()

    def test_g_no_internet_is_irrelevant(self):
        self.agent.connectivity = Mock(side_effect=AssertionError("Internet probe"))
        self.test_b_wifi_ready_immediately()
        self.agent.connectivity.assert_not_called()

    def test_h_lan_wins_even_with_ap_station_and_backoff(self):
        self.snapshot.update(ap_device="wlan0", ready=("ethernet", "eth0", "192.168.1.3"))
        self.recovery.next_attempt = 1000
        self.step(1)
        self.agent.stop_setup_ap.assert_called_once()
        self.assertIn("compose", self.agent.run.call_args.args[0])

    def test_final_recheck_protects_new_activation(self):
        self.recovery.retried = True
        self.recovery.failed_since = 0
        self.agent.recovery_snapshot.side_effect = [dict(self.snapshot), dict(self.snapshot, pending=True)]
        self.step(30)
        self.agent.start_setup_ap.assert_not_called()

    def test_manual_onboarding_cancels_recovery(self):
        self.agent.connect_wifi = Mock(return_value={"ok": True})
        result = self.agent.handle({"action": "connect_wifi", "ssid": "Home", "password": "secret"})
        self.assertTrue(result["ok"])
        self.agent.connect_wifi.assert_called_once_with("Home", "secret")
        self.assertFalse(self.recovery.enabled)
        self.assertFalse(self.agent._RECOVERY_MARKER.exists())

    def test_stack_failure_keeps_recovery_for_retry(self):
        self.snapshot["ready"] = ("ethernet", "eth0", "10.0.0.2")
        self.agent.run.return_value.returncode = 1
        self.step(0)
        self.assertTrue(self.recovery.enabled)
        self.agent.run.return_value.returncode = 0
        self.step(5)
        self.assertFalse(self.recovery.enabled)
        self.agent.start_setup_ap.assert_not_called()

    def test_failed_ap_start_is_rate_limited(self):
        self.snapshot["profiles"] = []
        self.agent.start_setup_ap.return_value = {"ok": False}
        for second in (0, 30, 35, 60, 149):
            self.step(second)
        self.agent.start_setup_ap.assert_called_once()
        self.step(150)
        self.assertEqual(self.agent.start_setup_ap.call_count, 2)

    def test_retry_rechecks_for_nm_activation(self):
        self.recovery.failed_since = 0
        self.agent.recovery_snapshot.side_effect = [dict(self.snapshot), dict(self.snapshot, pending=True)]
        self.step(30)
        self.agent.run.assert_not_called()
        self.agent.start_setup_ap.assert_not_called()

    def test_boot_request_is_idempotent_and_does_not_run_network_commands(self):
        self.recovery.next_attempt = 600
        for _ in range(2):
            self.assertTrue(self.agent.handle({"action": "ensure_network"})["ok"])
        self.assertEqual(self.recovery.next_attempt, 600)
        self.agent.run.assert_not_called()
        self.agent.start_setup_ap.assert_not_called()

    def test_busy_mutation_returns_without_waiting(self):
        held = threading.Event()
        release = threading.Event()
        def hold():
            with self.agent._NETWORK_LOCK:
                held.set()
                release.wait(2)
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            self.assertTrue(held.wait(1))
            self.assertFalse(self.agent.handle({"action": "start_setup_ap"})["ok"])
            self.agent.start_setup_ap.assert_not_called()
        finally:
            release.set()
            thread.join()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.agent = load_agent()
        self.agent.connection_rows = Mock(return_value=[("wlan0", "wifi", "connected", "Home")])
        self.agent.device_ipv4 = Mock(return_value=None)
        self.state = 70
        def run(args, timeout):
            text = ""
            if "GENERAL.STATE" in args:
                text = f"{self.state} (state)"
            elif "UUID,TYPE" in args:
                text = "home:802-11-wireless\nap:802-11-wireless\n"
            elif "802-11-wireless.mode" in args:
                text = "ap" if args[-1] == "ap" else "infrastructure"
            return subprocess.CompletedProcess(args, 0, text, "")
        self.agent.run = Mock(side_effect=run)

    def test_all_activation_states_including_ipv6_activated_protected(self):
        for self.state in (40, 50, 60, 70, 80, 90, 100, 110, -1):
            with self.subTest(state=self.state):
                self.assertTrue(self.agent.recovery_snapshot()["pending"])

    def test_terminal_failure_and_saved_client_profiles(self):
        self.state = 120
        result = self.agent.recovery_snapshot()
        self.assertFalse(result["pending"])
        self.assertEqual(result["profiles"], ["home"])

    def test_lan_preferred_and_setup_address_excluded(self):
        self.agent.connection_rows.return_value = [
            ("wlan0", "wifi", "connected", "sentero-setup-ap"),
            ("wlan1", "wifi", "connected", "Home"),
            ("eth0", "ethernet", "connected (externally)", "Wired")]
        self.agent.device_ipv4.side_effect = lambda dev: {"eth0": "10.0.0.2", "wlan1": "10.0.0.3", "wlan0": "192.168.50.1"}[dev]
        result = self.agent.recovery_snapshot()
        self.assertEqual(result["ready"], ("ethernet", "eth0", "10.0.0.2"))
        self.agent.run.assert_not_called()


class HostIntegrationTests(unittest.TestCase):
    def test_boot_only_hands_off_recovery_over_socket(self):
        with tempfile.TemporaryDirectory(prefix="sn-", dir="/tmp") as directory:
            root = Path(directory)
            sock = root / "net.sock"
            docker = root / "docker"
            docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$SENTERO_BOX_DIR/docker-call"\n')
            docker.chmod(0o755)
            requests = []
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(sock))
                server.listen(1)
                server.settimeout(5)
                def respond():
                    conn, _ = server.accept()
                    with conn:
                        requests.append(json.loads(conn.recv(4096)))
                        conn.sendall(b'{"ok":true,"pending":true}\n')
                thread = threading.Thread(target=respond)
                thread.start()
                env = dict(os.environ, SENTERO_BOX_DIR=str(root),
                           SENTERO_DOCKER_BIN=str(docker), SENTERO_PYTHON_BIN=sys.executable,
                           SENTERO_NETWORK_SOCKET=str(sock))
                script = Path(__file__).resolve().parents[1] / "box/scripts/start-box.sh"
                result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=10)
                thread.join(6)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(requests, [{"action": "ensure_network"}])
            self.assertEqual((root / "docker-call").read_text().strip(), "compose up -d --no-deps sentero")

    def test_actual_ap_function_rechecks_before_activation(self):
        agent = load_agent()
        agent.wifi_device = Mock(return_value="wlan0")
        agent.setup_ssid = Mock(return_value="Sentero-Test")
        for name in ("_write_captive_dns_config", "_ensure_captive_http_server",
                     "_disable_captive_redirect", "_remove_captive_dns_config"):
            setattr(agent, name, Mock())
        agent.run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        agent.recovery_snapshot = Mock(return_value={"ready": None, "pending": True})
        self.assertFalse(agent.start_setup_ap(automatic=True)["ok"])
        for call in agent.run.call_args_list:
            self.assertNotEqual(call.args[0][:3], ["nmcli", "connection", "up"])

    def test_connect_wifi_local_success_without_internet(self):
        agent = load_agent()
        agent.wifi_device = Mock(return_value="wlan0")
        agent.stop_setup_ap = Mock(return_value={"ok": True})
        agent.start_setup_ap = Mock()
        agent.post_connect_stack = Mock()
        agent.run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        agent.status = Mock(return_value={"network_ready": True, "wifi_active": True,
                                         "wifi_ip_address": "10.0.0.2", "internet_reachable": False})
        result = agent.connect_wifi("Home", "secret")
        self.assertTrue(result["ok"])
        agent.start_setup_ap.assert_not_called()
        agent.run.assert_called_once_with(["nmcli", "device", "wifi", "connect", "Home",
                                          "ifname", "wlan0", "password", "secret"], 60)

    def test_connect_failure_preserves_lan_and_restores_offline_onboarding(self):
        for lan in (True, False):
            with self.subTest(lan=lan):
                agent = load_agent()
                agent.wifi_device = Mock(return_value="wlan0")
                agent.stop_setup_ap = Mock()
                agent.start_setup_ap = Mock()
                agent.run = Mock(return_value=subprocess.CompletedProcess([], 4, "", ""))
                agent.status = Mock(return_value={"network_ready": lan})
                self.assertFalse(agent.connect_wifi("Home", "wrong")["ok"])
                self.assertEqual(agent.start_setup_ap.call_count, 0 if lan else 1)


if __name__ == "__main__":
    unittest.main()
