from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import deployment_build


class DeploymentBuildTests(unittest.TestCase):
    def test_version_validation_accepts_release_versions(self) -> None:
        self.assertEqual(deployment_build.validate_version("0.2.0"), "0.2.0")
        self.assertEqual(deployment_build.validate_version("0.3.0-beta1"), "0.3.0-beta1")

    def test_version_validation_rejects_unsafe_values(self) -> None:
        with self.assertRaises(SystemExit):
            deployment_build.validate_version("../0.2.0")

    def test_release_zip_contains_only_release_metadata_and_docker_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            release_json = root / "release.json"
            image_tar = root / "sentero-image.tar"
            bundle = root / "sentero-box-0.2.0.zip"

            release_json.write_text(json.dumps({
                "format": 1,
                "product": "Sentero Box",
                "version": "0.2.0",
                "image": "sentero/app:0.2.0",
                "image_tar": "sentero-image.tar",
            }))
            image_tar.write_bytes(b"docker image placeholder")

            deployment_build.create_release_zip(
                bundle_path=bundle,
                release_json=release_json,
                image_tar=image_tar,
            )

            with zipfile.ZipFile(bundle) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"release.json", "sentero-image.tar"},
                )

            deployment_build.validate_bundle(
                bundle,
                expected_version="0.2.0",
                expected_image="sentero/app:0.2.0",
            )

    def test_file_sha256_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "artifact.zip"
            artifact.write_bytes(b"sentero")
            self.assertEqual(
                deployment_build.file_sha256(artifact),
                deployment_build.file_sha256(artifact),
            )


if __name__ == "__main__":
    unittest.main()
