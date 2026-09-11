from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.fakes import NoNetworkSensorSource
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.notification_service import NotificationService
from backend.services.update_service import SenteroUpdateService, file_sha256


class RecordingProvider:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, contact: dict, title: str, text: str, config: dict) -> dict[str, str]:
        self.sent.append({"channel": str(config.get("_channel") or ""), "title": title, "text": text})
        return {"message_id": f"msg-{len(self.sent)}"}


class FailingProvider:
    def __init__(self) -> None:
        self.attempts = 0

    def send(self, contact: dict, title: str, text: str, config: dict) -> dict[str, str]:
        self.attempts += 1
        raise RuntimeError("temporary failure")


class UpdateServiceTests(unittest.TestCase):
    def test_version_comparison_handles_patch_and_prerelease_suffixes(self) -> None:
        service = SenteroUpdateService()

        self.assertTrue(service._is_newer("0.1.1", "0.1.0"))
        self.assertTrue(service._is_newer("0.2.0", "0.1.9"))
        self.assertFalse(service._is_newer("0.1.0", "0.1.0"))
        self.assertFalse(service._is_newer("0.1.0-beta", "0.1.0"))
        self.assertFalse(service._is_newer("0.1", "0.1.0"))

    def test_archive_integrity_accepts_matching_sha256_and_size(self) -> None:
        service = SenteroUpdateService()
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "sentero.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("sentero-0.1.1/version.json", "{}")
            service._verify_archive_integrity(
                archive,
                {"sha256": file_sha256(archive), "size_bytes": archive.stat().st_size},
            )

    def test_archive_integrity_rejects_missing_or_wrong_sha256(self) -> None:
        service = SenteroUpdateService()
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "sentero.zip"
            archive.write_bytes(b"not really a zip")
            with self.assertRaisesRegex(ValueError, "no sha256"):
                service._verify_archive_integrity(archive, {})
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                service._verify_archive_integrity(archive, {"sha256": "0" * 64})

    def test_archive_integrity_rejects_size_mismatch(self) -> None:
        service = SenteroUpdateService()
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "sentero.zip"
            archive.write_bytes(b"archive")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                service._verify_archive_integrity(archive, {"sha256": file_sha256(archive), "size_bytes": archive.stat().st_size + 1})

    def test_auto_check_no_update_sends_no_notification(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.0.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email"])

            result = SenteroUpdateService().auto_check_and_notify(notification)

            self.assertTrue(result["checked"])
            self.assertFalse(result["update_available"])
            self.assertEqual(providers["email"].sent, [])

    def test_auto_check_new_version_notifies_primary_contact_once(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email"])

            first = SenteroUpdateService().auto_check_and_notify(notification)
            second = SenteroUpdateService().auto_check_and_notify(notification)

            self.assertEqual(first["notified"], 1)
            self.assertEqual(second["skipped"], "not_due")
            self.assertEqual(len(providers["email"].sent), 1)
            self.assertIn("Version 1.1.0", providers["email"].sent[0]["text"])

    def test_newer_version_sends_new_notification(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email"])
            service = SenteroUpdateService()

            service.auto_check_and_notify(notification)
            self._write_json(env["state"], {"last_auto_check_at": "2026-01-01T00:00:00+00:00"})
            self._write_json(env["manifest"], self._manifest("1.2.0"))
            service.auto_check_and_notify(notification)

            self.assertEqual(len(providers["email"].sent), 2)
            self.assertIn("Version 1.2.0", providers["email"].sent[1]["text"])

    def test_telegram_and_email_are_both_used_when_active(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email", "telegram"])

            result = SenteroUpdateService().auto_check_and_notify(notification)

            self.assertEqual(result["notified"], 2)
            self.assertEqual(len(providers["email"].sent), 1)
            self.assertEqual(len(providers["telegram"].sent), 1)

    def test_partial_channel_failure_retries_only_failed_channel(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email", "telegram"])
            failing_email = FailingProvider()
            notification.providers["email"] = failing_email
            service = SenteroUpdateService()

            first = service.auto_check_and_notify(notification)
            self._write_json(env["state"], {"last_auto_check_at": "2026-01-01T00:00:00+00:00"})
            notification.providers["email"] = providers["email"]
            with patch("backend.services.notification_service.now", return_value=(datetime.now(timezone.utc) + timedelta(seconds=61)).isoformat()):
                second = service.auto_check_and_notify(notification)

            self.assertEqual(first["notified"], 1)
            self.assertEqual(failing_email.attempts, 1)
            self.assertEqual(len(providers["telegram"].sent), 1)
            self.assertEqual(second["notified"], 1)
            self.assertEqual(second["skipped_already_sent"], 1)
            self.assertEqual(len(providers["telegram"].sent), 1)
            self.assertEqual(len(providers["email"].sent), 1)

    def test_no_primary_contact_sends_nothing_and_does_not_crash(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email"], primary=False)

            result = SenteroUpdateService().auto_check_and_notify(notification)

            self.assertEqual(result["skipped"], "no_primary_contact")
            self.assertEqual(providers["email"].sent, [])

    def test_update_server_unreachable_sends_no_notification(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email"])
            with patch.object(SenteroUpdateService, "_load_manifest", side_effect=OSError("offline")):
                result = SenteroUpdateService().auto_check_and_notify(notification)

            self.assertTrue(result["checked"])
            self.assertEqual(result["notified"], 0)
            self.assertEqual(providers["email"].sent, [])

    def test_invalid_manifest_sends_no_notification(self) -> None:
        with self._auto_update_env("1.0.0", {"channels": {"stable": {}}}) as env:
            notification, providers = self._notification(env["db"], channels=["email"])

            result = SenteroUpdateService().auto_check_and_notify(notification)

            self.assertTrue(result["checked"])
            self.assertEqual(result["notified"], 0)
            self.assertEqual(providers["email"].sent, [])

    def test_auto_check_never_installs_update(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, _providers = self._notification(env["db"], channels=["email"])
            service = SenteroUpdateService()
            with patch.object(service, "install_update", side_effect=AssertionError("must not install")) as install:
                result = service.auto_check_and_notify(notification)

            self.assertEqual(result["notified"], 1)
            install.assert_not_called()

    def test_update_notification_contains_no_technical_internals(self) -> None:
        manifest = self._manifest(
            "1.1.0",
            release_notes=["Verbesserte Erkennung von Alltagssituationen"],
            extra={"download_url": "https://updates.example/sentero.zip", "sha256": "a" * 64},
        )
        with self._auto_update_env("1.0.0", manifest) as env:
            notification, providers = self._notification(env["db"], channels=["email"])

            SenteroUpdateService().auto_check_and_notify(notification)

            text = providers["email"].sent[0]["text"]
            self.assertIn("Verbesserte Erkennung von Alltagssituationen", text)
            self.assertNotIn("https://", text)
            self.assertNotIn("sha256", text.lower())
            self.assertNotIn("manifest", text.lower())
            self.assertNotIn("docker", text.lower())
            self.assertNotIn("container", text.lower())

    def test_importance_controls_user_facing_formulation(self) -> None:
        cases = {
            "normal": "Ein neues Sentero-Update ist verfügbar.",
            "important": "Ein wichtiges Sentero-Update ist verfügbar.",
            "security": "Ein Sicherheitsupdate für Sentero ist verfügbar.",
        }
        for importance, expected in cases.items():
            with self.subTest(importance=importance):
                with self._auto_update_env("1.0.0", self._manifest("1.1.0", extra={"importance": importance})) as env:
                    notification, providers = self._notification(env["db"], channels=["email"])
                    SenteroUpdateService().auto_check_and_notify(notification)
                    self.assertIn(expected, providers["email"].sent[0]["text"])

    def test_persisted_dedup_survives_service_restart(self) -> None:
        with self._auto_update_env("1.0.0", self._manifest("1.1.0")) as env:
            notification, providers = self._notification(env["db"], channels=["email"])

            SenteroUpdateService().auto_check_and_notify(notification)
            self._write_json(env["state"], {"last_auto_check_at": "2026-01-01T00:00:00+00:00"})
            restarted = SenteroUpdateService()
            restarted.auto_check_and_notify(notification)

            self.assertEqual(len(providers["email"].sent), 1)

    def _notification(self, database: Path, *, channels: list[str], primary: bool = True) -> tuple[NotificationService, dict[str, RecordingProvider]]:
        mapping = DeviceMappingService(database_path=database)
        mapping.sensor_source = NoNetworkSensorSource()
        service = NotificationService(mapping)
        providers = {"email": RecordingProvider(), "telegram": RecordingProvider()}
        service.providers["email"] = providers["email"]
        service.providers["telegram"] = providers["telegram"]
        with mapping.connect() as con:
            con.execute(
                """insert into trusted_contacts
                   (name, relationship, email, telegram_chat_id, active, created_at, updated_at,
                    preferred_channels, notification_enabled, primary_contact, actor_role)
                   values ('Hauptkontakt', 'owner', 'main@example.test', '12345', 1, ?, ?, ?, 1, ?, 'relative')""",
                (now(), now(), json.dumps(channels), int(primary)),
            )
            con.execute(
                "update notification_channel_settings set enabled = 1, config_json = ? where channel = 'email'",
                (json.dumps({"smtp_host": "smtp.example.test", "smtp_user": "sentero@example.test", "smtp_password": "secret", "_channel": "email"}),),
            )
            con.execute(
                "insert or replace into notification_channel_settings (channel, enabled, config_json, created_at, updated_at) values ('telegram', 1, ?, ?, ?)",
                (json.dumps({"bot_token": "token", "_channel": "telegram"}), now(), now()),
            )
            con.commit()
        return service, providers

    def _auto_update_env(self, current_version: str, manifest: dict):
        return AutoUpdateEnv(self, current_version, manifest)

    def _manifest(self, version: str, release_notes: list[str] | None = None, extra: dict | None = None) -> dict:
        latest = {
            "latest_version": version,
            "download_url": "https://updates.example/sentero.zip",
            "sha256": "a" * 64,
            "mandatory": False,
            "release_notes": release_notes or ["Kleinere Fehlerbehebungen"],
            "changes": release_notes or ["Kleinere Fehlerbehebungen"],
            "summary": "Die neue Version verbessert Sentero.",
            "layers": ["application"],
        }
        latest.update(extra or {})
        return {"channels": {"stable": latest}}

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


class AutoUpdateEnv:
    def __init__(self, test: UpdateServiceTests, current_version: str, manifest: dict) -> None:
        self.test = test
        self.current_version = current_version
        self.manifest_payload = manifest
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        self.version = self.root / "version.json"
        self.manifest = self.root / "latest.json"
        self.db = self.root / "sentero.db"
        self.patches: list[Any] = []

    def __enter__(self) -> dict[str, Path]:
        self.test._write_json(self.version, {"version": self.current_version, "build": "test", "commit": "test"})
        self.test._write_json(self.manifest, self.manifest_payload)
        self.patches = [
            patch("backend.services.update_service.STATE_FILE", self.state),
            patch("backend.services.update_service.VERSION_FILE", self.version),
            patch.dict(
                "os.environ",
                {
                    "SENTERO_UPDATE_MANIFEST_PATH": str(self.manifest),
                    "SENTERO_UPDATE_MANIFEST_URL": "",
                    "UPDATE_MANIFEST_URL": "",
                    "UPDATE_BASE_URL": "",
                    "SENTERO_UPDATE_BASE_URL": "",
                    "SENTERO_AUTO_UPDATE_CHECK": "true",
                    "SENTERO_UPDATE_CHECK_INTERVAL_HOURS": "24",
                },
                clear=False,
            ),
        ]
        for item in self.patches:
            item.start()
        return {"state": self.state, "version": self.version, "manifest": self.manifest, "db": self.db}

    def __exit__(self, *args: object) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
