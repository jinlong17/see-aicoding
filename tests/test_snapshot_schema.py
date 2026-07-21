import unittest

from see_aicoding.monitor import History, ProcSample
from see_aicoding.snapshot import build_snapshot


class SystemSnapshotSchemaTests(unittest.TestCase):
    def test_schema_v2_exposes_processes_programs_and_hardware(self):
        proc = ProcSample(
            pid=1234,
            ppid=1,
            name="worker",
            exe="/usr/local/bin/worker",
            cmdline_str="/usr/local/bin/worker --serve",
            create_time=1,
            cwd="/tmp/project",
            cpu_percent=24,
            rss=128 * 1024**2,
            vms=512 * 1024**2,
            memory_percent=2.5,
            num_threads=6,
            username="tester",
            status="running",
            read_bytes_per_s=1024,
            write_bytes_per_s=2048,
        )
        history = History()
        system_metrics = {
            "logical_cpus": 8,
            "physical_cpus": 4,
            "cpu": {"percent": 42, "per_core_percent": [40, 44]},
            "memory": {
                "total_bytes": 16 * 1024**3,
                "available_bytes": 8 * 1024**3,
                "used_bytes": 8 * 1024**3,
                "percent": 50,
            },
            "swap": {"total_bytes": 0, "used_bytes": 0, "percent": 0},
            "gpu": {
                "available": True,
                "provider": "test",
                "utilization_percent": 12,
                "devices": [],
                "processes": [{"pid": 1234, "gpu_memory_bytes": 64 * 1024**2, "gpu_percent": 7}],
            },
            "disks": [
                {
                    "id": "root",
                    "mountpoint": "/",
                    "total_bytes": 1000,
                    "used_bytes": 400,
                    "free_bytes": 600,
                    "percent": 40,
                    "is_system": True,
                }
            ],
            "disk_io": {"read_bytes_per_s": 1024, "write_bytes_per_s": 2048},
            "network": {"download_bytes_per_s": 10, "upload_bytes_per_s": 20},
            "process_summary": {"total": 1, "running": 1},
            "history": {"cpu_percent": [42], "memory_percent": [50], "gpu_percent": [12]},
            "sensors": [],
            "battery": None,
        }

        snapshot = build_snapshot(
            sessions=[],
            procs={proc.pid: proc},
            history=history,
            extensions=[],
            refresh_s=1.5,
            system_metrics=system_metrics,
        )

        self.assertEqual(snapshot["schema_version"], 2)
        self.assertEqual(snapshot["system"]["cpu_percent"], 42)
        self.assertEqual(snapshot["system"]["disk"]["id"], "root")
        self.assertEqual(snapshot["processes"]["scope"], "system")
        self.assertEqual(snapshot["processes"]["items"][0]["gpu_percent"], 7)
        self.assertEqual(snapshot["resources"]["programs"][0]["process_count"], 1)
        self.assertEqual(snapshot["resources"]["programs"][0]["read_bytes_per_s"], 1024)


if __name__ == "__main__":
    unittest.main()
