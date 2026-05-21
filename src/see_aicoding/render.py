"""Rendering — rich.Layout 3-zone view, progress bars, sparklines.

Layout:
    ┌─ AI Coding Monitor (header w/ progress bars) ─┐
    ├─ Claude ─┬─ Codex/OpenAI ─┬─ Cursor IDE ─────┤
    │ sessions │ sessions        │ sessions + exts │
    └──────────┴─────────────────┴─────────────────┘
"""
from __future__ import annotations

import getpass
import platform
import shutil
import socket
import time
from pathlib import Path

import psutil
from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .cursor_ext import ExtensionInfo, group_by_family
from .monitor import (
    KIND_META,
    ProjectSummary,
    ZONE_CLAUDE,
    ZONE_CODEX,
    ZONE_CURSOR,
    ZONE_META,
    History,
    Session,
    count_remote_conns,
    fmt_bytes,
    fmt_duration,
    proc_project,
    short_proc_label,
    sparkline,
)


# ─── Color helpers ─────────────────────────────────────────────────────────

COLOR_HOT = "#FF5C7A"
COLOR_WARN = "#FFD166"
COLOR_OK = "#32D583"
COLOR_COOL = "#43BFF2"
COLOR_ACCENT = "#3A4655"
COLOR_TEXT = "#E8EDF2"
COLOR_MUTED = "#A1ACB8"
COLOR_DIM = "#667382"
COLOR_PANEL = "#2A3442"
COLOR_EMPTY = "#101722"
COLOR_TRACK = "#101722"
COLOR_TREE = "#3A4655"
TABLE_HEADER_STYLE = f"bold {COLOR_TEXT} on #17202B"
PROJECT_PALETTE = (
    "#8FB4FF",
    "#9AD8C9",
    "#D6B6FF",
    "#F0C987",
    "#F2A0A8",
    "#A7C7A1",
)
MAX_CHILD_ROWS = 12
MAX_PROJECT_CHILD_ROWS = 5


def cpu_color(pct: float) -> str:
    if pct >= 70:
        return f"bold {COLOR_HOT}"
    if pct >= 30:
        return COLOR_WARN
    if pct >= 5:
        return COLOR_OK
    return COLOR_DIM


def mem_color(rss: int) -> str:
    mb = rss / (1024 * 1024)
    if mb >= 1024:
        return f"bold {COLOR_HOT}"
    if mb >= 512:
        return COLOR_WARN
    if mb >= 128:
        return COLOR_OK
    return COLOR_DIM


def compact_size(n: int) -> str:
    """Short byte label for dense header cells."""
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{round(n / 1024)}K"
    if n < 1024 * 1024 * 1024:
        return f"{round(n / 1024 / 1024)}M"
    gb = n / 1024 / 1024 / 1024
    return f"{gb:.1f}G".replace(".0G", "G")


def compact_rate(bytes_per_s: float) -> str:
    if bytes_per_s < 1024:
        return f"{bytes_per_s:.0f}B/s"
    if bytes_per_s < 1024 * 1024:
        return f"{bytes_per_s / 1024:.0f}K/s"
    if bytes_per_s < 1024 * 1024 * 1024:
        return f"{bytes_per_s / 1024 / 1024:.1f}M/s"
    return f"{bytes_per_s / 1024 / 1024 / 1024:.1f}G/s"


def system_name() -> str:
    mac_version = platform.mac_ver()[0]
    if mac_version:
        return f"macOS {mac_version}"
    name = platform.system() or "OS"
    release = platform.release()
    return f"{name} {release}".strip()


def local_disk_usage():
    try:
        return psutil.disk_usage(str(Path.home()))
    except (OSError, RuntimeError):
        return None


def project_color(project: str) -> str:
    total = sum((i + 1) * ord(ch) for i, ch in enumerate(project))
    return PROJECT_PALETTE[total % len(PROJECT_PALETTE)]


