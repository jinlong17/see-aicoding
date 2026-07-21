from __future__ import annotations

import unittest

from see_aicoding.web import normalize_dashboard_preferences


class DashboardPreferencesTests(unittest.TestCase):
    def test_normalizes_unknown_values_and_keeps_identity_column(self) -> None:
        result = normalize_dashboard_preferences(
            {
                "density": "huge",
                "hidden_sections": ["runtime", "unknown", "runtime"],
                "show_idle_ai": 1,
                "section_order": ["storage", "unknown", "storage", "coding"],
                "process_columns": ["cpu", "unknown"],
            }
        )

        self.assertEqual(result["density"], "compact")
        self.assertEqual(result["hidden_sections"], ["runtime"])
        self.assertTrue(result["show_idle_ai"])
        self.assertEqual(
            result["section_order"],
            ["storage", "coding", "overview", "leaders", "processes", "runtime"],
        )
        self.assertEqual(result["process_columns"], ["identity", "cpu"])

    def test_defaults_are_returned_for_non_object_input(self) -> None:
        result = normalize_dashboard_preferences(None)

        self.assertEqual(result["density"], "compact")
        self.assertEqual(result["hidden_sections"], [])
        self.assertEqual(result["section_order"][0], "coding")
        self.assertIn("network", result["process_columns"])


if __name__ == "__main__":
    unittest.main()
