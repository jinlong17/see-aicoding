"""SQLite persistence for resource samples, events, and local settings."""
from __future__ import annotations

import json
import math
import os
import platform
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


HISTORY_RANGES: dict[str, tuple[int, int]] = {
    "15m": (15 * 60, 180),
    "1h": (60 * 60, 240),
    "6h": (6 * 60 * 60, 288),
    "24h": (24 * 60 * 60, 288),
    "7d": (7 * 24 * 60 * 60, 336),
}


def default_database_path() -> Path:
    override = os.environ.get("SEE_AICODING_DB")
    if override:
        return Path(override).expanduser()
    if platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "see-aicoding" / "telemetry.sqlite3"


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _max_disk_percent(system_metrics: dict[str, Any]) -> float | None:
    values = [
        value
        for value in (
            _optional_float(disk.get("percent"))
            for disk in system_metrics.get("disks") or []
        )
        if value is not None
    ]
    return max(values) if values else None


class HistoryStore:
    """Small, thread-safe SQLite store opened only for each operation."""

    def __init__(
        self,
        path: str | Path | None = None,
        retention_days: int = 7,
        persist_interval_s: float = 5.0,
    ) -> None:
        self.path = Path(path).expanduser() if path is not None else default_database_path()
        self.retention_days = max(1, min(365, int(retention_days)))
        self.persist_interval_s = max(1.0, float(persist_interval_s))
        self.available = False
        self.last_error: str | None = None
        self._last_sample_at = 0.0
        self._last_prune_at = 0.0
        self._write_lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
            self.available = True
        except (OSError, sqlite3.Error) as exc:
            self.last_error = str(exc)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=2.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 2000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    cpu_percent REAL,
                    memory_percent REAL,
                    gpu_percent REAL,
                    swap_percent REAL,
                    disk_used_percent REAL,
                    disk_read_bps REAL,
                    disk_write_bps REAL,
                    disk_read_iops REAL,
                    disk_write_iops REAL,
                    disk_read_latency_ms REAL,
                    disk_write_latency_ms REAL,
                    network_download_bps REAL,
                    network_upload_bps REAL,
                    process_count INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_samples_timestamp
                    ON samples(timestamp);

                CREATE TABLE IF NOT EXISTS resource_events (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    resource TEXT NOT NULL,
                    label TEXT NOT NULL,
                    action TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    previous_severity TEXT,
                    current_severity TEXT,
                    value REAL,
                    unit TEXT,
                    threshold_value REAL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_resource_events_timestamp
                    ON resource_events(timestamp DESC);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )

    def _capture_error(self, exc: Exception) -> None:
        self.last_error = str(exc)

    def record_sample(
        self,
        system_metrics: dict[str, Any],
        timestamp: float | None = None,
    ) -> bool:
        if not self.available:
            return False
        now = time.time() if timestamp is None else float(timestamp)
        if now - self._last_sample_at < self.persist_interval_s:
            return False
        gpu = system_metrics.get("gpu") or {}
        disk_io = system_metrics.get("disk_io") or {}
        network = system_metrics.get("network") or {}
        process_summary = system_metrics.get("process_summary") or {}
        row = (
            now,
            _optional_float((system_metrics.get("cpu") or {}).get("percent")),
            _optional_float((system_metrics.get("memory") or {}).get("percent")),
            _optional_float(gpu.get("utilization_percent")) if gpu.get("available") else None,
            _optional_float((system_metrics.get("swap") or {}).get("percent")),
            _max_disk_percent(system_metrics),
            _optional_float(disk_io.get("read_bytes_per_s")),
            _optional_float(disk_io.get("write_bytes_per_s")),
            _optional_float(disk_io.get("read_iops")),
            _optional_float(disk_io.get("write_iops")),
            _optional_float(disk_io.get("read_latency_ms")),
            _optional_float(disk_io.get("write_latency_ms")),
            _optional_float(network.get("download_bytes_per_s")),
            _optional_float(network.get("upload_bytes_per_s")),
            int(process_summary.get("total") or 0),
        )
        try:
            with self._write_lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO samples (
                        timestamp, cpu_percent, memory_percent, gpu_percent,
                        swap_percent, disk_used_percent, disk_read_bps,
                        disk_write_bps, disk_read_iops, disk_write_iops,
                        disk_read_latency_ms, disk_write_latency_ms,
                        network_download_bps, network_upload_bps, process_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                self._last_sample_at = now
                if now - self._last_prune_at >= 3600:
                    cutoff = now - self.retention_days * 86400
                    connection.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff,))
                    connection.execute(
                        "DELETE FROM resource_events WHERE timestamp < ?",
                        (cutoff,),
                    )
                    self._last_prune_at = now
            self.last_error = None
            return True
        except (OSError, sqlite3.Error) as exc:
            self._capture_error(exc)
            return False

    def record_events(self, events: list[dict[str, Any]]) -> int:
        if not self.available or not events:
            return 0
        rows = [
            (
                str(event["id"]),
                float(event["timestamp"]),
                str(event["resource"]),
                str(event["label"]),
                str(event["action"]),
                str(event["severity"]),
                str(event.get("previous_severity") or ""),
                str(event.get("current_severity") or ""),
                _optional_float(event.get("value")),
                str(event.get("unit") or ""),
                _optional_float(event.get("threshold")),
                str(event["message"]),
            )
            for event in events
        ]
        try:
            with self._write_lock, self._connect() as connection:
                cursor = connection.executemany(
                    """
                    INSERT OR IGNORE INTO resource_events (
                        id, timestamp, resource, label, action, severity,
                        previous_severity, current_severity, value, unit,
                        threshold_value, message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            self.last_error = None
            return max(0, cursor.rowcount)
        except (OSError, sqlite3.Error) as exc:
            self._capture_error(exc)
            return 0

    def query_history(self, range_key: str = "1h") -> dict[str, Any]:
        if range_key not in HISTORY_RANGES:
            raise ValueError(f"Unknown history range: {range_key}")
        seconds, max_points = HISTORY_RANGES[range_key]
        end = time.time()
        start = end - seconds
        interval = max(1, int(math.ceil(seconds / max_points)))
        points: list[dict[str, Any]] = []
        if self.available:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        """
                        SELECT
                            CAST(timestamp / ? AS INTEGER) * ? AS timestamp,
                            AVG(cpu_percent) AS cpu_percent,
                            AVG(memory_percent) AS memory_percent,
                            AVG(gpu_percent) AS gpu_percent,
                            AVG(swap_percent) AS swap_percent,
                            AVG(disk_used_percent) AS disk_used_percent,
                            AVG(disk_read_bps) AS disk_read_bps,
                            AVG(disk_write_bps) AS disk_write_bps,
                            AVG(disk_read_iops) AS disk_read_iops,
                            AVG(disk_write_iops) AS disk_write_iops,
                            AVG(disk_read_latency_ms) AS disk_read_latency_ms,
                            AVG(disk_write_latency_ms) AS disk_write_latency_ms,
                            AVG(network_download_bps) AS network_download_bps,
                            AVG(network_upload_bps) AS network_upload_bps,
                            CAST(AVG(process_count) AS INTEGER) AS process_count
                        FROM samples
                        WHERE timestamp >= ?
                        GROUP BY CAST(timestamp / ? AS INTEGER)
                        ORDER BY timestamp ASC
                        """,
                        (interval, interval, start, interval),
                    ).fetchall()
                points = [dict(row) for row in rows]
                self.last_error = None
            except (OSError, sqlite3.Error) as exc:
                self._capture_error(exc)

        series_keys = (
            "cpu_percent",
            "memory_percent",
            "gpu_percent",
            "swap_percent",
            "disk_used_percent",
            "disk_read_bps",
            "disk_write_bps",
            "disk_read_iops",
            "disk_write_iops",
            "disk_read_latency_ms",
            "disk_write_latency_ms",
            "network_download_bps",
            "network_upload_bps",
            "process_count",
        )
        return {
            "range": range_key,
            "start": start,
            "end": end,
            "resolution_seconds": interval,
            "point_count": len(points),
            "timestamps": [point["timestamp"] for point in points],
            "series": {
                key: [point.get(key) for point in points]
                for key in series_keys
            },
            "persistence": self.status(),
        }

    def query_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.available:
            return []
        safe_limit = max(1, min(500, int(limit)))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, timestamp, resource, label, action, severity,
                           previous_severity, current_severity, value, unit,
                           threshold_value AS threshold, message
                    FROM resource_events
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (safe_limit,),
                ).fetchall()
            self.last_error = None
        except (OSError, sqlite3.Error) as exc:
            self._capture_error(exc)
            return []
        events = [dict(row) for row in rows]
        for event in events:
            event["timestamp_iso"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z",
                time.localtime(event["timestamp"]),
            )
        return events

    def load_setting(self, key: str, default: Any = None) -> Any:
        if not self.available:
            return default
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value_json FROM settings WHERE key = ?",
                    (key,),
                ).fetchone()
            return json.loads(row["value_json"]) if row else default
        except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            self._capture_error(exc)
            return default

    def save_setting(self, key: str, value: Any) -> bool:
        if not self.available:
            return False
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            with self._write_lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO settings(key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at
                    """,
                    (key, encoded, time.time()),
                )
            self.last_error = None
            return True
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            self._capture_error(exc)
            return False

    def status(self) -> dict[str, Any]:
        sample_count = 0
        event_count = 0
        first_sample = None
        last_sample = None
        if self.available:
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        """
                        SELECT COUNT(*) AS sample_count,
                               MIN(timestamp) AS first_sample,
                               MAX(timestamp) AS last_sample
                        FROM samples
                        """
                    ).fetchone()
                    event_row = connection.execute(
                        "SELECT COUNT(*) AS event_count FROM resource_events"
                    ).fetchone()
                sample_count = int(row["sample_count"] or 0)
                event_count = int(event_row["event_count"] or 0)
                first_sample = row["first_sample"]
                last_sample = row["last_sample"]
            except (OSError, sqlite3.Error) as exc:
                self._capture_error(exc)
        database_bytes = 0
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                database_bytes += candidate.stat().st_size
            except OSError:
                pass
        return {
            "available": self.available,
            "path": str(self.path),
            "database_bytes": database_bytes,
            "sample_count": sample_count,
            "event_count": event_count,
            "first_sample": first_sample,
            "last_sample": last_sample,
            "retention_days": self.retention_days,
            "persist_interval_seconds": self.persist_interval_s,
            "error": self.last_error,
        }

