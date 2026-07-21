"""Threshold evaluation and resource event timeline models.

The monitor samples frequently, so emitting an event for every sample would
make the timeline unusable.  This module records only state transitions and
uses a small hysteresis window to avoid alert flapping around a threshold.
"""
from __future__ import annotations

import time
import uuid
from collections import deque
from copy import deepcopy
from typing import Any, Callable


DEFAULT_THRESHOLDS: dict[str, dict[str, Any]] = {
    "cpu": {
        "label": "CPU utilization",
        "warning": 75.0,
        "critical": 90.0,
        "hysteresis": 3.0,
        "unit": "%",
        "enabled": True,
    },
    "memory": {
        "label": "Memory utilization",
        "warning": 80.0,
        "critical": 92.0,
        "hysteresis": 3.0,
        "unit": "%",
        "enabled": True,
    },
    "gpu": {
        "label": "GPU utilization",
        "warning": 80.0,
        "critical": 95.0,
        "hysteresis": 3.0,
        "unit": "%",
        "enabled": True,
    },
    "disk": {
        "label": "Disk utilization",
        "warning": 80.0,
        "critical": 92.0,
        "hysteresis": 2.0,
        "unit": "%",
        "enabled": True,
    },
    "swap": {
        "label": "Swap utilization",
        "warning": 45.0,
        "critical": 75.0,
        "hysteresis": 3.0,
        "unit": "%",
        "enabled": True,
    },
    "disk_latency": {
        "label": "Average disk latency",
        "warning": 20.0,
        "critical": 50.0,
        "hysteresis": 5.0,
        "unit": "ms",
        "enabled": True,
    },
}


