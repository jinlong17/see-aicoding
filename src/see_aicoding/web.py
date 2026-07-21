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
from .telemetry import SystemTelemetry, inspect_process, manage_process


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC_PACKAGE = "see_aicoding.web_static"


class MonitorState:
    def __init__(self, refresh_s: float):
        self.refresh_s = max(0.5, refresh_s)
        self.sampler = Sampler(include_all_users=True)
        self.history = History()
        self.telemetry = SystemTelemetry()
        self.extensions = scan_installed_extensions()
        self._lock = threading.Lock()
        self._cached_json = ""
        self._cached_at = 0.0

        self.sampler.snapshot()
        time.sleep(min(0.5, self.refresh_s))

    def snapshot_json(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._cached_json and now - self._cached_at < self.refresh_s * 0.8:
                return self._cached_json
            procs = self.sampler.snapshot()
            sessions = build_sessions(procs)
            self.history.record(sessions)
            system_metrics = self.telemetry.sample(
                procs,
                download_bytes_per_s=self.history.net_recv_per_s,
                upload_bytes_per_s=self.history.net_sent_per_s,
            )
            snapshot = build_snapshot(
                sessions,
                procs,
                self.history,
                self.extensions,
                self.refresh_s,
                system_metrics=system_metrics,
            )
            self._cached_json = json.dumps(
                snapshot,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self._cached_at = now
            return self._cached_json

    def process_details(self, pid: int) -> dict:
        return inspect_process(pid)

    def process_action(self, pid: int, action: str) -> dict:
        result = manage_process(pid, action)
        with self._lock:
            self._cached_at = 0.0
        return result


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
    server_version = "see-aicoding-web/0.2"

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
        if parsed.path.startswith("/api/process/"):
            self._serve_process_details(parsed.path)
            return
        if parsed.path == "/events":
            query = parse_qs(parsed.query)
            self._serve_events(once=query.get("once") == ["1"])
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/process/") and parsed.path.endswith("/action"):
            self._serve_process_action(parsed.path)
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

    @staticmethod
    def _pid_from_path(path: str, action: bool = False) -> int | None:
        parts = [part for part in path.split("/") if part]
        expected = ["api", "process", "pid", "action"] if action else ["api", "process", "pid"]
        if len(parts) != len(expected):
            return None
        if parts[:2] != expected[:2] or (action and parts[-1] != "action"):
            return None
        try:
            pid = int(parts[2])
        except ValueError:
            return None
        return pid if pid > 0 else None

    def _serve_process_details(self, path: str) -> None:
        pid = self._pid_from_path(path)
        if pid is None:
            self._serve_json({"error": "Invalid process id."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            details = self.monitor_server.state.process_details(pid)
        except psutil.NoSuchProcess:
            self._serve_json({"error": "Process no longer exists."}, HTTPStatus.NOT_FOUND)
            return
        except (psutil.AccessDenied, PermissionError) as exc:
            self._serve_json({"error": str(exc) or "Process access denied."}, HTTPStatus.FORBIDDEN)
            return
        except Exception as exc:  # pragma: no cover - defensive for platform APIs.
            self._serve_json_error(exc)
            return
        self._serve_json(details)

    def _serve_process_action(self, path: str) -> None:
        pid = self._pid_from_path(path, action=True)
        if pid is None:
            self._serve_json({"error": "Invalid process id."}, HTTPStatus.BAD_REQUEST)
            return
        origin = self.headers.get("Origin")
        if origin:
            origin_host = urlparse(origin).hostname or ""
            if not (origin_host == "localhost" or origin_host == "::1" or origin_host.startswith("127.")):
                self._serve_json({"error": "Cross-origin process actions are blocked."}, HTTPStatus.FORBIDDEN)
                return
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._serve_json({"error": "Expected application/json."}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > 4096:
            self._serve_json({"error": "Invalid request body."}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object.")
            action = str(payload.get("action") or "")
            result = self.monitor_server.state.process_action(pid, action)
        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON body."}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as exc:
            self._serve_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except psutil.NoSuchProcess:
            self._serve_json({"error": "Process no longer exists."}, HTTPStatus.NOT_FOUND)
            return
        except (psutil.AccessDenied, PermissionError) as exc:
            self._serve_json({"error": str(exc) or "Process access denied."}, HTTPStatus.FORBIDDEN)
            return
        except Exception as exc:  # pragma: no cover - defensive for platform APIs.
            self._serve_json_error(exc)
            return
        self._serve_json(result)

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
        self._serve_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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
