from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.config import load_agent_section
from backend.logging_config import get_logger
from backend.services.device_mapping_service import DeviceMappingService, now
from backend.services.llm.factory import HttpLLMClient, ProviderConfig, resolve_config_value, resolve_provider_config

logger = get_logger(__name__)

PROMPT_VERSION = "sentero-situation-shadow-v1"

ALLOWED_SITUATIONS = {
    "unknown",
    "normal_activity",
    "showering",
    "bathroom_use",
    "cooking",
    "resting",
    "sleeping",
    "night_bathroom_visit",
    "possible_exit",
    "possible_entry",
    "prolonged_inactivity",
    "prolonged_humidity",
    "unusual_activity_pattern",
}
ALLOWED_SEVERITIES = {"unknown", "normal", "notice", "warning"}
ALLOWED_ACTIONS = {"none", "observe", "review_later"}
SECRET_FIELD_TERMS = {
    "password",
    "passwort",
    "token",
    "secret",
    "api_key",
    "apikey",
    "email",
    "mail",
    "telegram",
    "chat_id",
    "bot",
    "ssid",
    "wifi",
    "wlan",
    "ip",
    "mqtt",
    "credential",
    "ieee",
}

SYSTEM_PROMPT = """Du bist der lokale Sentero Situation Interpreter.

Du analysierst ausschließlich die strukturierten Beobachtungen, die dir Sentero bereitstellt.
Deine Aufgabe ist, plausible Alltagssituationen im zeitlichen Kontext einzuschätzen.
Du darfst keine nicht vorhandenen Beobachtungen erfinden.
Du darfst keine medizinischen Diagnosen stellen.
Du darfst keine Sicherheit garantieren.
Ein einzelner Sensorgrenzwert ist nicht automatisch eine Gefahr.
Berücksichtige Raum, zeitlichen Verlauf, gleichzeitige Aktivität, Sensorfrische und bekannte Routineinformationen.
Unterscheide Beobachtung und Interpretation.
Wenn mehrere Erklärungen plausibel sind, reduziere die Confidence.
Wenn die Daten nicht ausreichen, gib unknown zurück.
Du löst keine Alarme aus und triffst keine Sicherheitsentscheidung.

Antwort nur als JSON mit:
{
  "situation": "unknown|normal_activity|showering|bathroom_use|cooking|resting|sleeping|night_bathroom_visit|possible_exit|possible_entry|prolonged_inactivity|prolonged_humidity|unusual_activity_pattern",
  "confidence": 0.0,
  "severity": "unknown|normal|notice|warning",
  "reason_codes": [],
  "summary": "",
  "recommended_action": "none|observe|review_later"
}"""


@dataclass(frozen=True)
class AIShadowConfig:
    enabled: bool = False
    interval_seconds: int = 300
    window_minutes: int = 60
    model: str = ""