def aggregate_project_stats(sessions: list[Session]) -> list[ProjectSummary]:
    grouped: dict[str, ProjectSummary] = {}
    for session in sessions:
        for project in session.project_stats:
            summary = grouped.setdefault(project.name, ProjectSummary(name=project.name))
            summary.cpu += project.cpu
            summary.rss += project.rss
            summary.proc_count += project.proc_count
            summary.latest_create_time = max(summary.latest_create_time, project.latest_create_time)
    return sorted(grouped.values(), key=lambda p: (-p.latest_create_time, p.name))


def project_name_text(project: str, prefix: str = "◆ ", name_style: str = COLOR_TEXT) -> Text:
    txt = Text(prefix, style=project_color(project))
    txt.append(project, style=name_style)
    return txt


def project_metric_text(project: ProjectSummary) -> Text:
    txt = Text()
    txt.append(f"{project.proc_count}p", style=COLOR_MUTED)
    txt.append(" ")
    cpu_label = f"{project.cpu:.0f}%" if project.cpu >= 10 else f"{project.cpu:.1f}%"
    txt.append(cpu_label, style=cpu_color(project.cpu))
    txt.append(" ")
    txt.append(compact_size(project.rss), style=mem_color(project.rss))
    return txt


def grouped_project_children(session: Session) -> tuple[dict[str, list], list]:
    by_project: dict[str, list] = {project.name: [] for project in session.project_stats}
    fallback_project = session.project_stats[0].name if len(session.project_stats) == 1 else ""
    helpers: list = []
    for child in sorted(session.descendants, key=lambda d: -d.create_time):
        project = proc_project(child)
        if project in by_project:
            by_project[project].append(child)
        elif fallback_project:
            by_project[fallback_project].append(child)
        else:
            helpers.append(child)
    return by_project, helpers


# ─── Progress bar (inline, monochrome → 3-color gradient) ──────────────────

BAR_FILLED = "▰"
BAR_EMPTY = "▱"


def progress_bar(
    value: float,
    total: float,
    width: int = 20,
    color: str | None = None,
    filled_char: str = BAR_FILLED,
    empty_char: str = BAR_EMPTY,
    empty_style: str = COLOR_EMPTY,
    min_filled: int = 0,
) -> Text:
    """ASCII progress bar: ▰▰▰▰▱▱▱▱  with optional color override."""
    if total <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, value / total))
    filled = int(ratio * width)
    if ratio > 0 and min_filled > 0:
        filled = min(width, max(min_filled, filled))
    if color is None:
        # Default: quiet-to-hot load ramp by ratio.
        if ratio >= 0.7:
            color = COLOR_HOT
        elif ratio >= 0.4:
            color = COLOR_WARN
        elif ratio >= 0.1:
            color = COLOR_OK
        else:
            color = COLOR_DIM
    txt = Text()
    txt.append(filled_char * filled, style=color)
    txt.append(empty_char * (width - filled), style=empty_style)
    return txt


# ─── Header ────────────────────────────────────────────────────────────────


