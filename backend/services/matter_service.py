from __future__ import annotations

import re
import socket
from typing import Any

from backend.logging_config import get_logger
from backend.services.homeassistant_service import HomeAssistantService


class MatterCommissioningUnavailable(RuntimeError):
    pass


logger = get_logger(__name__)


class MatterService:
    def __init__(self, ha: HomeAssistantService | None = None) -> None:
        self.ha = ha or HomeAssistantService()

    def validate_setup_payload(self, setup_code: str | None = None, qr_payload: str | None = None) -> str:
        payload = str(qr_payload or setup_code or "").strip()
        if not payload:
            raise ValueError("setup code required")
        if payload.startswith("MT:"):
            return payload
        compact = re.sub(r"[\s-]+", "", payload)
        if re.fullmatch(r"\d{11,21}", compact):
            return compact
        raise ValueError("invalid setup code")

    def check_ready(self) -> dict[str, Any]:
        states = self.ha.get_states()
        return {"ok": True, "state_count": len(states), "ha_url": self.ha.base_url}

    def capabilities(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "home_assistant": False,
            "matter_integration": False,
            "matter_server": False,
            "commissioning_available": False,
            "ipv6_available": ipv6_available(),
            "thread_available": False,
            "message": "Home Assistant nicht erreichbar",
            "details": {},
        }
        try:
            states = self.ha.get_states()
            result["home_assistant"] = True
            result["details"]["state_count"] = len(states)
            result["details"]["ha_url"] = self.ha.base_url
            logger.info("Matter capability check: HA erreichbar ha_url=%s states=%s", self.ha.base_url, len(states))
        except Exception as exc:
            result["details"]["home_assistant_error"] = str(exc)
            logger.info("Matter capability check: HA nicht erreichbar error=%s", exc)
            return result

        entries = self._websocket_list("config_entries/get")
        if entries is None:
            entries = self._websocket_list("config/config_entries/entry/list")
        if entries is not None:
            result["details"]["config_entries_checked"] = True
            matter_entries = [entry for entry in entries if str(entry.get("domain") or "").lower() == "matter"]
            thread_entries = [entry for entry in entries if str(entry.get("domain") or "").lower() in {"thread", "otbr"}]
            result["matter_integration"] = bool(matter_entries)
            result["thread_available"] = bool(thread_entries)
            result["matter_server"] = any(str(entry.get("state") or entry.get("source") or "").lower() not in {"setup_error", "not_loaded"} for entry in matter_entries)
            result["details"]["matter_entries"] = sanitize_entries(matter_entries)
            result["details"]["thread_entries"] = sanitize_entries(thread_entries)
        else:
            result["details"]["config_entries_checked"] = False
            result["matter_integration"] = self._registry_has_matter_devices()
            result["matter_server"] = result["matter_integration"]

        probe: dict[str, Any] | None = None
        if not result["matter_integration"] and not result["details"].get("config_entries_checked"):
            probe = self._commissioning_probe()
            result["details"]["commissioning_probe"] = probe
            if probe.get("available"):
                result["matter_integration"] = True
                result["matter_server"] = True
                result["commissioning_available"] = True
                result["message"] = "Sensor-Einrichtung bereit"
                logger.info("Matter capability check: Commissioning Endpoint verfügbar")
                return result

        if not result["matter_integration"]:
            result["message"] = "Matter Integration fehlt"
            logger.info("Matter capability check: Matter Integration fehlt")
            return result
        if not result["matter_server"]:
            result["message"] = "Matter Server nicht erreichbar"
            logger.info("Matter capability check: Matter Server fehlt")
            return result

        if probe is None:
            probe = self._commissioning_probe()
        result["details"]["commissioning_probe"] = probe
        result["commissioning_available"] = bool(probe.get("available"))
        if not result["commissioning_available"]:
            result["message"] = "Matter Commissioning nicht verfügbar"
            logger.info("Matter capability check: Commissioning Endpoint fehlt response=%s", probe)
            return result
        result["message"] = "Sensor-Einrichtung bereit"
        logger.info("Matter capability check: Commissioning Endpoint verfügbar")
        return result

    def commission(self, setup_payload: str) -> dict[str, Any]:
        try:
            response = self.ha.matter_commission(setup_payload, network_only=True, timeout=120)
        except Exception as exc:
            if is_unavailable_error(str(exc)):
                raise MatterCommissioningUnavailable("Matter Commissioning nicht verfügbar") from exc
            raise
        ok = bool(response.get("success", True))
        if response.get("error"):
            ok = False
        if not ok and is_unavailable_response(response):
            raise MatterCommissioningUnavailable("Matter Commissioning nicht verfügbar")
        return {"ok": ok, "response": response}

    def _websocket_list(self, command_type: str) -> list[dict[str, Any]] | None:
        try:
            response = self.ha.websocket_command({"type": command_type}, timeout=12)
        except Exception as exc:
            logger.info("Matter capability check: websocket command unavailable type=%s error=%s", command_type, exc)
            return None
        if response.get("success") is False:
            return None
        result = response.get("result")
        return result if isinstance(result, list) else None

    def _registry_has_matter_devices(self) -> bool:
        try:
            response = self.ha.websocket_command({"type": "config/device_registry/list"}, timeout=12)
        except Exception:
            return False
        devices = response.get("result")
        if not isinstance(devices, list):
            return False
        return any("matter" in str(device).lower() for device in devices)

    def _commissioning_probe(self) -> dict[str, Any]:
        """
        Testet ob der Matter Server WebSocket (Port 5580) erreichbar ist.
        Kein echter Pairing-Versuch — commission_with_code mit Dummy-Code
        schlägt erwartungsgemäß fehl, beweist aber dass der Endpoint existiert.
        """
        import asyncio, websockets, json

        url = self.ha._matter_websocket_url()

        async def _probe():
            try:
                async with websockets.connect(url, open_timeout=5) as ws:
                    await ws.send(json.dumps({
                        "message_id": "probe",
                        "command": "commission_with_code",
                        "args": {"code": "00000000000", "network_only": True}
                    }))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("message_id") == "probe":
                            return msg
            except Exception as exc:
                raise exc

        try:
            response = asyncio.run(_probe())
        except Exception as exc:
            return {"available": False, "error": str(exc)}

        # Jeder Response (auch Fehler) bedeutet: Server ist da
        if response.get("error", {}).get("code") == "unknown_command":
            return {"available": False, "response": response}
        return {"available": True, "response": response}


def is_unavailable_response(response: dict[str, Any]) -> bool:
    error = response.get("error") if isinstance(response, dict) else None
    if isinstance(error, dict):
        text = " ".join(str(error.get(key) or "") for key in ("code", "message", "translation_key"))
        return is_unavailable_error(text)
    return is_unavailable_error(str(response))


def is_unavailable_error(text: str) -> bool:
    value = text.lower()
    return any(marker in value for marker in ("unknown_command", "unknown command", "not found", "matter", "commission_with_code")) and any(
        marker in value for marker in ("unknown", "not found", "unavailable", "not available")
    )


def ipv6_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.close()
        return True
    except OSError:
        return False


def sanitize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = []
    for entry in entries:
        clean.append({
            "domain": entry.get("domain"),
            "title": entry.get("title"),
            "state": entry.get("state"),
            "source": entry.get("source"),
        })
    return clean
