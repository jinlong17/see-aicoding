import os
import plistlib
import unittest
from unittest import mock

import psutil

from see_aicoding.telemetry import GpuCollector, can_manage_process, manage_process
from see_aicoding.web import WebMonitorHandler


class GpuCollectorTests(unittest.TestCase):
    def test_apple_ioreg_metrics_are_normalized(self):
        memory_total = 16 * 1024**3
        plist = plistlib.dumps(
            [
                {
                    "model": "Apple M3 Pro",
                    "gpu-core-count": 18,
                    "PerformanceStatistics": {
                        "Device Utilization %": 37,
                        "In use system memory": 768 * 1024**2,
                    },
                }
            ]
        )
        collector = GpuCollector()
        collector._nvidia_smi = None
        collector._platform = "Darwin"

        with mock.patch(
            "see_aicoding.telemetry.subprocess.run",
            return_value=mock.Mock(returncode=0, stdout=plist),
        ):
            result = collector.sample(memory_total)

        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "macOS IOAccelerator")
        self.assertEqual(result["utilization_percent"], 37)
        self.assertEqual(result["memory_used_bytes"], 768 * 1024**2)
        self.assertEqual(result["devices"][0]["name"], "Apple M3 Pro")
        self.assertEqual(result["devices"][0]["cores"], 18)

    def test_missing_gpu_provider_has_explicit_unavailable_state(self):
        collector = GpuCollector()
        collector._nvidia_smi = None
        collector._platform = "UnsupportedOS"

        result = collector.sample()

        self.assertFalse(result["available"])
        self.assertIsNone(result["utilization_percent"])
        self.assertIn("No supported GPU", result["note"])


class ProcessGuardTests(unittest.TestCase):
    def test_monitor_process_is_protected(self):
        manageable, reason = can_manage_process(psutil.Process(os.getpid()))

        self.assertFalse(manageable)
        self.assertIn("protected", reason)

    def test_unknown_process_action_is_rejected_before_signaling(self):
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            manage_process(os.getpid(), "restart")

    def test_process_api_path_parser(self):
        self.assertEqual(WebMonitorHandler._pid_from_path("/api/process/421"), 421)
        self.assertEqual(
            WebMonitorHandler._pid_from_path("/api/process/421/action", action=True),
            421,
        )
        self.assertIsNone(WebMonitorHandler._pid_from_path("/api/process/not-a-pid"))
        self.assertIsNone(WebMonitorHandler._pid_from_path("/api/process/0"))


if __name__ == "__main__":
    unittest.main()
