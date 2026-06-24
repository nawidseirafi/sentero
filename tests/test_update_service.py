from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from backend.services.update_service import SenteroUpdateService, file_sha256


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


if __name__ == "__main__":
    unittest.main()
