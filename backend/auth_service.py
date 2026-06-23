from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from fastapi import HTTPException, Request, Response

from .device_mapping_service import DeviceMappingService, now

SESSION_COOKIE = "sentero_session"
SESSION_DAYS = 30
RESET_TOKEN_MINUTES = 30
PASSWORD_ITERATIONS = 260_000
ROLES = {"owner", "admin", "viewer"}
EMAIL_FROM = "Sentero <noreply@sentero.de>"
RATE_LIMIT: dict[str, list[float]] = {}


class SenteroAuthService:
    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping

    def status(self, request: Request) -> dict[str, Any]:
        user_count = self._user_count()
        user = self.user_from_request(request, required=False)
        return {
            "setup_required": user_count == 0,
            "authenticated": bool(user),
            "user": self._public_user(user) if user else None,
        }

    def setup(self, payload: dict[str, Any], response: Response, request: Request) -> dict[str, Any]:
        if self._user_count() > 0:
            raise HTTPException(status_code=400, detail="Sentero ist bereits eingerichtet.")
        email = normalize_email(payload.get("email"))
        password = str(payload.get("password") or "")
        confirm = str(payload.get("password_confirm") or payload.get("passwordConfirmation") or "")
        display_name = str(payload.get("display_name") or payload.get("name") or "").strip()
        if not display_name:
            raise HTTPException(status_code=400, detail="Bitte geben Sie einen Namen ein.")
        if not email:
            raise HTTPException(status_code=400, detail="Bitte geben Sie eine E-Mail-Adresse ein.")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="Das Passwort muss mindestens 8 Zeichen lang sein.")
        if password != confirm:
            raise HTTPException(status_code=400, detail="Die Passwörter stimmen nicht überein.")
        timestamp = now()
        with self.mapping.connect() as con:
            cur = con.execute(
                """insert into sentero_users
                   (email, password_hash, display_name, role, is_active, created_at, updated_at)
                   values (?, ?, ?, 'owner', 1, ?, ?)""",
                (email, hash_password(password), display_name, timestamp, timestamp),
            )
            con.commit()
            user = dict(con.execute("select * from sentero_users where id = ?", (cur.lastrowid,)).fetchone())
        token = self._create_session(int(user["id"]), request)
        self._set_cookie(response, token, request)
        return {"authenticated": True, "user": self._public_user(user)}

    def login(self, payload: dict[str, Any], response: Response, request: Request) -> dict[str, Any]:
        check_rate_limit(f"login:{client_key(request)}", limit=8, window_seconds=300)
        email = normalize_email(payload.get("email") or payload.get("username"))
        password = str(payload.get("password") or "")
        generic_error = HTTPException(status_code=401, detail="E-Mail oder Passwort ist nicht korrekt.")
        with self.mapping.connect() as con:
            row = con.execute("select * from sentero_users where lower(email) = ? and is_active = 1", (email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise generic_error
        user = dict(row)
        token = self._create_session(int(user["id"]), request)
        self._set_cookie(response, token, request)
        timestamp = now()
        with self.mapping.connect() as con:
            con.execute("update sentero_users set last_login_at = ?, updated_at = ? where id = ?", (timestamp, timestamp, user["id"]))
            con.commit()
        user["last_login_at"] = timestamp
        return {"authenticated": True, "user": self._public_user(user)}

    def logout(self, request: Request, response: Response) -> dict[str, bool]:
        token = self._token_from_request(request)
        if token:
            token_hash = hash_token(token)
            with self.mapping.connect() as con:
                con.execute("delete from sentero_sessions where token_hash = ?", (token_hash,))
                con.commit()
        response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
        return {"ok": True}

    def me(self, request: Request) -> dict[str, Any]:
        user = self.user_from_request(request, required=True)
        return {"user": self._public_user(user)}

    def update_me(self, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        user = self.user_from_request(request, required=True)
        email = normalize_email(payload.get("email"))
        display_name = str(payload.get("display_name") or payload.get("name") or "").strip()
        if not display_name:
            raise HTTPException(status_code=400, detail="Bitte geben Sie einen Namen ein.")
        if not email:
            raise HTTPException(status_code=400, detail="Bitte geben Sie eine E-Mail-Adresse ein.")
        with self.mapping.connect() as con:
            duplicate = con.execute(
                "select id from sentero_users where lower(email) = ? and id != ? and is_active = 1",
                (email, user["id"]),
            ).fetchone()
            if duplicate:
                raise HTTPException(status_code=400, detail="Diese E-Mail-Adresse wird bereits verwendet.")
            timestamp = now()
            con.execute(
                "update sentero_users set email = ?, display_name = ?, updated_at = ? where id = ?",
                (email, display_name, timestamp, user["id"]),
            )
            con.commit()
            row = con.execute("select * from sentero_users where id = ?", (user["id"],)).fetchone()
        return {"user": self._public_user(dict(row))}

    def change_password(self, payload: dict[str, Any], request: Request) -> dict[str, bool]:
        check_rate_limit(f"change-password:{client_key(request)}", limit=8, window_seconds=300)
        user = self.user_from_request(request, required=True)
        current_password = str(payload.get("current_password") or "")
        new_password = str(payload.get("new_password") or payload.get("password") or "")
        confirm = str(payload.get("new_password_confirm") or payload.get("password_confirm") or "")
        with self.mapping.connect() as con:
            row = con.execute("select * from sentero_users where id = ? and is_active = 1", (user["id"],)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Das aktuelle Passwort ist nicht korrekt.")
        if len(new_password) < 8:
            raise HTTPException(status_code=400, detail="Das neue Passwort muss mindestens 8 Zeichen lang sein.")
        if new_password != confirm:
            raise HTTPException(status_code=400, detail="Die Passwörter stimmen nicht überein.")
        with self.mapping.connect() as con:
            con.execute(
                "update sentero_users set password_hash = ?, updated_at = ? where id = ?",
                (hash_password(new_password), now(), user["id"]),
            )
            con.commit()
        return {"ok": True}

    def forgot_password(self, payload: dict[str, Any], request: Request) -> dict[str, str]:
        check_rate_limit(f"forgot:{client_key(request)}", limit=5, window_seconds=900)
        email = normalize_email(payload.get("email"))
        with self.mapping.connect() as con:
            row = con.execute("select * from sentero_users where lower(email) = ? and is_active = 1", (email,)).fetchone()
            email_setting = con.execute("select * from notification_channel_settings where channel = 'email'").fetchone()
        smtp_ready = False
        if email_setting:
            config = decode_json(email_setting["config_json"])
            smtp_ready = bool(email_setting["enabled"] and config.get("smtp_host") and config.get("smtp_user"))
        if not smtp_ready:
            return {"message": "Passwort zurücksetzen ist noch nicht verfügbar. Bitte wenden Sie sich an den Administrator."}
        if row:
            token = secrets.token_urlsafe(32)
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_MINUTES)).isoformat(timespec="seconds")
            with self.mapping.connect() as con:
                con.execute(
                    "insert into sentero_password_reset_tokens (user_id, token_hash, expires_at, created_at) values (?, ?, ?, ?)",
                    (row["id"], hash_token(token), expires_at, now()),
                )
                con.commit()
            reset_url = str(request.url_for("sentero_auth_status")).replace("/api/sentero/auth/status", f"/sentero?reset_token={token}")
            try:
                self._send_reset_email(email, reset_url, config)
            except Exception:
                return {"message": "Passwort zurücksetzen ist noch nicht verfügbar. Bitte wenden Sie sich an den Administrator."}
        return {"message": "Wenn die E-Mail-Adresse bekannt ist, erhalten Sie eine Nachricht zum Zurücksetzen des Passworts."}

    def reset_password(self, payload: dict[str, Any]) -> dict[str, bool]:
        token = str(payload.get("token") or "").strip()
        password = str(payload.get("password") or "")
        confirm = str(payload.get("password_confirm") or payload.get("passwordConfirmation") or "")
        if len(password) < 8:
            raise HTTPException(status_code=400, detail="Das Passwort muss mindestens 8 Zeichen lang sein.")
        if password != confirm:
            raise HTTPException(status_code=400, detail="Die Passwörter stimmen nicht überein.")
        token_hash = hash_token(token)
        with self.mapping.connect() as con:
            row = con.execute(
                """select * from sentero_password_reset_tokens
                   where token_hash = ? and used_at is null and expires_at > ?
                   order by id desc""",
                (token_hash, now()),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="Der Link ist ungültig oder abgelaufen.")
            timestamp = now()
            con.execute("update sentero_users set password_hash = ?, updated_at = ? where id = ?", (hash_password(password), timestamp, row["user_id"]))
            con.execute("update sentero_password_reset_tokens set used_at = ? where id = ?", (timestamp, row["id"]))
            con.execute("delete from sentero_sessions where user_id = ?", (row["user_id"],))
            con.commit()
        return {"ok": True}

    def user_from_request(self, request: Request, required: bool = True) -> dict[str, Any] | None:
        token = self._token_from_request(request)
        if not token:
            if required:
                raise HTTPException(status_code=401, detail="Nicht angemeldet.")
            return None
        token_hash = hash_token(token)
        with self.mapping.connect() as con:
            row = con.execute(
                """select u.* from sentero_sessions s
                   join sentero_users u on u.id = s.user_id
                   where s.token_hash = ? and s.expires_at > ? and u.is_active = 1""",
                (token_hash, now()),
            ).fetchone()
        if not row:
            if required:
                raise HTTPException(status_code=401, detail="Nicht angemeldet.")
            return None
        return dict(row)

    def _user_count(self) -> int:
        with self.mapping.connect() as con:
            row = con.execute("select count(*) as count from sentero_users where is_active = 1").fetchone()
        return int(row["count"] if row else 0)

    def _create_session(self, user_id: int, request: Request) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
        with self.mapping.connect() as con:
            con.execute(
                "insert into sentero_sessions (user_id, token_hash, expires_at, created_at) values (?, ?, ?, ?)",
                (user_id, hash_token(token), expires_at, now()),
            )
            con.commit()
        return token

    def _set_cookie(self, response: Response, token: str, request: Request) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=SESSION_DAYS * 24 * 60 * 60,
            path="/",
        )

    def _token_from_request(self, request: Request) -> str:
        return str(request.cookies.get(SESSION_COOKIE) or "").strip()

    def _public_user(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user.get("id"),
            "email": user.get("email"),
            "display_name": user.get("display_name"),
            "role": user.get("role"),
            "last_login_at": user.get("last_login_at"),
        }

    def _send_reset_email(self, email: str, reset_url: str, config: dict[str, Any]) -> None:
        message = EmailMessage()
        message["Subject"] = "Sentero Passwort zurücksetzen"
        message["From"] = EMAIL_FROM
        message["To"] = email
        message.set_content(
            "\n\n".join([
                "Sie können Ihr Sentero-Passwort über den folgenden Link zurücksetzen:",
                reset_url,
                "Wenn Sie diese Anfrage nicht gestellt haben, können Sie diese Nachricht ignorieren.",
            ])
        )
        port = int(config.get("smtp_port") or 587)
        with smtplib.SMTP(str(config.get("smtp_host")), port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(str(config.get("smtp_user")), str(config.get("smtp_password") or ""))
            smtp.send_message(message, from_addr=str(config.get("smtp_user") or EMAIL_FROM), to_addrs=[email])


def normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = str(encoded or "").split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def decode_json(value: Any) -> dict[str, Any]:
    try:
        data = value if isinstance(value, dict) else json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "local")


def check_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    now_ts = datetime.now(timezone.utc).timestamp()
    recent = [value for value in RATE_LIMIT.get(key, []) if now_ts - value < window_seconds]
    if len(recent) >= limit:
        raise HTTPException(status_code=429, detail="Bitte versuchen Sie es später erneut.")
    recent.append(now_ts)
    RATE_LIMIT[key] = recent
