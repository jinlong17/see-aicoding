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
from .observability import ThresholdEngine
from .persistence import HistoryStore
from .runtime import ContainerCollector, NetworkAttributionCollector, ServiceCollector
from .snapshot import build_snapshot
from .telemetry import (
    SystemTelemetry,
    WorkloadDiskCollector,
    inspect_process,
    manage_process,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC_PACKAGE = "see_aicoding.web_static"

_DASHBOARD_SECTION_IDS = {
    "leaders",
    "processes",
    "storage",
    "runtime",
    "coding",
}
_DASHBOARD_ORDER_IDS = {
    "coding",
    "overview",
    "leaders",
    "processes",
    "storage",
    "runtime",
}
_PROCESS_COLUMN_IDS = {
    "identity",
    "pid",
    "user",
    "state",
    "cpu",
    "memory",
    "gpu",
    "disk",
    "network",
    "threads",
    "age",
}
DEFAULT_DASHBOARD_PREFERENCES = {
    "density": "compact",
    "language": "en",
    "theme": "deep",
    "performance_mode": "balanced",
    "hidden_sections": [],
    "show_idle_ai": False,
    "section_order": [
        "coding",
        "overview",
        "leaders",
        "processes",
        "storage",
        "runtime",
    ],
    "process_columns": [
        "identity",
        "pid",
        "user",
        "state",
        "cpu",
        "memory",
        "gpu",
        "disk",
        "network",
        "threads",
        "age",
    ],
    "provider_quotas": {
        provider: {
            "five_hour_used_percent": None,
            "weekly_used_percent": None,
        }
        for provider in ("claude", "chatgpt", "cursor")
    },
    "quota_updated_at": None,
}


def _optional_percent(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(min(100.0, max(0.0, result)), 2)


def normalize_dashboard_preferences(value: object) -> dict:
    """Return the safe, forwards-compatible subset of dashboard preferences."""
    source = value if isinstance(value, dict) else {}
    density = source.get("density")
    language = source.get("language")
    theme = source.get("theme")
    performance_mode = source.get("performance_mode")
    hidden = source.get("hidden_sections")
    columns = source.get("process_columns")
    order = source.get("section_order")
    if density not in {"compact", "comfortable"}:
        density = DEFAULT_DASHBOARD_PREFERENCES["density"]
    if language not in {"en", "zh-CN"}:
        language = DEFAULT_DASHBOARD_PREFERENCES["language"]
    if theme not in {"light", "warm", "mint", "dark", "deep"}:
        theme = DEFAULT_DASHBOARD_PREFERENCES["theme"]
    if performance_mode not in {"realtime", "balanced", "efficient"}:
        performance_mode = DEFAULT_DASHBOARD_PREFERENCES["performance_mode"]
    if not isinstance(hidden, list):
        hidden = []
    if not isinstance(columns, list):
        columns = list(DEFAULT_DASHBOARD_PREFERENCES["process_columns"])
    if not isinstance(order, list):
        order = list(DEFAULT_DASHBOARD_PREFERENCES["section_order"])
    safe_order = []
    for item in order:
        if isinstance(item, str) and item in _DASHBOARD_ORDER_IDS and item not in safe_order:
            safe_order.append(item)
    safe_order.extend(
        item for item in DEFAULT_DASHBOARD_PREFERENCES["section_order"]
        if item not in safe_order
    )
    safe_columns = [
        item for item in columns
        if isinstance(item, str) and item in _PROCESS_COLUMN_IDS
    ]
    if "identity" not in safe_columns:
        safe_columns.insert(0, "identity")
    raw_quotas = source.get("provider_quotas")
    raw_quotas = raw_quotas if isinstance(raw_quotas, dict) else {}
    provider_quotas = {}
    for provider in ("claude", "chatgpt", "cursor"):
        raw_provider = raw_quotas.get(provider)
        raw_provider = raw_provider if isinstance(raw_provider, dict) else {}
        provider_quotas[provider] = {
            "five_hour_used_percent": _optional_percent(
                raw_provider.get("five_hour_used_percent")
            ),
            "weekly_used_percent": _optional_percent(
                raw_provider.get("weekly_used_percent")
            ),
        }
    quota_updated_at = source.get("quota_updated_at")
    try:
        quota_updated_at = float(quota_updated_at) if quota_updated_at is not None else None
    except (TypeError, ValueError):
        quota_updated_at = None
    return {
        "density": density,
        "language": language,
        "theme": theme,
        "performance_mode": performance_mode,
        "hidden_sections": sorted({
            item for item in hidden
            if isinstance(item, str) and item in _DASHBOARD_SECTION_IDS
        }),
        "show_idle_ai": bool(source.get("show_idle_ai", False)),
        "section_order": safe_order,
        "process_columns": safe_columns,
        "provider_quotas": provider_quotas,
        "quota_updated_at": quota_updated_at,
    }


class MonitorState:
    def __init__(self, refresh_s: float):
        self.refresh_s = max(0.5, refresh_s)
        self.sampler = Sampler(include_all_users=True)
        self.history = History()
        self.telemetry = SystemTelemetry()
        self.workload_disk = WorkloadDiskCollector()
        self.store = HistoryStore(persist_interval_s=max(5.0, self.refresh_s))
        stored_thresholds = self.store.load_setting("thresholds", {})
        self.thresholds = ThresholdEngine(
            stored_thresholds if isinstance(stored_thresholds, dict) else None
        )
        self.services = ServiceCollector()
        self.network_attribution = NetworkAttributionCollector()
        self.containers = ContainerCollector()
        self.extensions = scan_installed_extensions()
        self._lock = threading.Lock()
        self._runtime_lock = threading.Lock()
        self._cached_json = ""
        self._cached_stream_json = ""
        self._cached_snapshot: dict | None = None
        self._cached_at = 0.0
        self._observability_cache: dict | None = None
        self._observability_cached_at = 0.0

        self.sampler.snapshot()
        time.sleep(min(0.5, self.refresh_s))

    @staticmethod
    def _compact_snapshot(snapshot: dict) -> dict:
        processes = snapshot.get("processes") or {}
        resources = snapshot.get("resources") or {}
        observability = snapshot.get("observability") or {}

        def compact_leader(item: dict) -> dict:
            return {
                key: item.get(key)
                for key in (
                    "label",
                    "primary_pid",
                    "process_count",
                    "cpu_capacity_percent",
                    "memory_bytes",
                    "gpu_percent",
                    "gpu_memory_bytes",
                )
                if key in item
            }

        compact_zones = []
        for zone in snapshot.get("zones") or []:
            sessions = []
            for session in zone.get("sessions") or []:
                children = session.get("children") or []
                compact_children = [
                    {
                        key: child.get(key)
                        for key in (
                            "pid",
                            "name",
                            "label",
                            "cmdline",
                            "age_seconds",
                            "age_label",
                            "cpu_capacity_percent",
                            "memory_bytes",
                            "status",
                        )
                        if key in child
                    }
                    for child in children[:10]
                ]
                sessions.append(
                    {
                        **session,
                        "root": {"pid": (session.get("root") or {}).get("pid")},
                        "child_count": len(children),
                        "children": compact_children,
                    }
                )
            compact_zones.append({**zone, "sessions": sessions})

        return {
            **{key: value for key, value in snapshot.items() if key != "sessions"},
            "stream_compact": True,
            "processes": {**processes, "items": []},
            "resources": {
                **resources,
                "programs": [],
                "top_cpu": [compact_leader(item) for item in (resources.get("top_cpu") or [])[:5]],
                "top_memory": [compact_leader(item) for item in (resources.get("top_memory") or [])[:5]],
                "top_gpu": [compact_leader(item) for item in (resources.get("top_gpu") or [])[:5]],
                "top_disk": [],
            },
            "observability": {
                **observability,
                "events": (observability.get("events") or [])[:4],
            },
            "zones": compact_zones,
        }

    def snapshot_json(self, compact: bool = False) -> str:
        with self._lock:
            now = time.monotonic()
            if self._cached_snapshot is None or now - self._cached_at >= self.refresh_s * 0.8:
                procs = self.sampler.snapshot()
                sessions = build_sessions(procs)
                self.history.record(sessions)
                workload_storage = self.workload_disk.sample(sessions)
                system_metrics = self.telemetry.sample(
                    procs,
                    download_bytes_per_s=self.history.net_recv_per_s,
                    upload_bytes_per_s=self.history.net_sent_per_s,
                )
                generated_at = time.time()
                transitions = self.thresholds.evaluate(system_metrics, timestamp=generated_at)
                self.store.record_events(transitions)
                self.store.record_sample(system_metrics, timestamp=generated_at)
                if transitions:
                    self._observability_cached_at = 0.0
                self._cached_snapshot = build_snapshot(
                    sessions,
                    procs,
                    self.history,
                    self.extensions,
                    self.refresh_s,
                    system_metrics=system_metrics,
                    observability=self._observability_snapshot(),
                    workload_storage=workload_storage,
                )
                self._cached_json = ""
                self._cached_stream_json = ""
                self._cached_at = time.monotonic()
            if compact:
                if not self._cached_stream_json:
                    self._cached_stream_json = json.dumps(
                        self._compact_snapshot(self._cached_snapshot),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                return self._cached_stream_json
            if not self._cached_json:
                self._cached_json = json.dumps(
                    self._cached_snapshot,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            return self._cached_json

    def process_details(self, pid: int) -> dict:
        details = inspect_process(pid)
        with self._runtime_lock:
            network = self.network_attribution.sample()
        attributed = next(
            (item for item in network.get("items", []) if int(item.get("pid") or 0) == pid),
            None,
        )
        details["network_attribution"] = {
            "provider": network.get("provider"),
            "throughput_available": network.get("throughput_available", False),
            "note": network.get("note"),
            "item": attributed,
        }
        return details

    def process_action(self, pid: int, action: str) -> dict:
        result = manage_process(pid, action)
        with self._lock:
            self._cached_at = 0.0
        return result

    def threshold_snapshot(self) -> dict:
        with self._lock:
            return self._observability_snapshot()

    def _observability_snapshot(self) -> dict:
        now = time.monotonic()
        if self._observability_cache is not None and now - self._observability_cached_at < 30.0:
            return self._observability_cache
        result = self.thresholds.snapshot()
        if self.store.available:
            result["events"] = self.store.query_events(limit=100)
        result["persistence"] = self.store.status()
        result["history_ranges"] = ["15m", "1h", "6h", "24h", "7d"]
        self._observability_cache = result
        self._observability_cached_at = now
        return result

    def update_thresholds(self, updates: dict) -> dict:
        with self._lock:
            values = self.thresholds.update_thresholds(updates)
            persisted = self.store.save_setting("thresholds", values)
            self._cached_at = 0.0
            self._observability_cached_at = 0.0
            return {"thresholds": values, "persisted": persisted}

    def dashboard_preferences(self) -> dict:
        stored = self.store.load_setting("dashboard_preferences", {})
        return {
            "preferences": normalize_dashboard_preferences(stored),
            "persisted": self.store.available,
        }

    def update_dashboard_preferences(self, updates: dict) -> dict:
        current = self.dashboard_preferences()["preferences"]
        candidate = normalize_dashboard_preferences({**current, **updates})
        if candidate["provider_quotas"] != current["provider_quotas"]:
            updates = {**updates, "quota_updated_at": time.time()}
        preferences = normalize_dashboard_preferences({**current, **updates})
        persisted = self.store.save_setting("dashboard_preferences", preferences)
        return {"preferences": preferences, "persisted": persisted}

    def provider_usage(self) -> dict:
        preferences = self.dashboard_preferences()["preferences"]
        quotas = preferences["provider_quotas"]
        provider_meta = (
            ("claude", "Claude", "claude"),
            ("chatgpt", "ChatGPT", "codex"),
            ("cursor", "Cursor", "cursor"),
        )
        providers = []
        for provider_id, display_name, zone_id in provider_meta:
            values = quotas[provider_id]
            windows = []
            for window_id, duration_minutes, key in (
                ("five_hour", 300, "five_hour_used_percent"),
                ("weekly", 10080, "weekly_used_percent"),
            ):
                used = values[key]
                windows.append(
                    {
                        "id": window_id,
                        "duration_minutes": duration_minutes,
                        "used_percent": used,
                        "remaining_percent": round(100.0 - used, 2) if used is not None else None,
                        "resets_at": None,
                    }
                )
            available = any(window["used_percent"] is not None for window in windows)
            providers.append(
                {
                    "id": provider_id,
                    "display_name": display_name,
                    "workload_zone_id": zone_id,
                    "status": "available" if available else "unsupported",
                    "source": {
                        "kind": "manual_local",
                        "scope": "subscription",
                        "authoritative": False,
                    },
                    "observed_at": preferences["quota_updated_at"] if available else None,
                    "stale_after_seconds": None,
                    "windows": windows,
                    "reason": (
                        None
                        if available
                        else "No safe programmatic quota source is configured; add local percentages in Settings."
                    ),
                }
            )
        return {
            "schema_version": 1,
            "generated_at": time.time(),
            "providers": providers,
        }

    def history_snapshot(self, range_key: str) -> dict:
        return self.store.query_history(range_key)

    def persistent_events(self, limit: int) -> dict:
        return {
            "events": self.store.query_events(limit=limit),
            "persistence": self.store.status(),
        }

    def service_snapshot(self, force: bool = False) -> dict:
        with self._runtime_lock:
            return self.services.sample(force=force)

    def network_snapshot(self, force: bool = False) -> dict:
        with self._runtime_lock:
            return self.network_attribution.sample(force=force)

    def container_snapshot(self, force: bool = False) -> dict:
        with self._runtime_lock:
            return self.containers.sample(force=force)


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
    server_version = "see-aicoding-web/0.3"

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
        if parsed.path == "/api/thresholds":
            self._serve_json(self.monitor_server.state.threshold_snapshot())
            return
        if parsed.path == "/api/dashboard-preferences":
            self._serve_json(self.monitor_server.state.dashboard_preferences())
            return
        if parsed.path == "/api/provider-usage":
            self._serve_json(self.monitor_server.state.provider_usage())
            return
        if parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            range_key = (query.get("range") or ["1h"])[0]
            try:
                payload = self.monitor_server.state.history_snapshot(range_key)
            except ValueError as exc:
                self._serve_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._serve_json(payload)
            return
        if parsed.path == "/api/resource-events":
            query = parse_qs(parsed.query)
            try:
                limit = int((query.get("limit") or ["100"])[0])
            except ValueError:
                limit = 100
            self._serve_json(self.monitor_server.state.persistent_events(limit))
            return
        if parsed.path == "/api/services":
            query = parse_qs(parsed.query)
            force = (query.get("refresh") or ["0"])[0] == "1"
            self._serve_json(self.monitor_server.state.service_snapshot(force=force))
            return
        if parsed.path == "/api/network-attribution":
            query = parse_qs(parsed.query)
            force = (query.get("refresh") or ["0"])[0] == "1"
            self._serve_json(self.monitor_server.state.network_snapshot(force=force))
            return
        if parsed.path == "/api/containers":
            query = parse_qs(parsed.query)
            force = (query.get("refresh") or ["0"])[0] == "1"
            self._serve_json(self.monitor_server.state.container_snapshot(force=force))
            return
        if parsed.path.startswith("/api/process/"):
            self._serve_process_details(parsed.path)
            return
        if parsed.path == "/events":
            query = parse_qs(parsed.query)
            try:
                interval_s = float((query.get("interval") or ["3"])[0])
            except ValueError:
                interval_s = 3.0
            self._serve_events(
                once=query.get("once") == ["1"],
                interval_s=interval_s,
            )
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/thresholds":
            self._serve_threshold_update()
            return
        if parsed.path == "/api/dashboard-preferences":
            self._serve_dashboard_preferences_update()
            return
        if parsed.path.startswith("/api/process/") and parsed.path.endswith("/action"):
            self._serve_process_action(parsed.path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _request_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        origin_host = urlparse(origin).hostname or ""
        return (
            origin_host == "localhost"
            or origin_host == "::1"
            or origin_host.startswith("127.")
        )

    def _read_json_object(self, max_bytes: int = 4096) -> dict:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise TypeError("Expected application/json.")
        try:
            content_length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > max_bytes:
            raise ValueError("Invalid request body.")
        payload = json.loads(self.rfile.read(content_length))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        return payload

    def _serve_threshold_update(self) -> None:
        if not self._request_origin_allowed():
            self._serve_json(
                {"error": "Cross-origin threshold changes are blocked."},
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            payload = self._read_json_object(max_bytes=16384)
            updates = payload.get("thresholds", payload)
            if not isinstance(updates, dict):
                raise ValueError("Threshold updates must be an object.")
            result = self.monitor_server.state.update_thresholds(updates)
        except TypeError as exc:
            self._serve_json({"error": str(exc)}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON body."}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as exc:
            self._serve_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._serve_json(result)

    def _serve_dashboard_preferences_update(self) -> None:
        if not self._request_origin_allowed():
            self._serve_json(
                {"error": "Cross-origin preference changes are blocked."},
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            payload = self._read_json_object(max_bytes=16384)
            updates = payload.get("preferences", payload)
            if not isinstance(updates, dict):
                raise ValueError("Dashboard preferences must be an object.")
            result = self.monitor_server.state.update_dashboard_preferences(updates)
        except TypeError as exc:
            self._serve_json({"error": str(exc)}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        except json.JSONDecodeError:
            self._serve_json({"error": "Invalid JSON body."}, HTTPStatus.BAD_REQUEST)
            return
        except ValueError as exc:
            self._serve_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._serve_json(result)

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
        if not self._request_origin_allowed():
            self._serve_json(
                {"error": "Cross-origin process actions are blocked."},
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            payload = self._read_json_object()
            action = str(payload.get("action") or "")
            result = self.monitor_server.state.process_action(pid, action)
        except TypeError as exc:
            self._serve_json({"error": str(exc)}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
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

    def _serve_events(self, once: bool = False, interval_s: float = 3.0) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close" if once else "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        while True:
            try:
                payload = self.monitor_server.state.snapshot_json(compact=True)
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
            time.sleep(max(self.monitor_server.state.refresh_s, min(10.0, interval_s)))

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