class SituationInterpreter:
    """Interprets prepared facts only; it does not query Sentero data sources."""

    def __init__(self, *, config: AIShadowConfig | None = None, llm_client: Any | None = None) -> None:
        self.config = config or load_ai_shadow_config()
        self._llm_client = llm_client

    def interpret(self, context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        status = "ok"
        error_code = None
        model_name = ""
        try:
            client = self._local_llm_client()
            if client is None:
                status = "skipped"
                error_code = "local_llm_unavailable"
                assessment = unknown_assessment("Lokale Ollama-/Llama-Konfiguration ist nicht aktiv oder nicht verfügbar.", error_code=error_code)
            else:
                model_name = str(getattr(getattr(client, "config", None), "model", "") or self.config.model or "")
                response = client.generate(
                    prompt=(
                        "Bewerte diese bereits deterministisch verdichteten Sentero-Beobachtungen. "
                        "Nutze nur diese Fakten und antworte strikt als JSON.\n\n"
                        f"{json.dumps(context, ensure_ascii=False)}"
                    ),
                    system=SYSTEM_PROMPT,
                )
                assessment = validate_assessment(response.text)
        except Exception as exc:
            logger.warning(
                "AI shadow assessment skipped",
                extra={"component": "ai_shadow", "error_type": exc.__class__.__name__},
            )
            status = "failed"
            error_code = exc.__class__.__name__
            assessment = unknown_assessment("KI-Shadow-Auswertung konnte lokal nicht abgeschlossen werden.", error_code=error_code)
        duration_ms = int((time.perf_counter() - started) * 1000)
        return {
            "assessment": assessment,
            "duration_ms": duration_ms,
            "status": status,
            "error_code": error_code,
            "model_name": model_name,
        }

    def _local_llm_client(self) -> Any | None:
        if self._llm_client is not None:
            return self._llm_client
        llm_config = load_agent_section("llm")
        provider_config = resolve_provider_config(llm_config)
        if provider_config is None or provider_config.provider not in {"llama", "ollama"}:
            return None
        if self.config.model:
            provider_config = ProviderConfig(
                provider=provider_config.provider,
                api_key=provider_config.api_key,
                model=self.config.model,
                base_url=provider_config.base_url,
                timeout_seconds=provider_config.timeout_seconds,
            )
        return HttpLLMClient(provider_config)


class AIShadowService:
    """Orchestrates context loading, debouncing and persistence for shadow mode."""

    def __init__(
        self,
        mapping: DeviceMappingService | None = None,
        *,
        config: AIShadowConfig | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.mapping = mapping or DeviceMappingService()
        self.config = config or load_ai_shadow_config()
        self.interpreter = SituationInterpreter(config=self.config, llm_client=llm_client)
        self._llm_client = llm_client
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists sentero_ai_shadow_assessments (
                    id integer primary key autoincrement,
                    created_at text not null,
                    window_start text not null,
                    window_end text not null,
                    situation text not null,
                    confidence real not null,
                    severity text not null,
                    reason_codes_json text not null default '[]',
                    summary text not null,
                    recommended_action text not null,
                    model_name text,
                    prompt_version text not null,
                    context_hash text not null,
                    inference_duration_ms integer not null default 0,
                    status text not null,
                    error_code text,
                    context_snapshot_json text,
                    human_label text,
                    human_correct integer,
                    human_comment text,
                    reviewed_at text
                )"""
            )
            con.execute(
                "create index if not exists idx_ai_shadow_created_at on sentero_ai_shadow_assessments(created_at)"
            )
            con.commit()

    def maybe_run_async(self, *, current_behavior_state: dict[str, Any] | None = None) -> bool:
        if not self.config.enabled:
            return False
        if not self._due():
            return False
        with self._lock:
            if self._worker and self._worker.is_alive():
                return False
            self._worker = threading.Thread(
                target=self._run_guarded,
                kwargs={"current_behavior_state": current_behavior_state},
                name="sentero-ai-shadow",
                daemon=True,
            )
            self._worker.start()
            return True

    def run_once(self, *, current_behavior_state: dict[str, Any] | None = None) -> dict[str, Any]:
        self.ensure_schema()
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(minutes=self.config.window_minutes)
        context = self.build_context(window_start=window_start, window_end=window_end, current_behavior_state=current_behavior_state)
        return self.interpret_and_store(context)

    def build_context(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        current_behavior_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        roles = self._safe_roles()
        data_quality = self._sensor_freshness(roles, window_end)
        with self.mapping.connect() as con:
            rows = con.execute(
                """select event_time, role, room, entity_id, state, device_class, source,
                          human_activity_score, human_activity_confidence, human_activity_classification
                   from sentero_sensor_events
                   where event_time >= ? and event_time <= ?
                   order by event_time asc, id asc""",
                (window_start.isoformat(timespec="seconds"), window_end.isoformat(timespec="seconds")),
            ).fetchall()
            behavior_row = con.execute(
                "select assessment_time, status, confidence, summary, recommendation from behavior_assessments order by assessment_time desc, id desc limit 1"
            ).fetchone()
            personal_routine = self._personal_routine(con, window_end)
        events = [dict(row) for row in rows]
        rooms = sorted({str(item.get("room")) for item in [*events, *roles] if item.get("room")})
        local_time = window_end.astimezone(_sentero_timezone())
        context = {
            "window_minutes": self.config.window_minutes,
            "window_start": window_start.isoformat(timespec="seconds"),
            "window_end": window_end.isoformat(timespec="seconds"),
            "current_time": window_end.isoformat(timespec="seconds"),
            "local_time": local_time.isoformat(timespec="seconds"),
            "time_of_day": self._time_of_day(local_time),
            "rooms": {room: self._room_context(room, events, roles, window_end) for room in rooms},
            "door_events": self._door_events(events),
            "room_transitions": self._room_transitions(events),
            "known_away_signal": self._known_away_signal(events, roles, window_end),
            "sensor_freshness": data_quality,
            "personal_routine": personal_routine,
            "current_behavior_state": current_behavior_state or (dict(behavior_row) if behavior_row else None),
        }
        return context

    def interpret_and_store(self, context: dict[str, Any]) -> dict[str, Any]:
        context_json = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        context_hash = hashlib.sha256(context_json.encode("utf-8")).hexdigest()
        result = self.interpreter.interpret(context)
        assessment = result["assessment"]
        duration_ms = int(result.get("duration_ms") or 0)
        status = str(result.get("status") or "failed")
        error_code = result.get("error_code")
        model_name = str(result.get("model_name") or "")
        stored = self._store(context, assessment, context_hash, duration_ms, status, error_code, model_name)
        logger.debug(
            "AI shadow assessment",
            extra={
                "component": "ai_shadow",
                "situation": stored.get("situation"),
                "confidence": stored.get("confidence"),
                "duration_ms": duration_ms,
                "context_hash": context_hash,
                "status": status,
            },
        )
        return stored

    def history(self, *, start: str | None = None, end: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_schema()
        limit = max(1, min(int(limit or 100), 500))
        sql = "select * from sentero_ai_shadow_assessments"
        params: list[Any] = []
        clauses = []
        if start:
            clauses.append("created_at >= ?")
            params.append(start)
        if end:
            clauses.append("created_at <= ?")
            params.append(end)
        if clauses:
            sql += " where " + " and ".join(clauses)
        sql += " order by created_at desc, id desc limit ?"
        params.append(limit)
        with self.mapping.connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [self._row_to_assessment(row) for row in rows]

    def export(self, *, period_from: str | None = None, period_to: str | None = None) -> dict[str, Any]:
        self.ensure_schema()
        end = _parse_export_time(period_to) if period_to else datetime.now(timezone.utc)
        start = _parse_export_time(period_from) if period_from else end - timedelta(hours=24)
        if start > end:
            raise ValueError("from must be before or equal to to")
        with self.mapping.connect() as con:
            rows = con.execute(
                """select created_at, window_start, window_end, situation, confidence, severity,
                          reason_codes_json, summary, recommended_action, model_name, prompt_version,
                          inference_duration_ms, status, error_code, context_snapshot_json,
                          human_correct, human_label, human_comment, reviewed_at
                   from sentero_ai_shadow_assessments
                   where created_at >= ? and created_at <= ?
                   order by created_at asc, id asc""",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        assessments = [self._export_row(dict(row)) for row in rows]
        return {
            "export_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "period": {
                "from": start.isoformat(timespec="seconds"),
                "to": end.isoformat(timespec="seconds"),
            },
            "assessment_count": len(assessments),
            "assessments": assessments,
        }

    def _run_guarded(self, *, current_behavior_state: dict[str, Any] | None = None) -> None:
        try:
            self.run_once(current_behavior_state=current_behavior_state)
        except Exception:
            logger.exception("AI shadow worker failed", extra={"component": "ai_shadow"})

    def _due(self) -> bool:
        self.ensure_schema()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=self.config.interval_seconds)).isoformat(timespec="seconds")
        with self.mapping.connect() as con:
            row = con.execute(
                "select id from sentero_ai_shadow_assessments where created_at >= ? order by created_at desc, id desc limit 1",
                (cutoff,),
            ).fetchone()
        return row is None

    def _store(
        self,
        context: dict[str, Any],
        assessment: dict[str, Any],
        context_hash: str,
        duration_ms: int,
        status: str,
        error_code: str | None,
        model_name: str,
    ) -> dict[str, Any]:
        with self.mapping.connect() as con:
            cur = con.execute(
                """insert into sentero_ai_shadow_assessments
                   (created_at, window_start, window_end, situation, confidence, severity, reason_codes_json,
                    summary, recommended_action, model_name, prompt_version, context_hash, inference_duration_ms,
                    status, error_code, context_snapshot_json)
                   values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now(),
                    context["window_start"],
                    context["window_end"],
                    assessment["situation"],
                    float(assessment["confidence"]),
                    assessment["severity"],
                    json.dumps(assessment.get("reason_codes") or [], ensure_ascii=False),
                    assessment.get("summary") or "",
                    assessment["recommended_action"],
                    model_name,
                    PROMPT_VERSION,
                    context_hash,
                    duration_ms,
                    status,
                    error_code,
                    json.dumps(_minimized_context(context), ensure_ascii=False, sort_keys=True),
                ),
            )
            con.commit()
            row = con.execute("select * from sentero_ai_shadow_assessments where id = ?", (int(cur.lastrowid),)).fetchone()
        return self._row_to_assessment(row)

    def _row_to_assessment(self, row: Any) -> dict[str, Any]:
        data = dict(row)
        data["reason_codes"] = _safe_json_list(data.pop("reason_codes_json", "[]"))
        return data

    def _export_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "created_at": row.get("created_at"),
            "window_start": row.get("window_start"),
            "window_end": row.get("window_end"),
            "context": _sanitize_export_value(_safe_json_dict(row.get("context_snapshot_json"))),
            "ai": {
                "situation": row.get("situation"),
                "confidence": float(row.get("confidence") or 0),
                "severity": row.get("severity"),
                "reason_codes": _safe_json_list(row.get("reason_codes_json")),
                "summary": row.get("summary") or "",
                "recommended_action": row.get("recommended_action"),
            },
            "feedback": {
                "human_correct": row.get("human_correct"),
                "human_label": row.get("human_label"),
                "human_comment": row.get("human_comment"),
                "reviewed_at": row.get("reviewed_at"),
            },
            "technical": {
                "model_name": row.get("model_name"),
                "prompt_version": row.get("prompt_version"),
                "inference_duration_ms": int(row.get("inference_duration_ms") or 0),
                "status": row.get("status"),
                "error_code": row.get("error_code"),
            },
        }

    def _safe_roles(self) -> list[dict[str, Any]]:
        try:
            return self.mapping.roles(dev=True, include_state=True)
        except Exception:
            logger.warning("AI shadow role snapshot unavailable", extra={"component": "ai_shadow"})
            return []

    def _room_context(self, room: str, events: list[dict[str, Any]], roles: list[dict[str, Any]], now_dt: datetime) -> dict[str, Any]:
        room_events = [event for event in events if str(event.get("room") or "") == room]
        room_roles = [role for role in roles if str(role.get("room") or "") == room]
        activity = [event for event in room_events if _is_activity_event(event)]
        humidity = _numeric_series(room_events, "humidity")
        temperature = _numeric_series(room_events, "temperature")
        illuminance = _numeric_series(room_events, "illuminance")
        latest_activity = activity[-1] if activity else None
        stale_roles = [role for role in room_roles if role.get("stale") is True or role.get("reachable") is False]
        presence_now, presence_confidence = _fresh_state(room_roles, lambda role: role.get("presence") is True, lambda role: role.get("presence") is not None)
        motion_now, motion_confidence = _fresh_state(
            room_roles,
            lambda role: _truthy(role.get("motion")) or _motion_state_active(role.get("motion_state")),
            lambda role: role.get("motion") is not None or role.get("motion_state") is not None,
        )
        return {
            "presence_now": presence_now,
            "presence_confidence": presence_confidence,
            "motion_now": motion_now,
            "motion_confidence": motion_confidence,
            "last_activity_minutes_ago": _minutes_ago(latest_activity.get("event_time"), now_dt) if latest_activity else None,
            "activity_event_count": len(activity),
            "sustained_activity_minutes": _span_minutes(activity),
            "stillness_event_count": sum(1 for event in room_events if _is_stillness_event(event)),
            "humidity": _trend(humidity, now_dt),
            "temperature": _trend(temperature, now_dt),
            "illuminance": _trend(illuminance, now_dt),
            "fresh": not stale_roles,
            "stale_sensor_count": len(stale_roles),
        }

    def _door_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"event_time": event.get("event_time"), "room": event.get("room"), "state": event.get("state")}
            for event in events
            if _is_door_event(event)
        ][-10:]

    def _room_transitions(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        transitions = []
        last_room = None
        for event in [item for item in events if _is_activity_event(item)]:
            room = event.get("room")
            if room and last_room and room != last_room:
                transitions.append({"at": event.get("event_time"), "from": last_room, "to": room})
            if room:
                last_room = room
        return transitions[-10:]

    def _known_away_signal(self, events: list[dict[str, Any]], roles: list[dict[str, Any]], now_dt: datetime) -> dict[str, Any]:
        recent_presence = [event for event in events if _is_activity_event(event)]
        latest = recent_presence[-1] if recent_presence else None
        presence_now, presence_confidence = _fresh_state(roles, lambda role: role.get("presence") is True, lambda role: role.get("presence") is not None)
        return {
            "any_presence_now": presence_now,
            "presence_confidence": presence_confidence,
            "last_activity_minutes_ago": _minutes_ago(latest.get("event_time"), now_dt) if latest else None,
            "door_events_in_window": len([event for event in events if _is_door_event(event)]),
        }

    def _sensor_freshness(self, roles: list[dict[str, Any]], now_dt: datetime) -> dict[str, Any]:
        stale = [role for role in roles if role.get("stale") is True or role.get("reachable") is False]
        ages = []
        for role in roles:
            age = _minutes_ago(role.get("last_updated") or role.get("last_changed") or role.get("last_seen"), now_dt)
            if age is not None:
                ages.append(age)
        return {
            "configured_sensors": len(roles),
            "fresh_sensors": max(0, len(roles) - len(stale)),
            "stale_sensors": len(stale),
            "max_age_minutes": max(ages) if ages else None,
        }

    def _personal_routine(self, con: Any, window_end: datetime) -> dict[str, Any]:
        try:
            profile_row = con.execute("select * from behavior_profile where user_id = 1").fetchone()
            today_row = con.execute(
                "select * from behavior_daily_summary where date = ?",
                (window_end.astimezone(_sentero_timezone()).date().isoformat(),),
            ).fetchone()
        except Exception:
            return {"routine_available": False, "reason": "routine_tables_unavailable"}
        if not profile_row:
            return {"routine_available": False, "reason": "no_behavior_profile"}
        profile = dict(profile_row)
        learning_completed = bool(profile.get("learning_completed"))
        if not learning_completed:
            return {"routine_available": False, "learning_completed": False}
        today = dict(today_row) if today_row else {}
        return {
            "routine_available": True,
            "learning_completed": True,
            "typical": {
                "average_wakeup_time": profile.get("average_wakeup_time"),
                "average_sleep_time": profile.get("average_sleep_time"),
                "average_active_minutes": _rounded(profile.get("average_active_minutes")),
                "room_usage_patterns": _safe_json_dict(profile.get("room_usage_patterns")),
                "normal_door_usage": _safe_json_dict(profile.get("normal_door_usage")),
            },
            "today": {
                "date": today.get("date"),
                "first_activity": today.get("first_activity"),
                "last_activity": today.get("last_activity"),
                "active_minutes": int(today.get("active_minutes") or 0) if today else None,
                "room_usage": _safe_json_dict(today.get("room_usage")),
                "door_events": int(today.get("door_events") or 0) if today else None,
                "anomaly_score": int(today.get("anomaly_score") or 0) if today else None,
            },
        }

    @staticmethod
    def _time_of_day(value: datetime) -> str:
        if 5 <= value.hour < 11:
            return "morning"
        if 11 <= value.hour < 17:
            return "daytime"
        if 17 <= value.hour < 22:
            return "evening"
        return "night"


def load_ai_shadow_config() -> AIShadowConfig:
    section = load_agent_section("ai_shadow")
    return AIShadowConfig(
        enabled=_config_bool("SENTERO_AI_SHADOW_MODE", section.get("enabled"), False),
        interval_seconds=max(60, _config_int("SENTERO_AI_SHADOW_INTERVAL_SECONDS", section.get("interval_seconds"), 300)),
        window_minutes=max(15, min(_config_int("SENTERO_AI_SHADOW_WINDOW_MINUTES", section.get("window_minutes"), 60), 360)),
        model=resolve_config_value(os.getenv("SENTERO_AI_MODEL") or section.get("model") or ""),
    )


def validate_assessment(text_or_data: Any) -> dict[str, Any]:
    try:
        data = json.loads(_extract_json(str(text_or_data))) if not isinstance(text_or_data, dict) else text_or_data
    except Exception:
        return unknown_assessment("KI-Shadow-Ausgabe war kein gültiges JSON.", error_code="invalid_json")
    situation = str(data.get("situation") or "").strip()
    severity = str(data.get("severity") or "").strip()
    action = str(data.get("recommended_action") or "").strip()
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    if situation not in ALLOWED_SITUATIONS:
        return unknown_assessment("KI-Shadow-Ausgabe enthielt keine erlaubte Situation.", error_code="invalid_situation")
    if severity not in ALLOWED_SEVERITIES:
        return unknown_assessment("KI-Shadow-Ausgabe enthielt keine erlaubte Severity.", error_code="invalid_severity")
    if action not in ALLOWED_ACTIONS:
        return unknown_assessment("KI-Shadow-Ausgabe enthielt keine erlaubte Handlungsempfehlung.", error_code="invalid_action")
    if confidence < 0 or confidence > 1:
        return unknown_assessment("KI-Shadow-Ausgabe enthielt eine Konfidenz außerhalb 0.0 bis 1.0.", error_code="invalid_confidence")
    reason_codes = data.get("reason_codes") if isinstance(data.get("reason_codes"), list) else []
    return {
        "situation": situation,
        "confidence": round(confidence, 4),
        "severity": severity,
        "reason_codes": [str(item)[:80] for item in reason_codes[:12] if str(item or "").strip()],
        "summary": _safe_summary(data.get("summary")),
        "recommended_action": action,
    }


def unknown_assessment(summary: str, *, error_code: str | None = None) -> dict[str, Any]:
    return {
        "situation": "unknown",
        "confidence": 0.0,
        "severity": "unknown",
        "reason_codes": [],
        "summary": summary,
        "recommended_action": "none",
        "error_code": error_code,
    }


def _extract_json(text: str) -> str:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = clean.strip("`").strip()
        if clean.lower().startswith("json"):
            clean = clean[4:].strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end >= start:
        return clean[start : end + 1]
    return clean


def _minimized_context(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_minutes": context.get("window_minutes"),
        "window_start": context.get("window_start"),
        "window_end": context.get("window_end"),
        "local_time": context.get("local_time"),
        "time_of_day": context.get("time_of_day"),
        "rooms": context.get("rooms") or {},
        "door_events": context.get("door_events") or [],
        "room_transitions": context.get("room_transitions") or [],
        "known_away_signal": context.get("known_away_signal") or {},
        "sensor_freshness": context.get("sensor_freshness") or {},
        "personal_routine": context.get("personal_routine") or {"routine_available": False},
        "current_behavior_state": context.get("current_behavior_state"),
    }


def _numeric_series(events: list[dict[str, Any]], device_class: str) -> list[tuple[datetime, float]]:
    series = []
    for event in events:
        if str(event.get("device_class") or "").strip().lower() != device_class:
            continue
        try:
            value = float(event.get("state"))
            series.append((_parse_time(event.get("event_time")), value))
        except (TypeError, ValueError):
            continue
    return series


def _trend(series: list[tuple[datetime, float]], now_dt: datetime) -> dict[str, Any]:
    if not series:
        return {"now": None, "points": [], "delta_15m": None, "latest_age_minutes": None}
    points = [{"minutes_ago": _minutes_between(ts, now_dt), "value": round(value, 2)} for ts, value in series[-8:]]
    latest_ts, latest_value = series[-1]
    prior = _nearest_at_or_before(series, now_dt - timedelta(minutes=15))
    return {
        "now": round(latest_value, 2),
        "points": points,
        "delta_15m": round(latest_value - prior[1], 2) if prior else None,
        "latest_age_minutes": _minutes_between(latest_ts, now_dt),
    }


def _nearest_at_or_before(series: list[tuple[datetime, float]], target: datetime) -> tuple[datetime, float] | None:
    candidates = [item for item in series if item[0] <= target]
    return candidates[-1] if candidates else series[0] if series else None


def _is_activity_event(event: dict[str, Any]) -> bool:
    if _is_door_event(event):
        return False
    state = str(event.get("state") or "").strip().lower()
    device_class = str(event.get("device_class") or "").strip().lower()
    return state in {"on", "active", "detected", "present", "occupied", "true", "1"} and device_class in {"presence", "occupancy", "motion"}


def _is_stillness_event(event: dict[str, Any]) -> bool:
    text = " ".join(str(event.get(key) or "") for key in ("state", "device_class", "role", "entity_id")).lower().replace("-", "_")
    return any(term in text for term in ("still", "static", "stationary", "standstill"))


def _is_door_event(event: dict[str, Any]) -> bool:
    text = " ".join(str(event.get(key) or "") for key in ("role", "entity_id", "device_class")).lower()
    return any(term in text for term in ("door", "tuer", "tür", "contact", "opening"))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "on", "active", "detected", "present", "occupied", "1"}


