"""Local, delegated Microsoft mail authentication. No Graph or client secret."""
from __future__ import annotations

import imaplib
import json
import logging
import os
import re
import smtplib
import tempfile
import threading
import time
from pathlib import Path

import msal

from backend.config import config_str
from backend.paths import DATA_DIR

AUTH_METHOD = "microsoft_oauth2"
# MSAL adds offline_access (and openid/profile) itself; passing it is rejected.
SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All", "https://outlook.office.com/SMTP.Send"]
MAIL_HOSTS = {"outlook.office365.com", "outlook.office.com", "imap-mail.outlook.com",
              "smtp.office365.com", "smtp-mail.outlook.com"}
MICROSOFT_DOMAINS = {"outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.com", "live.de", "msn.com"}


class MicrosoftMailError(ValueError):
    def __init__(self, code: str = "temporarily_unavailable"):
        self.code = code
        super().__init__("Microsoft-Konto erneut verbinden." if code == "reconnect_required" else
                         "Microsoft-Verbindung ist noch nicht eingerichtet." if code == "not_configured" else
                         "Microsoft ist momentan nicht erreichbar. Bitte spaeter erneut versuchen.")


def uses_microsoft(config: dict) -> bool:
    return (config.get("auth_method") in {AUTH_METHOD, "OAuth2/Modern Auth"}
            or any(str(config.get(key) or "").strip().lower() in MAIL_HOSTS for key in ("imap_host", "smtp_host"))
            or str(config.get("smtp_user") or "").lower().rsplit("@", 1)[-1] in MICROSOFT_DOMAINS)


def validate_mail_host(host: str, encrypted: bool = True) -> None:
    if host.strip().lower() not in MAIL_HOSTS or not encrypted:
        raise MicrosoftMailError("not_configured")


