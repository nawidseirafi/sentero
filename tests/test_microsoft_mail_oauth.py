import asyncio
import imaplib
import json
import logging
import smtplib
import tempfile
import threading
import time
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from backend.services import microsoft_mail_oauth as oauth
from backend.services.notification_service import NotificationService, EmailNotificationProvider, mask_config
from backend.services.device_mapping_service import DeviceMappingService
from backend.agents.sentero.mail.imap_client import ImapMailClient
from backend.agents.sentero.mail.models import MailAssistantConfig, MailConfig
from backend.agents.sentero.mail.service import config_from_notification_settings
from backend.agents.sentero.mail.discovery import verify_mail_credentials, get_mail_settings
from backend.services.auth_service import SenteroAuthService
from backend.logging_config import mask_if_sensitive
from backend.services.audit_service import sanitize_metadata

MAILBOX = "box@outlook.com"
TOKEN = "secret-access-test"
REFRESH = "secret-refresh-test"
CONFIG = {"auth_method": oauth.AUTH_METHOD, "smtp_host": "smtp.office365.com", "smtp_port": 587,
          "smtp_user": MAILBOX, "imap_host": "outlook.office365.com", "imap_port": 993,
          "imap_user": MAILBOX, "smtp_encryption": "STARTTLS", "imap_encryption": "SSL"}


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = oauth.MicrosoftMailOAuth(Path(self.tmp.name) / "private/cache.json", client_id="test-app")
        self.app = Mock()
        self.app.get_accounts.return_value = [{"username": MAILBOX, "home_account_id": "account-id"}]
        self.app.acquire_token_silent_with_error.return_value = {"access_token": TOKEN}
        self.service._write({"cache": "{}", "account": MAILBOX, "home_account_id": "account-id",
                             "client_id": "test-app", "authority": self.service.authority})
        self.patch = patch.object(self.service, "_app", return_value=self.app)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_persistent_cache_and_permissions_after_restart(self):
        restarted = oauth.MicrosoftMailOAuth(self.service.path, client_id="test-app")
        with patch.object(restarted, "_app", return_value=self.app):
            self.assertEqual(restarted.token(MAILBOX), TOKEN)
        self.assertEqual(self.service.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.service.path.parent.stat().st_mode & 0o777, 0o700)
        self.app.initiate_device_flow.assert_not_called()

    def test_silent_refresh_and_retry_once_on_smtp_auth_rejection(self):
        smtp = Mock()
        smtp.auth.side_effect = [smtplib.SMTPAuthenticationError(535, b"expired"), None]
        with patch.object(oauth, "microsoft_mail_oauth", return_value=self.service):
            oauth.authenticate_mail(smtp, MAILBOX, "smtp")
        self.assertEqual([c.kwargs["force_refresh"] for c in self.app.acquire_token_silent_with_error.call_args_list], [False, True])
        smtp.login.assert_not_called()

    def test_invalid_grant_persists_reconnect_and_never_repolls(self):
        self.app.acquire_token_silent_with_error.return_value = {"error": "invalid_grant", "error_description": TOKEN + REFRESH}
        for _ in range(3):
            with self.assertRaises(oauth.MicrosoftMailError) as caught:
                self.service.token(MAILBOX)
            self.assertEqual(caught.exception.code, "reconnect_required")
        self.app.acquire_token_silent_with_error.assert_called_once()
        self.assertEqual(self.service.status()["status"], "reconnect_required")
        self.assertEqual(NotificationService._queue_retry_seconds(str(caught.exception), 1), 3600)
        self.assertNotIn(TOKEN, repr(caught.exception))

    def test_transient_error_is_sanitized_and_does_not_revoke_binding(self):
        self.app.acquire_token_silent_with_error.side_effect = RuntimeError(TOKEN + REFRESH)
        try:
            self.service.token(MAILBOX)
        except oauth.MicrosoftMailError as exc:
            self.assertEqual(exc.code, "temporarily_unavailable")
            self.assertNotIn(TOKEN, traceback.format_exc())
            self.assertNotIn(REFRESH, traceback.format_exc())
        else:
            self.fail("Expected token failure")
        self.assertEqual(self.service.status()["status"], "connected")

    def test_wrong_account_cannot_use_token(self):
        with self.assertRaises(oauth.MicrosoftMailError):
            self.service.token("other@outlook.com")
        self.app.acquire_token_silent_with_error.assert_not_called()

    def test_device_flow_only_returns_public_fields_and_persists_result(self):
        release = threading.Event()
        self.app.initiate_device_flow.return_value = {"device_code": "secret-device", "user_code": "ABCD-EFGH",
            "verification_uri": "https://microsoft.com/devicelogin", "expires_at": time.time() + 600}
        def acquire(*args, **kwargs):
            release.wait(2)
            return {"access_token": TOKEN, "refresh_token": REFRESH}
        self.app.acquire_token_by_device_flow.side_effect = acquire
        result = self.service.start(MAILBOX)
        self.assertEqual(result["status"], "pending")
        self.assertNotIn("secret-device", json.dumps(result))
        release.set()
        self.service._thread.join(3)
        result = self.service.status()
        self.assertEqual(result["status"], "connected")
        self.assertNotIn("user_code", result)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.app.initiate_device_flow.assert_called_once_with(scopes=oauth.SCOPES)

    def test_expired_flow_hides_code(self):
        self.service._flow = {"expires_at": time.time() - 1, "user_code": "old-code"}
        self.assertEqual(self.service.status()["status"], "expired")
        self.assertNotIn("old-code", json.dumps(self.service.status()))

    def test_disconnect_prevents_late_flow_result_restoring_cache(self):
        release = threading.Event()
        self.app.initiate_device_flow.return_value = {"device_code": "secret-device", "user_code": "ABCD",
            "verification_uri": "https://microsoft.com/devicelogin", "expires_at": time.time() + 600}
        self.app.acquire_token_by_device_flow.side_effect = lambda *a, **kw: (release.wait(2) or {}) and {"access_token": TOKEN}
        self.service.start(MAILBOX)
        self.service.disconnect()
        release.set()
        self.service._thread.join(3)
        self.assertFalse(self.service.path.exists())
        self.assertEqual(self.service.status()["status"], "reconnect_required")

    def test_unofficial_device_endpoint_rejected(self):
        self.app.initiate_device_flow.return_value = {"device_code": "x", "verification_uri": "https://example.invalid"}
        with self.assertRaises(oauth.MicrosoftMailError):
            self.service.start(MAILBOX)
        self.app.acquire_token_by_device_flow.assert_not_called()

    def test_initialization_and_status_never_contact_microsoft(self):
        with patch("msal.PublicClientApplication") as factory:
            service = oauth.MicrosoftMailOAuth(self.service.path, client_id="test-app")
            self.assertEqual(service.status()["status"], "connected")
            factory.assert_not_called()

    def test_unofficial_authority_rejected_before_network(self):
        service = oauth.MicrosoftMailOAuth(self.service.path, client_id="test-app", authority="https://evil.invalid/common")
        with patch("msal.PublicClientApplication") as factory:
            with self.assertRaises(oauth.MicrosoftMailError):
                service._app(None)
            factory.assert_not_called()


