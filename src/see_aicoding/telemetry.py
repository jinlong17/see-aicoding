"""System-wide telemetry collectors for the local web dashboard.

The terminal dashboard remains focused on AI coding sessions.  This module is
the web dashboard's hardware/process layer: it owns cross-platform system
metrics, disk and network histories, optional GPU adapters, and guarded process
inspection/actions.
"""
from __future__ import annotations

import csv
import getpass
import io
import os
import platform
import plistlib
import shutil
import subprocess
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

import psutil

from .monitor import ProcSample


GPU_SAMPLE_TIMEOUT_S = 1.0
PROTECTED_PROCESS_IDS = {0, 1}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    if value in {None, "", "N/A", "[N/A]", "Not Supported"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_number(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


class GpuCollector:
    """Best-effort GPU metrics without making GPU libraries hard dependencies."""

    def __init__(self) -> None:
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._platform = platform.system()

    def sample(self, system_memory_total: int = 0) -> dict[str, Any]:
        if self._nvidia_smi:
            result = self._sample_nvidia()
            if result.get("available"):
                return result
        if self._platform == "Darwin":
            return self._sample_apple(system_memory_total)
        if self._platform == "Linux":
            result = self._sample_linux_drm()
            if result.get("available"):
                return result
        return {
            "available": False,
            "provider": "none",
            "utilization_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "memory_percent": None,
            "devices": [],
            "processes": [],
            "note": "No supported GPU telemetry provider was detected.",
        }

    def _sample_nvidia(self) -> dict[str, Any]:
        fields = [
            "index",
            "name",
            "utilization.gpu",
            "memory.total",
            "memory.used",
            "memory.free",
            "temperature.gpu",
            "power.draw",
            "power.limit",
        ]
        try:
            result = subprocess.run(
                [
                    str(self._nvidia_smi),
                    f"--query-gpu={','.join(fields)}",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=GPU_SAMPLE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return {"available": False}
        if result.returncode != 0:
            return {"available": False}

        devices: list[dict[str, Any]] = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) < len(fields):
                continue
            values = [value.strip() for value in row]
            total_mb = _optional_number(values[3])
            used_mb = _optional_number(values[4])
            free_mb = _optional_number(values[5])
            total_bytes = int(total_mb * 1024 * 1024) if total_mb is not None else None
            used_bytes = int(used_mb * 1024 * 1024) if used_mb is not None else None
            devices.append(
                {
                    "id": values[0],
                    "name": values[1],
                    "utilization_percent": _optional_number(values[2]),
                    "memory_total_bytes": total_bytes,
                    "memory_used_bytes": used_bytes,
                    "memory_free_bytes": int(free_mb * 1024 * 1024) if free_mb is not None else None,
                    "memory_percent": (
                        used_bytes / total_bytes * 100.0
                        if used_bytes is not None and total_bytes
                        else None
                    ),
                    "temperature_c": _optional_number(values[6]),
                    "power_watts": _optional_number(values[7]),
                    "power_limit_watts": _optional_number(values[8]),
                    "cores": None,
                }
            )

        processes = self._sample_nvidia_processes()
        total_memory = sum(device.get("memory_total_bytes") or 0 for device in devices)
        used_memory = sum(device.get("memory_used_bytes") or 0 for device in devices)
        utilization_values = [
            device["utilization_percent"]
            for device in devices
            if device.get("utilization_percent") is not None
        ]
        return {
            "available": bool(devices),
            "provider": "nvidia-smi",
            "utilization_percent": (
                sum(utilization_values) / len(utilization_values)
                if utilization_values
                else None
            ),
            "memory_used_bytes": used_memory if total_memory else None,
            "memory_total_bytes": total_memory or None,
            "memory_percent": used_memory / total_memory * 100.0 if total_memory else None,
            "devices": devices,
            "processes": processes,
            "note": "Per-process GPU memory is reported for NVIDIA compute contexts.",
        }

    def _sample_nvidia_processes(self) -> list[dict[str, Any]]:
        try:
            result = subprocess.run(
                [
                    str(self._nvidia_smi),
                    "--query-compute-apps=pid,process_name,used_gpu_memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=GPU_SAMPLE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        processes: list[dict[str, Any]] = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) < 3:
                continue
            pid = _integer(row[0], -1)
            used_mb = _optional_number(row[2].strip())
            if pid <= 0:
                continue
            processes.append(
                {
                    "pid": pid,
                    "name": row[1].strip(),
                    "gpu_memory_bytes": int(used_mb * 1024 * 1024) if used_mb is not None else 0,
                    "gpu_percent": None,
                }
            )
        return processes

    def _sample_apple(self, system_memory_total: int) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["/usr/sbin/ioreg", "-r", "-d", "1", "-c", "IOAccelerator", "-a"],
                capture_output=True,
                timeout=GPU_SAMPLE_TIMEOUT_S,
                check=False,
            )
            rows = plistlib.loads(result.stdout) if result.returncode == 0 and result.stdout else []
        except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError):
            rows = []

        devices: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            stats = row.get("PerformanceStatistics") or {}
            utilization = _optional_number(stats.get("Device Utilization %"))
            if utilization is None:
                utilization = _optional_number(stats.get("Renderer Utilization %"))
            memory_used = _integer(stats.get("In use system memory"), 0)
            model = row.get("model") or row.get("MetalPluginName") or "Apple GPU"
            if isinstance(model, bytes):
                model = model.decode("utf-8", errors="replace").rstrip("\x00")
            devices.append(
                {
                    "id": str(index),
                    "name": str(model),
                    "utilization_percent": utilization,
                    "memory_total_bytes": system_memory_total or None,
                    "memory_used_bytes": memory_used,
                    "memory_free_bytes": (
                        max(0, system_memory_total - memory_used) if system_memory_total else None
                    ),
                    "memory_percent": (
                        memory_used / system_memory_total * 100.0 if system_memory_total else None
                    ),
                    "temperature_c": None,
                    "power_watts": None,
                    "power_limit_watts": None,
                    "cores": _integer(row.get("gpu-core-count"), 0) or None,
                }
            )

        utilization_values = [
            device["utilization_percent"]
            for device in devices
            if device.get("utilization_percent") is not None
        ]
        used_memory = sum(device.get("memory_used_bytes") or 0 for device in devices)
        return {
            "available": bool(devices),
            "provider": "macOS IOAccelerator",
            "utilization_percent": (
                sum(utilization_values) / len(utilization_values)
                if utilization_values
                else None
            ),
            "memory_used_bytes": used_memory if devices else None,
            "memory_total_bytes": system_memory_total or None,
            "memory_percent": (
                used_memory / system_memory_total * 100.0
                if devices and system_memory_total
                else None
            ),
            "devices": devices,
            "processes": [],
            "note": (
                "Apple GPU utilization is live. macOS does not expose reliable "
                "per-process GPU attribution through this unprivileged provider."
            ),
        }

    def _sample_linux_drm(self) -> dict[str, Any]:
        devices: list[dict[str, Any]] = []
        for card_path in sorted(Path("/sys/class/drm").glob("card[0-9]")):
            device_path = card_path / "device"
            utilization = _read_number(device_path / "gpu_busy_percent")
            total_memory = _read_number(device_path / "mem_info_vram_total")
            used_memory = _read_number(device_path / "mem_info_vram_used")
            if utilization is None and total_memory is None:
                continue
            driver = "Linux DRM GPU"
            try:
                uevent = (device_path / "uevent").read_text(encoding="utf-8")
                driver_line = next(
                    (line for line in uevent.splitlines() if line.startswith("DRIVER=")),
                    "",
                )
                if driver_line:
                    driver = f"{driver_line.split('=', 1)[1]} GPU"
            except OSError:
                pass
            devices.append(
                {
                    "id": card_path.name,
                    "name": driver,
                    "utilization_percent": utilization,
                    "memory_total_bytes": total_memory,
                    "memory_used_bytes": used_memory,
                    "memory_free_bytes": (
                        max(0, total_memory - used_memory)
                        if total_memory is not None and used_memory is not None
                        else None
                    ),
                    "memory_percent": (
                        used_memory / total_memory * 100.0
                        if used_memory is not None and total_memory
                        else None
                    ),
                    "temperature_c": None,
                    "power_watts": None,
                    "power_limit_watts": None,
                    "cores": None,
                }
            )
        utilization_values = [
            device["utilization_percent"]
            for device in devices
            if device.get("utilization_percent") is not None
        ]
        total_memory = sum(device.get("memory_total_bytes") or 0 for device in devices)
        used_memory = sum(device.get("memory_used_bytes") or 0 for device in devices)
        return {
            "available": bool(devices),
            "provider": "Linux DRM sysfs",
            "utilization_percent": (
                sum(utilization_values) / len(utilization_values)
                if utilization_values
                else None
            ),
            "memory_used_bytes": used_memory if total_memory else None,
            "memory_total_bytes": total_memory or None,
            "memory_percent": used_memory / total_memory * 100.0 if total_memory else None,
            "devices": devices,
            "processes": [],
            "note": "GPU data is provided by DRM sysfs; fields vary by driver.",
        }


class SystemTelemetry:
    """Collect system metrics and retain a short in-memory history."""

    def __init__(self, maxlen: int = 60) -> None:
        self.maxlen = maxlen
        self.gpu = GpuCollector()
        self.history: dict[str, deque[float]] = {
            key: deque(maxlen=maxlen)
            for key in (
                "cpu_percent",
                "memory_percent",
                "gpu_percent",
                "disk_read_bytes_per_s",
                "disk_write_bytes_per_s",
                "network_download_bytes_per_s",
                "network_upload_bytes_per_s",
            )
        }
        self._last_disk: tuple[int, int, float] | None = None
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._prime_disk()

    def _prime_disk(self) -> None:
        try:
            counters = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            counters = None
        if counters is not None:
            self._last_disk = (counters.read_bytes, counters.write_bytes, time.monotonic())

    def _disk_rates(self) -> tuple[float, float]:
        try:
            counters = psutil.disk_io_counters()
        except (OSError, RuntimeError):
            counters = None
        if counters is None:
            return 0.0, 0.0
        now = time.monotonic()
        read_rate = 0.0
        write_rate = 0.0
        if self._last_disk is not None:
            elapsed = max(0.001, now - self._last_disk[2])
            read_rate = max(0, counters.read_bytes - self._last_disk[0]) / elapsed
            write_rate = max(0, counters.write_bytes - self._last_disk[1]) / elapsed
        self._last_disk = (counters.read_bytes, counters.write_bytes, now)
        return read_rate, write_rate

    def _disks(self) -> list[dict[str, Any]]:
        disks: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        hidden_macos_mounts = {
            "/System/Volumes/FieldService",
            "/System/Volumes/Hardware",
            "/System/Volumes/Preboot",
            "/System/Volumes/Update",
            "/System/Volumes/VM",
            "/System/Volumes/iSCPreboot",
            "/System/Volumes/xarts",
        }
        try:
            partitions = psutil.disk_partitions(all=False)
        except (OSError, RuntimeError):
            partitions = []
        for partition in partitions:
            if platform.system() == "Darwin" and partition.mountpoint in hidden_macos_mounts:
                continue
            key = (partition.device, partition.mountpoint)
            if key in seen:
                continue
            seen.add(key)
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (OSError, PermissionError, RuntimeError):
                continue
            disks.append(
                {
                    "id": f"{partition.device}:{partition.mountpoint}",
                    "device": partition.device,
                    "mountpoint": partition.mountpoint,
                    "filesystem": partition.fstype,
                    "options": partition.opts,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "free_bytes": usage.free,
                    "percent": usage.percent,
                    "is_system": partition.mountpoint == "/",
                }
            )
        if not disks:
            try:
                usage = psutil.disk_usage(str(Path.home()))
            except (OSError, RuntimeError):
                usage = None
            if usage is not None:
                disks.append(
                    {
                        "id": "home",
                        "device": "local",
                        "mountpoint": str(Path.home()),
                        "filesystem": "",
                        "options": "",
                        "total_bytes": usage.total,
                        "used_bytes": usage.used,
                        "free_bytes": usage.free,
                        "percent": usage.percent,
                        "is_system": True,
                    }
                )
        if platform.system() == "Darwin" and any(
            disk["mountpoint"] == "/System/Volumes/Data" for disk in disks
        ):
            for disk in disks:
                disk["is_system"] = disk["mountpoint"] == "/System/Volumes/Data"
        return sorted(disks, key=lambda disk: (not disk["is_system"], disk["mountpoint"]))

    def _sensors(self) -> list[dict[str, Any]]:
        sensor_fn = getattr(psutil, "sensors_temperatures", None)
        if sensor_fn is None:
            return []
        try:
            groups = sensor_fn(fahrenheit=False)
        except (OSError, RuntimeError):
            return []
        sensors: list[dict[str, Any]] = []
        for group, entries in groups.items():
            for index, entry in enumerate(entries):
                sensors.append(
                    {
                        "id": f"{group}:{index}",
                        "group": group,
                        "label": entry.label or group,
                        "current_c": entry.current,
                        "high_c": entry.high,
                        "critical_c": entry.critical,
                    }
                )
        return sensors[:12]

    def sample(
        self,
        procs: dict[int, ProcSample],
        download_bytes_per_s: float = 0.0,
        upload_bytes_per_s: float = 0.0,
    ) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        cpu_percent = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        try:
            cpu_times = psutil.cpu_times_percent(interval=None)
        except (OSError, RuntimeError):
            cpu_times = None
        try:
            frequency = psutil.cpu_freq()
        except (OSError, RuntimeError):
            frequency = None
        try:
            load_average = os.getloadavg()
        except (AttributeError, OSError):
            load_average = (0.0, 0.0, 0.0)

        memory_used = max(0, vm.total - vm.available)
        memory_percent = memory_used / vm.total * 100.0 if vm.total else 0.0
        gpu = self.gpu.sample(vm.total)
        disk_read_rate, disk_write_rate = self._disk_rates()
        statuses = Counter(proc.status for proc in procs.values())
        current_user = getpass.getuser()

        values = {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "gpu_percent": _number(gpu.get("utilization_percent"), 0.0),
            "disk_read_bytes_per_s": disk_read_rate,
            "disk_write_bytes_per_s": disk_write_rate,
            "network_download_bytes_per_s": download_bytes_per_s,
            "network_upload_bytes_per_s": upload_bytes_per_s,
        }
        for key, value in values.items():
            self.history[key].append(float(value))

        battery = None
        battery_fn = getattr(psutil, "sensors_battery", None)
        if battery_fn is not None:
            try:
                battery_value = battery_fn()
            except (OSError, RuntimeError):
                battery_value = None
            if battery_value is not None:
                battery = {
                    "percent": battery_value.percent,
                    "plugged": battery_value.power_plugged,
                    "seconds_left": battery_value.secsleft,
                }

        return {
            "boot_time": psutil.boot_time(),
            "uptime_seconds": max(0.0, time.time() - psutil.boot_time()),
            "logical_cpus": psutil.cpu_count() or 1,
            "physical_cpus": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "cpu": {
                "percent": cpu_percent,
                "per_core_percent": per_core,
                "frequency_mhz": frequency.current if frequency else None,
                "frequency_min_mhz": frequency.min if frequency else None,
                "frequency_max_mhz": frequency.max if frequency else None,
                "load_1": load_average[0],
                "load_5": load_average[1],
                "load_15": load_average[2],
                "user_percent": getattr(cpu_times, "user", None),
                "system_percent": getattr(cpu_times, "system", None),
                "idle_percent": getattr(cpu_times, "idle", None),
            },
            "memory": {
                "total_bytes": vm.total,
                "available_bytes": vm.available,
                "used_bytes": memory_used,
                "free_bytes": getattr(vm, "free", 0),
                "percent": memory_percent,
                "active_bytes": getattr(vm, "active", None),
                "inactive_bytes": getattr(vm, "inactive", None),
                "cached_bytes": getattr(vm, "cached", None),
                "buffers_bytes": getattr(vm, "buffers", None),
                "wired_bytes": getattr(vm, "wired", None),
            },
            "swap": {
                "total_bytes": swap.total,
                "used_bytes": swap.used,
                "free_bytes": swap.free,
                "percent": swap.percent,
            },
            "gpu": gpu,
            "disks": self._disks(),
            "disk_io": {
                "read_bytes_per_s": disk_read_rate,
                "write_bytes_per_s": disk_write_rate,
            },
            "network": {
                "download_bytes_per_s": download_bytes_per_s,
                "upload_bytes_per_s": upload_bytes_per_s,
            },
            "process_summary": {
                "total": len(procs),
                "running": statuses.get(getattr(psutil, "STATUS_RUNNING", "running"), 0),
                "sleeping": statuses.get(getattr(psutil, "STATUS_SLEEPING", "sleeping"), 0),
                "stopped": statuses.get(getattr(psutil, "STATUS_STOPPED", "stopped"), 0),
                "zombie": statuses.get(getattr(psutil, "STATUS_ZOMBIE", "zombie"), 0),
                "threads": sum(proc.num_threads for proc in procs.values()),
                "current_user": sum(proc.username == current_user for proc in procs.values()),
            },
            "sensors": self._sensors(),
            "battery": battery,
            "history": {key: list(values) for key, values in self.history.items()},
        }


def can_manage_process(proc: psutil.Process) -> tuple[bool, str]:
    if proc.pid in PROTECTED_PROCESS_IDS or proc.pid in {os.getpid(), os.getppid()}:
        return False, "This process is protected by the monitor."
    try:
        owner = proc.username()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False, "The process owner could not be verified."
    if owner != getpass.getuser():
        return False, "Only processes owned by the current user can be managed."
    return True, ""


def inspect_process(pid: int) -> dict[str, Any]:
    """Return slower, on-demand details for one process."""
    proc = psutil.Process(pid)
    with proc.oneshot():
        try:
            cmdline = proc.cmdline()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cmdline = []
        try:
            cwd = proc.cwd()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cwd = None
        try:
            exe = proc.exe()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            exe = ""
        try:
            memory = proc.memory_info()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            memory = None
        try:
            cpu_times = proc.cpu_times()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            cpu_times = None
        try:
            io_counters = proc.io_counters()
        except (psutil.AccessDenied, psutil.NoSuchProcess, AttributeError):
            io_counters = None
        try:
            open_files = [item.path for item in proc.open_files()[:16]]
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            open_files = []
        try:
            children = [child.pid for child in proc.children(recursive=False)]
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            children = []
        try:
            connections_fn = getattr(proc, "net_connections", proc.connections)
            connections = connections_fn(kind="inet")
            connection_summary = dict(Counter(conn.status for conn in connections))
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            connection_summary = {}
        manageable, management_reason = can_manage_process(proc)
        created_at = proc.create_time()
        return {
            "pid": proc.pid,
            "ppid": proc.ppid(),
            "name": proc.name(),
            "username": proc.username(),
            "status": proc.status(),
            "exe": exe,
            "cwd": cwd,
            "cmdline": " ".join(cmdline) if cmdline else (exe or proc.name()),
            "create_time": created_at,
            "age_seconds": max(0.0, time.time() - created_at),
            "cpu_percent": proc.cpu_percent(interval=None),
            "cpu_time_seconds": (
                float(cpu_times.user + cpu_times.system) if cpu_times else 0.0
            ),
            "memory_bytes": memory.rss if memory else 0,
            "virtual_memory_bytes": memory.vms if memory else 0,
            "memory_percent": proc.memory_percent(),
            "threads": proc.num_threads(),
            "nice": proc.nice(),
            "read_bytes": io_counters.read_bytes if io_counters else 0,
            "write_bytes": io_counters.write_bytes if io_counters else 0,
            "open_files": open_files,
            "children": children,
            "connections": connection_summary,
            "manageable": manageable,
            "management_reason": management_reason,
        }


def manage_process(pid: int, action: str) -> dict[str, Any]:
    """Apply one guarded process action for a current-user process."""
    if action not in {"suspend", "resume", "terminate"}:
        raise ValueError("Unsupported process action.")
    proc = psutil.Process(pid)
    manageable, reason = can_manage_process(proc)
    if not manageable:
        raise PermissionError(reason)
    if action == "suspend":
        proc.suspend()
    elif action == "resume":
        proc.resume()
    else:
        proc.terminate()
    return {"ok": True, "pid": pid, "action": action}
