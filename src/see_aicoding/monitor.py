"""Core process monitoring — classification, sampling, session aggregation.

Pure data + logic. No rendering. No rich imports.
"""
from __future__ import annotations

import functools
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import psutil


# ─── Tool kinds ─────────────────────────────────────────────────────────────

KIND_CLAUDE_CLI = "claude-cli"
KIND_CLAUDE_CURSOR = "claude-cursor"
KIND_CODEX_DESKTOP = "codex-desktop"
KIND_CODEX_CLI = "codex-cli"
KIND_OPENAI_CURSOR = "openai-cursor"
KIND_OTHER_AI_CURSOR = "other-ai-cursor"
KIND_CURSOR_IDE = "cursor-ide"
KIND_MCP = "mcp"
KIND_CHILD = "child"
KIND_OTHER = "other"

ROOT_KINDS = {
    KIND_CLAUDE_CLI,
    KIND_CLAUDE_CURSOR,
    KIND_CODEX_DESKTOP,
    KIND_CODEX_CLI,
    KIND_OPENAI_CURSOR,
    KIND_OTHER_AI_CURSOR,
    KIND_CURSOR_IDE,
}

# Zone assignment for the 3-zone layout: every root kind belongs to exactly one zone.
ZONE_CLAUDE = "claude"
ZONE_CODEX = "codex"
ZONE_CURSOR = "cursor"

ZONE_OF: dict[str, str] = {
    KIND_CLAUDE_CLI: ZONE_CLAUDE,
    KIND_CLAUDE_CURSOR: ZONE_CLAUDE,
    KIND_CODEX_DESKTOP: ZONE_CODEX,
    KIND_CODEX_CLI: ZONE_CODEX,
    KIND_OPENAI_CURSOR: ZONE_CODEX,
    KIND_CURSOR_IDE: ZONE_CURSOR,
    KIND_OTHER_AI_CURSOR: ZONE_CURSOR,
}

# Display label + base color per kind.
KIND_META: dict[str, tuple[str, str]] = {
    KIND_CLAUDE_CLI: ("Claude CLI", "#B48CFF"),
    KIND_CLAUDE_CURSOR: ("Claude in Cursor", "#B48CFF"),
    KIND_CODEX_DESKTOP: ("Codex Desktop", "#30D5A8"),
    KIND_CODEX_CLI: ("Codex CLI", "#30D5A8"),
    KIND_OPENAI_CURSOR: ("OpenAI/Codex in Cursor", "#30D5A8"),
    KIND_OTHER_AI_CURSOR: ("Other AI in Cursor", "#43BFF2"),
    KIND_CURSOR_IDE: ("Cursor IDE", "#43BFF2"),
    KIND_MCP: ("MCP", "#A1ACB8"),
    KIND_CHILD: ("child", "#667382"),
    KIND_OTHER: ("other", "#667382"),
}

# Zone-level metadata for header / panels.
ZONE_META: dict[str, tuple[str, str, str]] = {
    # zone_id: (title, color, emoji)
    ZONE_CLAUDE: ("Claude", "#B48CFF", "◆"),
    ZONE_CODEX: ("Codex / OpenAI", "#30D5A8", "◆"),
    ZONE_CURSOR: ("Cursor IDE", "#43BFF2", "◆"),
}


# ─── Process classification patterns ────────────────────────────────────────

PATTERNS_CLAUDE_CLI = (
    re.compile(r"/\.local/share/claude/versions/"),
    re.compile(r"@anthropic-ai/claude-code"),
)
PATTERNS_CLAUDE_CURSOR = (
    re.compile(r"/(?:\.cursor|\.vscode)/extensions/anthropic\.claude-code-"),
)
PATTERNS_CODEX_DESKTOP = (
    re.compile(r"/Codex\.app/"),
    re.compile(r"/SkyComputerUseClient\.app/"),
)
PATTERNS_CODEX_CLI = (
    re.compile(r"/\.codex/(?!plugins/)"),
    re.compile(r"@openai/codex"),
    re.compile(r"(?:^|/)codex(?:\s|$)"),
)
PATTERNS_OPENAI_CURSOR = (
    re.compile(r"/(?:\.cursor|\.vscode)/extensions/openai\.(?:chatgpt|codex)-"),
)
PATTERNS_OTHER_AI_CURSOR = (
    re.compile(r"/(?:\.cursor|\.vscode)/extensions/github\.copilot-"),
    re.compile(r"/(?:\.cursor|\.vscode)/extensions/saoudrizwan\.claude-dev-"),
    re.compile(r"/(?:\.cursor|\.vscode)/extensions/continue\.continue-"),
    re.compile(r"/(?:\.cursor|\.vscode)/extensions/sourcegraph\.cody-ai-"),
)
PATTERNS_CURSOR_IDE = (
    re.compile(r"/Cursor\.app/"),
)
PATTERNS_MCP = (
    re.compile(r"@modelcontextprotocol/"),
    re.compile(r"mcp[-_]server"),
)


