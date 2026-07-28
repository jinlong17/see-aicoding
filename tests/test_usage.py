from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from see_aicoding.usage import (
    CodexRateLimitSource,
    ClaudeStatusLineSource,
    UsageSourceError,
    UsageCollector,
    capture_claude_statusline,
    claude_subscription_information,
    claude_statusline_configuration,
    find_codex_executables,
    normalize_codex_rate_limits,
)


class UsageSourceTests(unittest.TestCase):
    def test_codex_candidates_prefer_bundled_app_before_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "ChatGPT-codex"
            path_cli = root / "path-codex"
            for executable in (bundled, path_cli):
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                executable.chmod(0o700)

            with (
                mock.patch(
                    "see_aicoding.usage._bundled_codex_paths",
                    return_value=(bundled,),
                ),
                mock.patch(
                    "see_aicoding.usage.shutil.which",
                    return_value=str(path_cli),
                ),
                mock.patch.dict(os.environ, {}, clear=False),
            ):
                os.environ.pop("SEE_AICODING_CODEX_BIN", None)
                result = find_codex_executables()

            self.assertEqual(result, [str(bundled), str(path_cli)])

    def test_codex_source_falls_back_and_caches_compatible_candidate(self) -> None:
        source = CodexRateLimitSource()
        available = normalize_codex_rate_limits(
            {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 53,
                        "windowDurationMins": 10080,
                    }
                }
            },
            observed_at=1234,
        )
        with (
            mock.patch(
                "see_aicoding.usage.find_codex_executables",
                return_value=["/old/codex", "/Applications/ChatGPT.app/codex"],
            ),
            mock.patch(
                "see_aicoding.usage._is_executable_file",
                return_value=True,
            ),
            mock.patch.object(
                source,
                "_collect_from_executable",
                side_effect=[UsageSourceError("unsupported --stdio"), available],
            ) as collect,
        ):
            result = source.collect()
            fallback_calls = list(collect.call_args_list)
            collect.reset_mock(side_effect=True)
            collect.side_effect = [available]
            cached_result = source.collect()

        self.assertEqual(fallback_calls[0].args, ("/old/codex",))
        self.assertEqual(
            fallback_calls[1].args,
            ("/Applications/ChatGPT.app/codex",),
        )
        self.assertEqual(cached_result["status"], "available")
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(
            collect.call_args.args,
            ("/Applications/ChatGPT.app/codex",),
        )
        self.assertEqual(
            result["metadata"]["codex_executable"],
            "/Applications/ChatGPT.app/codex",
        )
        self.assertEqual(result["metadata"]["selection"], "automatic")

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

    def test_claude_subscription_information_reports_team_as_manual_only(self) -> None:
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "subscriptionType": "team",
                    "oauthToken": "must-not-be-returned",
                }
            ),
        )
        with mock.patch(
            "see_aicoding.usage.subprocess.run",
            return_value=completed,
        ) as run:
            result = claude_subscription_information("/mock/claude")

        self.assertEqual(result["subscription_type"], "team")
        self.assertFalse(result["automatic_supported"])
        self.assertTrue(result["auth_status_available"])
        self.assertNotIn("oauthToken", result)
        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["/mock/claude", "auth", "status", "--json"],
        )

    def test_missing_claude_snapshot_reports_configured_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "command",
                            "command": (
                                "/opt/anaconda3/bin/see-aicoding "
                                "--capture-claude-usage"
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )

            configuration = claude_statusline_configuration(settings)
            with mock.patch(
                "see_aicoding.usage.claude_subscription_information",
                return_value={
                    "subscription_type": "pro",
                    "automatic_supported": True,
                    "auth_status_available": True,
                },
            ):
                result = ClaudeStatusLineSource(
                    root / "missing-usage.json",
                    settings,
                ).collect()

            self.assertTrue(configuration["configured"])
            self.assertTrue(configuration["uses_absolute_executable"])
            self.assertTrue(result["metadata"]["capture_configured"])
            self.assertTrue(result["metadata"]["waiting_for_first_response"])
            self.assertTrue(result["metadata"]["automatic_supported"])
            self.assertIn("starts automatically", result["reason"])

    def test_missing_claude_snapshot_reports_team_as_manual_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "statusLine": {
                            "type": "command",
                            "command": (
                                "/opt/anaconda3/bin/see-aicoding "
                                "--capture-claude-usage"
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            source = ClaudeStatusLineSource(root / "missing-usage.json", settings)
            with mock.patch(
                "see_aicoding.usage.claude_subscription_information",
                return_value={
                    "subscription_type": "team",
                    "automatic_supported": False,
                    "auth_status_available": True,
                },
            ) as subscription:
                first = source.collect()
                second = source.collect()

            self.assertEqual(first["status"], "unsupported")
            self.assertEqual(first["metadata"]["subscription_type"], "team")
            self.assertFalse(first["metadata"]["automatic_supported"])
            self.assertFalse(first["metadata"]["waiting_for_first_response"])
            self.assertIn("Pro/Max", first["reason"])
            self.assertEqual(second["metadata"], first["metadata"])
            subscription.assert_called_once()

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
