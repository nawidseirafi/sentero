from __future__ import annotations

import json
from collections import Counter

from backend.services.device_mapping_service import DeviceMappingService


def main() -> None:
    mapping = DeviceMappingService()
    with mapping.connect() as con:
        rows = con.execute(
            """select event_time, room, device_class, state,
                      human_activity_score, human_activity_confidence,
                      human_activity_classification, human_activity_reasons
               from sentero_sensor_events
               where human_activity_score is not null
               order by event_time desc
               limit 200"""
        ).fetchall()

    classes = Counter(str(row["human_activity_classification"] or "unknown") for row in rows)
    print("Human activity shadow report")
    print("Events:", len(rows))
    print("Classes:", dict(classes))
    print()

    for row in rows[:30]:
        try:
            reasons = json.loads(str(row["human_activity_reasons"] or "[]"))
        except json.JSONDecodeError:
            reasons = []
        print(
            row["event_time"],
            row["room"] or "-",
            row["device_class"] or "-",
            row["state"],
            f"score={row['human_activity_score']}",
            f"confidence={row['human_activity_confidence']}",
            row["human_activity_classification"],
            reasons,
        )


if __name__ == "__main__":
    main()
