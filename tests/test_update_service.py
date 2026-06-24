from __future__ import annotations

import unittest

from backend.services.update_service import SenteroUpdateService


class UpdateServiceTests(unittest.TestCase):
    def test_version_comparison_handles_patch_and_prerelease_suffixes(self) -> None:
        service = SenteroUpdateService()

        self.assertTrue(service._is_newer("0.1.1", "0.1.0"))
        self.assertTrue(service._is_newer("0.2.0", "0.1.9"))
        self.assertFalse(service._is_newer("0.1.0", "0.1.0"))
        self.assertFalse(service._is_newer("0.1.0-beta", "0.1.0"))
        self.assertFalse(service._is_newer("0.1", "0.1.0"))


if __name__ == "__main__":
    unittest.main()
