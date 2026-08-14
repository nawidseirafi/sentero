from __future__ import annotations

import json
from typing import Any

from backend.services.device_mapping_service import DeviceMappingService, now

DEFAULT_AUDIT_RETENTION_DAYS = 180
AUDIT_TABLES = ("aal_audit_log", "aal_export_audit", "notification_logs")


class AuditService:
    def __init__(self, mapping: DeviceMappingService | None = None) -> None:
        self.mapping = mapping or DeviceMappingService()
        ensure_audit_schema(self.mapping)

    def transparency(self, limit: int = 100) -> dict[str, Any]:
        ensure_audit_schema(self.mapping)
        items = [
            *self._audit_log_items(),
            *self._export_items(),
            *self._notification_items(),
        ]
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        limited = items[: max(1, min(int(limit), 500))]
        return {
            "items": limited,
            "summary": {
                "total": len(items),
                "exports": sum(1 for item in items if item.get("category") == "export"),
                "notifications": sum(1 for item in items if item.get("category") == "notification"),
                "consents": sum(1 for item in items if item.get("category") == "consent"),
                "security": sum(1 for item in items if item.get("category") == "security"),
            },
            "retention": self.retention_status(),
        }

    def retention_status(self) -> dict[str, Any]:
        ensure_audit_schema(self.mapping)
        tables: list[dict[str, Any]] = []
        with self.mapping.connect() as con:
            for table in AUDIT_TABLES:
                row = con.execute(f"select count(*) as count, min(created_at) as oldest, max(created_at) as newest from {table}").fetchone()
                tables.append({"table": table, "count": row["count"], "oldest": row["oldest"], "newest": row["newest"]})
        return {"retention_days": DEFAULT_AUDIT_RETENTION_DAYS, "tables": tables}

    def cleanup(self, days: int = DEFAULT_AUDIT_RETENTION_DAYS) -> dict[str, Any]:
        ensure_audit_schema(self.mapping)
        normalized_days = max(30, min(int(days), 3650))
        deleted: dict[str, int] = {}
        with self.mapping.connect() as con:
            cutoff = con.execute("select datetime('now', ?)", (f"-{normalized_days} days",)).fetchone()[0]
            for table in AUDIT_TABLES:
                cur = con.execute(f"delete from {table} where created_at < ?", (cutoff,))
                deleted[table] = int(cur.rowcount or 0)
            con.commit()
        record_audit(
            self.mapping,
            event_type="retention_cleanup",
            category="security",
            status="completed",
            summary=f"Auditdaten aelter als {normalized_days} Tage geloescht.",
            metadata={"days": normalized_days, "deleted": deleted},
        )
        return {"deleted": deleted, "retention": self.retention_status()}

    def _audit_log_items(self) -> list[dict[str, Any]]:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select a.*, t.name as contact_name
                   from aal_audit_log a
                   left join trusted_contacts t on t.id = a.contact_id
                   order by a.created_at desc, a.id desc
                   limit 500"""
            ).fetchall()
        return [public_audit_row(dict(row)) for row in rows]

    def _export_items(self) -> list[dict[str, Any]]:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select e.*, t.name as contact_name
                   from aal_export_audit e
                   left join trusted_contacts t on t.id = e.contact_id
                   order by e.created_at desc, e.id desc
                   limit 500"""
            ).fetchall()
        items = []
        for row in rows:
            data = dict(row)
            items.append(
                {
                    "id": f"export-{data.get('id')}",
                    "category": "export",
                    "event_type": "export_sent",
                    "status": data.get("status"),
                    "summary": export_summary(data),
                    "contact_id": data.get("contact_id"),
                    "contact_name": data.get("contact_name"),
                    "actor_role": data.get("actor_role"),
                    "purpose": data.get("purpose"),
                    "data_classes": parse_classes(data.get("data_classes_json")),
                    "aggregation_level": data.get("aggregation_level"),
                    "raw_data_included": bool(data.get("raw_data_included")),
                    "created_at": data.get("created_at"),
                    "metadata": {
                        "export_type": data.get("export_type"),
                        "period_start": data.get("period_start"),
                        "period_end": data.get("period_end"),
                    },
                }
            )
        return items

    def _notification_items(self) -> list[dict[str, Any]]:
        with self.mapping.connect() as con:
            rows = con.execute(
                """select n.*, t.name as contact_name
                   from notification_logs n
                   left join trusted_contacts t on t.id = n.contact_id
                   order by n.created_at desc, n.id desc
                   limit 500"""
            ).fetchall()
        items = []
        for row in rows:
            data = dict(row)
            items.append(
                {
                    "id": f"notification-{data.get('id')}",
                    "category": "notification",
                    "event_type": "notification",
                    "status": data.get("status"),
                    "summary": notification_summary(data),
                    "contact_id": data.get("contact_id"),
                    "contact_name": data.get("contact_name"),
                    "purpose": "behavior_notification",
                    "data_classes": [data.get("data_class") or "health_adjacent"],
                    "aggregation_level": data.get("aggregation_level") or "summary",
                    "raw_data_included": False,
                    "created_at": data.get("created_at"),
                    "metadata": {
                        "channel": data.get("channel"),
                        "severity": data.get("severity"),
                        "message_title": data.get("message_title"),
                    },
                }
            )
        return items