def _motion_state_active(value: Any) -> bool:
    return str(value or "").strip().lower().replace("-", "_") in {"moving", "movement", "motion", "active", "moving_target", "large", "small"}


def _fresh_state(roles: list[dict[str, Any]], active_predicate: Any, known_predicate: Any) -> tuple[bool | None, str]:
    known = [role for role in roles if known_predicate(role)]
    fresh = [role for role in known if role.get("stale") is not True and role.get("reachable") is not False]
    if fresh:
        return any(active_predicate(role) for role in fresh), "fresh"
    if known:
        return None, "stale"
    return None, "unknown"


def _span_minutes(events: list[dict[str, Any]]) -> int:
    if len(events) < 2:
        return len(events)
    return max(1, _minutes_between(_parse_time(events[0].get("event_time")), _parse_time(events[-1].get("event_time"))))


def _minutes_ago(value: Any, now_dt: datetime) -> int | None:
    if not value:
        return None
    return _minutes_between(_parse_time(value), now_dt)


def _minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() / 60))


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_export_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Invalid export timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Invalid export timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_json_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _safe_json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sanitize_export_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_export_key(key):
                continue
            sanitized[str(key)] = _sanitize_export_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_export_value(item) for item in value]
    return value


def _is_sensitive_export_key(key: Any) -> bool:
    text = str(key or "").strip().lower()
    return any(term in text for term in SECRET_FIELD_TERMS)


def _rounded(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


def _safe_summary(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Keine belastbare Situationsinterpretation möglich."
    return text[:600]


def _sentero_timezone() -> timezone:
    name = str(os.getenv("SENTERO_TIMEZONE") or os.getenv("TZ") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            logger.warning("Invalid Sentero timezone, using system local timezone", extra={"component": "ai_shadow", "timezone": name})
    system_tz = datetime.now().astimezone().tzinfo
    return system_tz or timezone.utc


def _config_bool(env_name: str, value: Any, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        raw = value
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _config_int(env_name: str, value: Any, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None:
        raw = value
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default
