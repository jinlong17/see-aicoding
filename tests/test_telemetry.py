import os
import plistlib
import subprocess
import unittest
from unittest import mock

import psutil

from see_aicoding.telemetry import (
    CpuTemperatureCollector,
    GpuCollector,
    WORKLOAD_DISK_TIMEOUT_S,
    WorkloadDiskCollector,
    can_manage_process,
    manage_process,
)
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


class CpuTemperatureCollectorTests(unittest.TestCase):
    def test_selects_hottest_cpu_related_sensor(self):
        collector = CpuTemperatureCollector(cache_seconds=5)
        collector.osx_cpu_temp = None

        result = collector.sample(
            [
                {"group": "nvme", "label": "Composite", "current_c": 91.0},
                {"group": "coretemp", "label": "Core 0", "current_c": 68.5},
                {"group": "thermal", "label": "CPU Package", "current_c": 73.25},
                {"group": "thermal", "label": "CPU Socket", "current_c": None},
            ]
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "psutil sensors")
        self.assertEqual(result["label"], "CPU Package")
        self.assertEqual(result["temperature_c"], 73.25)

    def test_osx_temperature_helper_exception_is_unavailable(self):
        collector = CpuTemperatureCollector(cache_seconds=5)
        collector.osx_cpu_temp = "/usr/local/bin/osx-cpu-temp"

        with mock.patch(
            "see_aicoding.telemetry.subprocess.run",
            side_effect=subprocess.TimeoutExpired("osx-cpu-temp", timeout=2),
        ):
            result = collector._sample_osx_cpu_temp()

        self.assertFalse(result["available"])
        self.assertIsNone(result["temperature_c"])
        self.assertEqual(result["provider"], "osx-cpu-temp")
        self.assertIn("no readable temperature", result["note"])


class WorkloadDiskCollectorTests(unittest.TestCase):
    def test_probe_converts_du_kib_blocks_to_bytes(self):
        collector = WorkloadDiskCollector()
        collector.du = "/usr/bin/du"
        collector.nice = "/usr/bin/nice"
        completed = mock.Mock(returncode=0, stdout="42\t/tmp/example-project\n")

        with mock.patch(
            "see_aicoding.telemetry.subprocess.run",
            return_value=completed,
        ) as run:
            result = collector._probe("/tmp/example-project")

        self.assertEqual(result["allocated_bytes"], 42 * 1024)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["name"], "example-project")
        run.assert_called_once_with(
            [
                "/usr/bin/nice",
                "-n",
                "10",
                "/usr/bin/du",
                "-sk",
                "/tmp/example-project",
            ],
            capture_output=True,
            text=True,
            timeout=WORKLOAD_DISK_TIMEOUT_S,
            check=False,
        )

    def test_probe_timeout_returns_explicit_unavailable_state(self):
        collector = WorkloadDiskCollector()
        collector.du = "/usr/bin/du"
        collector.nice = None

        with mock.patch(
            "see_aicoding.telemetry.subprocess.run",
            side_effect=subprocess.TimeoutExpired("du", timeout=2),
        ):
            result = collector._probe("/tmp/example-project")

        self.assertIsNone(result["allocated_bytes"])
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("timed out", result["note"])


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