class MicrosoftMailOAuth:
    def __init__(self, path: Path | None = None, client_id: str | None = None, authority: str | None = None):
        self.path = path or DATA_DIR / "secrets" / "microsoft-mail.json"
        self.client_id = client_id if client_id is not None else (os.getenv("SENTERO_MICROSOFT_CLIENT_ID") or config_str("mail.microsoft.client_id", ""))
        self.authority = authority or os.getenv("SENTERO_MICROSOFT_AUTHORITY") or config_str("mail.microsoft.authority", "https://login.microsoftonline.com/common")
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._flow = None
        self._state = None
        self._generation = 0

    def _read(self):
        try:
            os.chmod(self.path, 0o600)
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise MicrosoftMailError("reconnect_required") from None

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".microsoft-")
        try:
            with os.fdopen(fd, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _app(self, cache):
        if not self.client_id or not re.fullmatch(r"https://login\.microsoftonline\.com/(common|consumers|organizations|[0-9a-fA-F-]{36})", self.authority):
            raise MicrosoftMailError("not_configured")
        # MSAL debug output can contain device-flow responses. Never propagate it.
        logging.getLogger("msal").disabled = True
        logging.getLogger("msal").setLevel(logging.CRITICAL)
        logging.getLogger("msal").propagate = False
        return msal.PublicClientApplication(self.client_id, authority=self.authority, token_cache=cache,
                                           timeout=10, enable_pii_log=False)

    def metadata(self):
        return {"auth_method": AUTH_METHOD, "microsoft_client_id": self.client_id,
                "microsoft_authority": self.authority}

    def status(self, mailbox: str = ""):
        with self._lock:
            if self._flow and time.time() >= self._flow.get("expires_at", 0):
                self._cancel.set()
                self._flow = None
                self._state = "expired"
            if self._flow:
                return {"status": "pending", "verification_uri": self._flow["verification_uri"],
                        "user_code": self._flow["user_code"],
                        "expires_in": max(0, int(self._flow["expires_at"] - time.time()))}
            try:
                data = self._read()
            except MicrosoftMailError:
                data = {"reconnect_required": True}
            bound = data.get("account") and (not mailbox or data["account"] == mailbox.strip().lower())
            connected = bool(bound and data.get("cache") and not data.get("reconnect_required")
                             and data.get("client_id") == self.client_id and data.get("authority") == self.authority)
            return {"status": self._state or ("connected" if connected else "reconnect_required"),
                    "account": data.get("account", "") if bound else ""}

    def start(self, mailbox: str):
        mailbox = mailbox.strip().lower()
        if not re.fullmatch(r"[^\s@\x00-\x1f]+@[^\s@\x00-\x1f]+", mailbox):
            raise MicrosoftMailError("not_configured")
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise MicrosoftMailError()
            cache = msal.SerializableTokenCache()
            try:
                app = self._app(cache)
                flow = app.initiate_device_flow(scopes=SCOPES)
                if not flow.get("device_code") or flow.get("verification_uri") not in {
                    "https://microsoft.com/devicelogin", "https://www.microsoft.com/devicelogin",
                    "https://login.microsoftonline.com/common/oauth2/deviceauth"}:
                    raise MicrosoftMailError()
            except MicrosoftMailError:
                raise
            except Exception:
                raise MicrosoftMailError() from None
            self._generation += 1
            generation = self._generation
            self._cancel = threading.Event()
            self._flow = flow
            self._state = None
            self._thread = threading.Thread(target=self._finish, args=(app, cache, flow, mailbox, generation, self._cancel), daemon=True)
            self._thread.start()
            return self.status()

    def _finish(self, app, cache, flow, mailbox, generation, cancel):
        try:
            result = app.acquire_token_by_device_flow(flow, exit_condition=lambda _: cancel.is_set())
            accounts = app.get_accounts(username=mailbox) if result.get("access_token") else []
            with self._lock:
                if generation != self._generation or cancel.is_set():
                    return
                if accounts and time.time() < flow["expires_at"]:
                    self._write({"cache": cache.serialize(), "account": mailbox,
                                 "home_account_id": accounts[0]["home_account_id"],
                                 "client_id": self.client_id, "authority": self.authority})
                    self._state = None
                else:
                    self._state = "expired" if result.get("error") == "expired_token" or time.time() >= flow["expires_at"] else "failed"
                self._flow = None
        except Exception:
            with self._lock:
                if generation == self._generation:
                    self._state, self._flow = "failed", None

    def disconnect(self):
        with self._lock:
            self._generation += 1
            self._cancel.set()
            self._flow, self._state = None, None
            self.path.unlink(missing_ok=True)

    def require_reconnect(self):
        with self._lock:
            data = self._read()
            data["reconnect_required"] = True
            self._write(data)

    def token(self, mailbox: str, force_refresh: bool = False):
        with self._lock:
            data = self._read()
            if (data.get("account") != mailbox.strip().lower() or not data.get("cache")
                    or data.get("reconnect_required") or data.get("client_id") != self.client_id
                    or data.get("authority") != self.authority):
                raise MicrosoftMailError("reconnect_required")
            try:
                cache = msal.SerializableTokenCache()
                cache.deserialize(data["cache"])
                app = self._app(cache)
                account = next((a for a in app.get_accounts() if a.get("home_account_id") == data.get("home_account_id")), None)
                result = app.acquire_token_silent_with_error(SCOPES, account=account, force_refresh=force_refresh) if account else None
                if cache.has_state_changed:
                    data["cache"] = cache.serialize()
                    self._write(data)
            except MicrosoftMailError:
                raise
            except Exception:
                raise MicrosoftMailError() from None
            if result and result.get("access_token"):
                return result["access_token"]
            if not result or result.get("error") in {"invalid_grant", "interaction_required", "consent_required", "login_required"}:
                self.require_reconnect()
                raise MicrosoftMailError("reconnect_required")
            raise MicrosoftMailError()


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def microsoft_mail_oauth():
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = MicrosoftMailOAuth()
        return _SERVICE


def authenticate_mail(client, mailbox: str, protocol: str):
    service = microsoft_mail_oauth()
    for attempt in range(2):
        token = service.token(mailbox, force_refresh=bool(attempt))
        payload = f"user={mailbox}\x01auth=Bearer {token}\x01\x01"
        try:
            if protocol == "imap":
                # imaplib does the base64 encoding; return raw UTF-8 bytes.
                client.authenticate("XOAUTH2", lambda challenge: payload.encode() if not challenge else b"")
            else:
                # smtplib.auth does base64 encoding and handles the SASL exchange.
                client.ehlo_or_helo_if_needed()
                client.auth("XOAUTH2", lambda challenge=None: payload if challenge is None else "")
            return
        except (imaplib.IMAP4.error, smtplib.SMTPAuthenticationError):
            if attempt:
                service.require_reconnect()
                raise MicrosoftMailError("reconnect_required") from None
        except Exception:
            raise MicrosoftMailError() from None