def render_header(sessions: list[Session], history: History, _refresh_s: float) -> Panel:
    n_cpus = psutil.cpu_count() or 1
    physical_cpus = psutil.cpu_count(logical=False) or n_cpus
    vm = psutil.virtual_memory()
    machine_cpu_pct = psutil.cpu_percent(interval=None)
    disk = local_disk_usage()

    total_cpu = sum(s.total_cpu for s in sessions)
    total_mem = sum(s.total_rss for s in sessions)
    cpu_ratio_of_machine = (total_cpu / n_cpus) / 100.0  # 1.0 = saturating all cores
    mem_ratio = total_mem / vm.total if vm.total else 0
    system_memory_pressure = max(0, vm.total - vm.available)
    system_memory_pct = (system_memory_pressure / vm.total * 100.0) if vm.total else 0.0
    columns = shutil.get_terminal_size((120, 24)).columns
    compact = columns < 170
    bar_width = 12 if compact else 26
    sys_bar_width = 6 if compact else 12

    body = Table.grid(expand=True, padding=(0, 2))
    body.add_column(ratio=9, no_wrap=True)
    body.add_column(ratio=8, no_wrap=True)
    body.add_column(ratio=8, no_wrap=True)

    counts = Text()
    counts.append("AI total ", style=f"bold {COLOR_MUTED}")
    counts.append(f"{len(sessions)}", style=f"bold {COLOR_TEXT}")
    counts.append(" sessions   ", style=COLOR_MUTED)
    counts.append(f"{sum(s.proc_count for s in sessions)}", style=f"bold {COLOR_TEXT}")
    counts.append(" processes", style=COLOR_MUTED)

    system_identity = Text()
    host = socket.gethostname().split(".")[0]
    system_identity.append(f"{getpass.getuser()}@{host}", style=f"bold {COLOR_TEXT}")
    if not compact:
        system_identity.append(f"  {system_name()}", style=COLOR_MUTED)

    ai_processor = Text()
    ai_processor.append("AI processor ", style=f"bold {COLOR_TEXT}")
    ai_processor.append_text(progress_bar(cpu_ratio_of_machine, 1.0, width=bar_width))
    capacity_pct = cpu_ratio_of_machine * 100.0
    ai_processor.append(f" {capacity_pct:.0f}% capacity", style=cpu_color(capacity_pct))
    cpu_share = f" ({total_cpu:.0f}% total)"
    ai_processor.append(cpu_share, style=COLOR_MUTED)

    ai_memory = Text()
    ai_memory.append("AI memory ", style=f"bold {COLOR_TEXT}")
    ai_memory.append_text(progress_bar(mem_ratio, 1.0, width=bar_width))
    ai_memory.append(f" {compact_size(total_mem)}", style=mem_color(total_mem))
    ai_memory_limit = (
        f"/{compact_size(vm.total)}"
        if compact
        else f"  {mem_ratio * 100:4.1f}% of {fmt_bytes(vm.total)}"
    )
    ai_memory.append(ai_memory_limit, style=COLOR_MUTED)

    system_processor = Text()
    system_processor.append("System processor ", style=f"bold {COLOR_MUTED}")
    system_processor.append_text(progress_bar(machine_cpu_pct / 100.0, 1.0, width=sys_bar_width))
    system_processor.append(f" {machine_cpu_pct:4.1f}% ", style=cpu_color(machine_cpu_pct))
    processor_detail = (
        f"{physical_cpus} physical cores"
        if compact
        else f"{physical_cpus} physical / {n_cpus} logical"
    )
    system_processor.append(processor_detail, style=COLOR_MUTED)

    system_memory = Text()
    system_memory.append("System memory ", style=f"bold {COLOR_MUTED}")
    system_memory.append_text(progress_bar(system_memory_pct / 100.0, 1.0, width=sys_bar_width))
    system_memory.append(
        f" {compact_size(system_memory_pressure)}/{compact_size(vm.total)}",
        style=cpu_color(system_memory_pct),
    )
    system_memory.append(f" {system_memory_pct:4.1f}%", style=COLOR_MUTED)

    local_storage = Text()
    local_storage.append("Local storage ", style=f"bold {COLOR_MUTED}")
    if disk is None:
        local_storage.append("unavailable", style=COLOR_DIM)
    else:
        local_storage.append(f"{compact_size(disk.used)}/{compact_size(disk.total)}", style=cpu_color(disk.percent))
        local_storage.append(f" {disk.percent:4.1f}%", style=COLOR_MUTED)

    spark = (
        sparkline(history.total_cpu, scale_max=max(100.0, max(history.total_cpu)))
        if history.total_cpu
        else ""
    )
    trend = Text()
    trend.append("Trend ", style=COLOR_MUTED)
    trend.append(spark or "collecting", style=COLOR_COOL if spark else COLOR_DIM)

    ai_summary = Text()
    ai_summary.append("AI memory total ", style=COLOR_MUTED)
    ai_summary.append(compact_size(total_mem), style=mem_color(total_mem))
    ai_summary.append("   processor share ", style=COLOR_MUTED)
    ai_summary.append(f"{cpu_ratio_of_machine * 100:4.1f}%", style=cpu_color(cpu_ratio_of_machine * 100))

    time_network = Text()
    time_network.append("Time ", style=f"bold {COLOR_MUTED}")
    time_network.append(time.strftime("%H:%M:%S" if compact else "%Y-%m-%d %H:%M:%S"), style=COLOR_TEXT)
    time_network.append("   Network ", style=f"bold {COLOR_MUTED}")
    time_network.append("download ", style=COLOR_COOL)
    time_network.append(compact_rate(history.net_recv_per_s), style=COLOR_TEXT)
    time_network.append("   upload ", style=COLOR_COOL)
    time_network.append(compact_rate(history.net_sent_per_s), style=COLOR_TEXT)

    body.add_row(time_network, counts, system_identity)
    body.add_row(ai_processor, ai_memory, system_processor)
    body.add_row(trend, ai_summary, system_memory)
    body.add_row(Text("", style=COLOR_DIM), Text("", style=COLOR_DIM), local_storage)

    return Panel(
        body,
        title=f"[bold {COLOR_TEXT}] see-aicoding [/]",
        subtitle=f"[{COLOR_MUTED}]AI workload with system context[/]",
        title_align="left",
        subtitle_align="right",
        border_style=COLOR_ACCENT,
        padding=(0, 1),
    )


