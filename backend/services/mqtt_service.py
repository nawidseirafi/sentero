from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from backend.config import config_int, config_str
from backend.logging_config import get_logger, is_debug_logging
from backend.paths import DATA_DIR, ENV_PATH

load_dotenv(ENV_PATH)
logger = get_logger(__name__)
DEFAULT_DB_PATH = DATA_DIR / "sentero.db"
MQTT_STATE_TABLE = "mqtt_last_states"


@dataclass(frozen=True)
class MqttMessage:
    topic: str
    payload: Any
    raw_payload: str
    received_at: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MqttService:
    def __init__(self, database_path: Path | None = None) -> None:
        self.host = (os.getenv("SENTERO_MQTT_HOST") or os.getenv("MQTT_HOST") or config_str("mqtt.host", "localhost") or "localhost").strip()
        self.port = int(os.getenv("SENTERO_MQTT_PORT") or os.getenv("MQTT_PORT") or config_int("mqtt.port", 1883))
        self.username = (os.getenv("SENTERO_MQTT_USERNAME") or os.getenv("MQTT_USERNAME") or "").strip()
        self.password = os.getenv("SENTERO_MQTT_PASSWORD") or os.getenv("MQTT_PASSWORD") or ""
        self.database_path = database_path or DEFAULT_DB_PATH

        # Persistent MQTT listener state. The old implementation created a new
        # short-lived subscription for every snapshot. Non-retained Zigbee2MQTT
        # device states were therefore very easy to miss. The live listener keeps
        # the latest value of every subscribed topic in memory instead.
        self._live_client = None
        self._live_lock = threading.RLock()
        self._live_messages: dict[str, MqttMessage] = {}
        self._live_topics: set[str] = set()
        self._live_started = False
        self._live_connected = threading.Event()
        # Consumers can subscribe to normalized raw MQTT messages without owning
        # another broker connection. This is used by the behavior event recorder
        # so motion/presence changes are persisted at message time instead of only
        # during the periodic snapshot loop.
        self._message_listeners: list[Callable[[str, Any, str], None]] = []

        # The in-memory MQTT cache is mirrored to SQLite. This gives Sentero an
        # immediate last-known state after a backend restart, even when a
        # Zigbee2MQTT device topic is not retained by the broker.
        self._ensure_persistent_cache_schema()
        self._restore_persistent_cache()

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

    def start_listener(self, topics: list[str] | tuple[str, ...] | set[str] | None = None) -> None:
        """Start one persistent MQTT subscription and cache the last message per topic.

        Calling this method repeatedly is safe. New topic filters are subscribed on
        the already-running client without creating another connection.
        """
        requested = {str(topic or "").strip() for topic in (topics or ["zigbee2mqtt/#"]) if str(topic or "").strip()}
        if not requested:
            return

        with self._live_lock:
            if self._live_started and self._live_client is not None:
                new_topics = requested - self._live_topics
                self._live_topics.update(requested)
                client = self._live_client
            else:
                new_topics = set()
                self._live_topics.update(requested)
                client = self._client()
                self._live_client = client
                self._live_started = True
                self._live_connected.clear()

                def on_connect(client, userdata, flags, reason_code, properties=None):  # type: ignore[no-untyped-def]
                    if not self._reason_code_ok(reason_code):
                        logger.error(
                            "MQTT live listener connection rejected",
                            extra={"component": "mqtt", "host": self.host, "port": self.port, "reason_code": str(reason_code)},
                        )
                        return
                    with self._live_lock:
                        subscriptions = sorted(self._live_topics)
                    for subscription in subscriptions:
                        client.subscribe(subscription)
                    self._live_connected.set()
                    logger.info(
                        "MQTT live listener connected",
                        extra={"component": "mqtt", "host": self.host, "port": self.port, "topics": subscriptions},
                    )

                def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):  # type: ignore[no-untyped-def]
                    self._live_connected.clear()
                    logger.warning(
                        "MQTT live listener disconnected",
                        extra={"component": "mqtt", "host": self.host, "port": self.port, "reason_code": str(reason_code)},
                    )

                def on_message(client, userdata, message):  # type: ignore[no-untyped-def]
                    raw = message.payload.decode("utf-8", errors="replace")
                    # An empty retained payload is MQTT's conventional retained-message
                    # deletion signal. Remove the old cached value as well.
                    if raw == "":
                        with self._live_lock:
                            self._live_messages.pop(message.topic, None)
                        self._delete_persistent_message(message.topic)
                        return
                    try:
                        payload: Any = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = raw
                    cached = MqttMessage(topic=message.topic, payload=payload, raw_payload=raw, received_at=utc_now())
                    with self._live_lock:
                        self._live_messages[message.topic] = cached
                    self._persist_message(cached)
                    self._notify_message_listeners(cached)
                    if is_debug_logging():
                        logger.debug(
                            "MQTT live message cached",
                            extra={"component": "mqtt", "topic": message.topic, "payload": payload},
                        )

                client.on_connect = on_connect
                client.on_disconnect = on_disconnect
                client.on_message = on_message
                try:
                    client.reconnect_delay_set(min_delay=1, max_delay=30)
                    # connect_async + loop_start lets paho reconnect automatically if
                    # the broker is temporarily unavailable during application startup.
                    client.connect_async(self.host, self.port, keepalive=30)
                    client.loop_start()
                    logger.info(
                        "MQTT live listener started",
                        extra={"component": "mqtt", "host": self.host, "port": self.port, "topics": sorted(self._live_topics)},
                    )
                except Exception:
                    self._live_client = None
                    self._live_started = False
                    self._live_connected.clear()
                    logger.exception("MQTT live listener failed to start", extra={"component": "mqtt"})
                    raise
                return

        # Already running: subscribe any newly requested filters immediately.
        for subscription in sorted(new_topics):
            try:
                client.subscribe(subscription)
                logger.info("MQTT live listener subscribed", extra={"component": "mqtt", "topic": subscription})
            except Exception:
                logger.exception("MQTT live listener subscribe failed", extra={"component": "mqtt", "topic": subscription})
                raise

    def add_message_listener(self, listener: Callable[[str, Any, str], None]) -> None:
        """Register a callback invoked for every non-empty MQTT message.

        Listeners run on Paho's network thread, so they must be thread-safe and
        should catch/contain their own expensive work. Duplicate registration is
        ignored.
        """
        with self._live_lock:
            if listener not in self._message_listeners:
                self._message_listeners.append(listener)

    def remove_message_listener(self, listener: Callable[[str, Any, str], None]) -> None:
        with self._live_lock:
            self._message_listeners = [item for item in self._message_listeners if item != listener]

    def _notify_message_listeners(self, message: MqttMessage) -> None:
        with self._live_lock:
            listeners = list(self._message_listeners)
        if not listeners:
            return
        received_at = str(message.received_at or utc_now())
        for listener in listeners:
            try:
                listener(message.topic, message.payload, received_at)
            except Exception:
                # A behavior/history consumer must never break MQTT ingestion.
                logger.exception(
                    "MQTT message listener failed",
                    extra={"component": "mqtt", "topic": message.topic},
                )

    def stop_listener(self) -> None:
        with self._live_lock:
            client = self._live_client
            self._live_client = None
            self._live_started = False
            self._live_connected.clear()
        if client is None:
            return
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            logger.debug("MQTT live listener shutdown failed", exc_info=True, extra={"component": "mqtt"})
        logger.info("MQTT live listener stopped", extra={"component": "mqtt"})

    def listener_started(self) -> bool:
        with self._live_lock:
            return self._live_started

    def listener_connected(self) -> bool:
        return self._live_connected.is_set()

    def cached_messages(self, topic_filter: str | None = None) -> list[MqttMessage]:
        """Return the current last-value cache, optionally filtered like `prefix/#`."""
        clean_filter = str(topic_filter or "").strip()
        with self._live_lock:
            messages = list(self._live_messages.values())
        if not clean_filter:
            return sorted(messages, key=lambda item: item.topic)
        return sorted(
            [message for message in messages if self._topic_matches(clean_filter, message.topic)],
            key=lambda item: item.topic,
        )

    def seed_cache(self, messages: list[MqttMessage]) -> None:
        """Merge a one-shot snapshot into the live cache.

        This is mainly useful for retained bridge metadata during the first request,
        before the asynchronous listener has finished connecting.
        """
        with self._live_lock:
            for message in messages:
                if not message.topic:
                    continue
                received_at = message.received_at or utc_now()
                cached = MqttMessage(
                    topic=message.topic,
                    payload=message.payload,
                    raw_payload=message.raw_payload,
                    received_at=received_at,
                )
                self._live_messages[message.topic] = cached
                self._persist_message(cached)

    @contextmanager
    def _cache_connection(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.database_path, timeout=30)
        try:
            con.execute("pragma busy_timeout = 30000")
            con.execute("pragma journal_mode = WAL")
            yield con
        finally:
            con.close()

    def _ensure_persistent_cache_schema(self) -> None:
        try:
            with self._cache_connection() as con:
                con.execute(
                    f"""create table if not exists {MQTT_STATE_TABLE} (
                        topic text primary key,
                        payload_json text not null,
                        raw_payload text not null,
                        received_at text not null,
                        updated_at text not null
                    )"""
                )
                con.commit()
        except Exception:
            # MQTT must remain usable even if persistence cannot be initialized.
            logger.exception(
                "MQTT persistent cache schema initialization failed",
                extra={"component": "mqtt", "database_path": str(self.database_path)},
            )

    def _restore_persistent_cache(self) -> None:
        try:
            with self._cache_connection() as con:
                rows = con.execute(
                    f"select topic, payload_json, raw_payload, received_at from {MQTT_STATE_TABLE}"
                ).fetchall()
        except Exception:
            logger.exception(
                "MQTT persistent cache restore failed",
                extra={"component": "mqtt", "database_path": str(self.database_path)},
            )
            return

        restored = 0
        with self._live_lock:
            for topic, payload_json, raw_payload, received_at in rows:
                clean_topic = str(topic or "").strip()
                if not clean_topic:
                    continue
                try:
                    payload: Any = json.loads(str(payload_json))
                except (json.JSONDecodeError, TypeError):
                    payload = str(raw_payload or "")
                self._live_messages[clean_topic] = MqttMessage(
                    topic=clean_topic,
                    payload=payload,
                    raw_payload=str(raw_payload or ""),
                    received_at=str(received_at or utc_now()),
                )
                restored += 1
        logger.info(
            "MQTT persistent cache restored",
            extra={"component": "mqtt", "message_count": restored, "database_path": str(self.database_path)},
        )

    def _persist_message(self, message: MqttMessage) -> None:
        if not message.topic:
            return
        received_at = str(message.received_at or utc_now())
        try:
            payload_json = json.dumps(message.payload, ensure_ascii=False)
        except (TypeError, ValueError):
            payload_json = json.dumps(message.raw_payload, ensure_ascii=False)
        try:
            with self._cache_connection() as con:
                con.execute(
                    f"""insert into {MQTT_STATE_TABLE}
                        (topic, payload_json, raw_payload, received_at, updated_at)
                        values (?, ?, ?, ?, ?)
                        on conflict(topic) do update set
                            payload_json = excluded.payload_json,
                            raw_payload = excluded.raw_payload,
                            received_at = excluded.received_at,
                            updated_at = excluded.updated_at""",
                    (message.topic, payload_json, message.raw_payload, received_at, utc_now()),
                )
                con.commit()
        except Exception:
            logger.exception(
                "MQTT persistent cache write failed",
                extra={"component": "mqtt", "topic": message.topic, "database_path": str(self.database_path)},
            )

    def _delete_persistent_message(self, topic: str) -> None:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            return
        try:
            with self._cache_connection() as con:
                con.execute(f"delete from {MQTT_STATE_TABLE} where topic = ?", (clean_topic,))
                con.commit()
        except Exception:
            logger.exception(
                "MQTT persistent cache delete failed",
                extra={"component": "mqtt", "topic": clean_topic, "database_path": str(self.database_path)},
            )

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

    def request_response(
        self,
        request_topic: str,
        response_topic: str,
        payload: dict[str, Any] | str | int | float | bool,
        timeout: float = 8.0,
        response_filter: Callable[[Any], bool] | None = None,
    ) -> MqttMessage:
        clean_request_topic = str(request_topic or "").strip()
        clean_response_topic = str(response_topic or "").strip()
        if not clean_request_topic or not clean_response_topic:
            raise RuntimeError("MQTT request and response topics are required.")
        client = self._client()
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        response_event = threading.Event()
        connected_event = threading.Event()
        response: dict[str, MqttMessage] = {}
        started = time.perf_counter()
        logger.debug(
            "MQTT request-response start",
            extra={"component": "mqtt", "request_topic": clean_request_topic, "response_topic": clean_response_topic, "timeout": timeout},
        )

        def on_connect(client, userdata, flags, reason_code, properties=None):  # type: ignore[no-untyped-def]
            if not self._reason_code_ok(reason_code):
                connected_event.set()
                return
            client.subscribe(clean_response_topic)
            connected_event.set()
            logger.debug("MQTT response subscribed", extra={"component": "mqtt", "topic": clean_response_topic, "reason_code": str(reason_code)})

        def on_message(client, userdata, message):  # type: ignore[no-untyped-def]
            raw = message.payload.decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            if response_filter and not response_filter(parsed):
                logger.debug("MQTT response ignored", extra={"component": "mqtt", "topic": message.topic})
                return
            response["message"] = MqttMessage(topic=message.topic, payload=parsed, raw_payload=raw, received_at=utc_now())
            if is_debug_logging():
                logger.debug("MQTT response received", extra={"component": "mqtt", "topic": message.topic, "payload": parsed})
            response_event.set()

        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.host, self.port, keepalive=20)
            client.loop_start()
            if not connected_event.wait(timeout=min(timeout, 3.0)):
                raise TimeoutError(f"MQTT response subscription timed out for {clean_response_topic}")
            if is_debug_logging():
                logger.debug("MQTT request payload", extra={"component": "mqtt", "topic": clean_request_topic, "payload": payload})
            result = client.publish(clean_request_topic, body, retain=False)
            result.wait_for_publish(timeout=min(timeout, 5.0))
            if result.rc != 0:
                raise RuntimeError(f"MQTT publish failed with rc={result.rc}")
            if not response_event.wait(timeout=timeout):
                raise TimeoutError(f"MQTT response timed out for {clean_response_topic}")
            logger.debug(
                "MQTT request-response completed",
                extra={
                    "component": "mqtt",
                    "request_topic": clean_request_topic,
                    "response_topic": clean_response_topic,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return response["message"]
        except Exception:
            logger.exception("MQTT request-response failed", extra={"component": "mqtt", "request_topic": clean_request_topic, "response_topic": clean_response_topic})
            raise
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                logger.debug("MQTT disconnect failed", exc_info=True, extra={"component": "mqtt"})

    def retained_messages(self, topic: str, timeout: float = 2.5) -> list[MqttMessage]:
        """Collect messages for a short period.

        Kept for bridge/bootstrap and request-style discovery. Runtime sensor state
        should use the persistent listener/cache instead of relying on this method.
        """
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
            messages.append(MqttMessage(topic=message.topic, payload=payload, raw_payload=raw, received_at=utc_now()))
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

    @staticmethod
    def _topic_matches(topic_filter: str, topic: str) -> bool:
        # Covers the filters used by Sentero (notably `zigbee2mqtt/#`) while also
        # supporting the MQTT single-level `+` wildcard.
        filter_parts = topic_filter.strip("/").split("/")
        topic_parts = topic.strip("/").split("/")
        for index, part in enumerate(filter_parts):
            if part == "#":
                return True
            if index >= len(topic_parts):
                return False
            if part != "+" and part != topic_parts[index]:
                return False
        return len(topic_parts) == len(filter_parts)

    @staticmethod
    def _reason_code_ok(reason_code: Any) -> bool:
        try:
            return int(reason_code) == 0
        except (TypeError, ValueError):
            return str(reason_code).strip().lower() in {"0", "success"}

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
