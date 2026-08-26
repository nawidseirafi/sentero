from __future__ import annotations

import sqlite3
import unittest

from backend.services.human_activity_service import HumanActivityScorer


class HumanActivityScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            """create table sentero_sensor_events (
                event_time text,
                room text,
                state text,
                device_class text,
                role text
            )"""
        )
        self.scorer = HumanActivityScorer()

    def tearDown(self) -> None:
        self.con.close()

    def test_small_isolated_night_motion_is_low_confidence_human_activity(self) -> None:
        result = self.scorer.assess(self.con, {
            "event_time": "2026-08-24T02:00:00+00:00",
            "room": "Wohnzimmer",
            "state": "on",
            "event_type": "motion",
            "device_class": "motion",
            "motion_state": "small",
        })

        self.assertIsNotNone(result)
        self.assertLess(result.score, 65)
        self.assertIn(result.classification, {"uncertain", "likely_non_human"})

    def test_door_corroborated_room_transition_is_likely_human(self) -> None:
        self.con.execute(
            "insert into sentero_sensor_events values (?,?,?,?,?)",
            ("2026-08-24T09:59:00+00:00", "Flur", "on", "opening", "main_door"),
        )
        self.con.commit()

        result = self.scorer.assess(self.con, {
            "event_time": "2026-08-24T10:00:00+00:00",
            "room": "Wohnzimmer",
            "state": "on",
            "event_type": "motion",
            "device_class": "motion",
            "motion_state": "large",
        })

        self.assertIsNotNone(result)
        self.assertEqual(result.classification, "likely_human")
        self.assertGreaterEqual(result.score, 65)

    def test_off_event_is_not_scored(self) -> None:
        result = self.scorer.assess(self.con, {
            "event_time": "2026-08-24T10:00:00+00:00",
            "room": "Wohnzimmer",
            "state": "off",
            "event_type": "motion",
            "device_class": "motion",
        })
        self.assertIsNone(result)

    def test_smoke_event_is_not_scored_as_human_activity(self) -> None:
        result = self.scorer.assess(self.con, {
            "event_time": "2026-08-24T10:00:00+00:00",
            "room": "Küche",
            "state": "on",
            "event_type": "smoke_alarm",
            "device_class": "smoke",
            "role": "kitchen_smoke",
        })
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
