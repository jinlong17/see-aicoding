from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from see_aicoding.observability import ThresholdEngine
from see_aicoding.persistence import HistoryStore


def metrics(cpu: float = 25.0) -> dict:
    return {
        "cpu": {"percent": cpu},
        "memory": {"percent": 52.0},
        "gpu": {"available": True, "utilization_percent": 18.0},
        "swap": {"percent": 2.0},
        "disks": [{"percent": 41.0}],
        "disk_io": {
            "read_bytes_per_s": 1024.0,
            "write_bytes_per_s": 2048.0,
            "read_iops": 4.0,
            "write_iops": 5.0,
            "read_latency_ms": 1.5,
            "write_latency_ms": 2.5,
        },
        "network": {
            "download_bytes_per_s": 4096.0,
            "upload_bytes_per_s": 512.0,
        },
        "process_summary": {"total": 99},
    }


class HistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = HistoryStore(
            Path(self.temp.name) / "history.sqlite3",
            retention_days=2,
            persist_interval_s=1,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_persists_and_queries_downsampled_history(self) -> None:
        bucket = int((time.time() - 10) / 5) * 5
        self.assertTrue(self.store.record_sample(metrics(30), timestamp=bucket + 1))
        self.assertTrue(self.store.record_sample(metrics(50), timestamp=bucket + 3))

        history = self.store.query_history("15m")
        self.assertEqual(history["range"], "15m")
        self.assertGreaterEqual(history["point_count"], 1)
        self.assertAlmostEqual(history["series"]["cpu_percent"][0], 40.0)
        self.assertEqual(history["persistence"]["sample_count"], 2)

    def test_events_and_threshold_settings_survive_new_store(self) -> None:
        engine = ThresholdEngine()
        events = engine.evaluate(metrics(96), timestamp=time.time())
        self.assertGreater(self.store.record_events(events), 0)
        self.assertTrue(
            self.store.save_setting(
                "thresholds",
                {"cpu": {"warning": 70, "critical": 90}},
            )
        )

        reopened = HistoryStore(self.store.path)
        self.assertEqual(reopened.query_events()[0]["resource"], "cpu")
        self.assertEqual(reopened.load_setting("thresholds")["cpu"]["warning"], 70)

    def test_unknown_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.query_history("forever")


if __name__ == "__main__":
    unittest.main()