class MailTransportTests(unittest.TestCase):
    def setUp(self):
        self.token_service = Mock()
        self.token_service.token.return_value = TOKEN
        self.token_service.status.return_value = {"status": "connected", "account": MAILBOX}
        self.token_service.metadata.return_value = {"auth_method": oauth.AUTH_METHOD, "microsoft_client_id": "test-app"}
        self.patch = patch.object(oauth, "_SERVICE", self.token_service)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_imap_unseen_and_seen_use_xoauth2_with_raw_bytes(self):
        config = MailAssistantConfig(auth_method=oauth.AUTH_METHOD, imap_host=CONFIG["imap_host"], imap_username=MAILBOX)
        with patch("imaplib.IMAP4_SSL") as factory:
            client = factory.return_value.__enter__.return_value
            client.search.return_value = ("OK", [b"7"])
            client.fetch.return_value = ("OK", [(b"7", b"From: user@example.com\r\nSubject: Sentero: Status\r\nMessage-ID: <a>\r\n\r\nHello")])
            mail = ImapMailClient(config)
            self.assertEqual(mail.fetch_unseen()[0].message_id, "<a>")
            mail.mark_processed("7")
            client.login.assert_not_called()
            client.search.assert_called_once_with(None, "UNSEEN")
            client.store.assert_called_once_with("7", "+FLAGS", "\\Seen")
            method, callback = client.authenticate.call_args.args
            self.assertEqual(method, "XOAUTH2")
            self.assertEqual(callback(b""), f"user={MAILBOX}\x01auth=Bearer {TOKEN}\x01\x01".encode())

    def test_smtp_and_assistant_direct_reply_preserve_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            service = NotificationService(DeviceMappingService(database_path=Path(directory) / "db"))
            with patch("smtplib.SMTP") as factory:
                smtp = factory.return_value.__enter__.return_value
                service.send_email_direct("recipient@example.com", "Re: Sentero", "Reply", CONFIG,
                                          headers={"In-Reply-To": "<parent>", "References": "<root> <parent>"})
                smtp.login.assert_not_called()
                method, callback = smtp.auth.call_args.args
                self.assertEqual(method, "XOAUTH2")
                self.assertEqual(callback(), f"user={MAILBOX}\x01auth=Bearer {TOKEN}\x01\x01")
                message = smtp.send_message.call_args.args[0]
                self.assertEqual(message["In-Reply-To"], "<parent>")
                self.assertEqual(message["References"], "<root> <parent>")
                self.assertTrue(message["Message-ID"])

    def test_password_provider_unchanged(self):
        with patch("smtplib.SMTP") as factory:
            EmailNotificationProvider().send({"email": "a@example.com"}, "Test", "Text",
                {"smtp_host": "smtp.example.com", "smtp_user": "user", "smtp_password": "password"})
            smtp = factory.return_value.__enter__.return_value
            smtp.login.assert_called_once_with("user", "password")
            smtp.auth.assert_not_called()
        with patch("imaplib.IMAP4_SSL") as factory:
            client = factory.return_value.__enter__.return_value
            client.search.return_value = ("OK", [b""])
            ImapMailClient(MailAssistantConfig(imap_host="imap.example.com", imap_username="u", imap_password="p")).fetch_unseen()
            client.login.assert_called_once_with("u", "p")
            client.authenticate.assert_not_called()

    def test_legacy_microsoft_password_never_used_and_foreign_host_rejected(self):
        config = dict(CONFIG, smtp_password="legacy")
        config.pop("auth_method")
        with patch("smtplib.SMTP") as factory:
            EmailNotificationProvider().send({"email": "a@example.com"}, "Test", "Text", config)
            factory.return_value.__enter__.return_value.login.assert_not_called()
        with self.assertRaises(oauth.MicrosoftMailError):
            EmailNotificationProvider().send({}, "Test", "Text", dict(CONFIG, smtp_host="evil.invalid"))

    def test_verification_uses_both_oauth_protocols(self):
        config = MailConfig(**{k: v for k, v in CONFIG.items() if k not in {"smtp_user", "imap_user"}}, source="manual")
        with patch("imaplib.IMAP4_SSL") as imap, patch("smtplib.SMTP") as smtp:
            self.assertTrue(verify_mail_credentials(config, MAILBOX, "")[0])
            imap.return_value.authenticate.assert_called_once()
            smtp.return_value.auth.assert_called_once()
            imap.return_value.login.assert_not_called()
            smtp.return_value.login.assert_not_called()

    def test_config_migration_outbox_delivery_disconnect_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            mapping = DeviceMappingService(database_path=Path(directory) / "db")
            service = NotificationService(mapping)
            with mapping.connect() as con:
                con.execute("insert or replace into notification_channel_settings (channel, enabled, config_json, created_at, updated_at) values ('email',1,?,'now','now')",
                            (json.dumps(dict(CONFIG, auth_method="password", smtp_password="legacy", imap_password="legacy")),))
                con.commit()
            cfg = config_from_notification_settings(mapping)
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.auth_method, oauth.AUTH_METHOD)
            self.assertEqual(cfg.imap_password, "")
            service.save_channel("email", True, dict(CONFIG, refresh_token=REFRESH, smtp_password="new-microsoft-password"))
            stored = service.stored_channel_config("email")
            self.assertEqual(stored["smtp_password"], "legacy")
            self.assertNotIn("refresh_token", stored)
            service._enqueue({"email": "a@example.com"}, "email", "red", "Test", "Body")
            with patch("smtplib.SMTP") as smtp:
                self.assertEqual(service.process_pending_queue()["sent"], 1)
                smtp.return_value.__enter__.return_value.login.assert_not_called()
            service._enqueue({"email": "a@example.com"}, "email", "red", "Test2", "Body")
            self.token_service.status.return_value = {"status": "reconnect_required"}
            self.token_service.token.side_effect = oauth.MicrosoftMailError("reconnect_required")
            service.process_pending_queue()
            self.assertEqual(service._pending_count(), 1)
            self.assertFalse(config_from_notification_settings(mapping).enabled)

    def test_secret_sanitizers(self):
        for key in ("access_token", "refresh_token", "oauth_token", "authorization", "token_cache", "device_code"):
            self.assertNotIn(TOKEN, str(mask_config({key: TOKEN})))
            self.assertNotIn(TOKEN, str(mask_if_sensitive(key, TOKEN)))
            self.assertNotIn(TOKEN, str(sanitize_metadata({key: TOKEN})))

    def test_reset_mail_uses_shared_smtp_provider(self):
        with patch("smtplib.SMTP") as factory:
            SenteroAuthService._send_reset_email(None, "a@example.com", "https://local/reset", CONFIG)
            factory.return_value.__enter__.return_value.auth.assert_called_once()
            factory.return_value.__enter__.return_value.login.assert_not_called()

    def test_assistant_poll_reads_answers_in_thread_and_marks_seen(self):
        from tests.test_mail_assistant import MailAssistantTest
        fixture = MailAssistantTest("test_allowed_contact_status_intent_receives_answer")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.assistant.notification = NotificationService(fixture.mapping)
        fixture.assistant._fixed_config = replace(fixture.config, auth_method=oauth.AUTH_METHOD,
            smtp_host=CONFIG["smtp_host"], smtp_username=MAILBOX, smtp_password="",
            imap_host=CONFIG["imap_host"], imap_username=MAILBOX, imap_password="")
        mail = fixture._mail("Ist alles in Ordnung?", recipient=MAILBOX)
        raw = (f"From: {mail.sender_email}\r\nTo: {MAILBOX}\r\nSubject: {mail.subject}\r\n"
               f"Message-ID: {mail.message_id}\r\n\r\n{mail.body}").encode()
        with patch("imaplib.IMAP4_SSL") as imap, patch("smtplib.SMTP") as smtp:
            inbox = imap.return_value.__enter__.return_value
            inbox.search.return_value = ("OK", [b"8"])
            inbox.fetch.return_value = ("OK", [(b"8", raw)])
            result = fixture.assistant.poll_once()
            self.assertEqual(result["marked_read"], 1)
            message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
            self.assertEqual(message["In-Reply-To"], mail.message_id)
            inbox.login.assert_not_called()
            smtp.return_value.__enter__.return_value.login.assert_not_called()

    def test_discovery_selects_oauth_and_no_app_password(self):
        with patch("backend.agents.sentero.mail.discovery.discover_mail_settings", return_value=None):
            config = asyncio.run(get_mail_settings(MAILBOX))
        self.assertEqual(config.auth_method, oauth.AUTH_METHOD)
        self.assertFalse(config.requires_app_password)

    def test_api_device_routes_require_session_and_never_return_tokens(self):
        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        from backend import main
        from types import SimpleNamespace
        auth = Mock()
        self.token_service.status.return_value = {"status": "connected", "account": MAILBOX}
        with patch.object(main, "get_services", return_value=SimpleNamespace(auth=auth)):
            client = TestClient(main.app)
            auth.user_from_request.side_effect = HTTPException(401, "Nicht angemeldet.")
            response = client.get("/api/mail/microsoft/connect/status")
            self.assertEqual(response.status_code, 401)
            auth.user_from_request.side_effect = None
            response = client.get("/api/mail/microsoft/connect/status")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(TOKEN, response.text)
            self.assertNotIn(REFRESH, response.text)
            self.assertEqual(client.post("/api/mail/microsoft/disconnect").status_code, 200)
            self.token_service.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
