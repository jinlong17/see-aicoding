"""Rendering — rich.Layout 3-zone view, progress bars, sparklines.

Layout:
    ┌─ AI Coding Monitor (header w/ progress bars) ─┐
    ├─ Claude ─┬─ Codex/OpenAI ─┬─ Cursor IDE ─────┤
    │ sessions │ sessions        │ sessions + exts │
    └──────────┴─────────────────┴─────────────────┘
"""
from __future__ import annotations

import shutil
import time

import psutil
from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .cursor_ext import ExtensionInfo, group_by_family
from .monitor import (
    KIND_META,
    ZONE_CLAUDE,
    ZONE_CODEX,
    ZONE_CURSOR,
    ZONE_META,
    History,
    Session,
    count_remote_conns,
    fmt_bytes,
    fmt_duration,
    short_proc_label,
    sparkline,
)


# ─── Color helpers ─────────────────────────────────────────────────────────


def cpu_color(pct: float) -> str:
    if pct >= 70:
        return "bold red"
    if pct >= 30:
        return "yellow"
    if pct >= 5:
        return "green"
    return "grey42"


def mem_color(rss: int) -> str:
    mb = rss / (1024 * 1024)
    if mb >= 1024:
        return "bold red"
    if mb >= 512:
        return "yellow"
    if mb >= 128:
        return "green"
    return "grey42"


def compact_size(n: int) -> str:
    """Short byte label for dense header cells."""
    return fmt_bytes(n).replace(".0", "")


# ─── Progress bar (inline, monochrome → 3-color gradient) ──────────────────

BAR_FILLED = "▰"
BAR_EMPTY = "▱"


def progress_bar(value: float, total: float, width: int = 20, color: str | None = None) -> Text:
    """ASCII progress bar: ▰▰▰▰▱▱▱▱  with optional color override."""
    if total <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, value / total))
    filled = int(ratio * width)
    if color is None:
        # Default: green→yellow→red gradient by ratio.
        if ratio >= 0.7:
            color = "red"
        elif ratio >= 0.4:
            color = "yellow"
        elif ratio >= 0.1:
            color = "green"
        else:
            color = "grey42"
    txt = Text()
    txt.append(BAR_FILLED * filled, style=color)
    txt.append(BAR_EMPTY * (width - filled), style="grey23")
    return txt


# ─── Header ────────────────────────────────────────────────────────────────


