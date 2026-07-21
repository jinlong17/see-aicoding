from __future__ import annotations

import unittest
from unittest import mock

from see_aicoding.runtime import (
    ContainerCollector,
    NetworkAttributionCollector,
    build_container_inventory,
    parse_container_size,
    parse_launchctl_services,
    parse_nettop_process_counters,
    parse_systemctl_services,
)


class ServiceParserTests(unittest.TestCase):
    def test_launchctl_rows_include_running_and_exited_services(self) -> None:
        rows = parse_launchctl_services(
            "PID\tStatus\tLabel\n1078\t0\tcom.apple.Finder\n-\t-9\tcom.apple.worker\n"
        )
        self.assertEqual(rows[0]["pid"], 1078)
        self.assertEqual(rows[0]["state"], "running")
        self.assertEqual(rows[1]["state"], "exited")
        self.assertEqual(rows[1]["status_code"], -9)

    def test_systemctl_rows_preserve_unit_state_and_description(self) -> None:
        rows = parse_systemctl_services(
            "ssh.service loaded active running OpenSSH server daemon\n"
            "● failed.service loaded failed failed Broken example\n"
        )
        self.assertEqual(rows[0]["name"], "ssh")
        self.assertEqual(rows[0]["state"], "active")
        self.assertEqual(rows[0]["description"], "OpenSSH server daemon")
        self.assertEqual(rows[1]["state"], "failed")


class NetworkAttributionTests(unittest.TestCase):
    def test_nettop_parser_uses_last_dot_as_pid_separator(self) -> None:
        rows = parse_nettop_process_counters(
            ",bytes_in,bytes_out,\nCodex (Service).15598,4177530,4013878,\n"
        )
        self.assertEqual(rows[0]["name"], "Codex (Service)")
        self.assertEqual(rows[0]["pid"], 15598)
        self.assertEqual(rows[0]["received_bytes"], 4177530)

    def test_nettop_counter_delta_becomes_per_process_rate(self) -> None:
        collector = NetworkAttributionCollector()
        collector.nettop = "/usr/bin/nettop"
        responses = [
            mock.Mock(returncode=0, stdout=",bytes_in,bytes_out,\nworker.42,100,50,\n"),
            mock.Mock(returncode=0, stdout=",bytes_in,bytes_out,\nworker.42,300,90,\n"),
        ]
        with mock.patch("see_aicoding.runtime.subprocess.run", side_effect=responses), mock.patch(
            "see_aicoding.runtime.time.monotonic", side_effect=[10.0, 12.0]
        ), mock.patch.object(collector, "_connection_details", return_value={}):
            first = collector._sample_nettop()
            second = collector._sample_nettop()

        self.assertFalse(first["throughput_available"])
        self.assertTrue(second["throughput_available"])
        self.assertEqual(second["items"][0]["received_bytes_per_s"], 100.0)
        self.assertEqual(second["items"][0]["sent_bytes_per_s"], 20.0)


class ContainerInventoryTests(unittest.TestCase):
    def test_docker_json_lines_merge_inventory_and_stats(self) -> None:
        ps_output = '{"ID":"abcdef1234567890","Names":"api","Image":"demo:latest","State":"running","Status":"Up 3 minutes","Ports":"127.0.0.1:8080->80/tcp"}\n'
        stats_output = '{"Container":"abcdef1234567890","Name":"api","CPUPerc":"12.5%","MemUsage":"256MiB / 2GiB","MemPerc":"12.5%","NetIO":"1.2MB / 800kB","BlockIO":"10MB / 5MB","PIDs":"7"}\n'

        items = build_container_inventory(ps_output, stats_output, "Docker")

        self.assertEqual(items[0]["name"], "api")
        self.assertTrue(items[0]["running"])
        self.assertEqual(items[0]["cpu_percent"], 12.5)
        self.assertEqual(items[0]["memory_usage_bytes"], 256 * 1024**2)
        self.assertEqual(items[0]["network_received_bytes"], 1_200_000)
        self.assertEqual(items[0]["pid_count"], 7)

    def test_container_size_supports_decimal_and_binary_units(self) -> None:
        self.assertEqual(parse_container_size("1.5GB"), 1_500_000_000)
        self.assertEqual(parse_container_size("1.5GiB"), int(1.5 * 1024**3))

    def test_missing_runtime_is_an_explicit_state(self) -> None:
        collector = ContainerCollector()
        collector.docker = None
        collector.podman = None
        result = collector.sample(force=True)
        self.assertFalse(result["available"])
        self.assertFalse(result["installed"])
        self.assertIn("Docker", result["note"])


if __name__ == "__main__":
    unittest.main()
