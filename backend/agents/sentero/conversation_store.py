from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.services.device_mapping_service import DeviceMappingService, now


class SenteroConversationStore:
    """Small local-only conversation memory shared by mail and Telegram.

    The table is intentionally bounded and stores only the recent dialogue needed
    to resolve follow-up wording such as "und davor?" or "war das normal?".
    """

    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.mapping.connect() as con:
            con.execute(
                """create table if not exists sentero_conversation_turns (
                    id integer primary key autoincrement,
                    channel text not null,
                    conversation_key text not null,
                    contact_id integer not null,
                    role text not null,
                    text text not null,
                    intent text,
                    slots_json text,
                    facts_json text,
                    created_at text not null,
                    foreign key(contact_id) references trusted_contacts(id)
                )"""
            )
            con.execute(
                """create index if not exists idx_sentero_conversation_turns_lookup
                   on sentero_conversation_turns(channel, conversation_key, contact_id, created_at, id)"""
            )
            con.commit()

    def recent(
        self,
        *,
        channel: str,
        conversation_key: str,
        contact_id: int,
        limit: int = 10,
        max_age_hours: int = 48,
    ) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat(timespec="seconds")
        with self.mapping.connect() as con:
            rows = con.execute(
                """select role, text, intent, slots_json, facts_json, created_at
                   from sentero_conversation_turns
                   where channel = ? and conversation_key = ? and contact_id = ? and created_at >= ?
                   order by id desc
                   limit ?""",
                (channel, conversation_key, contact_id, cutoff, max(1, min(int(limit), 20))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            data = dict(row)
            result.append(
                {
                    "role": str(data.get("role") or ""),
                    "text": str(data.get("text") or ""),
                    "intent": data.get("intent"),
                    "slots": _decode_json(data.get("slots_json"), {}),
                    "facts": _decode_json(data.get("facts_json"), {}),
                    "created_at": str(data.get("created_at") or ""),
                }
            )
        return result

    def add_exchange(
        self,
        *,
        channel: str,
        conversation_key: str,
        contact_id: int,
        question: str,
        answer: str,
        intent: str | None,
        slots: dict[str, Any] | None = None,
        facts: dict[str, Any] | None = None,
    ) -> None:
        created_at = now()
        with self.mapping.connect() as con:
            con.execute(
                """insert into sentero_conversation_turns
                   (channel, conversation_key, contact_id, role, text, intent, slots_json, facts_json, created_at)
                   values (?, ?, ?, 'user', ?, ?, ?, null, ?)""",
                (
                    channel,
                    conversation_key,
                    contact_id,
                    str(question or "")[:4000],
                    intent,
                    json.dumps(slots or {}, ensure_ascii=False, default=str),
                    created_at,
                ),
            )
            con.execute(
                """insert into sentero_conversation_turns
                   (channel, conversation_key, contact_id, role, text, intent, slots_json, facts_json, created_at)
                   values (?, ?, ?, 'assistant', ?, ?, ?, ?, ?)""",
                (
                    channel,
                    conversation_key,
                    contact_id,
                    str(answer or "")[:4000],
                    intent,
                    json.dumps(slots or {}, ensure_ascii=False, default=str),
                    json.dumps(_compact_facts(facts or {}), ensure_ascii=False, default=str),
                    created_at,
                ),
            )
            # Keep local memory bounded. 40 rows = about 20 exchanges per conversation.
            con.execute(
                """delete from sentero_conversation_turns
                   where id in (
                       select id from sentero_conversation_turns
                       where channel = ? and conversation_key = ? and contact_id = ?
                       order by id desc
                       limit -1 offset 40
                   )""",
                (channel, conversation_key, contact_id),
            )
            con.commit()


def _decode_json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _compact_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """Retain useful conversational anchors without copying huge sensor payloads."""
    compact: dict[str, Any] = {}
    for key in (
        "activity",
        "last_activity",
        "current_presence",
        "environment",
        "status",
        "findings",
        "event_count",
        "rooms",
        "night_activity_count",
        "thread_context",
    ):
        if key in facts:
            compact[key] = facts[key]
    dashboard = facts.get("dashboard")
    if isinstance(dashboard, dict):
        compact["dashboard"] = {
            key: dashboard.get(key)
            for key in (
                "person_name",
                "current_location",
                "behavior_status",
                "behavior_label",
                "behavior_summary",
                "first_activity",
                "last_activity",
            )
            if key in dashboard
        }
    return compact