def _iso_timestamp(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(timestamp))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ThresholdEngine:
    """Evaluate resource thresholds and retain transition-only events."""

    def __init__(
        self,
        thresholds: dict[str, dict[str, Any]] | None = None,
        max_events: int = 200,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._thresholds = deepcopy(DEFAULT_THRESHOLDS)
        self._states: dict[str, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=max(20, max_events))
        self._clock = clock
        if thresholds:
            self.update_thresholds(thresholds)

    @property
    def thresholds(self) -> dict[str, dict[str, Any]]:
        return deepcopy(self._thresholds)

    def update_thresholds(
        self,
        updates: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(updates, dict):
            raise ValueError("Threshold updates must be an object.")
        next_values = deepcopy(self._thresholds)
        for resource, values in updates.items():
            if resource not in next_values:
                raise ValueError(f"Unknown threshold resource: {resource}")
            if not isinstance(values, dict):
                raise ValueError(f"Threshold {resource} must be an object.")
            current = next_values[resource]
            warning = _optional_float(values.get("warning", current["warning"]))
            critical = _optional_float(values.get("critical", current["critical"]))
            if warning is None or critical is None:
                raise ValueError(f"Threshold {resource} values must be numeric.")
            if not 0 <= warning < critical <= 100:
                raise ValueError(
                    f"Threshold {resource} must satisfy 0 <= warning < critical <= 100."
                )
            current["warning"] = warning
            current["critical"] = critical
            if "enabled" in values:
                if not isinstance(values["enabled"], bool):
                    raise ValueError(f"Threshold {resource} enabled must be boolean.")
                current["enabled"] = values["enabled"]
        self._thresholds = next_values
        return self.thresholds

    def _observations(self, system_metrics: dict[str, Any]) -> dict[str, float | None]:
        gpu = system_metrics.get("gpu") or {}
        disk_io = system_metrics.get("disk_io") or {}
        disks = system_metrics.get("disks") or []
        disk_values = [
            value
            for value in (_optional_float(disk.get("percent")) for disk in disks)
            if value is not None
        ]
        return {
            "cpu": _optional_float((system_metrics.get("cpu") or {}).get("percent")),
            "memory": _optional_float((system_metrics.get("memory") or {}).get("percent")),
            "gpu": (
                _optional_float(gpu.get("utilization_percent"))
                if gpu.get("available")
                else None
            ),
            "disk": max(disk_values) if disk_values else None,
            "swap": _optional_float((system_metrics.get("swap") or {}).get("percent")),
            "disk_latency": max(
                _optional_float(disk_io.get("read_latency_ms")) or 0.0,
                _optional_float(disk_io.get("write_latency_ms")) or 0.0,
            ),
        }

    @staticmethod
    def _severity(value: float, rule: dict[str, Any], previous: str) -> str:
        warning = float(rule["warning"])
        critical = float(rule["critical"])
        hysteresis = max(0.0, float(rule.get("hysteresis", 0.0)))
        if previous == "critical" and value >= critical - hysteresis:
            return "critical"
        if value >= critical:
            return "critical"
        if previous == "warning" and value >= warning - hysteresis:
            return "warning"
        if value >= warning:
            return "warning"
        return "normal"

    def _transition_event(
        self,
        resource: str,
        rule: dict[str, Any],
        value: float | None,
        previous: str,
        current: str,
        timestamp: float,
        reason: str = "threshold",
    ) -> dict[str, Any]:
        if previous == "normal" and current != "normal":
            action = "opened"
        elif previous == "warning" and current == "critical":
            action = "escalated"
        elif previous == "critical" and current == "warning":
            action = "deescalated"
        else:
            action = "resolved"
        severity = current if current != "normal" else previous
        threshold = (
            float(rule[current])
            if current in {"warning", "critical"}
            else float(rule[previous]) if previous in {"warning", "critical"} else None
        )
        if action == "resolved" and reason == "unavailable":
            message = f"{rule['label']} is unavailable; the active alert was closed"
        elif action == "resolved":
            message = f"{rule['label']} recovered to {value:.1f}{rule['unit']}"
        elif action == "deescalated":
            message = f"{rule['label']} de-escalated from critical to warning at {value:.1f}{rule['unit']}"
        elif action == "escalated":
            message = f"{rule['label']} escalated to critical at {value:.1f}{rule['unit']}"
        else:
            message = f"{rule['label']} crossed the {current} threshold at {value:.1f}{rule['unit']}"
        return {
            "id": uuid.uuid4().hex,
            "timestamp": timestamp,
            "timestamp_iso": _iso_timestamp(timestamp),
            "resource": resource,
            "label": rule["label"],
            "action": action,
            "severity": severity,
            "previous_severity": previous,
            "current_severity": current,
            "value": value,
            "unit": rule["unit"],
            "threshold": threshold,
            "message": message,
        }

    def evaluate(
        self,
        system_metrics: dict[str, Any],
        timestamp: float | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate one sample and return newly-created transition events."""
        now = self._clock() if timestamp is None else timestamp
        observations = self._observations(system_metrics)
        transitions: list[dict[str, Any]] = []
        for resource, rule in self._thresholds.items():
            previous_state = self._states.get(resource, {})
            previous = str(previous_state.get("severity") or "normal")
            value = observations.get(resource)
            if not rule.get("enabled", True):
                value = None
            if value is None:
                if previous != "normal":
                    event = self._transition_event(
                        resource,
                        rule,
                        None,
                        previous,
                        "normal",
                        now,
                        reason="unavailable",
                    )
                    self._events.appendleft(event)
                    transitions.append(event)
                self._states.pop(resource, None)
                continue

            current = self._severity(value, rule, previous)
            if current != previous:
                event = self._transition_event(
                    resource,
                    rule,
                    value,
                    previous,
                    current,
                    now,
                )
                self._events.appendleft(event)
                transitions.append(event)
            if current == "normal":
                self._states.pop(resource, None)
            else:
                since = (
                    previous_state.get("since", now)
                    if previous == current
                    else now
                )
                self._states[resource] = {
                    "resource": resource,
                    "label": rule["label"],
                    "severity": current,
                    "value": value,
                    "unit": rule["unit"],
                    "threshold": float(rule[current]),
                    "since": since,
                    "since_iso": _iso_timestamp(since),
                    "updated_at": now,
                }
        return transitions

    def snapshot(self, event_limit: int = 80) -> dict[str, Any]:
        active = sorted(
            (deepcopy(value) for value in self._states.values()),
            key=lambda item: (
                0 if item["severity"] == "critical" else 1,
                -item["value"],
                item["resource"],
            ),
        )
        events = [deepcopy(event) for event in list(self._events)[: max(1, event_limit)]]
        return {
            "summary": {
                "active": len(active),
                "critical": sum(item["severity"] == "critical" for item in active),
                "warning": sum(item["severity"] == "warning" for item in active),
                "events_in_memory": len(self._events),
            },
            "thresholds": self.thresholds,
            "active": active,
            "events": events,
        }
