from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class HumanActivityAssessment:
    score: int
    confidence: float
    classification: str
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "classification": self.classification,
            "reasons": list(self.reasons),
        }


class HumanActivityScorer:
    """Conservative human-activity likelihood scorer.

    Shadow mode only: scores are stored alongside raw events but do not suppress,
    delete, rewrite or otherwise influence existing Sentero behavior calculations.
    """

    HISTORY_DAYS = 7

    def assess(self, con: Any, event: dict[str, Any]) -> HumanActivityAssessment | None:
        state = str(event.get("state") or "").strip().lower()
        if state not in {"on", "true", "1", "open", "detected", "present", "occupied"}:
            return None

        event_type = str(event.get("event_type") or "").strip().lower()
        device_class = str(event.get("device_class") or "").strip().lower()
        role = str(event.get("role") or "").strip().lower()
        entity_id = str(event.get("entity_id") or "").strip().lower()
        if event_type in {"smoke", "smoke_alarm", "safety"} or device_class == "smoke" or "smoke" in role or "rauch" in role or "smoke" in entity_id:
            return None
        room = str(event.get("room") or "").strip()
        event_time = self._parse_time(event.get("event_time"))
        motion_state = str(event.get("motion_state") or "").strip().lower().replace("-", "_")

        score = 50
        reasons: list[str] = []

        if event_type == "door" or device_class in {"door", "opening"}:
            score += 28
            reasons.append("door_interaction")

        if event_type == "presence" or device_class in {"presence", "occupancy"}:
            score += 10
            reasons.append("presence_detected")

        if event_type == "motion" or device_class == "motion":
            score += 5
            reasons.append("motion_detected")

        if motion_state in {"still", "static", "stationary", "standstill", "static_target"}:
            score += 14
            reasons.append("sustained_static_target")
        elif motion_state in {"large", "moving_target", "moving", "movement", "motion"}:
            score += 10
            reasons.append("clear_movement")
        elif motion_state in {"small", "micro", "micro_motion"}:
            score -= 8
            reasons.append("small_movement")

        recent = self._recent_active_events(con, event_time, minutes=15)
        recent_same_room = [
            item for item in recent
            if room and str(item.get("room") or "").strip() == room
        ]
        recent_other_room = [
            item for item in recent
            if room and str(item.get("room") or "").strip() not in {"", room}
        ]

        if recent_same_room:
            score += 9
            reasons.append("room_persistence")
        else:
            score -= 8
            reasons.append("isolated_event")

        if recent_other_room:
            score += 15
            reasons.append("room_transition")

        if any(
            str(item.get("device_class") or "").lower() in {"door", "opening"}
            or str(item.get("event_type") or "").lower() == "door"
            for item in recent
        ):
            score += 12
            reasons.append("door_corroboration")

        routine_days = self._routine_match_days(con, event_time, room)
        if routine_days >= 3:
            score += 10
            reasons.append("seven_day_routine_match")
        elif routine_days == 0 and event_time.hour < 5:
            score -= 8
            reasons.append("isolated_night_activity")

        burst = self._active_count(con, event_time, room, minutes=5)
        if burst >= 6 and not recent_other_room:
            score -= 7
            reasons.append("repetitive_single_room_burst")

        score = max(0, min(100, int(round(score))))
        if score >= 65:
            classification = "likely_human"
        elif score < 35:
            classification = "likely_non_human"
        else:
            classification = "uncertain"

        evidence_count = len(set(reasons))
        confidence = min(0.92, 0.45 + evidence_count * 0.055)
        if classification == "uncertain":
            confidence = min(confidence, 0.72)

        return HumanActivityAssessment(
            score=score,
            confidence=round(confidence, 2),
            classification=classification,
            reasons=reasons,
        )

    def _recent_active_events(self, con: Any, event_time: datetime, minutes: int) -> list[dict[str, Any]]:
        since = (event_time - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        until = event_time.isoformat(timespec="seconds")
        rows = con.execute(
            """select event_time, room, state, device_class, role,
                      case
                        when lower(coalesce(device_class,'')) in ('door','opening') then 'door'
                        when lower(coalesce(device_class,'')) = 'motion' then 'motion'
                        when lower(coalesce(device_class,'')) in ('presence','occupancy') then 'presence'
                        else 'sensor_state'
                      end as event_type
               from sentero_sensor_events
               where event_time >= ? and event_time < ?
                 and lower(coalesce(state, '')) in ('on', 'true', '1', 'open', 'detected', 'present', 'occupied')
               order by event_time desc
               limit 30""",
            (since, until),
        ).fetchall()
        return [dict(row) for row in rows]

    def _routine_match_days(self, con: Any, event_time: datetime, room: str) -> int:
        if not room:
            return 0
        since = (event_time - timedelta(days=self.HISTORY_DAYS)).isoformat(timespec="seconds")
        rows = con.execute(
            """select event_time
               from sentero_sensor_events
               where room = ?
                 and event_time >= ? and event_time < ?
                 and lower(coalesce(state, '')) in ('on', 'true', '1', 'open', 'detected', 'present', 'occupied')
               order by event_time asc""",
            (room, since, event_time.isoformat(timespec="seconds")),
        ).fetchall()

        matched_days: set[str] = set()
        current_minutes = event_time.hour * 60 + event_time.minute
        for row in rows:
            previous = self._parse_time(row["event_time"])
            previous_minutes = previous.hour * 60 + previous.minute
            delta_minutes = abs(previous_minutes - current_minutes)
            delta_minutes = min(delta_minutes, 24 * 60 - delta_minutes)
            if delta_minutes <= 90:
                matched_days.add(previous.date().isoformat())
        return len(matched_days)

    def _active_count(self, con: Any, event_time: datetime, room: str, minutes: int) -> int:
        if not room:
            return 0
        since = (event_time - timedelta(minutes=minutes)).isoformat(timespec="seconds")
        row = con.execute(
            """select count(*) as count
               from sentero_sensor_events
               where room = ?
                 and event_time >= ? and event_time < ?
                 and lower(coalesce(state, '')) in ('on', 'true', '1', 'open', 'detected', 'present', 'occupied')""",
            (room, since, event_time.isoformat(timespec="seconds")),
        ).fetchone()
        return int(row["count"] if row else 0)

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


def reasons_json(assessment: HumanActivityAssessment | None) -> str:
    return json.dumps(assessment.reasons if assessment else [], ensure_ascii=False)
