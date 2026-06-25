from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .device_discovery_service import DeviceDiscoveryService
from .device_mapping_service import DB_PATH, DB_TIMEOUT_SECONDS, DeviceMappingService, configure_sqlite_connection, now
from backend.logging_config import get_logger
from backend.services.matter_service import MatterCommissioningUnavailable, MatterService

logger = get_logger(__name__)


class CommissioningService:
    def __init__(self, database_path: Path | None = None, matter: MatterService | None = None, mapping: DeviceMappingService | None = None) -> None:
        self.database_path = database_path or DB_PATH
        self.matter = matter or MatterService()
        self.discovery = DeviceDiscoveryService(self.matter.ha)
        self.mapping = mapping or DeviceMappingService(database_path=self.database_path, ha=self.matter.ha)
        self.ensure_schema()

    def connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.database_path, timeout=DB_TIMEOUT_SECONDS)
        con.row_factory = sqlite3.Row
        configure_sqlite_connection(con)
        return con

    def ensure_schema(self) -> None:
        with self.connect() as con:
            ensure_schema(con)
            con.commit()

    def start(self, setup_code: str | None = None, qr_payload: str | None = None) -> dict[str, Any]:
        payload = self.matter.validate_setup_payload(setup_code=setup_code, qr_payload=qr_payload)
        capabilities = self.matter.capabilities()
        logger.debug("Commissioning capabilities checked", extra={"component": "wizard", "homeassistant_available": bool(capabilities.get("home_assistant"))})
        if not capabilities.get("commissioning_available"):
            if not capabilities.get("matter_integration"):
                logger.warning("Matter integration missing", extra={"component": "wizard"})
            elif not capabilities.get("matter_server"):
                logger.warning("Matter server missing", extra={"component": "wizard"})
            else:
                logger.warning("Commissioning endpoint missing", extra={"component": "wizard"})
            logger.warning(
                "Pairing unavailable",
                extra={
                    "component": "wizard",
                    "reason": "unavailable",
                    "capabilities": {key: capabilities.get(key) for key in ("home_assistant", "matter_integration", "matter_server", "commissioning_available", "ipv6_available", "thread_available")},
                },
            )
            raise MatterCommissioningUnavailable(str(capabilities.get("message") or "Matter Commissioning nicht verfügbar"))
        ready = self.matter.check_ready()
        baseline = self.discovery.snapshot()
        commissioning_id = uuid.uuid4().hex
        timestamp = now()
        with self.connect() as con:
            con.execute(
                """insert into commissioned_devices
                   (id, status, setup_payload, baseline_snapshot_json, ha_ready_json, created_at, updated_at)
                   values (?, ?, ?, ?, ?, ?, ?)""",
                (commissioning_id, "waiting", payload, json.dumps(baseline, ensure_ascii=False), json.dumps(ready, ensure_ascii=False), timestamp, timestamp),
            )
            con.commit()
        logger.info("Sensor pairing started", extra={"component": "wizard", "commissioning_id": commissioning_id, "baseline_states": len(baseline)})
        thread = threading.Thread(target=self._run_commissioning, args=(commissioning_id,), daemon=True)
        thread.start()
        return {"commissioning_id": commissioning_id}

    def capabilities(self, dev: bool = False) -> dict[str, Any]:
        capabilities = self.matter.capabilities()
        if not dev:
            capabilities.pop("details", None)
        return capabilities

    def status(self, commissioning_id: str, dev: bool = False) -> dict[str, Any]:
        row = self._session(commissioning_id)
        data = {"status": public_status(row["status"])}
        if dev:
            data.update({
                "commissioning_status": row["status"],
                "setup_payload": row["setup_payload"],
                "ha_response": json.loads(row["ha_response_json"] or "null"),
                "error": row["error"],
                "logs": json.loads(row["log_json"] or "[]"),
            })
        return data

    def device(self, commissioning_id: str, dev: bool = False) -> dict[str, Any]:
        row = self._session(commissioning_id)
        entities = json.loads(row["entity_ids"] or row["entity_ids_json"] or "[]")
        suggestions = json.loads(row["suggestions_json"] or "[]")
        detected = row["status"] == "completed" and bool(entities)
        data: dict[str, Any] = {
            "device_detected": detected,
            "friendly_name": row["friendly_name"] or ("Sensor" if detected else ""),
            "suggestions": public_suggestions(suggestions),
        }
        if dev:
            data.update({
                "device_id": row["device_id"],
                "entity_ids": entities,
                "suggestions_raw": suggestions,
                "home_assistant_response": json.loads(row["ha_response_json"] or "null"),
                "logs": json.loads(row["log_json"] or "[]"),
            })
        return data

    def assign(self, commissioning_id: str, room: str, role: str) -> dict[str, Any]:
        row = self._session(commissioning_id)
        entities = json.loads(row["entity_ids"] or row["entity_ids_json"] or "[]")
        if not entities:
            raise ValueError("no sensor found")
        clean_room = str(room or "").strip()
        clean_role = role_for_room(str(role or "").strip(), clean_room)
        suggestions = json.loads(row["suggestions_json"] or "[]")
        entity_id = choose_entity_for_role(entities, suggestions, role)
        current = {item.get("entity_id"): item for item in self.discovery.snapshot()}
        entity = current.get(entity_id, {"entity_id": entity_id, "domain": entity_id.split(".")[0] if "." in entity_id else ""})
        saved_role = self.mapping.upsert_role({
            "role": clean_role,
            "room": clean_room,
            "entity_id": entity_id,
            "device_id": row["device_id"] or entity.get("device_id"),
            "friendly_name": entity.get("friendly_name") or row["friendly_name"],
            "device_class": entity.get("device_class"),
            "domain": entity.get("domain") or (entity_id.split(".")[0] if "." in entity_id else ""),
            "source": "sentero_matter",
            "confidence": 100,
        })
        with self.connect() as con:
            con.execute(
                "update commissioned_devices set room = ?, role = ?, status = ?, updated_at = ? where id = ?",
                (clean_room, clean_role, "assigned", now(), commissioning_id),
            )
            con.commit()
        return {"status": "saved", "room": clean_room, "role": saved_role}

    def _session(self, commissioning_id: str) -> sqlite3.Row:
        with self.connect() as con:
            row = con.execute("select * from commissioned_devices where id = ?", (commissioning_id,)).fetchone()
        if not row:
            raise ValueError("pairing session not found")
        return row

    def _run_commissioning(self, commissioning_id: str) -> None:
        logs: list[dict[str, Any]] = []
        try:
            self._update(commissioning_id, status="commissioning", logs=append_log(logs, "connecting"))
            row = self._session(commissioning_id)
            logger.info("Matter commissioning request sent", extra={"component": "wizard", "commissioning_id": commissioning_id})
            logger.debug("Matter commissioning status running", extra={"component": "wizard", "commissioning_id": commissioning_id})
            logs = append_log(append_log(logs, "matter_commissioning_request_sent"), "matter_commissioning_status", {"status": "running"})
            self._update(commissioning_id, status="commissioning", logs=logs)
            response = self.matter.commission(row["setup_payload"])
            logger.debug("Matter commission response received", extra={"component": "wizard", "commissioning_id": commissioning_id, "response": response})
            self._update(commissioning_id, ha_response=response, logs=append_log(logs, "pairing_response", response))
            if not response.get("ok"):
                logger.warning("Matter commissioning failed", extra={"component": "wizard", "commissioning_id": commissioning_id, "reason": "pairing_failed"})
                self._update(commissioning_id, status="failed", error="pairing_failed", logs=append_log(logs, "matter_commissioning_status", {"status": "failed"}))
                return
            baseline = json.loads(row["baseline_snapshot_json"] or "[]")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                detected = self.discovery.detect_new_entities(baseline)
                entities = detected["entities"]
                if entities:
                    entity_ids = [item["entity_id"] for item in entities if item.get("entity_id")]
                    device_ids = detected["device_ids"]
                    device_info = (detected.get("devices") or [{}])[0] if detected.get("devices") else {}
                    friendly_name = next((item.get("friendly_name") for item in entities if item.get("friendly_name")), None) or "Sensor"
                    self._save_completed(
                        commissioning_id,
                        device_ids[0] if device_ids else None,
                        friendly_name,
                        device_info.get("manufacturer"),
                        device_info.get("model"),
                        entity_ids,
                        detected["suggestions"],
                        logs=append_log(logs, "device_detected", {"entity_count": len(entity_ids), "device_ids": device_ids}),
                    )
                    logger.info("Matter commissioning completed", extra={"component": "wizard", "commissioning_id": commissioning_id, "device_id": device_ids[0] if device_ids else None, "entity_count": len(entity_ids)})
                    return
                self._update(commissioning_id, status="commissioning", logs=append_log(logs, "waiting_for_sensor"))
                time.sleep(2)
            logger.warning("Matter commissioning timed out", extra={"component": "wizard", "commissioning_id": commissioning_id, "reason": "device_not_detected"})
            self._update(commissioning_id, status="failed", error="device_not_detected", logs=append_log(logs, "timeout"))
        except MatterCommissioningUnavailable as exc:
            logger.warning("Matter commissioning unavailable", extra={"component": "wizard", "commissioning_id": commissioning_id, "reason": "unavailable"})
            self._update(
                commissioning_id,
                status="failed",
                error="Matter Commissioning nicht verfügbar",
                logs=append_log(logs, "matter_commissioning_status", {"status": "failed", "reason": "unavailable"}),
            )
        except Exception as exc:
            logger.exception("Sentero sensor pairing failed", extra={"component": "wizard", "commissioning_id": commissioning_id})
            self._update(commissioning_id, status="failed", error=str(exc), logs=append_log(logs, "exception", {"error": str(exc)}))

    def _save_completed(self, commissioning_id: str, device_id: str | None, friendly_name: str, manufacturer: str | None, model: str | None, entity_ids: list[str], suggestions: list[dict[str, Any]], logs: list[dict[str, Any]]) -> None:
        timestamp = now()
        with self.connect() as con:
            con.execute(
                """update commissioned_devices
                   set status = ?, device_id = ?, friendly_name = ?, manufacturer = ?, model = ?, entity_ids = ?, entity_ids_json = ?, suggestions_json = ?, paired_at = ?, updated_at = ?, log_json = ?
                   where id = ?""",
                ("completed", device_id, friendly_name, manufacturer, model, json.dumps(entity_ids, ensure_ascii=False), json.dumps(entity_ids, ensure_ascii=False), json.dumps(suggestions, ensure_ascii=False), timestamp, timestamp, json.dumps(logs, ensure_ascii=False), commissioning_id),
            )
            con.commit()

    def _update(self, commissioning_id: str, status: str | None = None, error: str | None = None, ha_response: dict[str, Any] | None = None, logs: list[dict[str, Any]] | None = None) -> None:
        assignments = ["updated_at = ?"]
        params: list[Any] = [now()]
        if status:
            assignments.append("status = ?")
            params.append(status)
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if ha_response is not None:
            assignments.append("ha_response_json = ?")
            params.append(json.dumps(ha_response, ensure_ascii=False))
        if logs is not None:
            assignments.append("log_json = ?")
            params.append(json.dumps(logs, ensure_ascii=False))
        params.append(commissioning_id)
        with self.connect() as con:
            con.execute(f"update commissioned_devices set {', '.join(assignments)} where id = ?", params)
            con.commit()


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """create table if not exists commissioned_devices (
           id text primary key,
           device_id text,
           friendly_name text,
           manufacturer text,
           model text,
           entity_ids text not null default '[]',
           entity_ids_json text not null default '[]',
           paired_at text,
           room text,
           role text,
           status text not null,
           setup_payload text,
           baseline_snapshot_json text,
           suggestions_json text not null default '[]',
           ha_ready_json text,
           ha_response_json text,
           error text,
           log_json text not null default '[]',
           created_at text not null,
           updated_at text not null
        )"""
    )
    try:
        con.execute("alter table commissioned_devices add column entity_ids text not null default '[]'")
    except sqlite3.OperationalError:
        pass


def append_log(logs: list[dict[str, Any]], event: str, data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [*logs, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, "data": data or {}}]


def public_status(status: str) -> str:
    if status in {"waiting", "commissioning", "completed", "failed"}:
        return status
    if status == "assigned":
        return "completed"
    return "failed"


def public_suggestions(suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": item.get("role"), "kind": item.get("kind"), "label": item.get("label"), "score": item.get("score")} for item in suggestions]


def role_for_room(role: str, room: str) -> str:
    if role == "main_door":
        return "main_door"
    if role in {"contact", "window_contact"}:
        return "window_contact"
    return f"{room}_presence" if room else "presence"


def choose_entity_for_role(entity_ids: list[str], suggestions: list[dict[str, Any]], role: str) -> str:
    if role == "main_door":
        wanted = {"main_door", "contact"}
    elif role in {"contact", "window_contact"}:
        wanted = {"contact"}
    else:
        wanted = {"presence"}
    for suggestion in suggestions:
        if suggestion.get("role") in wanted or suggestion.get("kind") in wanted:
            entity_id = str(suggestion.get("entity_id") or "")
            if entity_id:
                return entity_id
    if not entity_ids:
        raise ValueError("no sensor entity found")
    return entity_ids[0]
