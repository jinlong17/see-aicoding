from __future__ import annotations

import json
import plistlib
import unittest

from see_aicoding.storage import (
    normalize_block_device,
    parse_diskutil_health,
    parse_smartctl_health,
)
from see_aicoding.telemetry import SystemTelemetry


class SmartParserTests(unittest.TestCase):
    def test_normalizes_common_partition_names(self) -> None:
        self.assertEqual(normalize_block_device("/dev/disk3s1s1"), "/dev/disk3")
        self.assertEqual(normalize_block_device("/dev/nvme0n1p2"), "/dev/nvme0n1")
        self.assertEqual(normalize_block_device("/dev/sda4"), "/dev/sda")

    def test_diskutil_nvme_health_fields_are_normalized(self) -> None:
        payload = plistlib.dumps(
            {
                "DeviceNode": "/dev/disk3",
                "DeviceIdentifier": "disk3",
                "MediaName": "APPLE SSD",
                "BusProtocol": "Apple Fabric",
                "SolidState": True,
                "TotalSize": 1000,
                "SMARTStatus": "Verified",
                "SMARTDeviceSpecificKeysMayVaryNotGuaranteed": {
                    "TEMPERATURE": 306,
                    "PERCENTAGE_USED": 5,
                    "AVAILABLE_SPARE": 100,
                    "MEDIA_ERRORS_0": 0,
                },
            }
        )
        result = parse_diskutil_health(payload)
        self.assertEqual(result["health"], "passed")
        self.assertAlmostEqual(result["temperature_c"], 32.9)
        self.assertEqual(result["percentage_used"], 5)

    def test_smartctl_health_fields_are_normalized(self) -> None:
        result = parse_smartctl_health(
            json.dumps(
                {
                    "model_name": "NVMe Test",
                    "smart_status": {"passed": True},
                    "temperature": {"current": 41},
                    "user_capacity": {"bytes": 2000},
                    "nvme_smart_health_information_log": {
                        "percentage_used": 7,
                        "media_errors": 0,
                    },
                }
            ),
            "/dev/nvme0n1",
        )
        self.assertEqual(result["smart_status"], "PASSED")
        self.assertEqual(result["temperature_c"], 41)
        self.assertEqual(result["percentage_used"], 7)


class DiskRateTests(unittest.TestCase):
    def test_iops_and_average_latency_are_derived_from_counter_deltas(self) -> None:
        previous = {
            "read_count": 10.0,
            "write_count": 20.0,
            "read_bytes": 1000.0,
            "write_bytes": 2000.0,
            "read_time": 50.0,
            "write_time": 80.0,
        }
        current = {
            "read_count": 14.0,
            "write_count": 22.0,
            "read_bytes": 5000.0,
            "write_bytes": 4000.0,
            "read_time": 62.0,
            "write_time": 90.0,
        }
        rates = SystemTelemetry._disk_counter_rates(current, previous, elapsed=2.0)
        self.assertEqual(rates["read_iops"], 2.0)
        self.assertEqual(rates["write_iops"], 1.0)
        self.assertEqual(rates["read_latency_ms"], 3.0)
        self.assertEqual(rates["write_latency_ms"], 5.0)
        self.assertEqual(rates["read_bytes_per_s"], 2000.0)


if __name__ == "__main__":
    unittest.main()
