from __future__ import annotations

import unittest

from see_aicoding.observability import ThresholdEngine


def system_sample(cpu: float = 20.0, memory: float = 30.0) -> dict:
    return {
        "cpu": {"percent": cpu},
        "memory": {"percent": memory},
        "gpu": {"available": False, "utilization_percent": None},
        "swap": {"percent": 0.0},
        "disks": [{"percent": 40.0}],
    }


class ThresholdEngineTests(unittest.TestCase):
    def test_records_transitions_without_repeating_each_sample(self) -> None:
        engine = ThresholdEngine()

        opened = engine.evaluate(system_sample(cpu=76), timestamp=10)
        self.assertEqual(len(opened), 1)
        self.assertEqual(engine.snapshot()["events"][0]["action"], "opened")
        self.assertEqual(engine.evaluate(system_sample(cpu=82), timestamp=11), [])

        escalation = engine.evaluate(system_sample(cpu=94), timestamp=12)
        self.assertEqual(escalation[0]["action"], "escalated")
        self.assertEqual(escalation[0]["severity"], "critical")

        resolution = engine.evaluate(system_sample(cpu=60), timestamp=13)
        self.assertEqual(resolution[0]["action"], "resolved")
        self.assertEqual(engine.snapshot()["summary"]["active"], 0)

    def test_hysteresis_prevents_warning_flapping(self) -> None:
        engine = ThresholdEngine()
        engine.evaluate(system_sample(cpu=76), timestamp=10)

        self.assertEqual(engine.evaluate(system_sample(cpu=73), timestamp=11), [])
        self.assertEqual(engine.snapshot()["active"][0]["severity"], "warning")
        resolution = engine.evaluate(system_sample(cpu=71), timestamp=12)
        self.assertEqual(resolution[0]["action"], "resolved")

    def test_threshold_updates_are_validated(self) -> None:
        engine = ThresholdEngine()
        result = engine.update_thresholds(
            {"cpu": {"warning": 70, "critical": 88, "enabled": False}}
        )
        self.assertEqual(result["cpu"]["warning"], 70)
        self.assertFalse(result["cpu"]["enabled"])
        with self.assertRaises(ValueError):
            engine.update_thresholds({"cpu": {"warning": 95, "critical": 90}})


if __name__ == "__main__":
    unittest.main()
