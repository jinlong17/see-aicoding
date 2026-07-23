from __future__ import annotations

import unittest
from unittest import mock

from see_aicoding.web import MonitorState, normalize_dashboard_preferences


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
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["theme"], "deep")
        self.assertEqual(result["performance_mode"], "balanced")
        self.assertEqual(result["hidden_sections"], [])
        self.assertEqual(result["section_order"][0], "coding")
        self.assertIn("network", result["process_columns"])
        self.assertEqual(
            set(result["provider_quotas"]),
            {"claude", "chatgpt", "cursor"},
        )

    def test_normalizes_language_theme_performance_and_provider_quotas(self) -> None:
        result = normalize_dashboard_preferences(
            {
                "language": "zh-CN",
                "theme": "mint",
                "performance_mode": "efficient",
                "provider_quotas": {
                    "claude": {
                        "five_hour_used_percent": "150.75",
                        "weekly_used_percent": -8,
                    },
                    "chatgpt": {
                        "five_hour_used_percent": "not-a-number",
                        "weekly_used_percent": "",
                    },
                    "cursor": "invalid",
                    "unknown": {"weekly_used_percent": 50},
                },
                "quota_updated_at": "1234.5",
            }
        )

        self.assertEqual(result["language"], "zh-CN")
        self.assertEqual(result["theme"], "mint")
        self.assertEqual(result["performance_mode"], "efficient")
        self.assertEqual(
            result["provider_quotas"],
            {
                "claude": {
                    "five_hour_used_percent": 100.0,
                    "weekly_used_percent": 0.0,
                },
                "chatgpt": {
                    "five_hour_used_percent": None,
                    "weekly_used_percent": None,
                },
                "cursor": {
                    "five_hour_used_percent": None,
                    "weekly_used_percent": None,
                },
            },
        )
        self.assertEqual(result["quota_updated_at"], 1234.5)

    def test_invalid_language_theme_and_performance_use_defaults(self) -> None:
        result = normalize_dashboard_preferences(
            {
                "language": "fr",
                "theme": "neon",
                "performance_mode": "turbo",
                "quota_updated_at": "yesterday",
            }
        )

        self.assertEqual(result["language"], "en")
        self.assertEqual(result["theme"], "deep")
        self.assertEqual(result["performance_mode"], "balanced")
        self.assertIsNone(result["quota_updated_at"])

    def test_provider_usage_exposes_three_services_and_remaining_percent(self) -> None:
        preferences = normalize_dashboard_preferences(
            {
                "provider_quotas": {
                    "claude": {
                        "five_hour_used_percent": 12.25,
                        "weekly_used_percent": None,
                    },
                    "chatgpt": {
                        "five_hour_used_percent": 100,
                        "weekly_used_percent": 63.33,
                    },
                    "cursor": {
                        "five_hour_used_percent": None,
                        "weekly_used_percent": None,
                    },
                },
                "quota_updated_at": 9876.5,
            }
        )
        state = object.__new__(MonitorState)
        state.dashboard_preferences = mock.Mock(
            return_value={"preferences": preferences, "persisted": False}
        )

        result = state.provider_usage()

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(
            [provider["id"] for provider in result["providers"]],
            ["claude", "chatgpt", "cursor"],
        )
        expected_metadata = {
            "claude": ("Claude", "claude"),
            "chatgpt": ("ChatGPT", "codex"),
            "cursor": ("Cursor", "cursor"),
        }
        by_id = {provider["id"]: provider for provider in result["providers"]}
        for provider_id, (display_name, zone_id) in expected_metadata.items():
            with self.subTest(provider=provider_id):
                provider = by_id[provider_id]
                self.assertEqual(provider["display_name"], display_name)
                self.assertEqual(provider["workload_zone_id"], zone_id)
                self.assertEqual(
                    [(window["id"], window["duration_minutes"]) for window in provider["windows"]],
                    [("five_hour", 300), ("weekly", 10080)],
                )
                self.assertEqual(provider["source"]["kind"], "manual_local")
                self.assertFalse(provider["source"]["authoritative"])

        self.assertEqual(by_id["claude"]["windows"][0]["remaining_percent"], 87.75)
        self.assertIsNone(by_id["claude"]["windows"][1]["remaining_percent"])
        self.assertEqual(by_id["chatgpt"]["windows"][0]["remaining_percent"], 0.0)
        self.assertEqual(by_id["chatgpt"]["windows"][1]["remaining_percent"], 36.67)
        self.assertEqual(by_id["chatgpt"]["observed_at"], 9876.5)
        self.assertEqual(by_id["cursor"]["status"], "unsupported")
        self.assertIsNone(by_id["cursor"]["observed_at"])

    def test_compact_stream_omits_heavy_compatibility_arrays(self) -> None:
        full = {
            "schema_version": 3,
            "sessions": [{"session_id": "one"}],
            "processes": {"total": 1, "items": [{"pid": 42}]},
            "resources": {
                "programs": [{"name": "worker"}],
                "top_cpu": [{"label": "worker", "primary_pid": 42, "members": [1, 2]}],
            },
            "observability": {"events": [{"id": index} for index in range(8)]},
            "system": {"cpu_percent": 7},
            "zones": [
                {
                    "id": "codex",
                    "title": "ChatGPT",
                    "sessions": [
                        {
                            "id": "one",
                            "root": {"pid": 42, "cmdline": "large"},
                            "children": [
                                {"pid": index, "label": f"child {index}", "exe": "/large/path"}
                                for index in range(12)
                            ],
                        }
                    ],
                }
            ],
        }

        compact = MonitorState._compact_snapshot(full)

        self.assertTrue(compact["stream_compact"])
        self.assertNotIn("sessions", compact)
        self.assertEqual(compact["processes"]["total"], 1)
        self.assertEqual(compact["processes"]["items"], [])
        self.assertEqual(compact["resources"]["programs"], [])
        self.assertNotIn("members", compact["resources"]["top_cpu"][0])
        self.assertEqual(len(compact["observability"]["events"]), 4)
        self.assertEqual(compact["zones"][0]["title"], "ChatGPT")
        session = compact["zones"][0]["sessions"][0]
        self.assertEqual(session["root"], {"pid": 42})
        self.assertEqual(session["child_count"], 12)
        self.assertEqual(len(session["children"]), 10)
        self.assertNotIn("exe", session["children"][0])


if __name__ == "__main__":
    unittest.main()
