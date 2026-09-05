from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from backend.services.notification_service import (
    TELEGRAM_BRAND_DESCRIPTION,
    TELEGRAM_BRAND_NAME,
    TELEGRAM_BRAND_SHORT_DESCRIPTION,
    TELEGRAM_PROFILE_PHOTO,
    TelegramNotificationProvider,
)


class TelegramBrandingTests(unittest.TestCase):
    def test_branding_asset_is_jpg(self) -> None:
        self.assertTrue(TELEGRAM_PROFILE_PHOTO.is_file())
        self.assertEqual(TELEGRAM_PROFILE_PHOTO.suffix.lower(), ".jpg")

    @patch("backend.services.notification_service.requests.post")
    @patch("backend.services.notification_service.requests.get")
    def test_apply_branding_sets_name_descriptions_and_profile_photo(self, get: Mock, post: Mock) -> None:
        get_response = Mock()
        get_response.json.return_value = {
            "ok": True,
            "result": {"id": 123, "username": "sentero_test_bot"},
        }
        get_response.status_code = 200
        get_response.raise_for_status.return_value = None
        get.return_value = get_response

        post_response = Mock()
        post_response.json.return_value = {"ok": True, "result": True}
        post_response.status_code = 200
        post_response.raise_for_status.return_value = None
        post.return_value = post_response

        result = TelegramNotificationProvider().apply_branding(
            {"bot_token": "123:TEST"}
        )

        self.assertEqual(result["bot_id"], 123)
        self.assertEqual(result["name"], "Sentero")
        self.assertEqual(post.call_count, 4)

        calls = post.call_args_list
        self.assertTrue(calls[0].args[0].endswith("/setMyProfilePhoto"))
        photo = json.loads(calls[0].kwargs["data"]["photo"])
        self.assertEqual(
            photo,
            {"type": "static", "photo": "attach://profile_photo"},
        )
        self.assertIn("profile_photo", calls[0].kwargs["files"])
        self.assertEqual(calls[0].kwargs["files"]["profile_photo"][2], "image/jpeg")

        self.assertTrue(calls[1].args[0].endswith("/setMyName"))
        self.assertEqual(calls[1].kwargs["json"], {"name": TELEGRAM_BRAND_NAME})

        self.assertTrue(calls[2].args[0].endswith("/setMyDescription"))
        self.assertEqual(
            calls[2].kwargs["json"],
            {"description": TELEGRAM_BRAND_DESCRIPTION},
        )

        self.assertTrue(calls[3].args[0].endswith("/setMyShortDescription"))
        self.assertEqual(
            calls[3].kwargs["json"],
            {"short_description": TELEGRAM_BRAND_SHORT_DESCRIPTION},
        )

    @patch("backend.services.notification_service.requests.post")
    @patch("backend.services.notification_service.requests.get")
    def test_apply_branding_attempts_profile_photo_before_rate_limited_name(self, get: Mock, post: Mock) -> None:
        get_response = Mock()
        get_response.json.return_value = {
            "ok": True,
            "result": {"id": 123, "username": "sentero_test_bot"},
        }
        get_response.status_code = 200
        get.return_value = get_response

        ok_response = Mock()
        ok_response.json.return_value = {"ok": True, "result": True}
        ok_response.status_code = 200
        rate_limit_response = Mock()
        rate_limit_response.json.return_value = {
            "ok": False,
            "description": "Too Many Requests: retry after 86339",
            "parameters": {"retry_after": 86339},
        }
        rate_limit_response.status_code = 429
        post.side_effect = [
            ok_response,
            rate_limit_response,
            ok_response,
            ok_response,
        ]

        result = TelegramNotificationProvider().apply_branding(
            {"bot_token": "123:TEST"}
        )

        self.assertTrue(post.call_args_list[0].args[0].endswith("/setMyProfilePhoto"))
        self.assertTrue(post.call_args_list[1].args[0].endswith("/setMyName"))
        self.assertTrue(result["branding_failed"])
        self.assertTrue(result["rate_limited"])
        self.assertEqual(result["retry_after"], 86339)
        self.assertIn("setMyName", result["failed_methods"])


if __name__ == "__main__":
    unittest.main()
