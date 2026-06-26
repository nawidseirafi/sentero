from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.services.box_network_service import BoxNetworkService
from backend.services.device_mapping_service import DeviceMappingService


class BoxNetworkServiceTests(unittest.TestCase):
    def test_default_mode_is_disabled_and_status_is_public(self) -> None:
        service = self.service()

        status = service.status()

        self.assertEqual(status["mode"], "disabled")
        self.assertEqual(status["local_url"], "http://sentero.local")
        self.assertNotIn("password", str(status).lower())

    def test_disabled_mode_saves_wifi_without_applying_os_changes(self) -> None:
        service = self.service()

        result = service.save_wifi({"ssid": "MeinWLAN", "password": "secret"})
        settings = service.settings(public=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(result["mode"], "disabled")
        self.assertEqual(settings["wifi_ssid"], "MeinWLAN")
        self.assertTrue(settings["wifi_password_set"])
        self.assertNotIn("secret", str(result))
        self.assertNotIn("secret", str(settings))

    def test_auto_mode_does_not_persist_when_adapter_fails(self) -> None:
        with patch.dict(os.environ, {"SENTERO_BOX_SETUP_MODE": "auto"}, clear=False):
            service = self.service()

            result = service.save_wifi({"ssid": "MeinWLAN", "password": "secret"})
            settings = service.settings(public=True)

        self.assertFalse(result["ok"])
        self.assertFalse(result["applied"])
        self.assertEqual(settings["wifi_ssid"], "")
        self.assertFalse(settings["wifi_password_set"])

    def test_wifi_requires_ssid_and_password(self) -> None:
        service = self.service()

        with self.assertRaises(ValueError):
            service.save_wifi({"ssid": "", "password": "secret"})
        with self.assertRaises(ValueError):
            service.save_wifi({"ssid": "MeinWLAN", "password": ""})

    def service(self) -> BoxNetworkService:
        path = Path(tempfile.mkdtemp(dir="/private/tmp")) / "sentero.db"
        return BoxNetworkService(DeviceMappingService(database_path=path))


if __name__ == "__main__":
    unittest.main()
