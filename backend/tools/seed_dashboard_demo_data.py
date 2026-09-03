from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


SOURCE = "dev_dashboard_seed"


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or clear local Sentero dashboard demo data.")
    parser.add_argument("--database", default="data/sentero.db", help="Path to the local Sentero SQLite database.")
    parser.add_argument("--days", type=int, default=14, help="Number of demo days to create.")
    parser.add_argument("--end-date", default=None, help="Last seeded date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--clear", action="store_true", help="Remove seeded data and restore backed-up summaries/profile.")
    args = parser.parse_args()

    database = Path(args.database)
    if not database.exists():
        raise SystemExit(f"Database not found: {database}")

    end_day = date.fromisoformat(args.end_date) if args.end_date else date.today()
    days = max(1, min(30, args.days))

    with sqlite3.connect(database) as con:
        con.row_factory = sqlite3.Row
        ensure_seed_tables(con)
        if args.clear:
            clear_seed(con)
            print("Dashboard demo data cleared.")
            return
        seed(con, end_day=end_day, days=days)
        print(f"Dashboard demo data seeded for {days} days through {end_day.isoformat()}.")


def ensure_seed_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        create table if not exists dev_dashboard_seed_dates (
            date text primary key,
            had_summary integer not null,
            summary_json text,
            created_at text not null
        )
        """
    )
    con.execute(
        """
        create table if not exists dev_dashboard_seed_profile_backup (
            user_id integer primary key,
            profile_json text not null,
            created_at text not null
        )
        """
    )


def seed(con: sqlite3.Connection, end_day: date, days: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    backup_profile(con, now)
    dates = [end_day - timedelta(days=days - 1 - index) for index in range(days)]
    for index, current_day in enumerate(dates):
        backup_summary(con, current_day, now)
        con.execute("delete from sentero_sensor_events where source = ? and substr(event_time, 1, 10) = ?", (SOURCE, current_day.isoformat()))
        summary, events = demo_day(current_day, index)
        con.execute(
            """
            insert into behavior_daily_summary
                (date, wakeup_time, first_activity, last_activity, active_minutes, inactivity_periods, room_usage, door_events, occupancy_score, anomaly_score)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(date) do update set
                wakeup_time = excluded.wakeup_time,
                first_activity = excluded.first_activity,
                last_activity = excluded.last_activity,
                active_minutes = excluded.active_minutes,
                inactivity_periods = excluded.inactivity_periods,
                room_usage = excluded.room_usage,
                door_events = excluded.door_events,
                occupancy_score = excluded.occupancy_score,
                anomaly_score = excluded.anomaly_score
            """,
            (
                current_day.isoformat(),
                summary["wakeup_time"],
                summary["first_activity"],
                summary["last_activity"],
                summary["active_minutes"],
                json.dumps(summary["inactivity_periods"], ensure_ascii=False),
                json.dumps(summary["room_usage"], ensure_ascii=False),
                summary["door_events"],
                summary["occupancy_score"],
                summary["anomaly_score"],
            ),
        )
        con.executemany(
            """
            insert into sentero_sensor_events
                (event_time, role, room, entity_id, state, device_class, source, created_at, data_class, aggregation_level, human_activity_score, human_activity_confidence, human_activity_classification, human_activity_reasons)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    event["event_time"],
                    event["role"],
                    event["room"],
                    event["entity_id"],
                    event["state"],
                    event["device_class"],
                    SOURCE,
                    now,
                    event["data_class"],
                    event["aggregation_level"],
                    event["human_activity_score"],
                    event["human_activity_confidence"],
                    event["human_activity_classification"],
                    json.dumps(event["human_activity_reasons"], ensure_ascii=False),
                )
                for event in events
            ],
        )
    update_profile(con, dates)
    con.commit()