# ─── Zone panel ────────────────────────────────────────────────────────────


def render_zone(
    zone_id: str,
    sessions: list[Session],
    history: History,
    show_tree: bool,
    hide_idle: bool,
    extensions: list[ExtensionInfo] | None = None,
) -> Panel:
    title_text, color, emoji = ZONE_META[zone_id]
    n_cpus = psutil.cpu_count() or 1

    zone_sessions = [s for s in sessions if s.zone == zone_id]
    zone_cpu = sum(s.total_cpu for s in zone_sessions)
    zone_mem = sum(s.total_rss for s in zone_sessions)
    zone_procs = sum(s.proc_count for s in zone_sessions)
    zone_projects = aggregate_project_stats(zone_sessions)

    # Zone header summary.
    terminal_columns = shutil.get_terminal_size((120, 24)).columns
    zone_bar_width = 22 if terminal_columns < 170 else 30
    zone_capacity_pct = zone_cpu / n_cpus
    header = Table.grid(expand=True, padding=(0, 1))
    header.add_column(ratio=1)
    header.add_column(width=22, no_wrap=True, justify="right")

    bar_text = progress_bar(
        zone_capacity_pct / 100.0,
        1.0,
        width=zone_bar_width,
        color=color,
        filled_char="█",
        empty_char="░",
        empty_style=COLOR_TRACK,
        min_filled=1,
    )

    counts = Text()
    counts.append(f"{len(zone_sessions)}", style=f"bold {color}")
    counts.append(" sessions", style=COLOR_MUTED)

    header.add_row(Text("Processor", style=COLOR_MUTED), counts)
    header.add_row(bar_text, Text(f"{zone_capacity_pct:4.1f}% capacity", style=cpu_color(zone_capacity_pct)))
    header.add_row(
        Text(f"Memory {fmt_bytes(zone_mem)}", style=mem_color(zone_mem)),
        Text(f"{zone_procs} processes", style=COLOR_MUTED),
    )
    if zone_projects:
        header.add_row(Text("Projects", style=COLOR_MUTED), Text("Processes CPU Memory", style=COLOR_DIM))
        for project in zone_projects[:5]:
            header.add_row(project_name_text(project.name, prefix="  ◆ "), project_metric_text(project))
        if len(zone_projects) > 5:
            header.add_row(Text(f"  +{len(zone_projects) - 5} projects", style=COLOR_MUTED), "")
    # Zone trend sparkline.
    if zone_id in history.zone_cpu and history.zone_cpu[zone_id]:
        zone_hist = history.zone_cpu[zone_id]
        spark = sparkline(zone_hist, scale_max=max(50.0, max(zone_hist)))
        header.add_row(Text(spark, style=color), Text("trend", style=COLOR_MUTED))

    # Visible sessions (filter idle).
    visible = zone_sessions if not hide_idle else [s for s in zone_sessions if s.total_cpu >= 0.5]
    hidden = len(zone_sessions) - len(visible)

    # Sessions table.
    tbl = Table(
        show_header=True,
        header_style=TABLE_HEADER_STYLE,
        border_style=COLOR_PANEL,
        expand=True,
        padding=(0, 1),
        pad_edge=False,
    )
    tbl.add_column("Session", overflow="ellipsis", no_wrap=True, ratio=2)
    tbl.add_column("CPU%", width=6, justify="right", no_wrap=True)
    tbl.add_column("Memory", width=8, justify="right", no_wrap=True)
    tbl.add_column("Age/Project", width=11, justify="right", no_wrap=True)
    tbl.add_column("Status", width=8, no_wrap=True)

    if not visible:
        msg = "(no active sessions)" if not zone_sessions else f"({hidden} idle hidden)"
        tbl.add_row(Text(msg, style=COLOR_MUTED), "", "", "", "")

    for s in visible:
        label, sk_color = KIND_META.get(s.kind, (s.kind, COLOR_MUTED))
        # Session label = tool + project.
        sess_label = Text()
        sess_label.append("● ", style=sk_color)
        session_project_style = COLOR_TEXT if s.projects else COLOR_MUTED
        sess_label.append(f"{s.project}", style=session_project_style)
        sess_label.append(f"  · {label}", style=COLOR_MUTED)

        # Status badge.
        conns = count_remote_conns(s.root.pid)
        if s.total_cpu >= 70:
            badge = Text("HOT", style=f"bold {COLOR_HOT}")
        elif s.total_cpu >= 10 or conns > 0:
            badge = Text("LIVE", style=COLOR_OK)
        elif s.total_cpu >= 1:
            badge = Text("WARM", style=COLOR_WARN)
        else:
            badge = Text("IDLE", style=COLOR_MUTED)

        tbl.add_row(
            sess_label,
            Text(f"{s.total_cpu:5.1f}", style=cpu_color(s.total_cpu)),
            Text(fmt_bytes(s.total_rss), style=mem_color(s.total_rss)),
            fmt_duration(s.uptime),
            badge,
        )

        project_children, helper_children = grouped_project_children(s)
        rendered_children = 0

        if s.project_stats:
            for project in s.project_stats:
                tbl.add_row(
                    project_name_text(project.name, prefix="  ◆ ", name_style=COLOR_MUTED),
                    Text(f"{project.cpu:5.1f}", style=cpu_color(project.cpu)),
                    Text(fmt_bytes(project.rss), style=mem_color(project.rss)),
                    Text(f"{project.proc_count}p", style=COLOR_MUTED),
                    "",
                )
                if show_tree:
                    children = project_children.get(project.name, [])
                    shown_children = children[:MAX_PROJECT_CHILD_ROWS]
                    for i, d in enumerate(shown_children):
                        last = i == len(shown_children) - 1 and len(children) <= MAX_PROJECT_CHILD_ROWS
                        prefix = "    └─ " if last else "    ├─ "
                        row_label = Text()
                        row_label.append(prefix, style=COLOR_TREE)
                        row_label.append(short_proc_label(d), style=COLOR_MUTED)
                        tbl.add_row(
                            row_label,
                            Text(f"{d.cpu_percent:5.1f}", style=cpu_color(d.cpu_percent)),
                            Text(fmt_bytes(d.rss), style=mem_color(d.rss)),
                            "",
                            "",
                        )
                    rendered_children += len(shown_children)
                    if len(children) > MAX_PROJECT_CHILD_ROWS:
                        hidden = children[MAX_PROJECT_CHILD_ROWS:]
                        tbl.add_row(
                            Text(f"    └─ +{len(hidden)} more", style=COLOR_DIM),
                            Text(f"{sum(d.cpu_percent for d in hidden):5.1f}", style=COLOR_MUTED),
                            Text(fmt_bytes(sum(d.rss for d in hidden)), style=COLOR_MUTED),
                            "",
                            "",
                        )

        if show_tree and not s.project_stats and s.descendants:
            descendants = sorted(s.descendants, key=lambda d: -d.create_time)
            kids = descendants[:MAX_CHILD_ROWS]
            for i, d in enumerate(kids):
                last = (i == len(kids) - 1) and (len(s.descendants) <= MAX_CHILD_ROWS)
                prefix = "  └─ " if last else "  ├─ "
                row_label = Text()
                row_label.append(prefix, style=COLOR_TREE)
                row_label.append(short_proc_label(d), style=COLOR_MUTED)
                tbl.add_row(
                    row_label,
                    Text(f"{d.cpu_percent:5.1f}", style=cpu_color(d.cpu_percent)),
                    Text(fmt_bytes(d.rss), style=mem_color(d.rss)),
                    "",
                    "",
                )
            if len(s.descendants) > MAX_CHILD_ROWS:
                hidden = descendants[MAX_CHILD_ROWS:]
                tbl.add_row(
                    Text(f"  └─ +{len(hidden)} more", style=COLOR_DIM),
                    Text(f"{sum(d.cpu_percent for d in hidden):5.1f}", style=COLOR_MUTED),
                    Text(fmt_bytes(sum(d.rss for d in hidden)), style=COLOR_MUTED),
                    "",
                    "",
                )

        if show_tree and s.project_stats and helper_children:
            remaining_slots = max(0, MAX_CHILD_ROWS - rendered_children)
            helpers = helper_children[:remaining_slots]
            if helpers:
                tbl.add_row(Text("  helpers", style=COLOR_DIM), "", "", "", "")
            for i, d in enumerate(helpers):
                last = i == len(helpers) - 1 and len(helper_children) <= remaining_slots
                prefix = "    └─ " if last else "    ├─ "
                row_label = Text()
                row_label.append(prefix, style=COLOR_TREE)
                row_label.append(short_proc_label(d), style=COLOR_MUTED)
                tbl.add_row(
                    row_label,
                    Text(f"{d.cpu_percent:5.1f}", style=cpu_color(d.cpu_percent)),
                    Text(fmt_bytes(d.rss), style=mem_color(d.rss)),
                    "",
                    "",
                )
            if len(helper_children) > len(helpers):
                hidden = helper_children[len(helpers):]
                tbl.add_row(
                    Text(f"    └─ +{len(hidden)} helpers", style=COLOR_DIM),
                    Text(f"{sum(d.cpu_percent for d in hidden):5.1f}", style=COLOR_MUTED),
                    Text(fmt_bytes(sum(d.rss for d in hidden)), style=COLOR_MUTED),
                    "",
                    "",
                )
    # If zone is Cursor, append extensions inventory.
    extras: list[RenderableType] = [tbl]
    if zone_id == ZONE_CURSOR and extensions:
        ext_tbl = render_ext_inventory(extensions)
        extras.append(ext_tbl)

    title = Text()
    title.append(f"{emoji}  ", style=color)
    title.append(title_text, style=f"bold {color}")
    return Panel(
        Group(header, *extras),
        title=title,
        title_align="left",
        border_style=color,
        padding=(0, 1),
    )