def ensure_audit_schema(mapping: DeviceMappingService) -> None:
    with mapping.connect() as con:
        con.execute(
            """create table if not exists aal_audit_log (
                id integer primary key autoincrement,
                event_type text not null,
                category text not null,
                actor_type text,
                actor_id text,
                contact_id integer,
                actor_role text,
                purpose text,
                data_classes_json text not null default '[]',
                aggregation_level text not null default 'summary',
                raw_data_included integer not null default 0,
                status text not null,
                summary text not null,
                metadata_json text not null default '{}',
                created_at text not null
            )"""
        )
        con.execute("create index if not exists idx_aal_audit_log_created on aal_audit_log(created_at)")
        con.execute(
            """create table if not exists aal_export_audit (
                id integer primary key autoincrement,
                token_id integer,
                contact_id integer,
                actor_role text,
                purpose text not null,
                export_type text not null,
                data_classes_json text not null default '[]',
                period_start text,
                period_end text,
                aggregation_level text not null default 'summary',
                raw_data_included integer not null default 0,
                status text not null,
                created_at text not null
            )"""
        )
        con.execute(
            """create table if not exists notification_logs (
                id integer primary key autoincrement,
                contact_id integer,
                channel text not null,
                severity text not null,
                status text not null,
                message_title text,
                error_message text,
                data_class text not null default 'health_adjacent',
                aggregation_level text not null default 'summary',
                created_at text not null
            )"""
        )
        con.commit()


def record_audit(
    mapping: DeviceMappingService,
    *,
    event_type: str,
    category: str,
    status: str,
    summary: str,
    contact_id: Any | None = None,
    actor_role: str | None = None,
    purpose: str | None = None,
    data_classes: list[str] | None = None,
    aggregation_level: str = "summary",
    raw_data_included: bool = False,
    actor_type: str | None = None,
    actor_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    ensure_audit_schema(mapping)
    clean_metadata = sanitize_metadata(metadata or {})
    with mapping.connect() as con:
        con.execute(
            """insert into aal_audit_log
               (event_type, category, actor_type, actor_id, contact_id, actor_role, purpose, data_classes_json,
                aggregation_level, raw_data_included, status, summary, metadata_json, created_at)
               values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                category,
                actor_type,
                actor_id,
                int(contact_id) if contact_id is not None else None,
                actor_role,
                purpose,
                json.dumps(data_classes or []),
                aggregation_level,
                1 if raw_data_included else 0,
                status,
                summary,
                json.dumps(clean_metadata, ensure_ascii=False),
                now(),
            ),
        )
        con.commit()


def public_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"audit-{row.get('id')}",
        "category": row.get("category"),
        "event_type": row.get("event_type"),
        "status": row.get("status"),
        "summary": row.get("summary"),
        "contact_id": row.get("contact_id"),
        "contact_name": row.get("contact_name"),
        "actor_role": row.get("actor_role"),
        "purpose": row.get("purpose"),
        "data_classes": parse_classes(row.get("data_classes_json")),
        "aggregation_level": row.get("aggregation_level") or "summary",
        "raw_data_included": bool(row.get("raw_data_included")),
        "created_at": row.get("created_at"),
        "metadata": sanitize_metadata(parse_json(row.get("metadata_json"))),
    }


def export_summary(row: dict[str, Any]) -> str:
    kind = str(row.get("export_type") or "Export")
    contact = str(row.get("contact_name") or "Partner")
    return f"{kind} an {contact} bereitgestellt."


def notification_summary(row: dict[str, Any]) -> str:
    channel = str(row.get("channel") or "Kanal")
    contact = str(row.get("contact_name") or "Kontakt")
    status = str(row.get("status") or "")
    if status.startswith("skipped"):
        return f"Benachrichtigung an {contact} ueber {channel} blockiert."
    return f"Benachrichtigung an {contact} ueber {channel} verarbeitet."


def parse_classes(value: Any) -> list[str]:
    parsed = parse_json(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def parse_json(value: Any) -> Any:
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    blocked = {"token", "token_hash", "password", "secret", "access_token", "authorization"}
    clean: dict[str, Any] = {}
    for key, value in metadata.items():
        if str(key).lower() in blocked:
            continue
        if isinstance(value, dict):
            clean[key] = sanitize_metadata(value)
        elif isinstance(value, list):
            clean[key] = ["[redacted]" if isinstance(item, str) and looks_secret(item) else item for item in value]
        elif isinstance(value, str) and looks_secret(value):
            clean[key] = "[redacted]"
        else:
            clean[key] = value
    return clean


def looks_secret(value: str) -> bool:
    return len(value) > 40 and (" " not in value)
