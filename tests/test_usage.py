from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from see_aicoding.usage import (
    ClaudeStatusLineSource,
    UsageCollector,
    capture_claude_statusline,
    normalize_codex_rate_limits,
)


class UsageSourceTests(unittest.TestCase):
    def test_codex_rate_limits_prefer_primary_codex_bucket(self) -> None:
        result = normalize_codex_rate_limits(
            {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 99,
                        "windowDurationMins": 300,
                    }
                },
                "rateLimitsByLimitId": {
                    "codex": {
                        "planType": "pro",
                        "primary": {
                            "usedPercent": 51,
                            "windowDurationMins": 10080,
                            "resetsAt": 5000,
                        },
                    },
                    "codex_bengalfox": {
                        "primary": {
                            "usedPercent": 7,
                            "windowDurationMins": 300,
                        }
                    },
                },
            },
            observed_at=1234,
        )

        self.assertEqual(result["status"], "available")
        self.assertEqual(result["source"]["kind"], "codex_app_server")
        self.assertEqual(result["metadata"]["plan_type"], "pro")
        self.assertEqual(
            result["windows"],
            [
                {
                    "id": "weekly",
                    "duration_minutes": 10080,
                    "used_percent": 51.0,
                    "remaining_percent": 49.0,
                    "resets_at": 5000.0,
                }
            ],
        )
        self.assertEqual(result["observed_at"], 1234)

    def test_claude_capture_persists_only_sanitized_rate_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude-usage.json"
            summary = capture_claude_statusline(
                {
                    "session_id": "private-session",
                    "oauth_token": "do-not-store",
                    "model": {"display_name": "Claude"},
                    "rate_limits": {
                        "five_hour": {
                            "used_percentage": 12.25,
                            "resets_at": 2000,
                            "extra": "drop-me",
                        },
                        "seven_day": {
                            "used_percentage": 44,
                            "resets_at": 3000,
                        },
                    },
                },
                path,
            )

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(summary, "Claude limits · 5h 12.25% · 7d 44%")
            self.assertEqual(set(payload), {"schema_version", "captured_at", "rate_limits"})
            self.assertNotIn("oauth_token", path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["rate_limits"],
                {
                    "five_hour": {
                        "used_percentage": 12.25,
                        "resets_at": 2000.0,
                    },
                    "seven_day": {
                        "used_percentage": 44.0,
                        "resets_at": 3000.0,
                    },
                },
            )
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_empty_claude_statusline_does_not_replace_last_good_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude-usage.json"
            path.write_text('{"last":"good"}', encoding="utf-8")

            summary = capture_claude_statusline({"rate_limits": None}, path)

            self.assertEqual(summary, "Claude limits · waiting for first response")
            self.assertEqual(path.read_text(encoding="utf-8"), '{"last":"good"}')

    def test_identical_claude_snapshot_writes_at_most_once_per_minute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude-usage.json"
            payload = {
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": 12,
                        "resets_at": 2000,
                    }
                }
            }
            with mock.patch("see_aicoding.usage.time.time", return_value=1000):
                capture_claude_statusline(payload, path)
            with mock.patch("see_aicoding.usage.time.time", return_value=1030):
                capture_claude_statusline(payload, path)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["captured_at"],
                1000,
            )

            with mock.patch("see_aicoding.usage.time.time", return_value=1061):
                capture_claude_statusline(payload, path)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["captured_at"],
                1061,
            )

    def test_claude_snapshot_is_read_as_weekly_and_five_hour_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "claude-usage.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "captured_at": time.time(),
                        "rate_limits": {
                            "five_hour": {"used_percentage": 20, "resets_at": 4000},
                            "seven_day": {"used_percentage": 30, "resets_at": 5000},
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = ClaudeStatusLineSource(path).collect()

            self.assertEqual(result["status"], "available")
            self.assertEqual(result["source"]["kind"], "claude_statusline")
            self.assertEqual(
                [(window["id"], window["used_percent"]) for window in result["windows"]],
                [("five_hour", 20.0), ("weekly", 30.0)],
            )

    def test_usage_collector_refreshes_off_thread_and_reuses_cache(self) -> None:
        release = mock.Mock()

        class Source:
            def __init__(self, provider_id: str) -> None:
                self.provider_id = provider_id
                self.calls = 0

            def collect(self) -> dict:
                self.calls += 1
                release()
                return {
                    "id": self.provider_id,
                    "status": "available",
                    "source": {
                        "kind": "test",
                        "scope": "test",
                        "authoritative": True,
                    },
                    "observed_at": time.time(),
                    "windows": [],
                }

        chatgpt = Source("chatgpt")
        claude = Source("claude")
        collector = UsageCollector(
            cache_seconds=60,
            chatgpt_source=chatgpt,
            claude_source=claude,
        )

        first = collector.sample()
        self.assertTrue(first["refreshing"])
        deadline = time.time() + 1
        current = first
        while current["refreshing"] and time.time() < deadline:
            time.sleep(0.01)
            current = collector.sample()

        self.assertFalse(current["refreshing"])
        self.assertEqual(chatgpt.calls, 1)
        self.assertEqual(claude.calls, 1)
        collector.sample()
        self.assertEqual(chatgpt.calls, 1)
        self.assertEqual(claude.calls, 1)
        self.assertEqual(release.call_count, 2)

    def test_disabled_usage_collector_pauses_and_resumes_future_refreshes(self) -> None:
        chatgpt = mock.Mock()
        claude = mock.Mock()
        for provider_id, source in (("chatgpt", chatgpt), ("claude", claude)):
            source.collect.return_value = {
                "id": provider_id,
                "status": "available",
                "source": {
                    "kind": "test",
                    "scope": "test",
                    "authoritative": True,
                },
                "observed_at": time.time(),
                "windows": [],
            }
        collector = UsageCollector(
            enabled=False,
            chatgpt_source=chatgpt,
            claude_source=claude,
        )

        paused = collector.sample(force=True)
        self.assertFalse(paused["enabled"])
        self.assertFalse(paused["refreshing"])
        self.assertIsNone(paused["next_refresh_at"])
        chatgpt.collect.assert_not_called()
        claude.collect.assert_not_called()

        collector.set_enabled(True)
        current = collector.sample()
        deadline = time.time() + 1
        while current["refreshing"] and time.time() < deadline:
            time.sleep(0.01)
            current = collector.sample()
        self.assertTrue(current["enabled"])
        chatgpt.collect.assert_called_once()
        claude.collect.assert_called_once()

        collector.set_enabled(False)
        collector.sample(force=True)
        chatgpt.collect.assert_called_once()
        claude.collect.assert_called_once()

    def test_failed_refresh_keeps_last_successful_quota_as_stale(self) -> None:
        class FlakySource:
            def __init__(self) -> None:
                self.calls = 0

            def collect(self) -> dict:
                self.calls += 1
                if self.calls > 1:
                    raise RuntimeError("temporary local failure")
                return {
                    "id": "chatgpt",
                    "status": "available",
                    "source": {
                        "kind": "codex_app_server",
                        "scope": "active_codex_profile",
                        "authoritative": True,
                    },
                    "observed_at": 1000,
                    "windows": [
                        {
                            "id": "weekly",
                            "duration_minutes": 10080,
                            "used_percent": 40,
                            "remaining_percent": 60,
                            "resets_at": 2000,
                        }
                    ],
                }

        chatgpt = FlakySource()
        claude = mock.Mock()
        claude.collect.return_value = {
            "id": "claude",
            "status": "unavailable",
            "source": {
                "kind": "claude_statusline",
                "scope": "active_claude_profile",
                "authoritative": True,
            },
            "windows": [],
        }
        collector = UsageCollector(chatgpt_source=chatgpt, claude_source=claude)

        current = collector.sample()
        deadline = time.time() + 1
        while current["refreshing"] and time.time() < deadline:
            time.sleep(0.01)
            current = collector.sample()
        collector.sample(force=True)
        current = collector.sample()
        while current["refreshing"] and time.time() < deadline:
            time.sleep(0.01)
            current = collector.sample()

        by_id = {provider["id"]: provider for provider in current["providers"]}
        self.assertEqual(by_id["chatgpt"]["status"], "stale")
        self.assertEqual(by_id["chatgpt"]["windows"][0]["used_percent"], 40)
        self.assertIn("last successful", by_id["chatgpt"]["reason"])


if __name__ == "__main__":
    unittest.main()
