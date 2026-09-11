import importlib.util
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api import routes

from backend.services import update_service as updates


class InstallRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state.json"
        self.service = updates.SenteroUpdateService()
        for mocked in (
            patch.object(updates, "STATE_FILE", self.state),
            patch.object(self.service, "version", return_value={"version": "0.4.6"}),
            patch.object(self.service, "execution_mode", return_value="appliance"),
            patch.object(self.service, "channel", return_value="stable"),
            patch.object(self.service, "manifest_url", return_value="https://example.invalid/latest.json"),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.latest = {"latest_version": "0.4.7", "channel": "stable", "appliance": {"bundle_url": "https://example.invalid/bundle.zip"}}
        self.write({
            "status": "update_available", "state": "update_available",
            "latest": self.latest, "update_available": True,
            "steps": [{"key": "done", "status": "success"}],
            "install": {"target_version": "0.4.6", "status": "success", "steps": [{"status": "success"}]},
        })

    def write(self, state):
        self.state.write_text(json.dumps(state))

    def test_no_install_request_never_uses_old_success_as_progress(self):
        with patch.object(self.service, "_request_appliance_updater") as request:
            state = self.service.status()
        request.assert_not_called()
        self.assertEqual(state["status"], "update_available")
        self.assertEqual(state["install"], {"status": "idle", "steps": []})
        self.assertEqual(state["steps"], [])
        self.assertEqual(state["previous_install"]["target_version"], "0.4.6")

    def test_check_resets_stale_install_progress_persistently(self):
        with patch.object(self.service, "_load_manifest", return_value=self.latest):
            self.service.check_for_updates()
        state = json.loads(self.state.read_text())
        self.assertEqual(state["status"], "update_available")
        self.assertEqual(state["install"]["status"], "idle")
        self.assertEqual(state["steps"], [])

    def test_real_socket_receives_target_after_state_is_persisted(self):
        path = str(Path(self.tmp.name) / "updater.sock")
        received = []
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path)
            server.listen(1)
            server.settimeout(3)
            def serve():
                connection, _ = server.accept()
                with connection:
                    received.append(json.loads(connection.recv(65536)))
                    received.append(json.loads(self.state.read_text()))
                    connection.sendall(b'{"ok":true,"accepted":true}\n')
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                with patch.dict("os.environ", {"SENTERO_UPDATER_SOCKET": path}):
                    app = FastAPI()
                    app.include_router(routes.router)
                    services = SimpleNamespace(update=self.service, auth=Mock())
                    services.auth.user_from_request.return_value = {"role": "owner", "email": "owner@example.invalid"}
                    with patch.object(routes, "get_services", return_value=services), TestClient(app) as client:
                        response = client.post("/api/sentero/system/update/install", json={})
                    self.assertEqual(response.status_code, 200)
                    result = response.json()
            finally:
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result["accepted"])
        self.assertEqual(received[0]["target_version"], "0.4.7")
        self.assertEqual(received[1]["install"]["target_version"], "0.4.7")
        self.assertEqual(received[1]["install"]["status"], "running")
        self.assertTrue(received[1]["install"]["started_at"])

    def test_connect_and_send_failures_are_persisted_as_failed(self):
        for operation in ("connect", "sendall"):
            with self.subTest(operation=operation):
                self.write({"status": "update_available", "latest": self.latest, "update_available": True})
                client = Mock()
                getattr(client, operation).side_effect = TimeoutError("transport failed")
                with patch.object(updates.socket, "socket", return_value=client):
                    result = self.service.install_update()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["install"]["status"], "failed")
                self.assertEqual(json.loads(self.state.read_text())["status"], "failed")

    def test_only_lost_response_after_send_is_uncertain(self):
        client = Mock()
        client.recv.side_effect = TimeoutError()
        with patch.object(updates.socket, "socket", return_value=client):
            result = self.service.install_update()
        client.sendall.assert_called_once()
        self.assertTrue(result["response_pending"])
        self.assertNotIn("accepted", result)

    def test_invalid_bundle_metadata_fails_before_socket(self):
        self.latest.pop("appliance")
        self.write({"status": "update_available", "latest": self.latest, "update_available": True})
        with patch.object(self.service, "_request_appliance_updater") as request:
            result = self.service.install_update()
        request.assert_not_called()
        self.assertEqual(result["install"]["status"], "failed")

    def test_old_host_failure_does_not_override_new_attempt(self):
        self.write({"status": "running", "install": {
            "target_version": "0.4.7", "started_at": "2026-09-11T12:00:00+00:00", "status": "running",
        }})
        with patch.object(self.service, "_request_appliance_updater", return_value={"ok": True, "state": {
            "target_version": "0.4.6", "status": "failed", "finished_at": "2026-09-10T12:00:00+00:00",
        }}):
            self.assertEqual(self.service.status()["status"], "running")

    def test_check_during_install_does_not_overwrite_running_state(self):
        self.write({"status": "running", "install": {"target_version": "0.4.7", "started_at": "2026-09-11T12:00:00+00:00"}})
        with patch.object(self.service, "_request_appliance_updater", return_value={"ok": True, "state": {}}), patch.object(self.service, "_load_manifest") as load:
            self.assertEqual(self.service.check_for_updates()["status"], "running")
        load.assert_not_called()


class HostDownloadTests(unittest.TestCase):
    def test_wrong_size_is_rejected_before_docker_load(self):
        spec = importlib.util.spec_from_file_location("updater_under_test", Path(__file__).resolve().parents[1] / "box/sentero-updater/sentero_updater.py")
        updater = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(updater)
        with tempfile.TemporaryDirectory() as tmp, patch.object(updater, "STATE_FILE", Path(tmp) / "state.json"), patch.object(updater, "read_env", return_value={}), patch.object(updater, "container_exists", return_value=False), patch.object(updater, "manifest_url", return_value="https://example.invalid"), patch.object(updater, "load_json_url", return_value={
            "latest_version": "0.4.7", "appliance": {"bundle_url": "https://example.invalid", "sha256": "a" * 64, "size_bytes": 42},
        }), patch.object(updater, "download", side_effect=lambda url, target, maximum: target.write_bytes(b"short")), patch.object(updater, "run") as run:
            result = updater.handle({"action": "install", "target_version": "0.4.7"})
        self.assertFalse(result["ok"])
        self.assertIn("Groesse", result["error"])
        run.assert_not_called()
