"""Command-line entry point for see-aicoding."""
from __future__ import annotations

import argparse
import signal
import sys
import time

import psutil
from rich.console import Console
from rich.live import Live

from . import __version__
from .cursor_ext import scan_installed_extensions
from .monitor import History, Sampler, build_sessions
from .render import render_all


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="see-aicoding",
        description="Live system-wide monitor for Claude Code, Codex, and Cursor AI coding processes.",
    )
    p.add_argument("-i", "--interval", type=float, default=1.5,
                   help="Refresh interval in seconds (default 1.5).")
    p.add_argument("--no-tree", action="store_true",
                   help="Hide descendant process tree (one row per session).")
    p.add_argument("-a", "--all", action="store_true",
                   help="Show idle sessions (default hides sessions below 0.5%% CPU).")
    p.add_argument("--once", action="store_true",
                   help="Print a single snapshot and exit (no live loop).")
    p.add_argument("--full-screen", action="store_true",
                   help="Use alternate-screen mode (clears terminal on exit, no scrollback).")
    p.add_argument("--version", action="version", version=f"see-aicoding {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    refresh = max(0.5, args.interval)
    show_tree = not args.no_tree
    hide_idle = not args.all
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
        console.print(render_all(sessions, history, extensions, refresh, show_tree, hide_idle))
        return 0

    stop = False

    def _sig(_n, _f):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    with Live(
        render_all([], history, extensions, refresh, show_tree, hide_idle),
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
            live.update(render_all(sessions, history, extensions, refresh, show_tree, hide_idle))
            elapsed = time.time() - t0
            time.sleep(max(0.05, refresh - elapsed))

    console.print("[grey50]bye.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