def classify(p: "ProcSample") -> str:
    """Order matters: most specific first."""
    hay = p.cmdline_str or p.exe or p.name
    if "chrome_crashpad_handler" in hay:
        return KIND_CHILD
    for pat in PATTERNS_CLAUDE_CURSOR:
        if pat.search(hay):
            return KIND_CLAUDE_CURSOR
    for pat in PATTERNS_OPENAI_CURSOR:
        if pat.search(hay):
            return KIND_OPENAI_CURSOR
    for pat in PATTERNS_OTHER_AI_CURSOR:
        if pat.search(hay):
            return KIND_OTHER_AI_CURSOR
    for pat in PATTERNS_CLAUDE_CLI:
        if pat.search(hay):
            return KIND_CLAUDE_CLI
    for pat in PATTERNS_CODEX_DESKTOP:
        if pat.search(hay):
            return KIND_CODEX_DESKTOP
    for pat in PATTERNS_CURSOR_IDE:
        if pat.search(hay):
            return KIND_CURSOR_IDE
    for pat in PATTERNS_CODEX_CLI:
        if pat.search(hay):
            return KIND_CODEX_CLI
    for pat in PATTERNS_MCP:
        if pat.search(hay):
            return KIND_MCP
    return KIND_OTHER


# ─── Helpers ────────────────────────────────────────────────────────────────

SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def sparkline(data: Iterable[float], scale_max: float | None = None) -> str:
    vals = list(data)
    if not vals:
        return ""
    mx = scale_max if scale_max is not None else (max(vals) or 1.0)
    if mx <= 0:
        return SPARK_CHARS[0] * len(vals)
    last = len(SPARK_CHARS) - 1
    return "".join(SPARK_CHARS[min(last, int(v / mx * last))] for v in vals)


def fmt_bytes(n: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.0f}{u}" if u in ("B", "KB") else f"{f:.1f}{u}"
        f /= 1024
    return f"{f:.1f}TB"


