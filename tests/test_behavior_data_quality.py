from __future__ import annotations

import unittest

from backend.behavior_agent import SenteroBehaviorAgent


class BehaviorDataQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = SenteroBehaviorAgent.__new__(SenteroBehaviorAgent)

    def test_unreliable_activity_sensors_make_heuristic_assessment_yellow(self) -> None:
        assessment = self.agent._heuristic_assessment({
            "anomaly_score": 25,
            "deviations": {"no_activity_today": True},
            "data_quality": {
                "activity_sensors": 1,
                "monitoring_reliable": False,
                "observation_limited": True,
                "stale_sensors": 1,
                "unreachable_sensors": 0,
            },
        })

        self.assertEqual(assessment["status"], "yellow")
        self.assertLessEqual(assessment["confidence"], 0.45)
        self.assertIn("Aktivitätssensoren", " ".join(assessment["findings"]))

    def test_partial_sensor_limit_prevents_plain_green(self) -> None:
        assessment = self.agent._apply_learning_policy(
            {
                "status": "green",
                "confidence": 0.9,
                "findings": [],
                "summary": "Alles normal.",
                "recommendation": "Keine Aktion erforderlich.",
                "email_subject": "",
                "email_body": "",
            },
            {
                "anomaly_score": 0,
                "learning": {"completed": True, "day": 14, "days": 14},
                "data_quality": {
                    "activity_sensors": 2,
                    "monitoring_reliable": True,
                    "observation_limited": True,
                    "stale_sensors": 1,
                    "unreachable_sensors": 0,
                },
            },
        )

        self.assertEqual(assessment["status"], "yellow")
        self.assertLessEqual(assessment["confidence"], 0.6)
        self.assertIn("Datenlage", assessment["summary"])


if __name__ == "__main__":
    unittest.main()
