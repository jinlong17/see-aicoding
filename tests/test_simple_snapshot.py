import json
import unittest

from see_aicoding.web import simple_snapshot


class SimpleSnapshotTests(unittest.TestCase):
    def test_simple_payload_preserves_live_metrics_without_detail_trees(self):
        snapshot = {
            "generated_at": 123,
            "system": {
                "cpu": {"percent": 27}, "memory": {"percent": 65},
                "gpu": {"available": False}, "disk_io": {"read_iops": 3},
                "process_summary": {"total": 600},
                "history": {"cpu_percent": [27], "network_download_bytes_per_s": [10]},
            },
            "processes": {"items": [{"pid": i, "command": "worker" * 50} for i in range(600)]},
            "zones": [{"sessions": list(range(100))}],
        }
        simple = simple_snapshot(snapshot)
        self.assertEqual(simple["system"]["cpu"], snapshot["system"]["cpu"])
        self.assertEqual(simple["system"]["gpu"], {"available": False})
        self.assertEqual(simple["system"]["history"]["network_download_bytes_per_s"], [10])
        self.assertNotIn("processes", simple)
        self.assertNotIn("zones", simple)
        self.assertNotIn("cpu_percent", simple["system"]["history"])
        self.assertLess(len(json.dumps(simple)), len(json.dumps(snapshot)) / 10)
        self.assertIn("processes", snapshot)  # Projection cannot damage other clients.