def fmt_duration(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d{(secs % 86400) // 3600:02d}h"


@functools.lru_cache(maxsize=1024)
def derive_project(cwd: str | None) -> str:
    if not cwd or cwd == "/":
        return "—"
    p = Path(cwd)
    home = Path.home()
    if p == home:
        return "—"
    ignored_prefixes = (
        "/tmp",
        "/private/tmp",
        "/var",
        "/System",
        "/usr",
        "/Applications",
    )
    if any(str(p).startswith(prefix) for prefix in ignored_prefixes):
        return "—"
    ignored_home_dirs = (
        home / ".codex" / "plugins",
        home / ".cursor" / "extensions",
        home / ".vscode" / "extensions",
    )
    if any(str(p).startswith(str(prefix)) for prefix in ignored_home_dirs):
        return "—"
    ignored_names = {
        "screen_recording",
    }
    if p.name in ignored_names:
        return "—"
    markers = ("package.json", ".git", "Cargo.toml", "pyproject.toml", "go.mod")
    cur = p
    for _ in range(8):
        for m in markers:
            if (cur / m).exists():
                return cur.name
        if cur.parent == cur:
            break
        cur = cur.parent
    try:
        rel = p.relative_to(home)
        parts = rel.parts
        if parts:
            return parts[-1] if len(parts) <= 2 else parts[-2]
    except ValueError:
        pass
    return p.name or "—"


GENERIC_PROJECT_LABELS = {
    "—",
    "(claude daemon)",
    "(Codex Desktop app)",
    "(Cursor IDE)",
}

FALLBACK_PROJECT_BY_KIND = {
    KIND_CODEX_DESKTOP: "(Codex Desktop app)",
    KIND_CODEX_CLI: "(Codex CLI)",
    KIND_OPENAI_CURSOR: "(Cursor OpenAI/Codex ext)",
    KIND_OTHER_AI_CURSOR: "(AI extension)",
    KIND_CURSOR_IDE: "(Cursor IDE)",
}


CURSOR_HOST_PROJECT_RE = re.compile(
    r"Cursor Helper \(Plugin\): extension-host \([^)]+\) (?P<project>.+?) \[\d+-\d+\]"
)


def is_meaningful_project(project: str) -> bool:
    return bool(project and project not in GENERIC_PROJECT_LABELS)


def project_from_cmdline(p: "ProcSample") -> str:
    match = CURSOR_HOST_PROJECT_RE.search(p.cmdline_str)
    if not match:
        return "—"
    project = match.group("project").strip()
    return project or "—"


def proc_project(p: "ProcSample") -> str:
    project = derive_project(p.cwd)
    if is_meaningful_project(project):
        return project
    return project_from_cmdline(p)


def session_project(p: "ProcSample") -> str:
    cmd = p.cmdline_str
    if "--bg-pty-host" in cmd or "--bg-spare" in cmd:
        return "(claude daemon)"
    if p.kind == KIND_CODEX_DESKTOP and "/Codex.app/Contents/MacOS/Codex" in p.exe:
        return "(Codex Desktop app)"
    if p.kind == KIND_CURSOR_IDE and "/Cursor.app/Contents/MacOS/Cursor" in p.exe:
        return "(Cursor IDE)"
    return derive_project(p.cwd)


@dataclass
class ProjectSummary:
    name: str
    cpu: float = 0.0
    rss: int = 0
    proc_count: int = 0
    latest_create_time: float = 0.0


def infer_session_project_stats(
    root: "ProcSample",
    descendants: list["ProcSample"],
) -> list[ProjectSummary]:
    root_project = session_project(root)
    fallback_project = root_project if is_meaningful_project(root_project) else ""

    projects: dict[str, ProjectSummary] = {}
    samples = sorted([root, *descendants], key=lambda p: -p.create_time)
    for sample in samples:
        project = root_project if sample.pid == root.pid else proc_project(sample)
        if not is_meaningful_project(project) and fallback_project:
            project = fallback_project
        if not is_meaningful_project(project):
            continue
        summary = projects.setdefault(project, ProjectSummary(name=project))
        summary.cpu += sample.cpu_percent
        summary.rss += sample.rss
        summary.proc_count += 1
        summary.latest_create_time = max(summary.latest_create_time, sample.create_time)
    return sorted(projects.values(), key=lambda p: (-p.latest_create_time, p.name))


def summarize_session_project(root: "ProcSample", projects: list[str]) -> str:
    root_project = session_project(root)
    if not projects:
        return FALLBACK_PROJECT_BY_KIND.get(root.kind, root_project)
    if len(projects) == 1:
        return projects[0]
    return f"{len(projects)} projects"


def short_proc_label(p: "ProcSample", width: int = 22) -> str:
    name = p.name
    cmd = p.cmdline_str
    hay = f"{cmd}  {name}"
    if "SkyComputerUseClient" in hay:
        return "SkyComputerUse"
    if "chrome_crashpad_handler" in hay:
        return "crashpad"
    if "Electron Framework" in hay:
        return "Electron-FW"
    if "Codex Helper" in hay:
        m = re.search(r"Codex Helper(?: \(([^)]+)\))?", hay)
        sub = m.group(1) if m and m.group(1) else "main"
        return f"Codex-Helper:{sub[:8]}"
    if "Cursor Helper" in hay:
        m = re.search(r"Cursor Helper(?: \(([^)]+)\))?", hay)
        sub = m.group(1) if m and m.group(1) else "main"
        return f"Cursor-Helper:{sub[:8]}"
    if "codex app-server" in hay:
        return "codex-app-server"
    if "node_repl" in hay:
        return "node-repl"
    if "--bg-pty-host" in hay:
        return "claude-bg-pty"
    if "--bg-spare" in hay:
        return "claude-bg-spare"
    if name in {"node", "python", "python3"}:
        parts = cmd.split()
        for part in parts[1:6]:
            if "/" in part:
                return f"{name}:{Path(part).name[:12]}"
        return name
    return name[:width]


def count_remote_conns(pid: int) -> int:
    """Established outbound connections — cheap activity proxy."""
    try:
        proc = psutil.Process(pid)
        if hasattr(proc, "net_connections"):
            conns = proc.net_connections(kind="inet")
        else:
            conns = proc.connections(kind="inet")
    except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError, RuntimeError):
        return 0
    n = 0
    for c in conns:
        if c.status == psutil.CONN_ESTABLISHED and c.raddr:
            ip = c.raddr.ip
            if ip.startswith(("127.", "::1", "fe80:")):
                continue
            n += 1
    return n


# ─── Data classes ──────────────────────────────────────────────────────────


@dataclass
class ProcSample:
    pid: int
    ppid: int
    name: str
    exe: str
    cmdline_str: str
    create_time: float
    cwd: str | None
    cpu_percent: float = 0.0
    rss: int = 0
    num_threads: int = 0
    kind: str = KIND_OTHER
    session_id: str | None = None
    is_root: bool = False


@dataclass
class Session:
    session_id: str
    kind: str
    root: ProcSample
    project: str
    projects: list[str] = field(default_factory=list)
    project_stats: list[ProjectSummary] = field(default_factory=list)
    descendants: list[ProcSample] = field(default_factory=list)

    @property
    def total_cpu(self) -> float:
        return self.root.cpu_percent + sum(d.cpu_percent for d in self.descendants)

    @property
    def total_rss(self) -> int:
        return self.root.rss + sum(d.rss for d in self.descendants)

    @property
    def proc_count(self) -> int:
        return 1 + len(self.descendants)

    @property
    def uptime(self) -> float:
        return time.time() - self.root.create_time

    @property
    def zone(self) -> str:
        return ZONE_OF.get(self.kind, ZONE_CURSOR)


# ─── Sampler ───────────────────────────────────────────────────────────────


class Sampler:
    """Holds persistent psutil.Process objects so cpu_percent() yields deltas."""

    def __init__(self):
        self._cache: dict[int, psutil.Process] = {}
        self._create_times: dict[int, float] = {}

    def _get(self, pid: int) -> psutil.Process | None:
        proc = self._cache.get(pid)
        if proc is not None:
            try:
                if proc.create_time() == self._create_times.get(pid):
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            self._cache.pop(pid, None)
            self._create_times.pop(pid, None)
        try:
            proc = psutil.Process(pid)
            self._cache[pid] = proc
            self._create_times[pid] = proc.create_time()
            proc.cpu_percent(interval=None)
            return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def snapshot(self) -> dict[int, ProcSample]:
        out: dict[int, ProcSample] = {}
        alive: set[int] = set()
        username = os.environ.get("USER") or None
        for proc in psutil.process_iter(["pid", "username"]):
            try:
                pid = proc.info["pid"]
                if username is not None and proc.info.get("username") != username:
                    continue
                alive.add(pid)
                p = self._get(pid)
                if p is None:
                    continue
                with p.oneshot():
                    try:
                        cmd_list = p.cmdline()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        cmd_list = []
                    try:
                        exe = p.exe()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        exe = ""
                    try:
                        cwd = p.cwd()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        cwd = None
                    try:
                        mem = p.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        mem = 0
                    try:
                        threads = p.num_threads()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        threads = 0
                    sample = ProcSample(
                        pid=pid,
                        ppid=p.ppid(),
                        name=p.name(),
                        exe=exe or "",
                        cmdline_str=" ".join(cmd_list) if cmd_list else (exe or p.name()),
                        create_time=self._create_times.get(pid, 0.0),
                        cwd=cwd,
                        cpu_percent=p.cpu_percent(interval=None),
                        rss=mem,
                        num_threads=threads,
                    )
                sample.kind = classify(sample)
                out[pid] = sample
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for dead in set(self._cache) - alive:
            self._cache.pop(dead, None)
            self._create_times.pop(dead, None)
        return out


# ─── Session aggregation ───────────────────────────────────────────────────


def _has_same_kind_ancestor(p: ProcSample, procs: dict[int, ProcSample]) -> bool:
    seen = {p.pid}
    cur = procs.get(p.ppid)
    while cur is not None and cur.pid not in seen:
        seen.add(cur.pid)
        if cur.kind == p.kind:
            return True
        cur = procs.get(cur.ppid)
    return False


def find_root(pid: int, procs: dict[int, ProcSample]) -> ProcSample | None:
    seen: set[int] = set()
    cur = procs.get(pid)
    while cur is not None and cur.pid not in seen:
        seen.add(cur.pid)
        if cur.is_root:
            return cur
        cur = procs.get(cur.ppid)
    return None


def build_sessions(procs: dict[int, ProcSample]) -> list[Session]:
    sessions: dict[int, Session] = {}

    for p in procs.values():
        if p.kind not in ROOT_KINDS:
            continue
        if _has_same_kind_ancestor(p, procs):
            continue
        sid = f"{p.kind}:{p.pid}"
        p.session_id = sid
        p.is_root = True
        sessions[p.pid] = Session(
            session_id=sid,
            kind=p.kind,
            root=p,
            project=session_project(p),
        )

    for p in procs.values():
        if p.is_root:
            continue
        root = find_root(p.ppid, procs)
        if root is None:
            continue
        p.session_id = root.session_id
        sessions[root.pid].descendants.append(p)

    for session in sessions.values():
        session.project_stats = infer_session_project_stats(session.root, session.descendants)
        session.projects = [p.name for p in session.project_stats]
        session.project = summarize_session_project(session.root, session.projects)

    # Sort: newest first (shortest uptime on top). Keep order stable as CPU changes.
    return sorted(
        sessions.values(),
        key=lambda s: -s.root.create_time,
    )


# ─── History ───────────────────────────────────────────────────────────────


class History:
    """Per-session ring buffers for sparklines."""

    def __init__(self, maxlen: int = 40):
        self.maxlen = maxlen
        self.cpu: dict[str, deque[float]] = {}
        self.mem: dict[str, deque[float]] = {}
        self.total_cpu: deque[float] = deque(maxlen=maxlen)
        self.total_mem: deque[float] = deque(maxlen=maxlen)
        # Per-zone history for header sparklines.
        self.zone_cpu: dict[str, deque[float]] = {
            ZONE_CLAUDE: deque(maxlen=maxlen),
            ZONE_CODEX: deque(maxlen=maxlen),
            ZONE_CURSOR: deque(maxlen=maxlen),
        }
        self.net_recv_per_s = 0.0
        self.net_sent_per_s = 0.0
        self._last_net_bytes: tuple[int, int] | None = None
        self._last_net_time: float | None = None
        self._prime_network()

    def _prime_network(self):
        try:
            counters = psutil.net_io_counters()
        except (OSError, RuntimeError):
            return
        self._last_net_bytes = (counters.bytes_recv, counters.bytes_sent)
        self._last_net_time = time.time()

    def _record_network(self):
        try:
            counters = psutil.net_io_counters()
        except (OSError, RuntimeError):
            return
        now = time.time()
        current = (counters.bytes_recv, counters.bytes_sent)
        if self._last_net_bytes is not None and self._last_net_time is not None:
            elapsed = max(0.001, now - self._last_net_time)
            recv_delta = max(0, current[0] - self._last_net_bytes[0])
            sent_delta = max(0, current[1] - self._last_net_bytes[1])
            self.net_recv_per_s = recv_delta / elapsed
            self.net_sent_per_s = sent_delta / elapsed
        self._last_net_bytes = current
        self._last_net_time = now

    def record(self, sessions: list[Session]):
        self._record_network()
        live_ids: set[str] = set()
        total_cpu = 0.0
        total_mem = 0
        zone_cpu_now = {z: 0.0 for z in self.zone_cpu}
        for s in sessions:
            live_ids.add(s.session_id)
            buf_c = self.cpu.setdefault(s.session_id, deque(maxlen=self.maxlen))
            buf_m = self.mem.setdefault(s.session_id, deque(maxlen=self.maxlen))
            buf_c.append(s.total_cpu)
            buf_m.append(s.total_rss / (1024 * 1024))
            total_cpu += s.total_cpu
            total_mem += s.total_rss
            zone_cpu_now[s.zone] += s.total_cpu
        self.total_cpu.append(total_cpu)
        self.total_mem.append(total_mem / (1024 * 1024))
        for z, v in zone_cpu_now.items():
            self.zone_cpu[z].append(v)
        for sid in list(self.cpu.keys()):
            if sid not in live_ids:
                self.cpu.pop(sid, None)
                self.mem.pop(sid, None)

    def spark_cpu(self, sid: str) -> str:
        data = self.cpu.get(sid)
        if not data:
            return ""
        return sparkline(data, scale_max=max(100.0, max(data)))
