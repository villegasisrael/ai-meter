from __future__ import annotations

import re
from datetime import datetime, timezone
from threading import RLock

from ai_meter.hardware.models import HardwareMetric, HardwareProviderStatus
from ai_meter.hardware.providers.base import HardwareProvider


class HardwareRegistry:
    """Collect providers, select sources and retain only fresh observations."""

    def __init__(
        self,
        providers: list[HardwareProvider],
        source_priority: list[str] | None = None,
    ) -> None:
        self._providers = providers
        self._source_priority = [
            _normalize_source(source) for source in (source_priority or [])
        ]
        self._metrics: dict[str, HardwareMetric] = {}
        self._statuses: dict[str, HardwareProviderStatus] = {}
        self._lock = RLock()

    def collect(self) -> list[HardwareMetric]:
        observations: list[HardwareMetric] = []
        statuses: dict[str, HardwareProviderStatus] = {}

        for provider in self._providers:
            initial = provider.probe()
            metrics: list[HardwareMetric] = []
            if initial.available:
                try:
                    metrics = provider.read()
                except Exception as exc:
                    initial.last_error = exc.__class__.__name__
            current = provider.probe()
            current.metric_count = len(metrics)
            if initial.last_error and not current.last_error:
                current.last_error = initial.last_error
            statuses[provider.name] = current
            observations.extend(metrics)

        selected: dict[str, HardwareMetric] = {}
        for metric in observations:
            current = selected.get(metric.id)
            if current is None or self._rank(metric) < self._rank(current):
                selected[metric.id] = metric

        with self._lock:
            self._metrics.update(selected)
            self._statuses = statuses
            return self._fresh_metrics_locked()

    def snapshot(self) -> list[HardwareMetric]:
        with self._lock:
            return self._fresh_metrics_locked()

    def status_snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                name: status.model_dump(mode="json")
                for name, status in self._statuses.items()
            }

    def primary_temperature(self, device_type: str) -> HardwareMetric | None:
        candidates = [
            metric
            for metric in self.snapshot()
            if metric.device_type == device_type and metric.kind == "temperature"
        ]
        return candidates[0] if candidates else None

    def _fresh_metrics_locked(self) -> list[HardwareMetric]:
        now = datetime.now(timezone.utc)
        fresh: list[HardwareMetric] = []
        expired: list[str] = []
        for metric_id, metric in self._metrics.items():
            age = (now - metric.timestamp).total_seconds()
            if age > max(0.1, metric.stale_after_s):
                expired.append(metric_id)
                continue
            fresh.append(metric)
        for metric_id in expired:
            self._metrics.pop(metric_id, None)
        return sorted(fresh, key=lambda metric: (metric.device_type, metric.device_id, metric.kind))

    def _rank(self, metric: HardwareMetric) -> int:
        values = (_normalize_source(metric.provider), _normalize_source(metric.source))
        for index, expected in enumerate(self._source_priority):
            if any(value == expected or value.startswith(expected) for value in values):
                return index
        return len(self._source_priority)


def _normalize_source(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
