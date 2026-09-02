from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "box" / "scripts" / "sentero_device_identity.py"
spec = importlib.util.spec_from_file_location("sentero_device_identity_test", MODULE_PATH)
assert spec and spec.loader
identity_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = identity_module
spec.loader.exec_module(identity_module)

IdentityConflictError = identity_module.IdentityConflictError
InvalidIdentityError = identity_module.InvalidIdentityError
create_device_identity = identity_module.create_device_identity
get_setup_ssid = identity_module.get_setup_ssid
legacy_setup_suffix = identity_module.legacy_setup_suffix
load_device_identity = identity_module.load_device_identity
validate_serial_number = identity_module.validate_serial_number

LABEL_MODULE_PATH = ROOT / "box" / "scripts" / "generate-setup-label.py"
label_spec = importlib.util.spec_from_file_location("generate_setup_label_test", LABEL_MODULE_PATH)
assert label_spec and label_spec.loader
label_module = importlib.util.module_from_spec(label_spec)
sys.modules[label_spec.name] = label_module
sys.modules["sentero_device_identity"] = identity_module
label_spec.loader.exec_module(label_module)


class DeviceIdentityTests(unittest.TestCase):
    def test_valid_serial_number(self) -> None:
        self.assertEqual(validate_serial_number("STB-00001234"), "STB-00001234")

    def test_short_serial_number_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            validate_serial_number("STB-1234")

    def test_serial_without_dash_is_invalid(self) -> None:
        with self.assertRaises(ValueError):
            validate_serial_number("STB12345678")

    def test_new_identity_creates_uuid_v4(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmpdir:
            path = Path(tmpdir) / "device" / "identity.json"
            identity = create_device_identity("STB-00001234", path)

            parsed = uuid.UUID(identity.device_id)
            self.assertEqual(parsed.version, 4)
            self.assertEqual(identity.serial_number, "STB-00001234")

    def test_second_load_returns_same_uuid(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmpdir:
            path = Path(tmpdir) / "device" / "identity.json"
            first = create_device_identity("STB-00001234", path)
            second = load_device_identity(path)

            self.assertEqual(second.device_id, first.device_id)

    def test_second_provisioning_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmpdir:
            path = Path(tmpdir) / "device" / "identity.json"
            first = create_device_identity("STB-00001234", path)
            second = create_device_identity("STB-00001234", path)

            self.assertEqual(second.device_id, first.device_id)

    def test_different_serial_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmpdir:
            path = Path(tmpdir) / "device" / "identity.json"
            first = create_device_identity("STB-00001234", path)

            with self.assertRaises(IdentityConflictError):
                create_device_identity("STB-00005678", path)
            self.assertEqual(load_device_identity(path).device_id, first.device_id)

    def test_corrupted_json_does_not_create_new_uuid(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmpdir:
            path = Path(tmpdir) / "device" / "identity.json"
            path.parent.mkdir(parents=True)
            path.write_text("{not-json", encoding="utf-8")

            with self.assertRaises(InvalidIdentityError):
                create_device_identity("STB-00001234", path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not-json")

    def test_setup_ssid_uses_last_four_serial_digits(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as tmpdir:
            path = Path(tmpdir) / "device" / "identity.json"
            identity = create_device_identity("STB-00001234", path)

            self.assertEqual(get_setup_ssid(identity), "Sentero-Setup-1234")

    def test_legacy_suffix_matches_existing_machine_id_mechanism(self) -> None:
        with patch.object(identity_module.Path, "read_text", return_value="legacy-machine\n"):
            self.assertEqual(legacy_setup_suffix(), hashlib.sha256(b"legacy-machine").hexdigest()[:4].upper())
        with patch.object(identity_module.Path, "read_text", side_effect=OSError), patch.object(
            identity_module.socket, "gethostname", return_value="sentero-host"
        ):
            self.assertEqual(legacy_setup_suffix(), hashlib.sha256(b"sentero-host").hexdigest()[:4].upper())

    def test_qr_payload_and_label_use_setup_ssid_and_serial(self) -> None:
        ssid = "Sentero-Setup-1234"

        self.assertEqual(label_module.wifi_payload(ssid), "WIFI:T:nopass;S:Sentero-Setup-1234;;")
        label = label_module.label_svg(ssid, "STB-00001234")
        self.assertIn("STB-00001234", label)
        self.assertIn("Sentero-Setup-1234", label)


if __name__ == "__main__":
    unittest.main()
