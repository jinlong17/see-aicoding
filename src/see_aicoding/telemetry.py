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
import re
import shutil
import subprocess
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any

import psutil

from .monitor import ProcSample, Session
from .storage import SmartCollector


GPU_SAMPLE_TIMEOUT_S = 1.0
CPU_TEMPERATURE_TIMEOUT_S = 1.0
WORKLOAD_DISK_TIMEOUT_S = 1.25
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


class CpuTemperatureCollector:
    """Best-effort CPU package temperature with an explicit unavailable state."""

    def __init__(self, cache_seconds: float = 10.0) -> None:
        self.cache_seconds = max(5.0, float(cache_seconds))
        self.platform = platform.system()
        self.osx_cpu_temp = shutil.which("osx-cpu-temp") if self.platform == "Darwin" else None
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def sample(self, sensors: list[dict[str, Any]]) -> dict[str, Any]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self.cache_seconds:
            return self._cached

        cpu_sensors = [
            sensor
            for sensor in sensors
            if re.search(
                r"(?:cpu|core|package|soc|tctl|tdie)",
                f"{sensor.get('group', '')} {sensor.get('label', '')}",
                re.IGNORECASE,
            )
            and sensor.get("current_c") is not None
        ]
        if cpu_sensors:
            hottest = max(cpu_sensors, key=lambda item: float(item.get("current_c") or 0.0))
            result = {
                "available": True,
                "temperature_c": float(hottest["current_c"]),
                "provider": "psutil sensors",
                "label": hottest.get("label") or hottest.get("group") or "CPU",
                "sampled_at": time.time(),
                "note": "Hottest readable CPU-related sensor.",
            }
        elif self.osx_cpu_temp:
            result = self._sample_osx_cpu_temp()
        else:
            result = {
                "available": False,
                "temperature_c": None,
                "provider": "none",
                "label": "CPU",
                "sampled_at": time.time(),
                "note": (
                    "macOS does not expose CPU temperature to unprivileged psutil; "
                    "configure a compatible unprivileged temperature helper to enable this reading."
                    if self.platform == "Darwin"
                    else "No readable CPU temperature sensor was detected on this system."
                ),
            }
        self._cached = result
        self._cached_at = now
        return result

    def _sample_osx_cpu_temp(self) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [str(self.osx_cpu_temp), "-C"],
                capture_output=True,
                text=True,
                timeout=CPU_TEMPERATURE_TIMEOUT_S,
                check=False,
            )
            match = (
                re.search(r"(-?\d+(?:\.\d+)?)", result.stdout)
                if result.returncode == 0
                else None
            )
        except (OSError, subprocess.SubprocessError):
            match = None
        temperature = float(match.group(1)) if match else None
        if temperature is not None and 0.0 <= temperature <= 130.0:
            return {
                "available": True,
                "temperature_c": temperature,
                "provider": "osx-cpu-temp",
                "label": "CPU package",
                "sampled_at": time.time(),
                "note": "Temperature is read through the optional osx-cpu-temp helper.",
            }
        return {
            "available": False,
            "temperature_c": None,
            "provider": "osx-cpu-temp",
            "label": "CPU",
            "sampled_at": time.time(),
            "note": "osx-cpu-temp is installed but returned no readable temperature.",
        }


