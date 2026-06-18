"""Local web monitor for see-aicoding."""
from __future__ import annotations

import errno
import json
import mimetypes
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

import psutil

from .cursor_ext import scan_installed_extensions
from .monitor import History, Sampler, build_sessions
from .snapshot import build_snapshot


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC_PACKAGE = "see_aicoding.web_static"


class MonitorState:
    def __init__(self, refresh_s: float):
        self.refresh_s = max(0.5, refresh_s)
        self.sampler = Sampler()
        self.history = History()
        self.extensions = scan_installed_extensions()
        self._lock = threading.Lock()

        self.sampler.snapshot()
        psutil.cpu_percent(interval=None)
        time.sleep(min(0.5, self.refresh_s))

    def snapshot_json(self) -> str:
        with self._lock:
            procs = self.sampler.snapshot()
            sessions = build_sessions(procs)
            self.history.record(sessions)
            snapshot = build_snapshot(sessions, procs, self.history, self.extensions, self.refresh_s)
        return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


class WebMonitorServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address, request_handler, state: MonitorState):
        super().__init__(server_address, request_handler)
        self.state = state

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class WebMonitorHandler(BaseHTTPRequestHandler):
    server_version = "see-aicoding-web/0.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[see-aicoding:web] {self.address_string()} {fmt % args}\n")

    @property
    def monitor_server(self) -> WebMonitorServer:
        return self.server  # type: ignore[return-value]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"", "/"}:
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return
        if parsed.path == "/api/snapshot":
            self._serve_snapshot()
            return
        if parsed.path == "/events":
            query = parse_qs(parsed.query)
            self._serve_events(once=query.get("once") == ["1"])
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _serve_snapshot(self) -> None:
        try:
            body = self.monitor_server.state.snapshot_json().encode("utf-8")
        except Exception as exc:  # pragma: no cover - defensive for local monitor.
            self._serve_json_error(exc)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self, once: bool = False) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close" if once else "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        while True:
            try:
                payload = self.monitor_server.state.snapshot_json()
                self.wfile.write(b"event: snapshot\n")
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:  # pragma: no cover - defensive for local monitor.
                try:
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    self.wfile.write(b"event: error\n")
                    self.wfile.write(f"data: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            if once:
                self.close_connection = True
                return
            time.sleep(self.monitor_server.state.refresh_s)

    def _serve_json_error(self, exc: Exception) -> None:
        body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, name: str, content_type: str | None = None) -> None:
        if "/" in name and name.split("/", 1)[0] in {"..", ""}:
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        if ".." in name.split("/"):
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        try:
            body = resources.files(STATIC_PACKAGE).joinpath(name).read_bytes()
        except (FileNotFoundError, ModuleNotFoundError):
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        guessed_type = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", guessed_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _validate_loopback_host(host: str) -> None:
    allowed = host in {"localhost", "::1"} or host.startswith("127.")
    if not allowed:
        raise ValueError("web monitor only binds to localhost addresses")


def _host_matches_listener(host: str, listener_host: str) -> bool:
    if host == "localhost":
        return listener_host in {"127.0.0.1", "::1", "localhost"}
    return host == listener_host


def _command_for_pid(pid: int) -> str | None:
    try:
        return " ".join(psutil.Process(pid).cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
        return None


def _find_listener_command_with_psutil(host: str, port: int) -> str | None:
    connections = psutil.net_connections(kind="tcp")
    for conn in connections:
        if conn.status != psutil.CONN_LISTEN or not conn.laddr:
            continue
        listener_host = getattr(conn.laddr, "ip", None)
        listener_port = getattr(conn.laddr, "port", None)
        if (
            listener_port != port
            or not listener_host
            or not _host_matches_listener(host, listener_host)
        ):
            continue
        if conn.pid is None:
            return None
        return _command_for_pid(conn.pid)
    return None


def _find_listener_command_with_lsof(port: int) -> str | None:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if not line.startswith("p"):
            continue
        try:
            pid = int(line[1:])
        except ValueError:
            continue
        command = _command_for_pid(pid)
        if command:
            return command
    return None


def _find_loopback_listener_command(host: str, port: int) -> str | None:
    try:
        command = _find_listener_command_with_psutil(host, port)
    except (psutil.AccessDenied, psutil.Error):
        command = None
    return command or _find_listener_command_with_lsof(port)


def _is_existing_see_aicoding_web(command: str | None) -> bool:
    return bool(command and "see-aicoding" in command and "--web" in command)


def run_web_server(host: str, port: int, refresh_s: float, open_browser: bool) -> int:
    _validate_loopback_host(host)
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url = f"http://{url_host}:{port}/"
    state = MonitorState(refresh_s)
    try:
        server = WebMonitorServer((host, port), WebMonitorHandler, state)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE and _is_existing_see_aicoding_web(
            _find_loopback_listener_command(host, port)
        ):
            print(f"see-aicoding web monitor is already running: {url}")
            if open_browser:
                webbrowser.open(url)
            return 0
        raise
    stop = False

    def _sig(_n, _f):
        nonlocal stop
        stop = True
        threading.Thread(target=server.shutdown, daemon=True).start()

    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    old_hup = signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    print(f"see-aicoding web monitor: {url}")
    print("Press Ctrl-C to quit.")
    if open_browser:
        webbrowser.open(url)

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        stop = True
    finally:
        server.server_close()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, old_hup)
    if stop:
        print("bye.")
    return 0
