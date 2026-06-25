from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from backend.logging_config import get_logger, is_debug_logging
from ..paths import CONFIG_PATH, ENV_PATH

logger = get_logger(__name__)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _resolve_secret(value: Any, env_values: dict[str, str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    keys = [
        text,
        text.upper(),
        text.replace("-", "_"),
        text.replace("-", "_").upper(),
    ]
    for key in keys:
        resolved = os.getenv(key) or env_values.get(key)
        if resolved:
            return resolved
    if re.fullmatch(r"[A-Z0-9_-]+", text):
        return ""
    return text


class HomeAssistantService:
    def __init__(self) -> None:
        env_values = _read_env_file(ENV_PATH)
        config = _read_yaml(CONFIG_PATH).get("home_assistant", {})
        self.base_url = (
            os.getenv("HA_URL")
            or env_values.get("HA_URL")
            or _resolve_secret(config.get("url"), env_values)
        ).rstrip("/")
        self.token = (
            os.getenv("HA_TOKEN")
            or env_values.get("HA_TOKEN")
            or _resolve_secret(config.get("token"), env_values)
        )
        self._connected_logged = False
        logger.debug(
            "Home Assistant service configured",
            extra={"component": "homeassistant", "ha_url": self.base_url, "token_configured": bool(self.token)},
        )

    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def get_states(self) -> list[dict[str, Any]]:
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        started = time.perf_counter()
        logger.debug("Home Assistant request start", extra={"component": "homeassistant", "method": "GET", "path": "/api/states"})
        try:
            with httpx.Client(timeout=8) as client:
                response = client.get(self._api_url("/api/states"), headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.exception("Home Assistant states request failed", extra={"component": "homeassistant", "ha_url": self.base_url})
            raise self._runtime_error("Home Assistant States konnten nicht geladen werden", exc) from exc
        logger.debug(
            "Home Assistant request completed",
            extra={
                "component": "homeassistant",
                "method": "GET",
                "path": "/api/states",
                "status_code": response.status_code,
                "item_count": len(data) if isinstance(data, list) else 0,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        if not self._connected_logged:
            logger.info("Home Assistant connected", extra={"component": "homeassistant", "ha_url": self.base_url})
            self._connected_logged = True
        if is_debug_logging():
            logger.debug("Home Assistant states response", extra={"component": "homeassistant", "item_count": len(data) if isinstance(data, list) else 0})
        return data if isinstance(data, list) else []

    def render_template(self, template: str) -> str:
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        logger.debug("Home Assistant template render start", extra={"component": "homeassistant"})
        try:
            with httpx.Client(timeout=8) as client:
                response = client.post(
                    self._api_url("/api/template"),
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={"template": template},
                )
                response.raise_for_status()
                return response.text
        except Exception as exc:
            logger.exception("Home Assistant template render failed", extra={"component": "homeassistant"})
            raise self._runtime_error("Home Assistant Template konnte nicht gerendert werden", exc) from exc

    def get_state(self, entity_id: str | None) -> dict[str, Any] | None:
        entity = (entity_id or "").strip()
        if not entity:
            return None
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        started = time.perf_counter()
        logger.debug("Home Assistant entity request start", extra={"component": "homeassistant", "entity_id": entity})
        try:
            with httpx.Client(timeout=8) as client:
                response = client.get(self._api_url(f"/api/states/{entity}"), headers=self._headers())
                if response.status_code == 404:
                    logger.warning("Home Assistant entity not found", extra={"component": "homeassistant", "entity_id": entity})
                    return None
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.exception("Home Assistant entity request failed", extra={"component": "homeassistant", "entity_id": entity})
            raise self._runtime_error(f"Home Assistant konnte {entity} nicht lesen", exc) from exc
        logger.debug(
            "Home Assistant entity request completed",
            extra={
                "component": "homeassistant",
                "entity_id": entity,
                "status_code": response.status_code,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return data if isinstance(data, dict) else None

    def fetch_entity_state(self, entity_id: str | None) -> dict[str, Any] | None:
        try:
            state = self.get_state(entity_id)
        except Exception:
            return None
        if not state:
            return None
        value = state.get("state")
        if value in (None, "", "unknown", "unavailable"):
            return None
        return state

    def get_calendars(self) -> list[dict[str, Any]]:
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        logger.debug("Home Assistant calendars request start", extra={"component": "homeassistant"})
        try:
            with httpx.Client(timeout=8) as client:
                response = client.get(self._api_url("/api/calendars"), headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.exception("Home Assistant calendars request failed", extra={"component": "homeassistant"})
            raise self._runtime_error("Home Assistant Kalender konnten nicht geladen werden", exc) from exc
        return data if isinstance(data, list) else []

    def get_calendar_events(self, entity_id: str, start: str, end: str) -> list[dict[str, Any]]:
        clean_entity_id = str(entity_id or "").strip()
        if not clean_entity_id:
            return []
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        logger.debug("Home Assistant calendar events request start", extra={"component": "homeassistant", "entity_id": clean_entity_id})
        try:
            with httpx.Client(timeout=8) as client:
                response = client.get(
                    self._api_url(f"/api/calendars/{clean_entity_id}"),
                    headers=self._headers(),
                    params={"start": start, "end": end},
                )
                if response.status_code == 404:
                    return []
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.exception("Home Assistant calendar events request failed", extra={"component": "homeassistant", "entity_id": clean_entity_id})
            raise self._runtime_error(f"Home Assistant Kalender {clean_entity_id} konnte nicht gelesen werden", exc) from exc
        return data if isinstance(data, list) else []

    def call_service(self, domain: str, service: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        clean_domain = str(domain or "").strip()
        clean_service = str(service or "").strip()
        if not clean_domain or not clean_service:
            raise RuntimeError("Home Assistant Service ist unvollstaendig.")
        logger.debug(
            "Home Assistant service call start",
            extra={"component": "homeassistant", "domain": clean_domain, "service": clean_service},
        )
        try:
            with httpx.Client(timeout=8) as client:
                response = client.post(
                    self._api_url(f"/api/services/{clean_domain}/{clean_service}"),
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.exception(
                "Home Assistant service call failed",
                extra={"component": "homeassistant", "domain": clean_domain, "service": clean_service},
            )
            raise self._runtime_error("Home Assistant Service-Aufruf fehlgeschlagen", exc) from exc
        return {"ok": True, "result": data}

    def websocket_command(self, command: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        """Sendet einen Command an die HA Core WebSocket API (Port 8123, mit Auth)."""
        if not self.configured():
            raise RuntimeError("Home Assistant URL oder Token ist nicht konfiguriert.")
        return asyncio.run(self._websocket_command(command, timeout=timeout))

    async def _websocket_command(self, command: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
        try:
            import websockets
        except Exception as exc:
            raise RuntimeError("Python-Paket 'websockets' ist fuer Home Assistant WebSocket nicht installiert.") from exc

        websocket_url = self._websocket_url()
        logger.debug("Home Assistant websocket command start", extra={"component": "homeassistant", "websocket_url": websocket_url})
        async with websockets.connect(websocket_url, open_timeout=timeout) as websocket:
            auth_required = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            if auth_required.get("type") != "auth_required":
                raise RuntimeError(f"Unerwartete Home Assistant WebSocket-Antwort: {auth_required}")

            await websocket.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth_result = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(f"Home Assistant WebSocket Auth fehlgeschlagen: {auth_result}")

            payload = dict(command)
            payload["id"] = 1
            await websocket.send(json.dumps(payload))
            response = json.loads(await asyncio.wait_for(websocket.recv(), timeout=timeout))
            logger.debug("Home Assistant websocket command completed", extra={"component": "homeassistant", "success": response.get("success")})
            return response if isinstance(response, dict) else {"success": False, "response": response}

    # ------------------------------------------------------------------
    # Interne Hilfsmethoden
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _api_url(self, path: str) -> str:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(
                f"Home Assistant URL ist ungueltig: {self.base_url!r}. "
                "Erwartet wird z.B. http://homeassistant.local:8123"
            )
        return f"{self.base_url}{path}"

    def _websocket_url(self) -> str:
        """HA Core WebSocket auf Port 8123 — mit Token-Auth."""
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(
                f"Home Assistant URL ist ungueltig: {self.base_url!r}. "
                "Erwartet wird z.B. http://homeassistant.local:8123"
            )
        scheme = "wss" if parsed.scheme == "https" else "ws"
        # Hostname ohne Port verwenden, damit Port 8123 aus base_url erhalten bleibt
        return f"{scheme}://{parsed.netloc}/api/websocket"

    def _runtime_error(self, message: str, exc: Exception) -> RuntimeError:
        if isinstance(exc, RuntimeError):
            return RuntimeError(f"{message}: {exc}")
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            body = exc.response.text[:200]
            return RuntimeError(f"{message}: HTTP {status} von {self.base_url}. {body}")
        if isinstance(exc, httpx.InvalidURL):
            return RuntimeError(f"{message}: Home Assistant URL ist ungueltig ({self.base_url!r}).")
        if isinstance(exc, httpx.HTTPError):
            return RuntimeError(f"{message}: {type(exc).__name__}: {exc}")
        return RuntimeError(f"{message}: {type(exc).__name__}: {exc}")
