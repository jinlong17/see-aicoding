"""On-demand runtime inventory collectors for services and containers."""
from __future__ import annotations

import csv
import getpass
import io
import json
import platform
import re
import shutil
import subprocess
import time
from collections import Counter
from typing import Any

import psutil


RUNTIME_COMMAND_TIMEOUT_S = 3.0


def parse_nettop_process_counters(output: str) -> list[dict[str, Any]]:
    """Parse nettop's per-process CSV mode into cumulative counters."""
    items: list[dict[str, Any]] = []
    for row in csv.reader(io.StringIO(output)):
        if len(row) < 3 or row[0] == "":
            continue
        match = re.match(r"^(.*)\.(\d+)$", row[0].strip())
        if not match:
            continue
        try:
            pid = int(match.group(2))
            received = int(float(row[1] or 0))
            sent = int(float(row[2] or 0))
        except ValueError:
            continue
        items.append(
            {
                "pid": pid,
                "name": match.group(1),
                "received_bytes": max(0, received),
                "sent_bytes": max(0, sent),
            }
        )
    return items


def parse_container_size(value: Any) -> int | None:
    text = str(value or "").strip()
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgtpe]?i?b)$", text, re.I)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    decimal_units = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4, "pb": 1000**5, "eb": 1000**6}
    binary_units = {"kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4, "pib": 1024**5, "eib": 1024**6}
    multiplier = binary_units.get(unit, decimal_units.get(unit))
    return int(number * multiplier) if multiplier is not None else None


def _parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().removesuffix("%")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_io_pair(value: Any) -> tuple[int | None, int | None]:
    parts = [part.strip() for part in str(value or "").split("/", 1)]
    if len(parts) != 2:
        return None, None
    return parse_container_size(parts[0]), parse_container_size(parts[1])


def _size_value(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return parse_container_size(value)


def _json_records(output: str) -> list[dict[str, Any]]:
    stripped = output.strip()
    if not stripped:
        return []
    try:
        payload = json.loads(stripped)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
    except json.JSONDecodeError:
        pass
    records: list[dict[str, Any]] = []
    for line in stripped.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def build_container_inventory(
    ps_output: str,
    stats_output: str,
    provider: str,
) -> list[dict[str, Any]]:
    process_rows = _json_records(ps_output)
    stats_rows = _json_records(stats_output)
    stats_by_key: dict[str, dict[str, Any]] = {}
    for stats in stats_rows:
        for key in (
            stats.get("Container"),
            stats.get("ContainerID"),
            stats.get("ID"),
            stats.get("id"),
            stats.get("Name"),
            stats.get("name"),
        ):
            if key:
                stats_by_key[str(key)] = stats
    items: list[dict[str, Any]] = []
    for row in process_rows:
        container_id = str(row.get("ID") or row.get("Id") or row.get("id") or "")
        names = row.get("Names") or row.get("Name") or row.get("names") or row.get("name") or container_id[:12]
        name = str(names[0] if isinstance(names, list) and names else names)
        stats = stats_by_key.get(container_id) or stats_by_key.get(container_id[:12]) or stats_by_key.get(name) or {}
        memory_usage, memory_limit = _parse_io_pair(
            stats.get("MemUsage") or stats.get("MemUsageBytes") or stats.get("mem_usage") or ""
        )
        if memory_usage is None:
            memory_usage = _size_value(stats.get("mem_usage") or stats.get("MemUsageBytes"))
            memory_limit = _size_value(stats.get("mem_limit") or stats.get("MemLimit"))
        network_received, network_sent = _parse_io_pair(stats.get("NetIO") or "")
        if network_received is None:
            network_received = _size_value(stats.get("net_input") or stats.get("NetInput"))
            network_sent = _size_value(stats.get("net_output") or stats.get("NetOutput"))
        block_read, block_write = _parse_io_pair(stats.get("BlockIO") or "")
        if block_read is None:
            block_read = _size_value(stats.get("block_input") or stats.get("BlockInput"))
            block_write = _size_value(stats.get("block_output") or stats.get("BlockOutput"))
        state = str(row.get("State") or row.get("Status") or row.get("state") or row.get("status") or "unknown").lower()
        running = state == "running" or state.startswith("up ")
        ports = row.get("Ports") or row.get("PortsString") or ""
        if isinstance(ports, list):
            port_parts = []
            for port in ports:
                if isinstance(port, dict):
                    host = port.get("host_ip") or port.get("HostIp") or ""
                    host_port = port.get("host_port") or port.get("HostPort") or ""
                    container_port = port.get("container_port") or port.get("ContainerPort") or port.get("PrivatePort") or ""
                    protocol = port.get("protocol") or port.get("Protocol") or "tcp"
                    port_parts.append(f"{host}:{host_port}->{container_port}/{protocol}".strip(":"))
                else:
                    port_parts.append(str(port))
            ports = ", ".join(port_parts)
        try:
            pid_count = int(float(stats.get("PIDs") or stats.get("Pids") or stats.get("pids") or 0))
        except (TypeError, ValueError):
            pid_count = 0
        items.append(
            {
                "id": container_id,
                "short_id": container_id[:12],
                "name": name,
                "image": str(row.get("Image") or row.get("ImageName") or row.get("image") or ""),
                "state": state,
                "status": str(row.get("Status") or row.get("RunningFor") or row.get("status") or state),
                "running": running,
                "ports": str(ports),
                "networks": str(row.get("Networks") or row.get("Network") or ""),
                "provider": provider,
                "cpu_percent": _parse_percent(stats.get("CPUPerc") or stats.get("CPU") or stats.get("CPUPercent") or stats.get("cpu_percent")),
                "memory_usage_bytes": memory_usage,
                "memory_limit_bytes": memory_limit,
                "memory_percent": _parse_percent(stats.get("MemPerc") or stats.get("MemPercent") or stats.get("mem_percent")),
                "network_received_bytes": network_received,
                "network_sent_bytes": network_sent,
                "block_read_bytes": block_read,
                "block_write_bytes": block_write,
                "pid_count": pid_count,
                "metrics_available": bool(stats),
            }
        )
    return sorted(items, key=lambda item: (not item["running"], item["name"].lower()))


def parse_launchctl_services(output: str) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("pid"):
            continue
        parts = stripped.split(None, 2)
        if len(parts) != 3:
            continue
        pid_text, status_text, label = parts
        try:
            pid = int(pid_text) if pid_text != "-" else None
            status_code = int(status_text)
        except ValueError:
            continue
        if pid is not None:
            state = "running"
        elif status_code == 0:
            state = "inactive"
        else:
            state = "exited"
        services.append(
            {
                "id": label,
                "name": label,
                "description": "",
                "pid": pid,
                "state": state,
                "sub_state": "running" if pid is not None else "not running",
                "status_code": status_code,
                "scope": "user",
            }
        )
    return services


def parse_systemctl_services(output: str) -> list[dict[str, Any]]:
    services: list[dict[str, Any]] = []
    for line in output.splitlines():
        stripped = line.strip().lstrip("●").strip()
        if not stripped:
            continue
        parts = stripped.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load_state, active_state, sub_state = parts[:4]
        description = parts[4] if len(parts) > 4 else ""
        services.append(
            {
                "id": unit,
                "name": unit.removesuffix(".service"),
                "description": description,
                "pid": None,
                "state": active_state,
                "sub_state": sub_state,
                "load_state": load_state,
                "status_code": None,
                "scope": "system",
            }
        )
    return services


def _service_payload(
    provider: str,
    scope: str,
    services: list[dict[str, Any]],
    note: str,
) -> dict[str, Any]:
    counts = Counter(service.get("state") or "unknown" for service in services)
    return {
        "available": True,
        "provider": provider,
        "scope": scope,
        "summary": {
            "total": len(services),
            "running": counts.get("running", 0) + counts.get("active", 0),
            "inactive": counts.get("inactive", 0),
            "failed": counts.get("failed", 0),
            "exited": counts.get("exited", 0),
        },
        "items": sorted(
            services,
            key=lambda item: (
                item.get("state") not in {"running", "active"},
                str(item.get("name") or item.get("id") or "").lower(),
            ),
        ),
        "note": note,
        "sampled_at": time.time(),
    }


class ServiceCollector:
    """Collect launchd or systemd services with a short cache."""

    def __init__(self, cache_seconds: float = 10.0) -> None:
        self.cache_seconds = max(1.0, float(cache_seconds))
        self.platform = platform.system()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def sample(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at < self.cache_seconds
        ):
            return self._cached
        if self.platform == "Darwin":
            result = self._sample_launchd()
        elif self.platform == "Linux":
            result = self._sample_systemd()
        else:
            result = {
                "available": False,
                "provider": "none",
                "scope": "none",
                "summary": {},
                "items": [],
                "note": "System service inventory is available on macOS launchd and Linux systemd.",
                "sampled_at": time.time(),
            }
        self._cached = result
        self._cached_at = now
        return result

    def _sample_launchd(self) -> dict[str, Any]:
        executable = shutil.which("launchctl") or "/bin/launchctl"
        try:
            result = subprocess.run(
                [executable, "list"],
                capture_output=True,
                text=True,
                timeout=RUNTIME_COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._unavailable("launchd", str(exc))
        if result.returncode != 0:
            return self._unavailable("launchd", result.stderr.strip() or "launchctl failed")
        return _service_payload(
            "launchd",
            "user",
            parse_launchctl_services(result.stdout),
            "当前登录用户的 launchd 域；受保护的系统域可能需要更高权限。",
        )

    def _sample_systemd(self) -> dict[str, Any]:
        executable = shutil.which("systemctl")
        if not executable:
            return self._unavailable("systemd", "systemctl was not found")
        try:
            result = subprocess.run(
                [
                    executable,
                    "list-units",
                    "--type=service",
                    "--all",
                    "--no-legend",
                    "--no-pager",
                    "--plain",
                ],
                capture_output=True,
                text=True,
                timeout=RUNTIME_COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._unavailable("systemd", str(exc))
        if result.returncode != 0:
            return self._unavailable("systemd", result.stderr.strip() or "systemctl failed")
        return _service_payload(
            "systemd",
            "system",
            parse_systemctl_services(result.stdout),
            "systemd 单元状态为只读清单；服务控制仍由系统权限策略负责。",
        )

    @staticmethod
    def _unavailable(provider: str, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "provider": provider,
            "scope": "unknown",
            "summary": {},
            "items": [],
            "note": reason,
            "sampled_at": time.time(),
        }


def _endpoint_text(endpoint: Any) -> str:
    if not endpoint:
        return ""
    host = getattr(endpoint, "ip", None)
    port = getattr(endpoint, "port", None)
    if host is None and isinstance(endpoint, tuple) and endpoint:
        host = endpoint[0]
        port = endpoint[1] if len(endpoint) > 1 else None
    if host is None:
        return ""
    return f"{host}:{port}" if port is not None else str(host)


class NetworkAttributionCollector:
    """Attribute process network activity with explicit capability levels."""

    def __init__(self, cache_seconds: float = 3.0) -> None:
        self.cache_seconds = max(1.0, float(cache_seconds))
        self.platform = platform.system()
        self.nettop = shutil.which("nettop") if self.platform == "Darwin" else None
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._nettop_counters: dict[int, tuple[int, int]] = {}
        self._nettop_at: float | None = None

    def sample(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at < self.cache_seconds
        ):
            return self._cached
        if self.nettop:
            result = self._sample_nettop()
            if result.get("available"):
                self._cached = result
                self._cached_at = now
                return result
        result = self._sample_connections()
        self._cached = result
        self._cached_at = now
        return result

    def _sample_nettop(self) -> dict[str, Any]:
        try:
            process = subprocess.run(
                [
                    str(self.nettop),
                    "-P",
                    "-L",
                    "1",
                    "-x",
                    "-J",
                    "bytes_in,bytes_out",
                ],
                capture_output=True,
                text=True,
                timeout=RUNTIME_COMMAND_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._unavailable("macOS nettop", str(exc))
        if process.returncode != 0:
            return self._unavailable(
                "macOS nettop",
                process.stderr.strip() or f"nettop returned {process.returncode}",
            )

        rows = parse_nettop_process_counters(process.stdout)
        now = time.monotonic()
        elapsed = max(0.001, now - self._nettop_at) if self._nettop_at is not None else None
        combined: dict[int, dict[str, Any]] = {}
        for row in rows:
            item = combined.setdefault(
                row["pid"],
                {
                    "pid": row["pid"],
                    "name": row["name"],
                    "received_bytes": 0,
                    "sent_bytes": 0,
                },
            )
            item["received_bytes"] += row["received_bytes"]
            item["sent_bytes"] += row["sent_bytes"]

        items: list[dict[str, Any]] = []
        current_counters: dict[int, tuple[int, int]] = {}
        for pid, row in combined.items():
            received = int(row["received_bytes"])
            sent = int(row["sent_bytes"])
            current_counters[pid] = (received, sent)
            previous = self._nettop_counters.get(pid)
            received_rate = None
            sent_rate = None
            if previous is not None and elapsed is not None:
                received_rate = max(0, received - previous[0]) / elapsed
                sent_rate = max(0, sent - previous[1]) / elapsed
            items.append(
                {
                    **row,
                    "received_bytes_per_s": received_rate,
                    "sent_bytes_per_s": sent_rate,
                    "connection_count": 0,
                    "established_count": 0,
                    "listen_count": 0,
                    "remote_endpoints": [],
                }
            )
        self._nettop_counters = current_counters
        self._nettop_at = now

        items.sort(
            key=lambda item: -(
                (item["received_bytes_per_s"] or 0)
                + (item["sent_bytes_per_s"] or 0)
                if elapsed is not None
                else item["received_bytes"] + item["sent_bytes"]
            )
        )
        details = self._connection_details(item["pid"] for item in items[:80])
        for item in items:
            item.update(details.get(item["pid"], {}))
        total_received_rate = sum(item["received_bytes_per_s"] or 0 for item in items)
        total_sent_rate = sum(item["sent_bytes_per_s"] or 0 for item in items)
        return {
            "available": True,
            "throughput_available": elapsed is not None,
            "provider": "macOS nettop",
            "summary": {
                "process_count": len(items),
                "active_processes": sum(
                    (item["received_bytes_per_s"] or 0) + (item["sent_bytes_per_s"] or 0) > 0
                    for item in items
                ),
                "received_bytes_per_s": total_received_rate,
                "sent_bytes_per_s": total_sent_rate,
                "connection_count": sum(item.get("connection_count", 0) for item in items),
            },
            "items": items,
            "note": (
                "nettop 提供逐进程字节差分；连接端点仅限当前用户可读取的进程。"
                if elapsed is not None
                else "已建立 nettop 累计计数基线；下一次刷新后显示逐进程吞吐率。"
            ),
            "sampled_at": time.time(),
        }

    def _sample_connections(self) -> dict[str, Any]:
        by_pid: dict[int, list[Any]] = {}
        try:
            connections = psutil.net_connections(kind="inet")
            for connection in connections:
                pid = getattr(connection, "pid", None)
                if pid is not None:
                    by_pid.setdefault(int(pid), []).append(connection)
        except (psutil.AccessDenied, psutil.Error, OSError):
            current_user = getpass.getuser()
            for process in psutil.process_iter(["pid", "username"]):
                if process.info.get("username") != current_user:
                    continue
                try:
                    connection_fn = getattr(process, "net_connections", process.connections)
                    process_connections = list(connection_fn(kind="inet"))
                    if process_connections:
                        by_pid[int(process.pid)] = process_connections
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    continue
        details = self._connection_details(by_pid.keys(), supplied=by_pid)
        items: list[dict[str, Any]] = []
        for pid, detail in details.items():
            try:
                name = psutil.Process(pid).name()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                name = f"PID {pid}"
            items.append(
                {
                    "pid": pid,
                    "name": name,
                    "received_bytes": None,
                    "sent_bytes": None,
                    "received_bytes_per_s": None,
                    "sent_bytes_per_s": None,
                    **detail,
                }
            )
        items.sort(key=lambda item: (-item["connection_count"], item["name"].lower()))
        return {
            "available": bool(items),
            "throughput_available": False,
            "provider": "psutil socket inventory",
            "summary": {
                "process_count": len(items),
                "active_processes": len(items),
                "received_bytes_per_s": None,
                "sent_bytes_per_s": None,
                "connection_count": sum(item["connection_count"] for item in items),
            },
            "items": items,
            "note": (
                "当前平台未提供无特权逐进程字节计数；这里显示连接、监听端口和远端归因。"
                if items
                else "当前权限无法读取逐进程网络连接；Linux 吞吐归因通常需要 eBPF 或审计代理。"
            ),
            "sampled_at": time.time(),
        }

    def _connection_details(
        self,
        pids: Any,
        supplied: dict[int, list[Any]] | None = None,
    ) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for pid_value in pids:
            pid = int(pid_value)
            connections = supplied.get(pid, []) if supplied is not None else None
            if connections is None:
                try:
                    process = psutil.Process(pid)
                    connection_fn = getattr(process, "net_connections", process.connections)
                    connections = connection_fn(kind="inet")
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    continue
            statuses = Counter(str(getattr(conn, "status", "NONE")) for conn in connections)
            endpoints = sorted(
                {
                    endpoint
                    for endpoint in (_endpoint_text(getattr(conn, "raddr", None)) for conn in connections)
                    if endpoint
                }
            )
            result[pid] = {
                "connection_count": len(connections),
                "established_count": statuses.get(getattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED"), 0),
                "listen_count": statuses.get(getattr(psutil, "CONN_LISTEN", "LISTEN"), 0),
                "remote_endpoints": endpoints[:5],
            }
        return result

    @staticmethod
    def _unavailable(provider: str, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "throughput_available": False,
            "provider": provider,
            "summary": {},
            "items": [],
            "note": reason,
            "sampled_at": time.time(),
        }


class ContainerCollector:
    """Collect Docker or Podman container inventory without daemon libraries."""

    def __init__(self, cache_seconds: float = 5.0) -> None:
        self.cache_seconds = max(2.0, float(cache_seconds))
        self.docker = shutil.which("docker")
        self.podman = shutil.which("podman")
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def sample(self, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at < self.cache_seconds
        ):
            return self._cached
        attempts: list[dict[str, Any]] = []
        if self.docker:
            attempts.append(self._sample_docker())
            if attempts[-1].get("available"):
                self._cached = attempts[-1]
                self._cached_at = now
                return self._cached
        if self.podman:
            attempts.append(self._sample_podman())
            if attempts[-1].get("available"):
                self._cached = attempts[-1]
                self._cached_at = now
                return self._cached
        if attempts:
            result = attempts[0]
            if len(attempts) > 1:
                result["note"] = "; ".join(
                    f"{attempt.get('provider')}: {attempt.get('note')}"
                    for attempt in attempts
                )
        else:
            result = {
                "available": False,
                "installed": False,
                "provider": "none",
                "summary": {},
                "items": [],
                "note": "未检测到 Docker 或 Podman CLI；安装任一运行时后会自动出现容器清单。",
                "sampled_at": time.time(),
            }
        self._cached = result
        self._cached_at = now
        return result

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=RUNTIME_COMMAND_TIMEOUT_S + 1,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def _sample_docker(self) -> dict[str, Any]:
        ps_result = self._run(
            [str(self.docker), "ps", "-a", "--no-trunc", "--format", "{{json .}}"]
        )
        if ps_result is None or ps_result.returncode != 0:
            reason = (
                ps_result.stderr.strip()
                if ps_result is not None
                else "docker command failed"
            )
            return self._unavailable("Docker", reason or "Docker daemon is unavailable")
        stats_result = self._run(
            [
                str(self.docker),
                "stats",
                "--no-stream",
                "--no-trunc",
                "--format",
                "{{json .}}",
            ]
        )
        stats_output = (
            stats_result.stdout
            if stats_result is not None and stats_result.returncode == 0
            else ""
        )
        items = build_container_inventory(ps_result.stdout, stats_output, "Docker")
        return self._payload(
            "Docker",
            items,
            metrics_available=stats_result is not None and stats_result.returncode == 0,
        )

    def _sample_podman(self) -> dict[str, Any]:
        ps_result = self._run([str(self.podman), "ps", "-a", "--format", "json"])
        if ps_result is None or ps_result.returncode != 0:
            reason = (
                ps_result.stderr.strip()
                if ps_result is not None
                else "podman command failed"
            )
            return self._unavailable("Podman", reason or "Podman service is unavailable")
        stats_result = self._run(
            [str(self.podman), "stats", "--all", "--no-stream", "--format", "json"]
        )
        stats_output = (
            stats_result.stdout
            if stats_result is not None and stats_result.returncode == 0
            else ""
        )
        items = build_container_inventory(ps_result.stdout, stats_output, "Podman")
        return self._payload(
            "Podman",
            items,
            metrics_available=stats_result is not None and stats_result.returncode == 0,
        )

    @staticmethod
    def _payload(
        provider: str,
        items: list[dict[str, Any]],
        metrics_available: bool,
    ) -> dict[str, Any]:
        running = [item for item in items if item["running"]]
        return {
            "available": True,
            "installed": True,
            "provider": provider,
            "metrics_available": metrics_available,
            "summary": {
                "total": len(items),
                "running": len(running),
                "stopped": len(items) - len(running),
                "cpu_percent": sum(item.get("cpu_percent") or 0 for item in running),
                "memory_usage_bytes": sum(item.get("memory_usage_bytes") or 0 for item in running),
                "network_received_bytes": sum(item.get("network_received_bytes") or 0 for item in running),
                "network_sent_bytes": sum(item.get("network_sent_bytes") or 0 for item in running),
            },
            "items": items,
            "note": (
                f"{provider} 容器清单与单次 stats 指标。"
                if metrics_available
                else f"{provider} 清单可用，但运行时未返回 stats 指标。"
            ),
            "sampled_at": time.time(),
        }

    @staticmethod
    def _unavailable(provider: str, reason: str) -> dict[str, Any]:
        return {
            "available": False,
            "installed": True,
            "provider": provider,
            "summary": {},
            "items": [],
            "note": reason,
            "sampled_at": time.time(),
        }
