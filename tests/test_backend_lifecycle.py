from __future__ import annotations

import unittest

from backend.services.container import get_services, reset_services_for_tests


class BackendLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_services_for_tests()

    def test_openapi_does_not_instantiate_backend_services(self) -> None:
        reset_services_for_tests()

        from backend.main import AUTH_SCHEME_NAME, PUBLIC_PATHS, PUBLIC_PREFIXES, app

        app.openapi_schema = None
        schema = app.openapi()

        self.assertEqual(get_services.cache_info().currsize, 0)
        self.assertIn(AUTH_SCHEME_NAME, schema.get("components", {}).get("securitySchemes", {}))
        self.assertNotIn("security", schema["paths"]["/api/sentero/auth/login"]["post"])
        self.assertIn("/api/sentero/exchange/v1/daily-status", schema["paths"])
        self.assertIn("/api/sentero/exchange/v1/event-summary", schema["paths"])
        self.assertIn("/api/sentero/exchange/v1/system-status", schema["paths"])
        self.assertNotIn("/api/sentero/exchange/daily-status", schema["paths"])
        self.assertNotIn("/api/sentero/exchange/event-summary", schema["paths"])
        self.assertNotIn("/api/sentero/exchange/system-status", schema["paths"])

        for path, operations in schema.get("paths", {}).items():
            normalized_path = path.rstrip("/") or "/"
            if not normalized_path.startswith("/api/") or normalized_path in PUBLIC_PATHS or any(normalized_path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
                continue
            for method, operation in operations.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                self.assertIn({AUTH_SCHEME_NAME: []}, operation.get("security", []), path)


if __name__ == "__main__":
    unittest.main()
