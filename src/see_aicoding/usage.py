"""Low-overhead, local-only AI provider quota collection."""
from __future__ import annotations

import copy
import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .persistence import default_database_path


DEFAULT_CACHE_SECONDS = 300.0
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_RETRY_SECONDS = 30.0
MAX_RETRY_SECONDS = 600.0
FORCE_REFRESH_COOLDOWN_SECONDS = 10.0
CLAUDE_STALE_SECONDS = 24 * 60 * 60
CLAUDE_WRITE_DEDUP_SECONDS = 60.0
MAX_STATUSLINE_BYTES = 1024 * 1024

_PROVIDER_META = {
    "chatgpt": ("codex_app_server", "active_codex_profile"),
    "claude": ("claude_statusline", "active_claude_profile"),
    "cursor": ("unsupported", "personal_subscription"),
}


class UsageSourceError(RuntimeError):
    """A safe-to-display quota source failure."""


def _percent(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return round(min(100.0, max(0.0, result)), 2)


def _timestamp(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _source(kind: str, scope: str, authoritative: bool) -> dict[str, object]:
    return {
        "kind": kind,
        "scope": scope,
        "authoritative": authoritative,
    }


def _provider_result(
    provider_id: str,
    status: str,
    *,
    windows: list[dict[str, object]] | None = None,
    observed_at: float | None = None,
    stale_after_seconds: float | None = None,
    reason: str | None = None,
    authoritative: bool = True,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    kind, scope = _PROVIDER_META[provider_id]
    return {
        "id": provider_id,
        "status": status,
        "source": _source(kind, scope, authoritative),
        "observed_at": observed_at,
        "stale_after_seconds": stale_after_seconds,
        "windows": windows or [],
        "reason": reason,
        "metadata": metadata or {},
    }


def _window_id(duration_minutes: int | None, fallback: str) -> str:
    if duration_minutes == 300:
        return "five_hour"
    if duration_minutes == 10080:
        return "weekly"
    if duration_minutes is not None:
        return f"window_{duration_minutes}m"
    return fallback


def _normalize_window(value: object, fallback: str) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    used = _percent(value.get("usedPercent"))
    if used is None:
        return None
    try:
        duration = int(value["windowDurationMins"])
    except (KeyError, TypeError, ValueError):
        duration = None
    return {
        "id": _window_id(duration, fallback),
        "duration_minutes": duration,
        "used_percent": used,
        "remaining_percent": round(100.0 - used, 2),
        "resets_at": _timestamp(value.get("resetsAt")),
    }


def normalize_codex_rate_limits(result: object, observed_at: float | None = None) -> dict[str, object]:
    """Normalize the current Codex app-server rate-limit response."""
    if not isinstance(result, dict):
        raise UsageSourceError("Codex returned an invalid rate-limit response.")

    limits = None
    by_limit_id = result.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict):
        limits = by_limit_id.get("codex")
    if not isinstance(limits, dict):
        limits = result.get("rateLimits")
    if not isinstance(limits, dict):
        return _provider_result(
            "chatgpt",
            "unavailable",
            reason="The active Codex profile did not return subscription rate limits.",
        )

    windows = [
        window
        for window in (
            _normalize_window(limits.get("primary"), "primary"),
            _normalize_window(limits.get("secondary"), "secondary"),
        )
        if window is not None
    ]
    if not windows:
        return _provider_result(
            "chatgpt",
            "unavailable",
            reason="The active Codex profile did not return a usable quota window.",
        )
    return _provider_result(
        "chatgpt",
        "available",
        windows=windows,
        observed_at=time.time() if observed_at is None else observed_at,
        stale_after_seconds=DEFAULT_CACHE_SECONDS * 2,
        metadata={"plan_type": limits.get("planType")},
    )


def _bundled_codex_paths() -> tuple[Path, ...]:
    return (
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path.home() / "Applications" / "ChatGPT.app" / "Contents" / "Resources" / "codex",
        Path.home() / "Applications" / "Codex.app" / "Contents" / "Resources" / "codex",
    )


def _is_executable_file(path: str | Path) -> bool:
    candidate = Path(path).expanduser()
    return candidate.is_file() and os.access(candidate, os.X_OK)


def find_codex_executables() -> list[str]:
    """Return local Codex candidates in safest automatic-selection order."""
    override = os.environ.get("SEE_AICODING_CODEX_BIN")
    if override:
        path = Path(override).expanduser()
        return [str(path)] if _is_executable_file(path) else []

    candidates: list[str] = []
    # App-bundled Codex versions move with their supported app-server protocol,
    # so prefer them over an arbitrary older CLI found first on PATH.
    for bundled in _bundled_codex_paths():
        if _is_executable_file(bundled):
            candidates.append(str(bundled))
    command = shutil.which("codex")
    if command and _is_executable_file(command):
        candidates.append(command)
    return list(dict.fromkeys(candidates))


def find_codex_executable() -> str | None:
    """Return the preferred candidate for callers that only need one path."""
    candidates = find_codex_executables()
    return candidates[0] if candidates else None


def _put_stdout_lines(stream: Any, target: queue.Queue[object]) -> None:
    try:
        for line in iter(stream.readline, ""):
            target.put(line)
    finally:
        target.put(None)


def _send_rpc(process: subprocess.Popen[str], payload: dict[str, object]) -> None:
    if process.stdin is None:
        raise UsageSourceError("Codex app-server stdin is unavailable.")
    process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _wait_rpc_response(
    messages: queue.Queue[object],
    request_id: int,
    deadline: float,
) -> object:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UsageSourceError("Codex rate-limit request timed out.")
        try:
            line = messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise UsageSourceError("Codex rate-limit request timed out.") from exc
        if line is None:
            raise UsageSourceError("Codex app-server exited before returning rate limits.")
        try:
            message = json.loads(str(line))
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        error = message.get("error")
        if isinstance(error, dict):
            safe_message = str(error.get("message") or "Codex app-server request failed.")
            raise UsageSourceError(safe_message[:240])
        return message.get("result")


class CodexRateLimitSource:
    """Read the active local Codex profile through its supported app-server protocol."""

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._selected_executable: str | None = None

    def _collect_from_executable(self, executable: str) -> dict[str, object]:
        process: subprocess.Popen[str] | None = None
        messages: queue.Queue[object] = queue.Queue()
        try:
            process = subprocess.Popen(
                [executable, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            if process.stdout is None:
                raise UsageSourceError("Codex app-server stdout is unavailable.")
            threading.Thread(
                target=_put_stdout_lines,
                args=(process.stdout, messages),
                name="see-aicoding-codex-stdout",
                daemon=True,
            ).start()
            deadline = time.monotonic() + self.timeout_seconds
            _send_rpc(
                process,
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "see-aicoding", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                },
            )
            _wait_rpc_response(messages, 1, deadline)
            _send_rpc(process, {"method": "initialized", "params": {}})
            _send_rpc(
                process,
                {"id": 2, "method": "account/rateLimits/read", "params": None},
            )
            result = _wait_rpc_response(messages, 2, deadline)
            return normalize_codex_rate_limits(result)
        except (OSError, BrokenPipeError) as exc:
            raise UsageSourceError(f"Unable to query the local Codex app-server: {exc}") from exc
        finally:
            if process is not None:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1.0)

    def collect(self) -> dict[str, object]:
        candidates = find_codex_executables()
        if self._selected_executable and _is_executable_file(self._selected_executable):
            candidates = [
                self._selected_executable,
                *(
                    candidate
                    for candidate in candidates
                    if candidate != self._selected_executable
                ),
            ]
        if not candidates:
            return _provider_result(
                "chatgpt",
                "unavailable",
                reason="No executable Codex app-server candidate was found.",
            )

        errors: list[str] = []
        for executable in candidates:
            try:
                result = self._collect_from_executable(executable)
            except UsageSourceError as exc:
                errors.append(str(exc))
                continue
            self._selected_executable = executable
            metadata = result.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata.update(
                {
                    "codex_executable": executable,
                    "selection": (
                        "environment_override"
                        if os.environ.get("SEE_AICODING_CODEX_BIN")
                        else "automatic"
                    ),
                }
            )
            result["metadata"] = metadata
            return result

        last_error = errors[-1] if errors else "The app-server protocol was unavailable."
        raise UsageSourceError(
            "No compatible local Codex app-server was found after "
            f"{len(candidates)} attempt(s). Last error: {last_error}"
        )


def default_claude_usage_path() -> Path:
    override = os.environ.get("SEE_AICODING_CLAUDE_USAGE_FILE")
    if override:
        return Path(override).expanduser()
    return default_database_path().parent / "claude-usage.json"


def default_claude_settings_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir).expanduser() / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def claude_statusline_configuration(path: str | Path | None = None) -> dict[str, object]:
    """Inspect whether Claude Code is configured to invoke the safe capture mode."""
    settings_path = (
        Path(path).expanduser()
        if path is not None
        else default_claude_settings_path()
    )
    try:
        if settings_path.stat().st_size > 1024 * 1024:
            return {"configured": False, "settings_path": str(settings_path)}
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {"configured": False, "settings_path": str(settings_path)}
    status_line = payload.get("statusLine") if isinstance(payload, dict) else None
    if not isinstance(status_line, dict) or status_line.get("type") != "command":
        return {"configured": False, "settings_path": str(settings_path)}
    command = status_line.get("command")
    if not isinstance(command, str):
        return {"configured": False, "settings_path": str(settings_path)}
    try:
        arguments = shlex.split(command)
    except ValueError:
        arguments = []
    configured = "--capture-claude-usage" in arguments[1:]
    return {
        "configured": configured,
        "settings_path": str(settings_path),
        "uses_absolute_executable": bool(arguments and Path(arguments[0]).is_absolute()),
    }


def _normalize_claude_window(
    value: object,
    window_id: str,
    duration_minutes: int,
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    used = _percent(value.get("used_percentage"))
    if used is None:
        return None
    return {
        "id": window_id,
        "duration_minutes": duration_minutes,
        "used_percent": used,
        "remaining_percent": round(100.0 - used, 2),
        "resets_at": _timestamp(value.get("resets_at")),
    }


class ClaudeStatusLineSource:
    """Read a sanitized snapshot explicitly written by Claude Code status-line."""

    def __init__(
        self,
        path: str | Path | None = None,
        settings_path: str | Path | None = None,
    ) -> None:
        self.path = Path(path).expanduser() if path is not None else default_claude_usage_path()
        self.settings_path = (
            Path(settings_path).expanduser()
            if settings_path is not None
            else default_claude_settings_path()
        )

    def collect(self) -> dict[str, object]:
        if not self.path.is_file():
            configuration = claude_statusline_configuration(self.settings_path)
            configured = bool(configuration["configured"])
            return _provider_result(
                "claude",
                "unavailable",
                reason=(
                    "Claude quota capture is configured and starts automatically "
                    "in a new Claude Code session after the first eligible response."
                    if configured
                    else (
                        "Claude automatic quota capture is not configured. "
                        "Set Claude Code statusLine.command to an absolute "
                        "see-aicoding path with '--capture-claude-usage'."
                    )
                ),
                metadata={
                    "capture_configured": configured,
                    "waiting_for_first_response": configured,
                    "uses_absolute_executable": configuration.get(
                        "uses_absolute_executable",
                        False,
                    ),
                },
            )
        try:
            if self.path.stat().st_size > 64 * 1024:
                raise UsageSourceError("Claude quota snapshot is unexpectedly large.")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UsageSourceError(f"Unable to read the Claude quota snapshot: {exc}") from exc
        if not isinstance(payload, dict):
            raise UsageSourceError("Claude quota snapshot has an invalid format.")
        rate_limits = payload.get("rate_limits")
        rate_limits = rate_limits if isinstance(rate_limits, dict) else {}
        windows = [
            window
            for window in (
                _normalize_claude_window(rate_limits.get("five_hour"), "five_hour", 300),
                _normalize_claude_window(rate_limits.get("seven_day"), "weekly", 10080),
            )
            if window is not None
        ]
        observed_at = _timestamp(payload.get("captured_at"))
        if not windows or observed_at is None:
            return _provider_result(
                "claude",
                "unavailable",
                reason="Claude has not supplied subscription rate limits in this session yet.",
            )
        age = max(0.0, time.time() - observed_at)
        stale = age > CLAUDE_STALE_SECONDS
        return _provider_result(
            "claude",
            "stale" if stale else "available",
            windows=windows,
            observed_at=observed_at,
            stale_after_seconds=CLAUDE_STALE_SECONDS,
            reason=(
                "Claude quota snapshot is more than 24 hours old."
                if stale
                else None
            ),
        )


def _safe_claude_rate_limits(payload: object) -> dict[str, dict[str, float]]:
    if not isinstance(payload, dict):
        return {}
    source = payload.get("rate_limits")
    source = source if isinstance(source, dict) else {}
    result: dict[str, dict[str, float]] = {}
    for key in ("five_hour", "seven_day"):
        value = source.get(key)
        if not isinstance(value, dict):
            continue
        used = _percent(value.get("used_percentage"))
        if used is None:
            continue
        window: dict[str, float] = {"used_percentage": used}
        resets_at = _timestamp(value.get("resets_at"))
        if resets_at is not None:
            window["resets_at"] = resets_at
        result[key] = window
    return result


def capture_claude_statusline(payload: object, path: str | Path | None = None) -> str:
    """Persist only Claude's public status-line rate-limit fields and return display text."""
    rate_limits = _safe_claude_rate_limits(payload)
    parts = []
    if "five_hour" in rate_limits:
        parts.append(f"5h {rate_limits['five_hour']['used_percentage']:g}%")
    if "seven_day" in rate_limits:
        parts.append(f"7d {rate_limits['seven_day']['used_percentage']:g}%")
    if not rate_limits:
        return "Claude limits · waiting for first response"

    destination = Path(path).expanduser() if path is not None else default_claude_usage_path()
    now = time.time()
    try:
        if destination.stat().st_size <= 64 * 1024:
            existing = json.loads(destination.read_text(encoding="utf-8"))
            existing_at = _timestamp(existing.get("captured_at")) if isinstance(existing, dict) else None
            if (
                isinstance(existing, dict)
                and existing.get("rate_limits") == rate_limits
                and existing_at is not None
                and now - existing_at < CLAUDE_WRITE_DEDUP_SECONDS
            ):
                return "Claude limits · " + " · ".join(parts)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "schema_version": 1,
        "captured_at": now,
        "rate_limits": rate_limits,
    }
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return "Claude limits · " + " · ".join(parts)


class UsageCollector:
    """Cache provider quotas and refresh them on a daemon thread."""

    def __init__(
        self,
        *,
        cache_seconds: float = DEFAULT_CACHE_SECONDS,
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        enabled: bool = True,
        chatgpt_source: object | None = None,
        claude_source: object | None = None,
    ) -> None:
        self.cache_seconds = max(60.0, float(cache_seconds))
        self.retry_seconds = max(5.0, float(retry_seconds))
        self.chatgpt_source = chatgpt_source or CodexRateLimitSource()
        self.claude_source = claude_source or ClaudeStatusLineSource()
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._refreshing = False
        self._last_force_monotonic = float("-inf")
        self._next_attempt_monotonic = 0.0
        self._next_attempt_at = 0.0
        self._failures = 0
        initial_status = "refreshing" if self._enabled else "disabled"
        initial_reason = (
            "Automatic local quota refresh is starting."
            if self._enabled
            else "Automatic local quota refresh is disabled in dashboard settings."
        )
        self._providers = {
            "chatgpt": _provider_result(
                "chatgpt",
                initial_status,
                reason=initial_reason,
            ),
            "claude": _provider_result(
                "claude",
                initial_status,
                reason=initial_reason,
            ),
            "cursor": _provider_result(
                "cursor",
                "unsupported",
                reason=(
                    "Cursor does not expose a supported personal quota source; "
                    "use manual local values if needed."
                ),
                authoritative=False,
            ),
        }

    def set_enabled(self, enabled: bool) -> bool:
        """Enable or pause future quota refreshes without discarding cached data."""
        with self._lock:
            enabled = bool(enabled)
            if enabled and not self._enabled:
                self._next_attempt_monotonic = 0.0
                self._next_attempt_at = 0.0
            self._enabled = enabled
            return self._enabled

    @staticmethod
    def _error(provider_id: str, exc: Exception) -> dict[str, object]:
        message = str(exc).strip() or "Automatic quota refresh failed."
        return _provider_result(
            provider_id,
            "error",
            reason=message[:300],
        )

    def _collect_all(self) -> dict[str, dict[str, object]]:
        providers: dict[str, dict[str, object]] = {}
        for provider_id, source in (
            ("chatgpt", self.chatgpt_source),
            ("claude", self.claude_source),
        ):
            try:
                providers[provider_id] = source.collect()  # type: ignore[attr-defined]
            except Exception as exc:
                providers[provider_id] = self._error(provider_id, exc)
        providers["cursor"] = _provider_result(
            "cursor",
            "unsupported",
            reason=(
                "Cursor does not expose a supported personal quota source; "
                "use manual local values if needed."
            ),
            authoritative=False,
        )
        return providers

    def _refresh(self) -> None:
        providers = self._collect_all()
        now = time.time()
        monotonic_now = time.monotonic()
        has_error = any(value.get("status") == "error" for value in providers.values())
        with self._lock:
            safe_providers = {}
            for provider_id, provider in providers.items():
                previous = self._providers.get(provider_id) or {}
                previous_windows = previous.get("windows")
                if (
                    provider.get("status") == "error"
                    and previous.get("status") in {"available", "stale"}
                    and isinstance(previous_windows, list)
                    and previous_windows
                ):
                    failure_reason = provider.get("reason") or "Automatic refresh failed."
                    safe_providers[provider_id] = {
                        **copy.deepcopy(previous),
                        "status": "stale",
                        "reason": (
                            "Automatic refresh failed; showing the last successful "
                            f"local data. {failure_reason}"
                        ),
                    }
                else:
                    safe_providers[provider_id] = provider
            self._providers = safe_providers
            self._refreshing = False
            if has_error:
                self._failures += 1
                delay = min(
                    MAX_RETRY_SECONDS,
                    self.retry_seconds * (2 ** min(self._failures - 1, 5)),
                )
            else:
                self._failures = 0
                delay = self.cache_seconds
            self._next_attempt_monotonic = monotonic_now + delay
            self._next_attempt_at = now + delay

    def sample(self, force: bool = False) -> dict[str, object]:
        now = time.time()
        monotonic_now = time.monotonic()
        with self._lock:
            force_allowed = (
                self._enabled
                and force
                and monotonic_now - self._last_force_monotonic
                >= FORCE_REFRESH_COOLDOWN_SECONDS
            )
            due = self._enabled and (
                self._next_attempt_monotonic <= monotonic_now or force_allowed
            )
            if due and not self._refreshing:
                if force_allowed:
                    self._last_force_monotonic = monotonic_now
                self._refreshing = True
                threading.Thread(
                    target=self._refresh,
                    name="see-aicoding-usage",
                    daemon=True,
                ).start()
            providers = copy.deepcopy(self._providers)
            refreshing = self._refreshing
            next_attempt_at = self._next_attempt_at
            enabled = self._enabled
            cooldown = max(
                0.0,
                FORCE_REFRESH_COOLDOWN_SECONDS
                - (monotonic_now - self._last_force_monotonic),
            )
        return {
            "schema_version": 1,
            "generated_at": now,
            "enabled": enabled,
            "refreshing": refreshing,
            "next_refresh_at": (next_attempt_at or None) if enabled else None,
            "refresh_cooldown_seconds": round(cooldown, 2),
            "providers": list(providers.values()),
        }
