"""JSON snapshot model for the web monitor."""
from __future__ import annotations

import getpass
import platform
import socket
import time
from pathlib import Path
from typing import Any

import psutil

from .cursor_ext import ExtensionInfo
from .monitor import (
    KIND_META,
    ProcSample,
    ProjectSummary,
    Session,
    ZONE_CLAUDE,
    ZONE_CODEX,
    ZONE_CURSOR,
    ZONE_META,
    History,
    fmt_duration,
    sparkline,
)
from .render import build_resource_groups, resource_group_detail


IDLE_CPU_THRESHOLD = 0.5
LOGICAL_CPU_COUNT = psutil.cpu_count() or 1


def _cpu_capacity(cpu_percent: float) -> float:
    return cpu_percent / LOGICAL_CPU_COUNT


def _system_name() -> str:
    mac_version = platform.mac_ver()[0]
    if mac_version:
        return f"macOS {mac_version}"
    return f"{platform.system()} {platform.release()}".strip()


def _disk_usage() -> dict[str, Any] | None:
    try:
        disk = psutil.disk_usage(str(Path.home()))
    except (OSError, RuntimeError):
        return None
    return {
        "total_bytes": disk.total,
        "used_bytes": disk.used,
        "free_bytes": disk.free,
        "percent": disk.percent,
    }


def _proc_label(proc: ProcSample) -> str:
    name = proc.name or ""
    if proc.exe:
        name = Path(proc.exe).name or name
    return name or f"pid {proc.pid}"


def _shorten(value: str, limit: int = 260) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit - 1]}..."