# ─── Cursor extensions inventory ───────────────────────────────────────────


def render_ext_inventory(extensions: list[ExtensionInfo]) -> Panel:
    grouped = group_by_family(extensions)
    tbl = Table(
        show_header=True,
        header_style=TABLE_HEADER_STYLE,
        border_style=COLOR_PANEL,
        expand=True,
        padding=(0, 1),
        pad_edge=False,
    )
    tbl.add_column("Installed AI extension", overflow="ellipsis", no_wrap=True, ratio=2)
    tbl.add_column("Version", width=14, no_wrap=True)
    tbl.add_column("Host", width=7, no_wrap=True)

    if not extensions:
        tbl.add_row(Text("(no AI extensions detected)", style=COLOR_MUTED), "", "")
    else:
        # Order families: claude, openai, copilot, cline, continue, cody, then anything else.
        fam_order = ["claude", "openai", "copilot", "cline", "continue", "cody", "codeium", "tabnine"]
        seen_fams: set[str] = set()
        for fam in fam_order + sorted(grouped.keys()):
            if fam in seen_fams or fam not in grouped:
                continue
            seen_fams.add(fam)
            for ext in grouped[fam]:
                lbl = Text()
                lbl.append("● ", style=ext.color)
                lbl.append(ext.display_name, style=COLOR_TEXT)
                tbl.add_row(
                    lbl,
                    Text(f"v{ext.version}" if ext.version else "—", style=COLOR_MUTED),
                    Text(ext.host, style=COLOR_MUTED),
                )
    return Panel(
        tbl,
        title=f"[bold {COLOR_MUTED}]Installed AI extensions[/]",
        border_style=COLOR_PANEL,
        padding=(0, 0),
    )