class WorkloadDiskCollector:
    """Measure active project allocation asynchronously and retain a long cache."""

    def __init__(
        self,
        cache_seconds: float = 300.0,
        min_probe_interval_s: float = 5.0,
    ) -> None:
        self.cache_seconds = max(60.0, float(cache_seconds))
        self.min_probe_interval_s = max(1.0, float(min_probe_interval_s))
        self.du = shutil.which("du")
        self.nice = shutil.which("nice")
        self._cache: dict[str, dict[str, Any]] = {}
        self._pending: set[str] = set()
        self._last_probe_at = 0.0
        self._lock = threading.Lock()

    def sample(self, sessions: list[Session]) -> dict[str, Any]:
        projects: dict[str, str] = {}
        home = Path.home().resolve()
        broad_home_directories = {
            home / name
            for name in (
                "Desktop",
                "Documents",
                "Downloads",
                "Library",
                ".codex",
                ".cursor",
                ".vscode",
            )
        }
        for session in sessions:
            for project in session.project_stats:
                if not project.path:
                    continue
                try:
                    resolved = Path(project.path).resolve()
                    if not resolved.is_relative_to(home) or resolved in broad_home_directories:
                        continue
                except (OSError, RuntimeError):
                    continue
                projects[str(resolved)] = project.name

        now = time.monotonic()
        with self._lock:
            if len(self._cache) > 128:
                inactive = sorted(
                    (
                        (path, float(value.get("monotonic_at") or 0.0))
                        for path, value in self._cache.items()
                        if path not in projects and path not in self._pending
                    ),
                    key=lambda item: item[1],
                )
                for path, _sampled_at in inactive[: max(0, len(self._cache) - 128)]:
                    self._cache.pop(path, None)
            due = [
                path
                for path in projects
                if path not in self._pending
                and (
                    path not in self._cache
                    or now - float(self._cache[path].get("monotonic_at") or 0.0)
                    >= float(self._cache[path].get("ttl_seconds") or self.cache_seconds)
                )
            ]
            if due and now - self._last_probe_at >= self.min_probe_interval_s:
                path = due[0]
                self._pending.add(path)
                self._last_probe_at = now
                threading.Thread(
                    target=self._probe_and_store,
                    args=(path,),
                    daemon=True,
                    name="see-aicoding-workload-disk",
                ).start()

            items = []
            for path, name in projects.items():
                cached = self._cache.get(path)
                if cached:
                    items.append(
                        {
                            key: value
                            for key, value in cached.items()
                            if key not in {"monotonic_at", "ttl_seconds"}
                        }
                    )
                else:
                    items.append(
                        {
                            "path": path,
                            "name": name,
                            "allocated_bytes": None,
                            "status": "measuring" if path in self._pending else "pending",
                            "sampled_at": None,
                            "note": "Queued for a staggered background disk measurement.",
                        }
                    )
        return {
            "provider": "du" if self.du else "none",
            "cache_seconds": self.cache_seconds,
            "items": items,
            "pending_count": sum(item["status"] in {"pending", "measuring"} for item in items),
        }

    def _probe_and_store(self, path: str) -> None:
        result = self._probe(path)
        with self._lock:
            self._cache[path] = {
                **result,
                "monotonic_at": time.monotonic(),
                "ttl_seconds": self.cache_seconds if result["status"] == "available" else 60.0,
            }
            self._pending.discard(path)

    def _probe(self, path: str) -> dict[str, Any]:
        sampled_at = time.time()
        name = Path(path).name or path
        if not self.du:
            return {
                "path": path,
                "name": name,
                "allocated_bytes": None,
                "status": "unavailable",
                "sampled_at": sampled_at,
                "note": "The du command is unavailable.",
            }
        try:
            command = [str(self.du), "-sk", path]
            if self.nice:
                command = [str(self.nice), "-n", "10", *command]
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=WORKLOAD_DISK_TIMEOUT_S,
                check=False,
            )
            blocks = int(process.stdout.split()[0]) if process.returncode == 0 else None
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            blocks = None
        return {
            "path": path,
            "name": name,
            "allocated_bytes": blocks * 1024 if blocks is not None else None,
            "status": "available" if blocks is not None else "unavailable",
            "sampled_at": sampled_at,
            "note": (
                "Allocated project size; refreshed in a staggered background probe."
                if blocks is not None
                else "Project size probe timed out or the path was not readable."
            ),
        }


