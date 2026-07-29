import unittest
from unittest import mock

from see_aicoding.monitor import (
    KIND_CODEX_CLI,
    History,
    ProcSample,
    ProjectSummary,
    Session,
)
from see_aicoding.snapshot import build_snapshot


class SystemSnapshotSchemaTests(unittest.TestCase):
    def test_schema_v3_exposes_processes_programs_hardware_and_events(self):
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

        with mock.patch(
            "see_aicoding.snapshot.psutil.virtual_memory"
        ) as virtual_memory:
            snapshot = build_snapshot(
                sessions=[],
                procs={proc.pid: proc},
                history=history,
                extensions=[],
                refresh_s=1.5,
                system_metrics=system_metrics,
            )
        virtual_memory.assert_not_called()

        self.assertEqual(snapshot["schema_version"], 3)
        self.assertEqual(snapshot["system"]["cpu_percent"], 42)
        self.assertEqual(snapshot["system"]["disk"]["id"], "root")
        self.assertEqual(snapshot["processes"]["scope"], "system")
        self.assertEqual(snapshot["processes"]["tree"]["root_pids"], [1234])
        self.assertEqual(snapshot["processes"]["tree"]["max_depth"], 1)
        self.assertEqual(snapshot["processes"]["items"][0]["gpu_percent"], 7)
        self.assertEqual(snapshot["resources"]["programs"][0]["process_count"], 1)
        self.assertEqual(snapshot["resources"]["programs"][0]["read_bytes_per_s"], 1024)
        self.assertIn("observability", snapshot)

    def test_workload_disk_fields_are_exposed_at_all_ai_levels(self):
        project_path = "/tmp/example-project"
        proc = ProcSample(
            pid=4321,
            ppid=1,
            name="codex",
            exe="/usr/local/bin/codex",
            cmdline_str="codex",
            create_time=1,
            cwd=project_path,
            cpu_percent=8,
            rss=64 * 1024**2,
            kind=KIND_CODEX_CLI,
        )
        project = ProjectSummary(
            name="example-project",
            path=project_path,
            cpu=8,
            rss=64 * 1024**2,
            proc_count=1,
            latest_create_time=1,
        )
        session = Session(
            session_id="codex_cli:4321",
            kind=KIND_CODEX_CLI,
            root=proc,
            project=project.name,
            projects=[project.name],
            project_stats=[project],
        )
        workload_storage = {
            "provider": "du",
            "cache_seconds": 300,
            "pending_count": 0,
            "items": [
                {
                    "path": project_path,
                    "name": project.name,
                    "allocated_bytes": 12_345_678,
                    "status": "available",
                    "sampled_at": 1234.5,
                    "note": "test measurement",
                }
            ],
        }

        snapshot = build_snapshot(
            sessions=[session],
            procs={proc.pid: proc},
            history=History(),
            extensions=[],
            refresh_s=2.0,
            workload_storage=workload_storage,
        )

        self.assertEqual(snapshot["ai"]["workload_storage"], workload_storage)
        session_item = snapshot["sessions"][0]
        self.assertEqual(session_item["disk_usage_bytes"], 12_345_678)
        self.assertEqual(session_item["disk_usage_status"], "available")
        self.assertEqual(session_item["disk_usage_available_count"], 1)
        self.assertEqual(session_item["disk_usage_project_count"], 1)
        project_item = session_item["project_stats"][0]
        self.assertEqual(project_item["path"], project_path)
        self.assertEqual(project_item["disk_usage_bytes"], 12_345_678)
        self.assertEqual(project_item["disk_usage_status"], "available")
        self.assertEqual(project_item["disk_usage_sampled_at"], 1234.5)
        chatgpt_zone = next(zone for zone in snapshot["zones"] if zone["id"] == "codex")
        self.assertEqual(chatgpt_zone["disk_usage_bytes"], 12_345_678)
        self.assertEqual(chatgpt_zone["disk_usage_status"], "available")
        self.assertEqual(chatgpt_zone["disk_usage_available_count"], 1)
        self.assertEqual(chatgpt_zone["disk_usage_project_count"], 1)

    def test_compact_stream_omits_full_inventory_without_changing_full_schema(self):
        root = ProcSample(
            pid=5000,
            ppid=1,
            name="codex",
            exe="/usr/local/bin/codex",
            cmdline_str="codex app-server",
            create_time=1,
            cwd="/tmp/project",
            cpu_percent=8,
            rss=64 * 1024**2,
            kind=KIND_CODEX_CLI,
        )
        children = [
            ProcSample(
                pid=5001 + index,
                ppid=root.pid,
                name=f"helper-{index}",
                exe=f"/tmp/helper-{index}",
                cmdline_str=f"/tmp/helper-{index} --serve",
                create_time=100 + index,
                cwd="/tmp/project",
                cpu_percent=0.1,
                rss=1024,
            )
            for index in range(12)
        ]
        session = Session(
            session_id="codex_cli:5000",
            kind=KIND_CODEX_CLI,
            root=root,
            project="project",
            projects=["project"],
            project_stats=[
                ProjectSummary(
                    name="project",
                    path="/tmp/project",
                    cpu=9.2,
                    rss=root.rss + sum(child.rss for child in children),
                    proc_count=13,
                    latest_create_time=111,
                )
            ],
            descendants=children,
        )
        observability = {
            "summary": {"active": 0, "critical": 0, "warning": 0},
            "thresholds": {},
            "active": [],
            "events": [{"id": index} for index in range(7)],
        }

        compact = build_snapshot(
            sessions=[session],
            procs={proc.pid: proc for proc in [root, *children]},
            history=History(),
            extensions=[],
            refresh_s=3,
            observability=observability,
            compact=True,
            generated_at=1234.5,
        )
        full = build_snapshot(
            sessions=[session],
            procs={proc.pid: proc for proc in [root, *children]},
            history=History(),
            extensions=[],
            refresh_s=3,
            observability=observability,
            generated_at=1234.5,
        )

        self.assertTrue(compact["stream_compact"])
        self.assertEqual(compact["generated_at"], full["generated_at"])
        self.assertNotIn("sessions", compact)
        self.assertEqual(compact["processes"]["items"], [])
        self.assertEqual(compact["resources"]["programs"], [])
        self.assertEqual(compact["resources"]["top_disk"], [])
        self.assertNotIn("members", compact["resources"]["top_cpu"][0])
        self.assertEqual(len(compact["observability"]["events"]), 4)
        zone_session = next(
            zone for zone in compact["zones"] if zone["id"] == "codex"
        )["sessions"][0]
        self.assertEqual(zone_session["root"], {"pid": root.pid})
        self.assertEqual(zone_session["child_count"], 12)
        self.assertEqual(len(zone_session["children"]), 10)
        self.assertEqual(len(full["sessions"][0]["children"]), 12)
        self.assertEqual(len(full["processes"]["items"]), 13)


if __name__ == "__main__":
    unittest.main()
