from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from backend.config import config_int, config_str
from backend.logging_config import get_logger, is_debug_logging
from backend.paths import ENV_PATH

load_dotenv(ENV_PATH)
logger = get_logger(__name__)


@dataclass(frozen=True)
class MqttMessage:
    topic: str
    payload: Any
    raw_payload: str


class MqttService:
    def __init__(self) -> None:
        self.host = (os.getenv("SENTERO_MQTT_HOST") or os.getenv("MQTT_HOST") or config_str("mqtt.host", "localhost") or "localhost").strip()
        self.port = int(os.getenv("SENTERO_MQTT_PORT") or os.getenv("MQTT_PORT") or config_int("mqtt.port", 1883))
        self.username = (os.getenv("SENTERO_MQTT_USERNAME") or os.getenv("MQTT_USERNAME") or "").strip()
        self.password = os.getenv("SENTERO_MQTT_PASSWORD") or os.getenv("MQTT_PASSWORD") or ""
        logger.debug(
            "MQTT service configured",
            extra={"component": "mqtt", "host": self.host, "port": self.port, "username_configured": bool(self.username)},
        )

    def configured(self) -> bool:
        return bool(self.host and self.port)

    def client_available(self) -> bool:
        try:
            self._client()
            return True
        except RuntimeError:
            return False

    def publish(self, topic: str, payload: dict[str, Any] | str | int | float | bool, retain: bool = False) -> dict[str, Any]:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            raise RuntimeError("MQTT topic is required.")
        client = self._client()
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        started = time.perf_counter()
        logger.debug("MQTT publish start", extra={"component": "mqtt", "topic": clean_topic, "retain": retain})
        try:
            client.connect(self.host, self.port, keepalive=20)
            logger.info("MQTT broker connected", extra={"component": "mqtt", "host": self.host, "port": self.port})
            if is_debug_logging():
                logger.debug("MQTT publish payload", extra={"component": "mqtt", "topic": clean_topic, "payload": payload})
            result = client.publish(clean_topic, body, retain=retain)
            result.wait_for_publish(timeout=5)
            if result.rc != 0:
                raise RuntimeError(f"MQTT publish failed with rc={result.rc}")
            logger.debug(
                "MQTT publish completed",
                extra={"component": "mqtt", "topic": clean_topic, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
            return {"ok": True, "topic": clean_topic, "payload": payload}
        except Exception:
            logger.exception("MQTT publish failed", extra={"component": "mqtt", "topic": clean_topic})
            raise
        finally:
            try:
                client.disconnect()
                logger.info("MQTT broker disconnected", extra={"component": "mqtt", "host": self.host, "port": self.port})
            except Exception:
                logger.debug("MQTT disconnect failed", exc_info=True, extra={"component": "mqtt"})

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list[MqttMessage]:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            return []
        messages: list[MqttMessage] = []
        client = self._client()
        started = time.perf_counter()
        logger.debug("MQTT retained snapshot start", extra={"component": "mqtt", "topic": clean_topic, "timeout": timeout})

        def on_connect(client, userdata, flags, reason_code, properties=None):  # type: ignore[no-untyped-def]
            client.subscribe(clean_topic)
            logger.debug(
                "MQTT subscribed",
                extra={"component": "mqtt", "topic": clean_topic, "reason_code": str(reason_code)},
            )

        def on_message(client, userdata, message):  # type: ignore[no-untyped-def]
            raw = message.payload.decode("utf-8", errors="replace")
            if raw == "":
                return
            try:
                payload: Any = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            messages.append(MqttMessage(topic=message.topic, payload=payload, raw_payload=raw))
            if is_debug_logging():
                logger.debug("MQTT message received", extra={"component": "mqtt", "topic": message.topic, "payload": payload})

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.host, self.port, keepalive=20)
            logger.debug("MQTT broker connected", extra={"component": "mqtt", "host": self.host, "port": self.port})
            client.loop_start()
            deadline = time.monotonic() + max(timeout, 0.1)
            while time.monotonic() < deadline:
                time.sleep(0.05)
        except Exception:
            logger.exception("MQTT retained snapshot failed", extra={"component": "mqtt", "topic": clean_topic})
            raise
        finally:
            try:
                client.loop_stop()
                client.disconnect()
                logger.debug("MQTT broker disconnected", extra={"component": "mqtt", "host": self.host, "port": self.port})
            except Exception:
                logger.debug("MQTT disconnect failed", exc_info=True, extra={"component": "mqtt"})
        logger.debug(
            "MQTT retained snapshot completed",
            extra={
                "component": "mqtt",
                "topic": clean_topic,
                "message_count": len(messages),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return messages

    def _client(self):  # type: ignore[no-untyped-def]
        try:
            import paho.mqtt.client as mqtt
        except Exception as exc:
            logger.exception("MQTT client package unavailable", extra={"component": "mqtt"})
            raise RuntimeError("Python-Paket 'paho-mqtt' ist fuer MQTT nicht installiert.") from exc
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        return client