def _proc_to_dict(
    proc: ProcSample,
    full_cmdline: bool = True,
    gpu_process: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cmdline = proc.cmdline_str if full_cmdline else _shorten(proc.cmdline_str)
    gpu_process = gpu_process or {}
    return {
        "pid": proc.pid,
        "ppid": proc.ppid,
        "name": proc.name,
        "label": _proc_label(proc),
        "exe": proc.exe,
        "cmdline": cmdline,
        "cwd": proc.cwd,
        "create_time": proc.create_time,
        "age_seconds": max(0.0, time.time() - proc.create_time) if proc.create_time else 0.0,
        "age_label": fmt_duration(max(0.0, time.time() - proc.create_time)) if proc.create_time else "0s",
        "cpu_percent": proc.cpu_percent,
        "cpu_capacity_percent": _cpu_capacity(proc.cpu_percent),
        "cpu_time_seconds": proc.cpu_time_seconds,
        "memory_bytes": proc.rss,
        "virtual_memory_bytes": proc.vms,
        "memory_percent": proc.memory_percent,
        "threads": proc.num_threads,
        "username": proc.username,
        "status": proc.status,
        "read_bytes": proc.read_bytes,
        "write_bytes": proc.write_bytes,
        "read_bytes_per_s": proc.read_bytes_per_s,
        "write_bytes_per_s": proc.write_bytes_per_s,
        "gpu_percent": gpu_process.get("gpu_percent"),
        "gpu_memory_bytes": gpu_process.get("gpu_memory_bytes", 0),
        "kind": proc.kind,
        "missing_cwd": proc.cwd is None,
        "missing_exe": not bool(proc.exe),
    }


def _process_list_item(
    proc: ProcSample,
    gpu_process: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact row data; full details are available from /api/process/<pid>."""
    data = _proc_to_dict(proc, full_cmdline=False, gpu_process=gpu_process)
    data["cmdline"] = _shorten(data["cmdline"], limit=160)
    return {
        key: data[key]
        for key in (
            "pid",
            "ppid",
            "name",
            "label",
            "cmdline",
            "age_seconds",
            "age_label",
            "cpu_percent",
            "cpu_capacity_percent",
            "memory_bytes",
            "memory_percent",
            "threads",
            "username",
            "status",
            "read_bytes_per_s",
            "write_bytes_per_s",
            "gpu_percent",
            "gpu_memory_bytes",
            "kind",
        )
    }


def _process_tree_metadata(procs: list[ProcSample]) -> dict[str, Any]:
    by_pid = {proc.pid: proc for proc in procs}
    roots = sorted(
        proc.pid
        for proc in procs
        if proc.ppid not in by_pid or proc.ppid == proc.pid
    )
    depth_cache: dict[int, int] = {}

    def depth(pid: int, trail: set[int] | None = None) -> int:
        if pid in depth_cache:
            return depth_cache[pid]
        trail = set() if trail is None else trail
        if pid in trail:
            return 1
        proc = by_pid.get(pid)
        if proc is None or proc.ppid not in by_pid or proc.ppid == pid:
            result = 1
        else:
            result = 1 + depth(proc.ppid, {*trail, pid})
        depth_cache[pid] = result
        return result

    return {
        "root_pids": roots,
        "root_count": len(roots),
        "edge_count": sum(proc.ppid in by_pid and proc.ppid != proc.pid for proc in procs),
        "max_depth": max((depth(proc.pid) for proc in procs), default=0),
    }


def _project_to_dict(
    project: ProjectSummary,
    disk_by_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    disk = (disk_by_path or {}).get(project.path or "") or {}
    return {
        "name": project.name,
        "path": project.path,
        "cpu_percent": project.cpu,
        "cpu_capacity_percent": _cpu_capacity(project.cpu),
        "memory_bytes": project.rss,
        "process_count": project.proc_count,
        "latest_create_time": project.latest_create_time,
        "disk_usage_bytes": disk.get("allocated_bytes"),
        "disk_usage_status": disk.get("status", "unavailable"),
        "disk_usage_sampled_at": disk.get("sampled_at"),
    }


def _session_status(session: Session) -> str:
    if session.total_cpu >= 70:
        return "HOT"
    if session.total_cpu >= 10:
        return "LIVE"
    if session.total_cpu >= 1:
        return "WARM"
    return "IDLE"


def _session_to_dict(
    session: Session,
    gpu_by_pid: dict[int, dict[str, Any]] | None = None,
    disk_by_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gpu_by_pid = gpu_by_pid or {}
    label, color = KIND_META.get(session.kind, (session.kind, "#A1ACB8"))
    active = session.total_cpu >= IDLE_CPU_THRESHOLD
    project_paths = {project.path for project in session.project_stats if project.path}
    disk_values = [
        (disk_by_path or {}).get(path or "") or {}
        for path in project_paths
    ]
    disk_usage_values = [
        int(item["allocated_bytes"])
        for item in disk_values
        if item.get("allocated_bytes") is not None
    ]
    disk_pending = sum(
        item.get("status") in {"pending", "measuring"} for item in disk_values
    )
    disk_unavailable = sum(item.get("status") == "unavailable" for item in disk_values)
    return {
        "id": session.session_id,
        "kind": session.kind,
        "kind_label": label,
        "color": color,
        "zone": session.zone,
        "project": session.project,
        "projects": list(session.projects),
        "project_stats": [
            _project_to_dict(project, disk_by_path=disk_by_path)
            for project in session.project_stats
        ],
        "root": _proc_to_dict(session.root, gpu_process=gpu_by_pid.get(session.root.pid)),
        "children": [
            _proc_to_dict(
                proc,
                full_cmdline=False,
                gpu_process=gpu_by_pid.get(proc.pid),
            )
            for proc in sorted(session.descendants, key=lambda p: -p.create_time)
        ],
        "cpu_percent": session.total_cpu,
        "cpu_capacity_percent": _cpu_capacity(session.total_cpu),
        "memory_bytes": session.total_rss,
        "disk_usage_bytes": sum(disk_usage_values) if disk_usage_values else None,
        "disk_usage_status": (
            "partial"
            if disk_usage_values and (disk_pending or disk_unavailable)
            else "available"
            if disk_usage_values and len(disk_usage_values) == len(project_paths)
            else "measuring"
            if disk_pending
            else "unavailable"
        ),
        "disk_usage_available_count": len(disk_usage_values),
        "disk_usage_project_count": len(project_paths),
        "process_count": session.proc_count,
        "uptime_seconds": session.uptime,
        "uptime_label": fmt_duration(session.uptime),
        "status": _session_status(session),
        "active": active,
    }


def _zone_to_dict(
    zone_id: str,
    sessions: list[Session],
    history: History,
    gpu_by_pid: dict[int, dict[str, Any]] | None = None,
    disk_by_path: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    title, color, marker = ZONE_META[zone_id]
    zone_sessions = [session for session in sessions if session.zone == zone_id]
    active_sessions = [session for session in zone_sessions if session.total_cpu >= IDLE_CPU_THRESHOLD]
    cpu_total = sum(session.total_cpu for session in active_sessions)
    memory_total = sum(session.total_rss for session in active_sessions)
    process_total = sum(session.proc_count for session in active_sessions)
    project_map: dict[str, ProjectSummary] = {}
    for session in active_sessions:
        for project in session.project_stats:
            project_key = project.path or project.name
            existing = project_map.setdefault(
                project_key,
                ProjectSummary(name=project.name, path=project.path),
            )
            existing.cpu += project.cpu
            existing.rss += project.rss
            existing.proc_count += project.proc_count
            existing.latest_create_time = max(existing.latest_create_time, project.latest_create_time)
            if existing.path is None and project.path:
                existing.path = project.path
    projects = sorted(project_map.values(), key=lambda p: (-p.latest_create_time, p.name))
    project_paths = {project.path for project in projects if project.path}
    disk_values = [
        (disk_by_path or {}).get(path or "") or {}
        for path in project_paths
    ]
    disk_usage_values = [
        int(item["allocated_bytes"])
        for item in disk_values
        if item.get("allocated_bytes") is not None
    ]
    disk_pending = sum(
        item.get("status") in {"pending", "measuring"} for item in disk_values
    )
    disk_unavailable = sum(item.get("status") == "unavailable" for item in disk_values)
    zone_history = list(history.zone_cpu.get(zone_id, []))
    return {
        "id": zone_id,
        "title": title,
        "color": color,
        "marker": marker,
        "session_count": len(active_sessions),
        "total_session_count": len(zone_sessions),
        "process_count": process_total,
        "cpu_percent": cpu_total,
        "cpu_capacity_percent": _cpu_capacity(cpu_total),
        "memory_bytes": memory_total,
        "projects": [
            _project_to_dict(project, disk_by_path=disk_by_path)
            for project in projects
        ],
        "disk_usage_bytes": sum(disk_usage_values) if disk_usage_values else None,
        "disk_usage_status": (
            "partial"
            if disk_usage_values and (disk_pending or disk_unavailable)
            else "available"
            if disk_usage_values and len(disk_usage_values) == len(project_paths)
            else "measuring"
            if disk_pending
            else "unavailable"
        ),
        "disk_usage_available_count": len(disk_usage_values),
        "disk_usage_project_count": len(project_paths),
        "disk_usage_pending_count": disk_pending,
        "history": zone_history,
        "sparkline": sparkline(zone_history, scale_max=max([50.0, *zone_history])) if zone_history else "",
        "sessions": [
            _session_to_dict(
                session,
                gpu_by_pid=gpu_by_pid,
                disk_by_path=disk_by_path,
            )
            for session in zone_sessions
        ],
    }


def _extension_to_dict(ext: ExtensionInfo) -> dict[str, Any]:
    return {
        "id": ext.ext_id,
        "short_id": ext.short_id,
        "display_name": ext.display_name,
        "family": ext.family,
        "color": ext.color,
        "version": ext.version,
        "host": ext.host,
        "path": str(ext.path),
    }


def _resource_item(
    proc: ProcSample,
    gpu_by_pid: dict[int, dict[str, Any]] | None = None,
    system_memory_total: int = 0,
) -> dict[str, Any]:
    gpu_by_pid = gpu_by_pid or {}
    data = _proc_to_dict(
        proc,
        full_cmdline=False,
        gpu_process=gpu_by_pid.get(proc.pid),
    )
    data["memory_percent_of_system"] = 0.0
    if system_memory_total:
        data["memory_percent_of_system"] = proc.rss / system_memory_total * 100.0
    return data


def _resource_group_to_dict(
    group,
    gpu_by_pid: dict[int, dict[str, Any]] | None = None,
    include_members: bool = True,
    system_memory_total: int = 0,
) -> dict[str, Any]:
    gpu_by_pid = gpu_by_pid or {}
    members = sorted(group.procs, key=lambda p: (-p.rss, -p.cpu_percent, p.pid))
    gpu_memory = sum(
        (gpu_by_pid.get(proc.pid) or {}).get("gpu_memory_bytes", 0)
        for proc in group.procs
    )
    gpu_percent_values = [
        (gpu_by_pid.get(proc.pid) or {}).get("gpu_percent")
        for proc in group.procs
        if (gpu_by_pid.get(proc.pid) or {}).get("gpu_percent") is not None
    ]
    data = {
        "key": group.key,
        "label": group.label,
        "detail": resource_group_detail(group),
        "primary_pid": group.primary_pid,
        "process_count": group.proc_count,
        "pids": [proc.pid for proc in members],
        "cpu_percent": group.cpu_percent,
        "cpu_capacity_percent": group.cpu_percent / LOGICAL_CPU_COUNT,
        "memory_bytes": group.rss,
        "read_bytes_per_s": sum(proc.read_bytes_per_s for proc in group.procs),
        "write_bytes_per_s": sum(proc.write_bytes_per_s for proc in group.procs),
        "gpu_memory_bytes": gpu_memory,
        "gpu_percent": sum(gpu_percent_values) if gpu_percent_values else None,
        "usernames": sorted({proc.username for proc in group.procs if proc.username}),
        "members": (
            [
                _resource_item(
                    proc,
                    gpu_by_pid=gpu_by_pid,
                    system_memory_total=system_memory_total,
                )
                for proc in members[:4]
            ]
            if include_members
            else []
        ),
    }
    data["memory_percent_of_system"] = (
        group.rss / system_memory_total * 100.0 if system_memory_total else 0.0
    )
    return data


def build_snapshot(
    sessions: list[Session],
    procs: dict[int, ProcSample],
    history: History,
    extensions: list[ExtensionInfo],
    refresh_s: float,
    system_metrics: dict[str, Any] | None = None,
    observability: dict[str, Any] | None = None,
    workload_storage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a browser-friendly snapshot from monitor samples."""
    now = time.time()
    active_sessions = [session for session in sessions if session.total_cpu >= IDLE_CPU_THRESHOLD]
    total_cpu = sum(session.total_cpu for session in active_sessions)
    total_mem = sum(session.total_rss for session in active_sessions)
    process_count = sum(session.proc_count for session in active_sessions)
    vm = psutil.virtual_memory()
    mem_used = max(0, vm.total - vm.available)
    all_procs = list(procs.values())
    resource_groups = build_resource_groups(all_procs)
    gpu = (system_metrics or {}).get("gpu") or {}
    gpu_by_pid = {
        int(item["pid"]): item
        for item in gpu.get("processes", [])
        if item.get("pid") is not None
    }
    disk_by_path = {
        str(item["path"]): item
        for item in (workload_storage or {}).get("items", [])
        if item.get("path")
    }
    program_items = [
        _resource_group_to_dict(
            group,
            gpu_by_pid=gpu_by_pid,
            include_members=False,
            system_memory_total=vm.total,
        )
        for group in resource_groups
    ]
    top_memory = sorted(resource_groups, key=lambda g: (-g.rss, -g.cpu_percent, g.label))[:5]
    top_cpu = sorted(
        resource_groups,
        key=lambda g: (-(g.cpu_percent / LOGICAL_CPU_COUNT), -g.rss, g.label),
    )[:5]
    top_disk = sorted(
        program_items,
        key=lambda item: (
            -(item["read_bytes_per_s"] + item["write_bytes_per_s"]),
            -item["memory_bytes"],
            item["label"],
        ),
    )[:5]
    top_gpu = sorted(
        [
            item
            for item in program_items
            if item["gpu_memory_bytes"] or item["gpu_percent"] is not None
        ],
        key=lambda item: (
            -(item["gpu_percent"] or 0),
            -item["gpu_memory_bytes"],
            item["label"],
        ),
    )[:5]

    if system_metrics is None:
        disk = _disk_usage()
        system_metrics = {
            "logical_cpus": psutil.cpu_count() or 1,
            "physical_cpus": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "cpu": {"percent": psutil.cpu_percent(interval=None), "per_core_percent": []},
            "memory": {
                "total_bytes": vm.total,
                "available_bytes": vm.available,
                "used_bytes": mem_used,
                "percent": (mem_used / vm.total * 100.0) if vm.total else 0.0,
            },
            "swap": {},
            "gpu": gpu,
            "disks": [disk] if disk else [],
            "disk_io": {
                "read_bytes_per_s": 0.0,
                "write_bytes_per_s": 0.0,
                "read_iops": 0.0,
                "write_iops": 0.0,
                "read_latency_ms": 0.0,
                "write_latency_ms": 0.0,
                "devices": [],
            },
            "storage_health": {
                "available": False,
                "provider": "none",
                "devices": [],
            },
            "network": {
                "download_bytes_per_s": history.net_recv_per_s,
                "upload_bytes_per_s": history.net_sent_per_s,
            },
            "process_summary": {"total": len(all_procs)},
            "history": {},
            "sensors": [],
            "battery": None,
        }

    disks = system_metrics.get("disks") or []
    system_disk = next(
        (disk for disk in disks if disk.get("is_system")),
        disks[0] if disks else None,
    )
    cpu = system_metrics.get("cpu") or {}

    total_history = list(history.total_cpu)
    return {
        "schema_version": 3,
        "generated_at": now,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "refresh_interval": refresh_s,
        "idle_cpu_threshold": IDLE_CPU_THRESHOLD,
        "system": {
            "user": getpass.getuser(),
            "hostname": socket.gethostname().split(".")[0],
            "platform": _system_name(),
            **system_metrics,
            # Compatibility aliases for older web clients.
            "cpu_percent": cpu.get("percent", 0.0),
            "disk": system_disk,
        },
        "ai": {
            "active_session_count": len(active_sessions),
            "total_session_count": len(sessions),
            "process_count": process_count,
            "cpu_percent": total_cpu,
            "cpu_capacity_percent": _cpu_capacity(total_cpu),
            "memory_bytes": total_mem,
            "memory_percent": (total_mem / vm.total * 100.0) if vm.total else 0.0,
            "history": total_history,
            "memory_history_mb": list(history.total_mem),
            "sparkline": sparkline(total_history, scale_max=max([100.0, *total_history])) if total_history else "",
            "workload_storage": workload_storage or {
                "provider": "none",
                "cache_seconds": 0,
                "items": [],
                "pending_count": 0,
            },
        },
        "zones": [
            _zone_to_dict(
                ZONE_CLAUDE,
                sessions,
                history,
                gpu_by_pid=gpu_by_pid,
                disk_by_path=disk_by_path,
            ),
            _zone_to_dict(
                ZONE_CODEX,
                sessions,
                history,
                gpu_by_pid=gpu_by_pid,
                disk_by_path=disk_by_path,
            ),
            _zone_to_dict(
                ZONE_CURSOR,
                sessions,
                history,
                gpu_by_pid=gpu_by_pid,
                disk_by_path=disk_by_path,
            ),
        ],
        "sessions": [
            _session_to_dict(
                session,
                gpu_by_pid=gpu_by_pid,
                disk_by_path=disk_by_path,
            )
            for session in sessions
        ],
        "processes": {
            "scope": "system",
            "tree": _process_tree_metadata(all_procs),
            "items": [
                _process_list_item(
                    proc,
                    gpu_process=gpu_by_pid.get(proc.pid),
                )
                for proc in sorted(
                    all_procs,
                    key=lambda proc: (-_cpu_capacity(proc.cpu_percent), -proc.rss, proc.pid),
                )
            ],
        },
        "resources": {
            "mode": "groups",
            "programs": sorted(
                program_items,
                key=lambda item: (-item["cpu_capacity_percent"], -item["memory_bytes"], item["label"]),
            ),
            "top_memory": [
                _resource_group_to_dict(
                    group,
                    gpu_by_pid=gpu_by_pid,
                    system_memory_total=vm.total,
                )
                for group in top_memory
            ],
            "top_cpu": [
                _resource_group_to_dict(
                    group,
                    gpu_by_pid=gpu_by_pid,
                    system_memory_total=vm.total,
                )
                for group in top_cpu
            ],
            "top_disk": top_disk,
            "top_gpu": top_gpu,
        },
        "observability": observability or {
            "summary": {"active": 0, "critical": 0, "warning": 0},
            "thresholds": {},
            "active": [],
            "events": [],
        },
        "extensions": [_extension_to_dict(ext) for ext in extensions],
    }
