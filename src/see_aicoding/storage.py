"""Disk health adapters used by the Web resource dashboard."""
from __future__ import annotations

import json
import platform
import plistlib
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


SMART_COMMAND_TIMEOUT_S = 4.0


def normalize_block_device(device: str) -> str:
    """Best-effort partition-to-whole-device normalization."""
    value = str(device or "")
    if not value.startswith("/dev/"):
        return value
    if re.match(r"^/dev/disk\d+s\d+", value):
        return re.sub(r"(disk\d+)s\d+.*$", r"\1", value)
    if re.match(r"^/dev/(?:nvme\d+n\d+|mmcblk\d+)p\d+$", value):
        return re.sub(r"p\d+$", "", value)
    if re.match(r"^/dev/[a-z]+\d+$", value):
        return re.sub(r"\d+$", "", value)
    return value


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _temperature_c(value: Any) -> float | None:
    temperature = _number(value)
    if temperature is None:
        return None
    # Apple's NVMe payload commonly reports whole Kelvin (for example 306).
    if temperature > 200:
        temperature -= 273.15
    return round(temperature, 1)


def parse_diskutil_health(payload: bytes) -> dict[str, Any]:
    data = plistlib.loads(payload)
    raw_smart = data.get("SMARTDeviceSpecificKeysMayVaryNotGuaranteed") or {}
    status = str(data.get("SMARTStatus") or "Unknown")
    status_lower = status.lower()
    if status_lower in {"verified", "passed", "ok"}:
        health = "passed"
    elif status_lower in {"failing", "failed", "fatal"}:
        health = "failed"
    else:
        health = "unknown"
    return {
        "device": str(data.get("DeviceNode") or ""),
        "identifier": str(data.get("DeviceIdentifier") or ""),
        "model": str(data.get("MediaName") or data.get("IORegistryEntryName") or "Disk"),
        "protocol": str(data.get("BusProtocol") or ""),
        "solid_state": data.get("SolidState"),
        "internal": data.get("Internal"),
        "size_bytes": int(data.get("TotalSize") or data.get("Size") or 0),
        "smart_status": status,
        "health": health,
        "temperature_c": _temperature_c(raw_smart.get("TEMPERATURE")),
        "percentage_used": _number(raw_smart.get("PERCENTAGE_USED")),
        "available_spare_percent": _number(raw_smart.get("AVAILABLE_SPARE")),
        "power_on_hours": _number(raw_smart.get("POWER_ON_HOURS_0")),
        "power_cycles": _number(raw_smart.get("POWER_CYCLES_0")),
        "media_errors": _number(raw_smart.get("MEDIA_ERRORS_0")),
        "unsafe_shutdowns": _number(raw_smart.get("UNSAFE_SHUTDOWNS_0")),
    }


def parse_smartctl_health(output: str, device: str) -> dict[str, Any]:
    data = json.loads(output)
    smart = data.get("smart_status") or {}
    nvme = data.get("nvme_smart_health_information_log") or {}
    temperature = (data.get("temperature") or {}).get("current")
    if temperature is None:
        temperature = nvme.get("temperature")
    passed = smart.get("passed")
    if passed is True:
        health = "passed"
        status = "PASSED"
    elif passed is False:
        health = "failed"
        status = "FAILED"
    else:
        health = "unknown"
        status = "Unknown"
    model = data.get("model_name") or data.get("product") or Path(device).name
    return {
        "device": device,
        "identifier": Path(device).name,
        "model": str(model),
        "protocol": str(data.get("device", {}).get("protocol") or ""),
        "solid_state": data.get("rotation_rate") == 0 if "rotation_rate" in data else None,
        "internal": None,
        "size_bytes": int((data.get("user_capacity") or {}).get("bytes") or 0),
        "smart_status": status,
        "health": health,
        "temperature_c": _temperature_c(temperature),
        "percentage_used": _number(nvme.get("percentage_used")),
        "available_spare_percent": _number(nvme.get("available_spare")),
        "power_on_hours": _number((data.get("power_on_time") or {}).get("hours")),
        "power_cycles": _number(data.get("power_cycle_count") or nvme.get("power_cycles")),
        "media_errors": _number(nvme.get("media_errors")),
        "unsafe_shutdowns": _number(nvme.get("unsafe_shutdowns")),
    }


class SmartCollector:
    """Cache unprivileged SMART/NVMe health probes."""

    def __init__(self, cache_seconds: float = 60.0) -> None:
        self.cache_seconds = max(10.0, float(cache_seconds))
        self.platform = platform.system()
        self.diskutil = shutil.which("diskutil") or (
            "/usr/sbin/diskutil" if self.platform == "Darwin" else None
        )
        self.smartctl = shutil.which("smartctl")
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def sample(self, disks: list[dict[str, Any]], force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if (
            not force
            and self._cached is not None
            and now - self._cached_at < self.cache_seconds
        ):
            return self._cached
        devices = sorted(
            {
                normalize_block_device(str(disk.get("device") or ""))
                for disk in disks
                if str(disk.get("device") or "").startswith("/dev/")
            }
        )
        if self.platform == "Darwin" and self.diskutil:
            result = self._sample_diskutil(devices)
        elif self.smartctl:
            result = self._sample_smartctl(devices)
        else:
            result = {
                "available": False,
                "provider": "none",
                "devices": [],
                "note": "SMART provider unavailable; install smartmontools on Linux or use diskutil on macOS.",
                "sampled_at": time.time(),
            }
        self._cached = result
        self._cached_at = now
        return result

    def _sample_diskutil(self, devices: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for device in devices:
            try:
                process = subprocess.run(
                    [str(self.diskutil), "info", "-plist", device],
                    capture_output=True,
                    timeout=SMART_COMMAND_TIMEOUT_S,
                    check=False,
                )
                if process.returncode == 0 and process.stdout:
                    results.append(parse_diskutil_health(process.stdout))
                else:
                    errors.append(f"{device}: diskutil returned {process.returncode}")
            except (OSError, ValueError, plistlib.InvalidFileException, subprocess.SubprocessError) as exc:
                errors.append(f"{device}: {exc}")
        return {
            "available": bool(results),
            "provider": "macOS diskutil",
            "devices": results,
            "note": (
                "SMART/NVMe health is reported by diskutil without elevated privileges."
                if results
                else "; ".join(errors) or "No block devices were detected."
            ),
            "sampled_at": time.time(),
        }

    def _sample_smartctl(self, devices: list[str]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for device in devices:
            try:
                process = subprocess.run(
                    [str(self.smartctl), "-j", "-a", device],
                    capture_output=True,
                    text=True,
                    timeout=SMART_COMMAND_TIMEOUT_S,
                    check=False,
                )
                # smartctl uses bitmask exit codes; valid JSON can accompany warnings.
                if process.stdout.strip().startswith("{"):
                    results.append(parse_smartctl_health(process.stdout, device))
                else:
                    errors.append(f"{device}: {process.stderr.strip() or 'no JSON response'}")
            except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
                errors.append(f"{device}: {exc}")
        return {
            "available": bool(results),
            "provider": "smartctl",
            "devices": results,
            "note": (
                "SMART data is read through smartctl; some devices may require additional permissions."
                if results
                else "; ".join(errors) or "No block devices were detected."
            ),
            "sampled_at": time.time(),
        }