class SystemTelemetry:
    """Collect system metrics and retain a short in-memory history."""

    def __init__(self, maxlen: int = 60) -> None:
        self.maxlen = maxlen
        self.gpu = GpuCollector()
        self.cpu_temperature = CpuTemperatureCollector()
        self.smart = SmartCollector()
        self._gpu_cached: dict[str, Any] | None = None
        self._gpu_cached_at = 0.0
        self._disks_cached: list[dict[str, Any]] | None = None
        self._disks_cached_at = 0.0
        self._sensors_cached: list[dict[str, Any]] = []
        self._sensors_cached_at = 0.0
        self._battery_cached: dict[str, Any] | None = None
        self._battery_cached_at = 0.0
        self._frequency_cached: Any = None
        self._frequency_cached_at = 0.0
        self._boot_time = psutil.boot_time()
        self._logical_cpus = psutil.cpu_count() or 1
        self._physical_cpus = psutil.cpu_count(logical=False) or self._logical_cpus
        self._current_user = getpass.getuser()
        self.history: dict[str, deque[float]] = {
            key: deque(maxlen=maxlen)
            for key in (
                "cpu_percent",
                "memory_percent",
                "gpu_percent",
                "disk_read_bytes_per_s",
                "disk_write_bytes_per_s",
                "disk_read_iops",
                "disk_write_iops",
                "disk_read_latency_ms",
                "disk_write_latency_ms",
                "network_download_bytes_per_s",
                "network_upload_bytes_per_s",
            )
        }
        self._last_disk: tuple[dict[str, float], float] | None = None
        self._last_disk_devices: dict[str, dict[str, float]] = {}
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self._prime_disk()

    def _prime_disk(self) -> None:
        try:
            counters = psutil.disk_io_counters()
            device_counters = psutil.disk_io_counters(perdisk=True) or {}
        except (OSError, RuntimeError):
            counters = None
        if counters is not None:
            self._last_disk = (self._disk_counter_values(counters), time.monotonic())
            self._last_disk_devices = {
                name: self._disk_counter_values(counter)
                for name, counter in device_counters.items()
            }

    @staticmethod
    def _disk_counter_values(counter: Any) -> dict[str, float]:
        return {
            "read_count": float(getattr(counter, "read_count", 0) or 0),
            "write_count": float(getattr(counter, "write_count", 0) or 0),
            "read_bytes": float(getattr(counter, "read_bytes", 0) or 0),
            "write_bytes": float(getattr(counter, "write_bytes", 0) or 0),
            "read_time": float(getattr(counter, "read_time", 0) or 0),
            "write_time": float(getattr(counter, "write_time", 0) or 0),
        }

    @staticmethod
    def _disk_counter_rates(
        current: dict[str, float],
        previous: dict[str, float] | None,
        elapsed: float,
    ) -> dict[str, float]:
        if previous is None:
            return {
                "read_bytes_per_s": 0.0,
                "write_bytes_per_s": 0.0,
                "read_iops": 0.0,
                "write_iops": 0.0,
                "read_latency_ms": 0.0,
                "write_latency_ms": 0.0,
            }
        deltas = {
            key: max(0.0, current[key] - previous.get(key, current[key]))
            for key in current
        }
        read_ops = deltas["read_count"]
        write_ops = deltas["write_count"]
        return {
            "read_bytes_per_s": deltas["read_bytes"] / elapsed,
            "write_bytes_per_s": deltas["write_bytes"] / elapsed,
            "read_iops": read_ops / elapsed,
            "write_iops": write_ops / elapsed,
            # psutil normalizes disk read/write time to milliseconds.
            "read_latency_ms": deltas["read_time"] / read_ops if read_ops else 0.0,
            "write_latency_ms": deltas["write_time"] / write_ops if write_ops else 0.0,
        }

    def _disk_rates(self) -> dict[str, Any]:
        try:
            counters = psutil.disk_io_counters()
            device_counters = psutil.disk_io_counters(perdisk=True) or {}
        except (OSError, RuntimeError):
            counters = None
        if counters is None:
            return {
                "read_bytes_per_s": 0.0,
                "write_bytes_per_s": 0.0,
                "read_iops": 0.0,
                "write_iops": 0.0,
                "read_latency_ms": 0.0,
                "write_latency_ms": 0.0,
                "devices": [],
            }
        now = time.monotonic()
        elapsed = max(0.001, now - self._last_disk[1]) if self._last_disk else 1.0
        current = self._disk_counter_values(counters)
        rates = self._disk_counter_rates(
            current,
            self._last_disk[0] if self._last_disk else None,
            elapsed,
        )
        device_values = {
            name: self._disk_counter_values(counter)
            for name, counter in device_counters.items()
        }
        devices = []
        for name, values in device_values.items():
            device_rates = self._disk_counter_rates(
                values,
                self._last_disk_devices.get(name),
                elapsed,
            )
            devices.append(
                {
                    "device": name,
                    **device_rates,
                    "read_count": int(values["read_count"]),
                    "write_count": int(values["write_count"]),
                    "read_bytes": int(values["read_bytes"]),
                    "write_bytes": int(values["write_bytes"]),
                }
            )
        self._last_disk = (current, now)
        self._last_disk_devices = device_values
        rates["devices"] = sorted(
            devices,
            key=lambda item: -(
                item["read_bytes_per_s"] + item["write_bytes_per_s"]
            ),
        )
        return rates

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
        return sensors[:32]

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
        monotonic_now = time.monotonic()
        if monotonic_now - self._frequency_cached_at >= 5.0:
            try:
                self._frequency_cached = psutil.cpu_freq()
            except (OSError, RuntimeError):
                self._frequency_cached = None
            self._frequency_cached_at = monotonic_now
        frequency = self._frequency_cached
        try:
            load_average = os.getloadavg()
        except (AttributeError, OSError):
            load_average = (0.0, 0.0, 0.0)

        memory_used = max(0, vm.total - vm.available)
        memory_percent = memory_used / vm.total * 100.0 if vm.total else 0.0
        if self._gpu_cached is None or monotonic_now - self._gpu_cached_at >= 5.0:
            self._gpu_cached = self.gpu.sample(vm.total)
            self._gpu_cached_at = monotonic_now
        gpu = self._gpu_cached
        disk_io = self._disk_rates()
        if self._disks_cached is None or monotonic_now - self._disks_cached_at >= 15.0:
            self._disks_cached = self._disks()
            self._disks_cached_at = time.monotonic()
        disks = self._disks_cached
        storage_health = self.smart.sample(disks)
        statuses = Counter(proc.status for proc in procs.values())
        if monotonic_now - self._sensors_cached_at >= 10.0:
            self._sensors_cached = self._sensors()
            self._sensors_cached_at = monotonic_now
        sensors = self._sensors_cached
        cpu_temperature = self.cpu_temperature.sample(sensors)

        values = {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "gpu_percent": _number(gpu.get("utilization_percent"), 0.0),
            "disk_read_bytes_per_s": disk_io["read_bytes_per_s"],
            "disk_write_bytes_per_s": disk_io["write_bytes_per_s"],
            "disk_read_iops": disk_io["read_iops"],
            "disk_write_iops": disk_io["write_iops"],
            "disk_read_latency_ms": disk_io["read_latency_ms"],
            "disk_write_latency_ms": disk_io["write_latency_ms"],
            "network_download_bytes_per_s": download_bytes_per_s,
            "network_upload_bytes_per_s": upload_bytes_per_s,
        }
        for key, value in values.items():
            self.history[key].append(float(value))

        if monotonic_now - self._battery_cached_at >= 10.0:
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
            self._battery_cached = battery
            self._battery_cached_at = monotonic_now
        battery = self._battery_cached

        return {
            "boot_time": self._boot_time,
            "uptime_seconds": max(0.0, time.time() - self._boot_time),
            "logical_cpus": self._logical_cpus,
            "physical_cpus": self._physical_cpus,
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
                "temperature": cpu_temperature,
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
            "disks": disks,
            "disk_io": disk_io,
            "storage_health": storage_health,
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
                "current_user": sum(proc.username == self._current_user for proc in procs.values()),
            },
            "sensors": sensors,
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
