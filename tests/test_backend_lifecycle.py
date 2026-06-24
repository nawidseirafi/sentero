from __future__ import annotations

import unittest

from backend.services.container import get_services, reset_services_for_tests


class BackendLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_services_for_tests()

    def test_openapi_does_not_instantiate_backend_services(self) -> None:
        reset_services_for_tests()

        from backend.main import AUTH_SCHEME_NAME, PUBLIC_PATHS, app

        app.openapi_schema = None
        schema = app.openapi()

        self.assertEqual(get_services.cache_info().currsize, 0)
        self.assertIn(AUTH_SCHEME_NAME, schema.get("components", {}).get("securitySchemes", {}))
        self.assertNotIn("security", schema["paths"]["/api/sentero/auth/login"]["post"])

        for path, operations in schema.get("paths", {}).items():
            normalized_path = path.rstrip("/") or "/"
            if not normalized_path.startswith("/api/") or normalized_path in PUBLIC_PATHS:
                continue
            for method, operation in operations.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                self.assertIn({AUTH_SCHEME_NAME: []}, operation.get("security", []), path)


if __name__ == "__main__":
    unittest.main()
