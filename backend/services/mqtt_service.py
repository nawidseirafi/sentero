from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from backend.paths import ENV_PATH

load_dotenv(ENV_PATH)


@dataclass(frozen=True)
class MqttMessage:
    topic: str
    payload: Any
    raw_payload: str


class MqttService:
    def __init__(self) -> None:
        self.host = os.getenv("MQTT_HOST", "mosquitto").strip()
        self.port = int(os.getenv("MQTT_PORT", "1883") or "1883")
        self.username = os.getenv("MQTT_USERNAME", "").strip()
        self.password = os.getenv("MQTT_PASSWORD", "")

    def configured(self) -> bool:
        return bool(self.host and self.port)

    def publish(self, topic: str, payload: dict[str, Any] | str | int | float | bool, retain: bool = False) -> dict[str, Any]:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            raise RuntimeError("MQTT topic is required.")
        client = self._client()
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        try:
            client.connect(self.host, self.port, keepalive=20)
            result = client.publish(clean_topic, body, retain=retain)
            result.wait_for_publish(timeout=5)
            if result.rc != 0:
                raise RuntimeError(f"MQTT publish failed with rc={result.rc}")
            return {"ok": True, "topic": clean_topic, "payload": payload}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list[MqttMessage]:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            return []
        messages: list[MqttMessage] = []
        client = self._client()

        def on_connect(client, userdata, flags, reason_code, properties=None):  # type: ignore[no-untyped-def]
            client.subscribe(clean_topic)

        def on_message(client, userdata, message):  # type: ignore[no-untyped-def]
            raw = message.payload.decode("utf-8", errors="replace")
            if raw == "":
                return
            try:
                payload: Any = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            messages.append(MqttMessage(topic=message.topic, payload=payload, raw_payload=raw))

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.host, self.port, keepalive=20)
            client.loop_start()
            deadline = time.monotonic() + max(timeout, 0.1)
            while time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
        return messages

    def _client(self):  # type: ignore[no-untyped-def]
        try:
            import paho.mqtt.client as mqtt
        except Exception as exc:
            raise RuntimeError("Python-Paket 'paho-mqtt' ist fuer MQTT nicht installiert.") from exc
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if self.username:
            client.username_pw_set(self.username, self.password or None)
        return client