def render_header(sessions: list[Session], history: History, _refresh_s: float) -> Panel:
    n_cpus = psutil.cpu_count() or 1
    vm = psutil.virtual_memory()
    machine_cpu = psutil.cpu_percent(interval=None)

    total_cpu = sum(s.total_cpu for s in sessions)
    total_mem = sum(s.total_rss for s in sessions)
    cpu_ratio_of_machine = (total_cpu / n_cpus) / 100.0  # 1.0 = saturating all cores
    mem_ratio = total_mem / vm.total if vm.total else 0
    compact = shutil.get_terminal_size((120, 24)).columns < 110
    bar_width = 8 if compact else 14

    body = Table.grid(expand=True, padding=(0, 1))
    body.add_column(ratio=5, no_wrap=True)
    body.add_column(ratio=5, no_wrap=True)
    body.add_column(ratio=4, no_wrap=True)

    cpu = Text()
    cpu.append("AI CPU ", style="bold bright_white")
    cpu.append_text(progress_bar(cpu_ratio_of_machine, 1.0, width=bar_width))
    cpu.append(
        f" {total_cpu:.0f}% " if compact else f" {total_cpu:5.1f}% ",
        style=cpu_color(cpu_ratio_of_machine * 100),
    )
    cpu_share = (
        f"{cpu_ratio_of_machine * 100:.0f}%/{n_cpus}c"
        if compact
        else f"{cpu_ratio_of_machine * 100:4.1f}%/{n_cpus} cores"
    )
    cpu.append(cpu_share, style="grey50")

    mem = Text()
    mem.append("AI MEM ", style="bold bright_white")
    mem.append_text(progress_bar(mem_ratio, 1.0, width=bar_width))
    mem.append(f" {compact_size(total_mem)}", style=mem_color(total_mem))
    mem_limit = (
        f"/{compact_size(vm.total)}"
        if compact
        else f"  {mem_ratio * 100:4.1f}% of {fmt_bytes(vm.total)}"
    )
    mem.append(mem_limit, style="grey50")

    machine = Text()
    machine.append("SYS " if compact else "Machine ", style="bold grey70")
    machine_cpu_label = (
        f"CPU {machine_cpu:.0f}% " if compact else f"CPU {machine_cpu:4.1f}% "
    )
    machine_mem_label = (
        f"MEM {vm.percent:.0f}%" if compact else f"MEM {vm.percent:4.1f}%"
    )
    machine.append(machine_cpu_label, style=cpu_color(machine_cpu))
    machine.append(machine_mem_label, style=cpu_color(vm.percent))

    spark = (
        sparkline(history.total_cpu, scale_max=max(100.0, max(history.total_cpu)))
        if history.total_cpu
        else ""
    )
    trend = Text()
    trend.append("Trend ", style="grey50")
    trend.append(spark or "collecting", style="bright_cyan" if spark else "grey42")

    counts = Text()
    counts.append(f"{len(sessions)}", style="bold white")
    counts.append(" sess   " if compact else " sessions   ", style="grey50")
    counts.append(f"{sum(s.proc_count for s in sessions)}", style="bold white")
    counts.append(" proc" if compact else " processes", style="grey50")

    now = Text(
        time.strftime("%H:%M:%S" if compact else "%Y-%m-%d %H:%M:%S"),
        style="bright_white",
    )

    body.add_row(cpu, mem, machine)
    body.add_row(trend, counts, now)

    return Panel(
        body,
        title="[bold bright_blue] see-aicoding [/]",
        subtitle="[grey50]AI coding process monitor[/]",
        title_align="left",
        subtitle_align="right",
        border_style="bright_blue",
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

    # Zone header summary.
    header = Table.grid(expand=True, padding=(0, 1))
    header.add_column(ratio=1)
    header.add_column(width=12, no_wrap=True, justify="right")

    bar = progress_bar(zone_cpu / n_cpus / 100.0, 1.0, width=18)
    bar_text = Text()
    bar_text.append_text(bar)
    bar_text.append(f"  {zone_cpu:5.1f}%", style=cpu_color(zone_cpu / n_cpus * 100))

    counts = Text()
    counts.append(f"{len(zone_sessions)}", style=f"bold {color}")
    counts.append(" sessions", style="grey50")

    header.add_row(bar_text, counts)
    header.add_row(
        Text(f"MEM {fmt_bytes(zone_mem)}", style=mem_color(zone_mem)),
        Text(f"{zone_procs} procs", style="grey50"),
    )
    # Zone trend sparkline.
    if zone_id in history.zone_cpu and history.zone_cpu[zone_id]:
        zone_hist = history.zone_cpu[zone_id]
        spark = sparkline(zone_hist, scale_max=max(50.0, max(zone_hist)))
        header.add_row(Text(spark, style=color), Text("trend", style="grey50"))

    # Visible sessions (filter idle).
    visible = zone_sessions if not hide_idle else [s for s in zone_sessions if s.total_cpu >= 0.5]
    hidden = len(zone_sessions) - len(visible)

    # Sessions table.
    tbl = Table(
        show_header=True,
        header_style=f"bold white on grey15",
        border_style="grey23",
        expand=True,
        padding=(0, 1),
        pad_edge=False,
    )
    tbl.add_column("Session", overflow="ellipsis", no_wrap=True, ratio=2)
    tbl.add_column("CPU%", width=6, justify="right", no_wrap=True)
    tbl.add_column("Mem", width=7, justify="right", no_wrap=True)
    tbl.add_column("Up", width=6, justify="right", no_wrap=True)
    tbl.add_column("Status", width=8, no_wrap=True)

    if not visible:
        msg = "(no active sessions)" if not zone_sessions else f"({hidden} idle hidden)"
        tbl.add_row(Text(msg, style="grey50"), "", "", "", "")

    for s in visible:
        label, sk_color = KIND_META.get(s.kind, (s.kind, "white"))
        # Session label = tool + project.
        sess_label = Text()
        sess_label.append("● ", style=sk_color)
        sess_label.append(f"{s.project}", style="white")
        sess_label.append(f"  · {label}", style="grey50")

        # Status badge.
        conns = count_remote_conns(s.root.pid)
        if s.total_cpu >= 70:
            badge = Text("🔴 hot", style="bold red")
        elif s.total_cpu >= 10 or conns > 0:
            badge = Text("🟢 live", style="green")
        elif s.total_cpu >= 1:
            badge = Text("🟡 lite", style="yellow")
        else:
            badge = Text("⚪ idle", style="grey50")

        tbl.add_row(
            sess_label,
            Text(f"{s.total_cpu:5.1f}", style=cpu_color(s.total_cpu)),
            Text(fmt_bytes(s.total_rss), style=mem_color(s.total_rss)),
            fmt_duration(s.uptime),
            badge,
        )

        if show_tree and s.descendants:
            descendants = sorted(s.descendants, key=lambda d: -d.create_time)
            kids = descendants[:5]
            for i, d in enumerate(kids):
                last = (i == len(kids) - 1) and (len(s.descendants) <= 5)
                prefix = "  └─ " if last else "  ├─ "
                _, kind_color = KIND_META.get(d.kind, (d.kind, "grey50"))
                row_label = Text()
                row_label.append(prefix, style="grey50")
                row_label.append(short_proc_label(d), style=kind_color)
                tbl.add_row(
                    row_label,
                    Text(f"{d.cpu_percent:5.1f}", style=cpu_color(d.cpu_percent)),
                    Text(fmt_bytes(d.rss), style=mem_color(d.rss)),
                    "",
                    "",
                )
            if len(s.descendants) > 5:
                more = len(s.descendants) - 5
                more_cpu = sum(d.cpu_percent for d in descendants[5:])
                more_mem = sum(d.rss for d in descendants[5:])
                tbl.add_row(
                    Text(f"  └─ +{more} more", style="grey42"),
                    Text(f"{more_cpu:5.1f}", style="grey50"),
                    Text(fmt_bytes(more_mem), style="grey50"),
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
        header_style="bold white on grey15",
        border_style="grey23",
        expand=True,
        padding=(0, 1),
        pad_edge=False,
    )
    tbl.add_column("Installed AI extension", overflow="ellipsis", no_wrap=True, ratio=2)
    tbl.add_column("Version", width=14, no_wrap=True)
    tbl.add_column("Host", width=7, no_wrap=True)

    if not extensions:
        tbl.add_row(Text("(no AI extensions detected)", style="grey50"), "", "")
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
                lbl.append(ext.display_name, style="white")
                tbl.add_row(
                    lbl,
                    Text(f"v{ext.version}" if ext.version else "—", style="grey50"),
                    Text(ext.host, style="grey50"),
                )
    return Panel(tbl, title="[bold]Installed AI extensions[/]", border_style="grey30", padding=(0, 0))


# ─── Footer ─────────────────────────────────────────────────────────────────


def render_footer(refresh_s: float, show_tree: bool, hide_idle: bool, hidden_total: int) -> Text:
    t = Text()
    t.append("  refresh ", style="grey50")
    t.append(f"{refresh_s:.1f}s", style="white")
    t.append("   tree ", style="grey50")
    t.append("on" if show_tree else "off", style="white")
    t.append("   idle ", style="grey50")
    if hide_idle:
        t.append(f"{hidden_total} hidden", style="white")
        t.append(" (use --all to show)", style="grey42")
    else:
        t.append("shown", style="white")
    t.append("   sort ", style="grey50")
    t.append("newest first", style="white")
    t.append("   ", style="grey50")
    t.append("Ctrl-C to quit", style="grey42")
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
        Layout(name="header", size=4),
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
