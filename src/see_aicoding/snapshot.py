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
    count_remote_conns,
    fmt_duration,
    sparkline,
)
from .render import build_resource_groups, group_cpu_capacity, resource_group_detail


IDLE_CPU_THRESHOLD = 0.5


def _cpu_capacity(cpu_percent: float) -> float:
    logical_cpus = psutil.cpu_count() or 1
    return cpu_percent / logical_cpus


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


def _proc_to_dict(proc: ProcSample, full_cmdline: bool = True) -> dict[str, Any]:
    cmdline = proc.cmdline_str if full_cmdline else _shorten(proc.cmdline_str)
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
        "memory_bytes": proc.rss,
        "threads": proc.num_threads,
        "kind": proc.kind,
        "missing_cwd": proc.cwd is None,
        "missing_exe": not bool(proc.exe),
    }


def _project_to_dict(project: ProjectSummary) -> dict[str, Any]:
    return {
        "name": project.name,
        "cpu_percent": project.cpu,
        "cpu_capacity_percent": _cpu_capacity(project.cpu),
        "memory_bytes": project.rss,
        "process_count": project.proc_count,
        "latest_create_time": project.latest_create_time,
    }


def _session_status(session: Session) -> str:
    if session.total_cpu >= 70:
        return "HOT"
    if session.total_cpu >= 10 or count_remote_conns(session.root.pid) > 0:
        return "LIVE"
    if session.total_cpu >= 1:
        return "WARM"
    return "IDLE"


def _session_to_dict(session: Session) -> dict[str, Any]:
    label, color = KIND_META.get(session.kind, (session.kind, "#A1ACB8"))
    active = session.total_cpu >= IDLE_CPU_THRESHOLD
    return {
        "id": session.session_id,
        "kind": session.kind,
        "kind_label": label,
        "color": color,
        "zone": session.zone,
        "project": session.project,
        "projects": list(session.projects),
        "project_stats": [_project_to_dict(project) for project in session.project_stats],
        "root": _proc_to_dict(session.root),
        "children": [
            _proc_to_dict(proc, full_cmdline=False)
            for proc in sorted(session.descendants, key=lambda p: -p.create_time)
        ],
        "cpu_percent": session.total_cpu,
        "cpu_capacity_percent": _cpu_capacity(session.total_cpu),
        "memory_bytes": session.total_rss,
        "process_count": session.proc_count,
        "uptime_seconds": session.uptime,
        "uptime_label": fmt_duration(session.uptime),
        "status": _session_status(session),
        "active": active,
    }


def _zone_to_dict(zone_id: str, sessions: list[Session], history: History) -> dict[str, Any]:
    title, color, marker = ZONE_META[zone_id]
    zone_sessions = [session for session in sessions if session.zone == zone_id]
    active_sessions = [session for session in zone_sessions if session.total_cpu >= IDLE_CPU_THRESHOLD]
    cpu_total = sum(session.total_cpu for session in active_sessions)
    memory_total = sum(session.total_rss for session in active_sessions)
    process_total = sum(session.proc_count for session in active_sessions)
    project_map: dict[str, ProjectSummary] = {}
    for session in active_sessions:
        for project in session.project_stats:
            existing = project_map.setdefault(project.name, ProjectSummary(name=project.name))
            existing.cpu += project.cpu
            existing.rss += project.rss
            existing.proc_count += project.proc_count
            existing.latest_create_time = max(existing.latest_create_time, project.latest_create_time)
    projects = sorted(project_map.values(), key=lambda p: (-p.latest_create_time, p.name))
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
        "projects": [_project_to_dict(project) for project in projects],
        "history": zone_history,
        "sparkline": sparkline(zone_history, scale_max=max([50.0, *zone_history])) if zone_history else "",
        "sessions": [_session_to_dict(session) for session in zone_sessions],
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


def _resource_item(proc: ProcSample) -> dict[str, Any]:
    data = _proc_to_dict(proc, full_cmdline=False)
    data["memory_percent_of_system"] = 0.0
    try:
        total = psutil.virtual_memory().total
    except (OSError, RuntimeError):
        total = 0
    if total:
        data["memory_percent_of_system"] = proc.rss / total * 100.0
    return data


def _resource_group_to_dict(group) -> dict[str, Any]:
    members = sorted(group.procs, key=lambda p: (-p.rss, -p.cpu_percent, p.pid))
    data = {
        "key": group.key,
        "label": group.label,
        "detail": resource_group_detail(group),
        "primary_pid": group.primary_pid,
        "process_count": group.proc_count,
        "pids": [proc.pid for proc in members],
        "cpu_percent": group.cpu_percent,
        "cpu_capacity_percent": group_cpu_capacity(group),
        "memory_bytes": group.rss,
        "members": [_resource_item(proc) for proc in members[:6]],
    }
    try:
        total = psutil.virtual_memory().total
    except (OSError, RuntimeError):
        total = 0
    data["memory_percent_of_system"] = group.rss / total * 100.0 if total else 0.0
    return data


def build_snapshot(
    sessions: list[Session],
    procs: dict[int, ProcSample],
    history: History,
    extensions: list[ExtensionInfo],
    refresh_s: float,
) -> dict[str, Any]:
    """Build a browser-friendly snapshot from monitor samples."""
    now = time.time()
    active_sessions = [session for session in sessions if session.total_cpu >= IDLE_CPU_THRESHOLD]
    total_cpu = sum(session.total_cpu for session in active_sessions)
    total_mem = sum(session.total_rss for session in active_sessions)
    process_count = sum(session.proc_count for session in active_sessions)
    vm = psutil.virtual_memory()
    machine_cpu = psutil.cpu_percent(interval=None)
    mem_used = max(0, vm.total - vm.available)
    all_procs = list(procs.values())
    resource_groups = build_resource_groups(all_procs)
    top_memory = sorted(resource_groups, key=lambda g: (-g.rss, -g.cpu_percent, g.label))[:5]
    top_cpu = sorted(resource_groups, key=lambda g: (-group_cpu_capacity(g), -g.rss, g.label))[:5]

    total_history = list(history.total_cpu)
    return {
        "schema_version": 1,
        "generated_at": now,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
        "refresh_interval": refresh_s,
        "idle_cpu_threshold": IDLE_CPU_THRESHOLD,
        "system": {
            "user": getpass.getuser(),
            "hostname": socket.gethostname().split(".")[0],
            "platform": _system_name(),
            "logical_cpus": psutil.cpu_count() or 1,
            "physical_cpus": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "cpu_percent": machine_cpu,
            "memory": {
                "total_bytes": vm.total,
                "available_bytes": vm.available,
                "used_bytes": mem_used,
                "percent": (mem_used / vm.total * 100.0) if vm.total else 0.0,
            },
            "disk": _disk_usage(),
            "network": {
                "download_bytes_per_s": history.net_recv_per_s,
                "upload_bytes_per_s": history.net_sent_per_s,
            },
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
        },
        "zones": [
            _zone_to_dict(ZONE_CLAUDE, sessions, history),
            _zone_to_dict(ZONE_CODEX, sessions, history),
            _zone_to_dict(ZONE_CURSOR, sessions, history),
        ],
        "sessions": [_session_to_dict(session) for session in sessions],
        "resources": {
            "mode": "groups",
            "top_memory": [_resource_group_to_dict(group) for group in top_memory],
            "top_cpu": [_resource_group_to_dict(group) for group in top_cpu],
        },
        "extensions": [_extension_to_dict(ext) for ext in extensions],
    }