# ─── Footer ─────────────────────────────────────────────────────────────────


def render_footer(refresh_s: float, show_tree: bool, hide_idle: bool, hidden_total: int) -> Text:
    t = Text()
    t.append("  refresh ", style=COLOR_MUTED)
    t.append(f"{refresh_s:.1f}s", style=COLOR_TEXT)
    t.append("   tree ", style=COLOR_MUTED)
    t.append("on" if show_tree else "off", style=COLOR_TEXT)
    t.append("   idle ", style=COLOR_MUTED)
    if hide_idle:
        t.append(f"{hidden_total} hidden", style=COLOR_TEXT)
        t.append(" (use --all to show)", style=COLOR_DIM)
    else:
        t.append("shown", style=COLOR_TEXT)
    t.append("   sort ", style=COLOR_MUTED)
    t.append("newest first", style=COLOR_TEXT)
    t.append("   ", style=COLOR_MUTED)
    t.append("Ctrl-C to quit", style=COLOR_DIM)
    return t


# ─── Full render ───────────────────────────────────────────────────────────


def render_all(
    sessions: list[Session],
    history: History,
    extensions: list[ExtensionInfo],
    refresh_s: float,
    show_tree: bool,
    hide_idle: bool,
) -> RenderableType:
    """Build the full layout: header + 3 zones + footer."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=6),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=1),
    )
    layout["header"].update(render_header(sessions, history, refresh_s))
    body = Layout()
    body.split_row(
        Layout(name="claude", ratio=1),
        Layout(name="codex", ratio=1),
        Layout(name="cursor", ratio=1),
    )
    body["claude"].update(render_zone(ZONE_CLAUDE, sessions, history, show_tree, hide_idle))
    body["codex"].update(render_zone(ZONE_CODEX, sessions, history, show_tree, hide_idle))
    body["cursor"].update(render_zone(ZONE_CURSOR, sessions, history, show_tree, hide_idle, extensions))
    layout["body"].update(body)

    hidden_total = sum(1 for s in sessions if s.total_cpu < 0.5) if hide_idle else 0
    layout["footer"].update(render_footer(refresh_s, show_tree, hide_idle, hidden_total))
    return layout
