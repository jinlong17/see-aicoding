"""Command-line entry point for see-aicoding."""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time

from . import __version__


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="see-aicoding",
        description="Local system resource monitor with focused Claude, ChatGPT, and Cursor workload context.",
    )
    p.add_argument("-i", "--interval", type=float, default=1.5,
                   help="Refresh interval in seconds (default 1.5).")
    p.add_argument("--no-tree", action="store_true",
                   help="Hide descendant process tree (one row per session).")
    p.add_argument("--hide-idle", action="store_true",
                   help="Hide sessions below 0.5%% CPU (default; kept for compatibility).")
    p.add_argument("-a", "--all", action="store_true",
                   help="Show all sessions, including idle sessions.")
    p.add_argument("--once", action="store_true",
                   help="Print a single snapshot and exit (no live loop).")
    p.add_argument("--full-screen", action="store_true",
                   help="Use alternate-screen mode (clears terminal on exit, no scrollback).")
    p.add_argument("--web", action="store_true",
                   help="Run the compact local system dashboard instead of the terminal AI view.")
    p.add_argument("--open", action="store_true",
                   help="Open the web monitor in the default browser (only with --web).")
    p.add_argument("--host", default="127.0.0.1",
                   help="Web monitor host (default 127.0.0.1; localhost only).")
    p.add_argument("--port", type=int, default=8765,
                   help="Web monitor port (default 8765).")
    p.add_argument(
        "--capture-claude-usage",
        action="store_true",
        help=(
            "Read one Claude Code status-line JSON payload from stdin, persist only "
            "its quota fields locally, print a compact status line, and exit."
        ),
    )
    p.add_argument("--version", action="version", version=f"see-aicoding {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.capture_claude_usage:
        from .usage import MAX_STATUSLINE_BYTES, capture_claude_statusline

        raw = sys.stdin.buffer.read(MAX_STATUSLINE_BYTES + 1)
        if len(raw) > MAX_STATUSLINE_BYTES:
            print("Claude status-line payload exceeds the 1 MiB safety limit.", file=sys.stderr)
            return 2
        try:
            payload = json.loads(raw.decode("utf-8"))
            print(capture_claude_statusline(payload))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            print(f"Unable to capture Claude quota data: {exc}", file=sys.stderr)
            return 2
        return 0

    import psutil
    from rich.console import Console
    from rich.live import Live

    from .cursor_ext import scan_installed_extensions
    from .monitor import History, Sampler, build_sessions
    from .render import render_all

    refresh = max(0.5, args.interval)
    show_tree = not args.no_tree
    hide_idle = not args.all

    if args.web:
        from .web import run_web_server

        return run_web_server(args.host, args.port, refresh, args.open)

    console = Console()
    sampler = Sampler()
    history = History()

    # Scan extensions once at startup (filesystem doesn't change mid-session usually).
    extensions = scan_installed_extensions()

    # Warm-up: prime cpu_percent.
    sampler.snapshot()
    psutil.cpu_percent(interval=None)
    time.sleep(min(0.5, refresh))

    if args.once:
        procs = sampler.snapshot()
        sessions = build_sessions(procs)
        history.record(sessions)
        console.print(render_all(sessions, procs, history, extensions, refresh, show_tree, hide_idle))
        return 0

    stop = False

    def _sig(_n, _f):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    with Live(
        render_all([], {}, history, extensions, refresh, show_tree, hide_idle),
        console=console,
        refresh_per_second=max(1, int(1 / refresh)),
        screen=args.full_screen,
        transient=False,
    ) as live:
        while not stop:
            t0 = time.time()
            procs = sampler.snapshot()
            sessions = build_sessions(procs)
            history.record(sessions)
            live.update(render_all(sessions, procs, history, extensions, refresh, show_tree, hide_idle))
            elapsed = time.time() - t0
            time.sleep(max(0.05, refresh - elapsed))

    console.print("[#667382]bye.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
