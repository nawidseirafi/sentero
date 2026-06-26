from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.config import config_float, config_int
from backend.logging_config import get_logger, is_debug_logging
from backend.services.device_mapping_service import DeviceMappingService, now

logger = get_logger(__name__)

DEFAULT_DISCOVERY_PORT = 37020
DEFAULT_DISCOVERY_WAIT_SECONDS = 6.0
DEFAULT_HTTP_PORT = 80
DEFAULT_STORE_INTERVAL_SECONDS = 15.0
DISCOVERY_TYPE = "sentero-discovery"


@dataclass(frozen=True)
class PendingEsp32Sensor:
    device_id: str
    ip_address: str
    http_port: int
    model: str | None
    firmware: str | None
    sensor_type: str
    capabilities: list[str]
    last_seen_at: str


class Esp32DiscoveryService:
    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
        self._listener_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists esp32_pending_sensors (
                    device_id text primary key,
                    ip_address text not null,
                    http_port integer not null default 80,
                    model text,
                    firmware text,
                    sensor_type text not null,
                    capabilities_json text not null,
                    raw_payload_json text,
                    first_seen_at text not null,
                    last_seen_at text not null,
                    status text not null
                )"""
            )
            columns = {row["name"] for row in con.execute("pragma table_info(esp32_pending_sensors)").fetchall()}
            if "http_port" not in columns:
                con.execute("alter table esp32_pending_sensors add column http_port integer not null default 80")
            con.commit()

    def status(self) -> dict[str, Any]:
        sensors = [sensor_to_public(sensor) for sensor in self.pending()]
        return {
            "listening": self.is_listening(),
            "port": self.port(),
            "pending": sensors,
        }

    def ensure_listening(self) -> None:
        if not self.enabled():
            return
        with self._lock:
            if self._listener_thread and self._listener_thread.is_alive():
                return
            self._stop_event.clear()
            self._listener_thread = threading.Thread(target=self._listen_loop, name="sentero-esp32-discovery", daemon=True)
            self._listener_thread.start()
            logger.info("ESP32 UDP discovery listener started", extra={"component": "esp32_discovery", "port": self.port()})

    def stop(self) -> None:
        self._stop_event.set()

    def is_listening(self) -> bool:
        return bool(self._listener_thread and self._listener_thread.is_alive())

    def wait_for_pending(self, device_id: str | None = None, timeout: float | None = None) -> PendingEsp32Sensor | None:
        self.ensure_listening()
        deadline = time.monotonic() + (timeout if timeout is not None else self.wait_timeout())
        while time.monotonic() <= deadline:
            sensor = self.get_pending(device_id) if device_id else self.latest_pending()
            if sensor:
                return sensor
            time.sleep(0.2)
        return None

    def pending(self) -> list[PendingEsp32Sensor]:
        with self.mapping.connect() as con:
            rows = con.execute(
                "select * from esp32_pending_sensors where status = 'pending' order by last_seen_at desc"
            ).fetchall()
        return [row_to_sensor(dict(row)) for row in rows]

    def latest_pending(self) -> PendingEsp32Sensor | None:
        sensors = self.pending()
        return sensors[0] if sensors else None

    def get_pending(self, device_id: str | None) -> PendingEsp32Sensor | None:
        clean_id = str(device_id or "").strip()
        if not clean_id:
            return None
        with self.mapping.connect() as con:
            row = con.execute(
                "select * from esp32_pending_sensors where device_id = ? and status = 'pending'",
                (clean_id,),
            ).fetchone()
        return row_to_sensor(dict(row)) if row else None

    def mark_provisioned(self, device_id: str) -> None:
        with self.mapping.connect() as con:
            con.execute(
                "update esp32_pending_sensors set status = 'provisioned', last_seen_at = ? where device_id = ?",
                (now(), device_id),
            )
            con.commit()

    def ingest_datagram(self, payload: bytes | str | dict[str, Any], address: tuple[str, int] | str) -> PendingEsp32Sensor | None:
        try:
            data = decode_payload(payload)
            ip_address = address[0] if isinstance(address, tuple) else str(address)
            sensor = validate_discovery_payload(data, ip_address)
            stored = self._store(sensor, data)
            log_extra = {
                "component": "esp32_discovery",
                "device_id": sensor.device_id,
                "sensor_type": sensor.sensor_type,
                "ip_address": ip_address,
                "http_port": sensor.http_port,
            }
            if stored:
                logger.info("ESP32 presence sensor discovered", extra=log_extra)
            else:
                logger.debug("ESP32 presence sensor heartbeat received", extra=log_extra)
            return sensor
        except ValueError as exc:
            logger.warning("Invalid ESP32 discovery payload", extra={"component": "esp32_discovery", "reason": str(exc)})
            return None
        except Exception:
            logger.exception("Failed to process ESP32 discovery payload", extra={"component": "esp32_discovery"})
            return None

    def port(self) -> int:
        return int(os.getenv("SENTERO_ESP32_DISCOVERY_PORT") or config_int("esp32.discovery_port", DEFAULT_DISCOVERY_PORT))

    def wait_timeout(self) -> float:
        return float(os.getenv("SENTERO_ESP32_DISCOVERY_WAIT_TIMEOUT") or config_float("esp32.discovery_wait_timeout", DEFAULT_DISCOVERY_WAIT_SECONDS))

    def enabled(self) -> bool:
        value = str(os.getenv("SENTERO_ESP32_DISCOVERY_ENABLED") or "").strip().lower()
        if value:
            return value in {"1", "true", "yes", "on"}
        return True

    def _listen_loop(self) -> None:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1.0)
            sock.bind(("", self.port()))
            while not self._stop_event.is_set():
                try:
                    payload, address = sock.recvfrom(8192)
                    if is_debug_logging():
                        logger.debug("ESP32 UDP discovery datagram received", extra={"component": "esp32_discovery", "address": address[0]})
                    self.ingest_datagram(payload, address)
                except socket.timeout:
                    continue
        except OSError:
            logger.exception("ESP32 UDP discovery listener failed", extra={"component": "esp32_discovery", "port": self.port()})
        finally:
            if sock:
                sock.close()

    def store_interval(self) -> float:
        return float(os.getenv("SENTERO_ESP32_DISCOVERY_STORE_INTERVAL") or config_float("esp32.discovery_store_interval", DEFAULT_STORE_INTERVAL_SECONDS))

    def _store(self, sensor: PendingEsp32Sensor, raw_payload: dict[str, Any]) -> bool:
        with self.mapping.connect() as con:
            existing = con.execute(
                "select * from esp32_pending_sensors where device_id = ?",
                (sensor.device_id,),
            ).fetchone()
            if existing and not self._should_store(dict(existing), sensor):
                return False
            first_seen = existing["first_seen_at"] if existing else sensor.last_seen_at
            con.execute(
                """insert into esp32_pending_sensors
                   (device_id, ip_address, http_port, model, firmware, sensor_type, capabilities_json, raw_payload_json, first_seen_at, last_seen_at, status)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                   on conflict(device_id) do update set
                     ip_address = excluded.ip_address,
                     http_port = excluded.http_port,
                     model = excluded.model,
                     firmware = excluded.firmware,
                     sensor_type = excluded.sensor_type,
                     capabilities_json = excluded.capabilities_json,
                     raw_payload_json = excluded.raw_payload_json,
                     last_seen_at = excluded.last_seen_at,
                     status = 'pending'""",
                (
                    sensor.device_id,
                    sensor.ip_address,
                    sensor.http_port,
                    sensor.model,
                    sensor.firmware,
                    sensor.sensor_type,
                    json.dumps(sensor.capabilities, ensure_ascii=False),
                    json.dumps(raw_payload, ensure_ascii=False),
                    first_seen,
                    sensor.last_seen_at,
                ),
            )
            con.commit()
            return True

    def _should_store(self, existing: dict[str, Any], sensor: PendingEsp32Sensor) -> bool:
        if existing.get("status") != "pending":
            return True
        if str(existing.get("ip_address") or "") != sensor.ip_address:
            return True
        if valid_port(existing.get("http_port")) != sensor.http_port:
            return True
        if str(existing.get("model") or "") != str(sensor.model or ""):
            return True
        if str(existing.get("firmware") or "") != str(sensor.firmware or ""):
            return True
        try:
            existing_capabilities = json.loads(existing.get("capabilities_json") or "[]")
        except json.JSONDecodeError:
            existing_capabilities = []
        if sorted(str(item) for item in existing_capabilities) != sorted(sensor.capabilities):
            return True
        return seconds_since(existing.get("last_seen_at")) >= self.store_interval()


def decode_payload(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        text = payload.decode("utf-8")
    else:
        text = str(payload)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("payload_not_object")
    return data


def validate_discovery_payload(payload: dict[str, Any], ip_address: str) -> PendingEsp32Sensor:
    if payload.get("type") != DISCOVERY_TYPE:
        raise ValueError("wrong_type")
    if int(payload.get("protocol") or 0) != 1:
        raise ValueError("unsupported_protocol")
    device_id = str(payload.get("device_id") or payload.get("deviceId") or "").strip()
    if not device_id:
        raise ValueError("missing_device_id")
    sensor_type = str(payload.get("sensor_type") or payload.get("sensorType") or "").strip() or "presence_radar"
    if sensor_type not in {"presence_radar", "presence_sensor"}:
        raise ValueError("unsupported_sensor_type")
    capabilities = payload.get("capabilities") or []
    if not isinstance(capabilities, list):
        capabilities = []
    return PendingEsp32Sensor(
        device_id=device_id,
        ip_address=ip_address,
        http_port=valid_port(payload.get("http_port") or payload.get("httpPort") or payload.get("provisioning_port") or payload.get("provisioningPort")),
        model=clean_text(payload.get("model")),
        firmware=clean_text(payload.get("firmware")),
        sensor_type="presence_radar",
        capabilities=[str(item).strip() for item in capabilities if str(item).strip()],
        last_seen_at=now(),
    )


def row_to_sensor(row: dict[str, Any]) -> PendingEsp32Sensor:
    try:
        capabilities = json.loads(row.get("capabilities_json") or "[]")
    except json.JSONDecodeError:
        capabilities = []
    if not isinstance(capabilities, list):
        capabilities = []
    return PendingEsp32Sensor(
        device_id=str(row.get("device_id") or ""),
        ip_address=str(row.get("ip_address") or ""),
        http_port=valid_port(row.get("http_port")),
        model=clean_text(row.get("model")),
        firmware=clean_text(row.get("firmware")),
        sensor_type=str(row.get("sensor_type") or "presence_radar"),
        capabilities=[str(item) for item in capabilities],
        last_seen_at=str(row.get("last_seen_at") or ""),
    )


def sensor_to_public(sensor: PendingEsp32Sensor) -> dict[str, Any]:
    return {
        "id": sensor.device_id,
        "name": sensor.model or "Präsenzsensor",
        "type": sensor.sensor_type,
        "http_port": sensor.http_port,
        "model": sensor.model,
        "firmware": sensor.firmware,
        "capabilities": sensor.capabilities,
        "last_seen_at": sensor.last_seen_at,
    }


def clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def valid_port(value: Any) -> int:
    try:
        port = int(value or DEFAULT_HTTP_PORT)
    except (TypeError, ValueError):
        return DEFAULT_HTTP_PORT
    return port if 1 <= port <= 65535 else DEFAULT_HTTP_PORT


def seconds_since(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return DEFAULT_STORE_INTERVAL_SECONDS
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
    except ValueError:
        return DEFAULT_STORE_INTERVAL_SECONDS