def clear_seed(con: sqlite3.Connection) -> None:
    rows = con.execute("select * from dev_dashboard_seed_dates order by date").fetchall()
    for row in rows:
        if int(row["had_summary"]):
            summary = json.loads(row["summary_json"] or "{}")
            con.execute(
                """
                insert into behavior_daily_summary
                    (date, wakeup_time, first_activity, last_activity, active_minutes, inactivity_periods, room_usage, door_events, occupancy_score, anomaly_score)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(date) do update set
                    wakeup_time = excluded.wakeup_time,
                    first_activity = excluded.first_activity,
                    last_activity = excluded.last_activity,
                    active_minutes = excluded.active_minutes,
                    inactivity_periods = excluded.inactivity_periods,
                    room_usage = excluded.room_usage,
                    door_events = excluded.door_events,
                    occupancy_score = excluded.occupancy_score,
                    anomaly_score = excluded.anomaly_score
                """,
                (
                    summary["date"],
                    summary.get("wakeup_time"),
                    summary.get("first_activity"),
                    summary.get("last_activity"),
                    int(summary.get("active_minutes") or 0),
                    summary.get("inactivity_periods") or "[]",
                    summary.get("room_usage") or "{}",
                    int(summary.get("door_events") or 0),
                    float(summary.get("occupancy_score") or 0),
                    int(summary.get("anomaly_score") or 0),
                ),
            )
        else:
            con.execute("delete from behavior_daily_summary where date = ?", (row["date"],))
    profile_rows = con.execute("select * from dev_dashboard_seed_profile_backup").fetchall()
    for row in profile_rows:
        profile = json.loads(row["profile_json"])
        con.execute(
            """
            insert into behavior_profile
                (user_id, average_wakeup_time, average_sleep_time, average_active_minutes, room_usage_patterns, normal_door_usage, learning_completed, learning_started_at, learning_completed_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(user_id) do update set
                average_wakeup_time = excluded.average_wakeup_time,
                average_sleep_time = excluded.average_sleep_time,
                average_active_minutes = excluded.average_active_minutes,
                room_usage_patterns = excluded.room_usage_patterns,
                normal_door_usage = excluded.normal_door_usage,
                learning_completed = excluded.learning_completed,
                learning_started_at = excluded.learning_started_at,
                learning_completed_at = excluded.learning_completed_at
            """,
            (
                int(profile.get("user_id") or 1),
                profile.get("average_wakeup_time"),
                profile.get("average_sleep_time"),
                float(profile.get("average_active_minutes") or 0),
                profile.get("room_usage_patterns") or "{}",
                profile.get("normal_door_usage") or "{}",
                int(profile.get("learning_completed") or 0),
                profile.get("learning_started_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                profile.get("learning_completed_at"),
            ),
        )
    con.execute("delete from sentero_sensor_events where source = ?", (SOURCE,))
    con.execute("delete from dev_dashboard_seed_dates")
    con.execute("delete from dev_dashboard_seed_profile_backup")
    con.commit()


def backup_summary(con: sqlite3.Connection, current_day: date, created_at: str) -> None:
    existing = con.execute("select * from behavior_daily_summary where date = ?", (current_day.isoformat(),)).fetchone()
    con.execute(
        """
        insert into dev_dashboard_seed_dates (date, had_summary, summary_json, created_at)
        values (?, ?, ?, ?)
        on conflict(date) do nothing
        """,
        (
            current_day.isoformat(),
            int(existing is not None),
            json.dumps(dict(existing), ensure_ascii=False) if existing else None,
            created_at,
        ),
    )


def backup_profile(con: sqlite3.Connection, created_at: str) -> None:
    existing = con.execute("select * from behavior_profile where user_id = 1").fetchone()
    if not existing:
        return
    con.execute(
        """
        insert into dev_dashboard_seed_profile_backup (user_id, profile_json, created_at)
        values (?, ?, ?)
        on conflict(user_id) do nothing
        """,
        (1, json.dumps(dict(existing), ensure_ascii=False), created_at),
    )


def update_profile(con: sqlite3.Connection, dates: list[date]) -> None:
    summaries = [demo_day(current_day, index)[0] for index, current_day in enumerate(dates)]
    wakeup_avg = average_minutes([minutes_of_day(item["wakeup_time"]) for item in summaries])
    sleep_avg = average_minutes([minutes_of_day(time_from_iso(item["last_activity"])) for item in summaries])
    active_avg = sum(int(item["active_minutes"]) for item in summaries) / len(summaries)
    normal_door_usage = {"average_daily_events": round(sum(int(item["door_events"]) for item in summaries) / len(summaries), 2)}
    room_usage = {"kitchen": 0.34, "living_room": 0.38, "bathroom": 0.12, "bedroom": 0.16}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute(
        """
        insert into behavior_profile
            (user_id, average_wakeup_time, average_sleep_time, average_active_minutes, room_usage_patterns, normal_door_usage, learning_completed, learning_started_at, learning_completed_at)
        values (1, ?, ?, ?, ?, ?, 1, ?, ?)
        on conflict(user_id) do update set
            average_wakeup_time = excluded.average_wakeup_time,
            average_sleep_time = excluded.average_sleep_time,
            average_active_minutes = excluded.average_active_minutes,
            room_usage_patterns = excluded.room_usage_patterns,
            normal_door_usage = excluded.normal_door_usage,
            learning_completed = excluded.learning_completed,
            learning_completed_at = excluded.learning_completed_at
        """,
        (
            format_minutes(wakeup_avg),
            format_minutes(sleep_avg),
            round(active_avg, 1),
            json.dumps(room_usage, ensure_ascii=False),
            json.dumps(normal_door_usage, ensure_ascii=False),
            now,
            now,
        ),
    )


def demo_day(current_day: date, index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    wake_offsets = [-8, 4, 11, -3, 18, 7, 0, 14, 22, -5, 9, 16, 28, 6]
    active_minutes = [104, 118, 126, 111, 92, 135, 122, 96, 88, 128, 142, 109, 74, 116]
    wake = minutes_to_time(7 * 60 + 18 + wake_offsets[index % len(wake_offsets)])
    last = minutes_to_time(22 * 60 + 8 + [0, 14, -9, 22, 8, -16, 11][index % 7])
    anomaly = index in {4, 12}
    door_events = 2 if index not in {5, 12} else 0
    events = build_events(current_day, wake, last, active_minutes[index % len(active_minutes)], door_events, anomaly)
    first_activity = iso(current_day, wake)
    last_activity = iso(current_day, last)
    inactivity = []
    if anomaly:
        rest_start = datetime.combine(current_day, time(13, 35), tzinfo=timezone.utc)
        rest_end = rest_start + timedelta(minutes=210 if index == 12 else 185)
        inactivity.append({"from": rest_start.isoformat(timespec="minutes"), "to": rest_end.isoformat(timespec="minutes"), "minutes": int((rest_end - rest_start).total_seconds() / 60)})
    return {
        "wakeup_time": wake,
        "first_activity": first_activity,
        "last_activity": last_activity,
        "active_minutes": active_minutes[index % len(active_minutes)],
        "inactivity_periods": inactivity,
        "room_usage": {"bedroom": 8, "bathroom": 14, "kitchen": 38, "living_room": 52, "hallway": 10},
        "door_events": door_events,
        "occupancy_score": min(100, round(active_minutes[index % len(active_minutes)] / 6, 1)),
        "anomaly_score": 25 if anomaly else 0,
    }, events


def build_events(current_day: date, wake: str, last: str, minutes: int, door_events: int, anomaly: bool) -> list[dict[str, Any]]:
    anchors = [
        (wake, "bedroom_presence", "bedroom", "presence"),
        (add_minutes(wake, 18), "bathroom_motion", "bathroom", "motion"),
        (add_minutes(wake, 41), "kitchen_motion", "kitchen", "motion"),
        ("11:47", "hallway_motion", "hallway", "motion"),
        ("16:08" if anomaly else "14:22", "living_room_presence", "living_room", "presence"),
        (last, "living_room_presence", "living_room", "presence"),
    ]
    result: list[dict[str, Any]] = []
    for time_text, role, room, device_class in anchors:
        result.append(event(current_day, time_text, role, room, "on", device_class, 78, "routine_activity"))
    for offset in range(max(0, minutes - len(anchors))):
        hour = 8 + (offset % 12)
        minute = (offset * 7) % 60
        room = ["kitchen", "living_room", "bathroom", "hallway"][offset % 4]
        result.append(event(current_day, f"{hour:02d}:{minute:02d}", f"{room}_motion", room, "on", "motion", 64, "activity_cluster"))
    if door_events:
        result.append(event(current_day, "10:34", "entrance_door", "entrance", "open", "door", 50, "door_opened"))
        result.append(event(current_day, "11:44", "entrance_door", "entrance", "open", "door", 50, "door_opened"))
    if anomaly:
        result.append(event(current_day, "18:43", "bathroom_humidity", "bathroom", "72", "humidity", None, "humidity_notice", "health_adjacent"))
    return sorted(result, key=lambda item: item["event_time"])


def event(
    current_day: date,
    time_text: str,
    role: str,
    room: str,
    state: str,
    device_class: str,
    score: int | None,
    classification: str,
    data_class: str = "personal_behavior",
) -> dict[str, Any]:
    return {
        "event_time": iso(current_day, time_text),
        "role": role,
        "room": room,
        "entity_id": f"dev_sample.{role}",
        "state": state,
        "device_class": device_class,
        "data_class": data_class,
        "aggregation_level": "event",
        "human_activity_score": score,
        "human_activity_confidence": 0.9 if score is not None else None,
        "human_activity_classification": classification,
        "human_activity_reasons": ["Lokale Dashboard-Testdaten"],
    }


def iso(current_day: date, time_text: str) -> str:
    hour, minute = [int(part) for part in time_text.split(":")[:2]]
    return datetime.combine(current_day, time(hour, minute), tzinfo=timezone.utc).isoformat(timespec="seconds")


def add_minutes(time_text: str, minutes: int) -> str:
    hour, minute = [int(part) for part in time_text.split(":")[:2]]
    value = hour * 60 + minute + minutes
    return minutes_to_time(value)


def minutes_to_time(value: int) -> str:
    value = max(0, min(23 * 60 + 59, value))
    return f"{value // 60:02d}:{value % 60:02d}"


def minutes_of_day(value: str | None) -> int | None:
    if not value:
        return None
    hour, minute = [int(part) for part in value.split(":")[:2]]
    return hour * 60 + minute


def average_minutes(values: list[int | None]) -> int:
    actual = [value for value in values if value is not None]
    return round(sum(actual) / len(actual)) if actual else 0


def format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def time_from_iso(value: str) -> str:
    return value[11:16]


if __name__ == "__main__":
    main()
