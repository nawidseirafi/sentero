from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deployment_build import deployment_manifest, file_sha256, latest_manifest


class DeploymentBuildTests(unittest.TestCase):
    def test_latest_manifest_uses_public_download_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "sentero-0.1.1.zip"
            archive.write_bytes(b"sentero release")
            manifest = latest_manifest(
                version="0.1.1",
                zip_path=archive,
                base_url="https://seirafi.de/robotersteve/sentero/",
            )

        stable = manifest["channels"]["stable"]
        self.assertEqual(stable["download_url"], "https://seirafi.de/robotersteve/sentero/stable/releases/sentero-0.1.1.zip")
        self.assertIn("sha256", stable)
        self.assertIn("size_bytes", stable)
        self.assertNotIn("/Users/", stable["download_url"])

    def test_deployment_manifest_keeps_artifact_relative_and_url_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "sentero-0.1.1.zip"
            archive.write_bytes(b"sentero release")
            expected_hash = file_sha256(archive)
            expected_size = archive.stat().st_size
            manifest = deployment_manifest(
                version="0.1.1",
                zip_path=archive,
                base_url="https://seirafi.de/robotersteve/sentero",
            )

        self.assertEqual(manifest["artifact"], "releases/sentero-0.1.1.zip")
        self.assertEqual(manifest["artifact_url"], "https://seirafi.de/robotersteve/sentero/stable/releases/sentero-0.1.1.zip")
        self.assertEqual(manifest["manifest"], "latest.json")
        self.assertEqual(manifest["sha256"], expected_hash)
        self.assertEqual(manifest["size_bytes"], expected_size)
        self.assertNotIn("/Users/", manifest["artifact"])

    def test_latest_manifest_includes_archive_hash_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "sentero-0.1.1.zip"
            archive.write_bytes(b"sentero release")
            expected_hash = file_sha256(archive)
            expected_size = archive.stat().st_size
            manifest = latest_manifest(
                version="0.1.1",
                zip_path=archive,
                base_url="https://seirafi.de/robotersteve/sentero",
            )

        stable = manifest["channels"]["stable"]
        self.assertEqual(stable["sha256"], expected_hash)
        self.assertEqual(stable["size_bytes"], expected_size)


if __name__ == "__main__":
    unittest.main()
