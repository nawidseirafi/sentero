from __future__ import annotations

import unittest
from pathlib import Path

from deployment_build import deployment_manifest, latest_manifest


class DeploymentBuildTests(unittest.TestCase):
    def test_latest_manifest_uses_public_download_url(self) -> None:
        manifest = latest_manifest(
            version="0.1.1",
            zip_path=Path("/tmp/sentero-0.1.1.zip"),
            base_url="https://seirafi.de/robotersteve/sentero/",
        )

        stable = manifest["channels"]["stable"]
        self.assertEqual(stable["download_url"], "https://seirafi.de/robotersteve/sentero/stable/releases/sentero-0.1.1.zip")
        self.assertNotIn("/Users/", stable["download_url"])

    def test_deployment_manifest_keeps_artifact_relative_and_url_public(self) -> None:
        manifest = deployment_manifest(
            version="0.1.1",
            zip_path=Path("/tmp/sentero-0.1.1.zip"),
            base_url="https://seirafi.de/robotersteve/sentero",
        )

        self.assertEqual(manifest["artifact"], "releases/sentero-0.1.1.zip")
        self.assertEqual(manifest["artifact_url"], "https://seirafi.de/robotersteve/sentero/stable/releases/sentero-0.1.1.zip")
        self.assertEqual(manifest["manifest"], "latest.json")
        self.assertNotIn("/Users/", manifest["artifact"])


if __name__ == "__main__":
    unittest.main()
