from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.agents.sentero.mail.discovery import email_domain, get_mail_settings, parse_ispdb_config
from backend.agents.sentero.mail.models import MailEncryption


ISPDB_XML = b"""<?xml version="1.0"?>
<clientConfig version="1.1">
  <emailProvider id="example.test">
    <incomingServer type="imap">
      <hostname>imap.example.test</hostname>
      <port>993</port>
      <socketType>SSL</socketType>
      <authentication>password-cleartext</authentication>
      <username>%EMAILADDRESS%</username>
    </incomingServer>
    <outgoingServer type="smtp">
      <hostname>smtp.example.test</hostname>
      <port>587</port>
      <socketType>STARTTLS</socketType>
      <authentication>password-cleartext</authentication>
      <username>%EMAILADDRESS%</username>
    </outgoingServer>
  </emailProvider>
</clientConfig>
"""


class MailDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    def test_parse_ispdb_config_extracts_imap_and_smtp_settings(self) -> None:
        config = parse_ispdb_config(ISPDB_XML)

        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.imap_host, "imap.example.test")
        self.assertEqual(config.imap_port, 993)
        self.assertEqual(config.imap_encryption, MailEncryption.SSL)
        self.assertEqual(config.smtp_host, "smtp.example.test")
        self.assertEqual(config.smtp_port, 587)
        self.assertEqual(config.smtp_encryption, MailEncryption.STARTTLS)
        self.assertEqual(config.auth_method, "password-cleartext")
        self.assertEqual(config.source, "ispdb")

    def test_email_domain_rejects_invalid_addresses(self) -> None:
        self.assertEqual(email_domain("Max Mustermann <max@gmx.de>"), "gmx.de")
        self.assertIsNone(email_domain("not-an-email"))
        self.assertIsNone(email_domain("max@localhost"))
        self.assertIsNone(email_domain("max@@gmx.de"))

    async def test_get_mail_settings_uses_fallback_when_ispdb_has_no_result(self) -> None:
        async def no_discovery(email: str):
            return None

        with patch("backend.agents.sentero.mail.discovery.discover_mail_settings", no_discovery):
            config = await get_mail_settings("max@gmail.com")

        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.smtp_host, "smtp.gmail.com")
        self.assertTrue(config.requires_app_password)
        self.assertEqual(config.source, "fallback")

    async def test_get_mail_settings_returns_none_for_unknown_domain(self) -> None:
        async def no_discovery(email: str):
            return None

        with patch("backend.agents.sentero.mail.discovery.discover_mail_settings", no_discovery):
            config = await get_mail_settings("max@example.invalid")

        self.assertIsNone(config)

    def test_verify_endpoint_uses_credential_verification(self) -> None:
        payload = {
            "email": "max@example.test",
            "password": "secret",
            "config": {
                "imap_host": "imap.example.test",
                "imap_port": 993,
                "imap_encryption": "SSL",
                "smtp_host": "smtp.example.test",
                "smtp_port": 587,
                "smtp_encryption": "STARTTLS",
                "source": "manual",
            },
        }
        with patch("backend.api.routes.verify_mail_credentials", return_value=(True, None)) as verify:
            response = TestClient(app).post("/api/mail/verify", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Senden und Empfangen funktioniert."})
        verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
