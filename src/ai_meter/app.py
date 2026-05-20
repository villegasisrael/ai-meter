from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from ai_meter.collectors.base import CollectBatch
from ai_meter.collectors.claude import ClaudeCollector
from ai_meter.collectors.claude_api_usage import ClaudeApiUsageCollector, ParsedUsage
from ai_meter.collectors.codex import CodexCollector
from ai_meter.collectors.system import SystemCollector
from ai_meter.config import AppConfig
from ai_meter.paths import AppPaths
from ai_meter.security import redact_sensitive
from ai_meter.storage.db import Database


@dataclass
class DashboardSnapshot:
    timestamp: datetime
    provider_rows: list[dict[str, Any]]
    provider_meta: dict[str, dict[str, Any]]
    recent_events: list[dict[str, Any]]
    claude_usage: list[dict[str, Any]]
    codex_usage: list[dict[str, Any]]
    system_meta: dict[str, Any]
    claude_api_usage: ParsedUsage | None = None
    cpu_history: list[float] = field(default_factory=list)


class MonitorEngine:
    def __init__(
        self,
        config: AppConfig,
        paths: AppPaths,
        offset_store: Any,
        db: Database | None = None,
    ) -> None:
        self.config = config
        self.paths = paths
        self.offset_store = offset_store
        self.db = db

        self.codex_collector = (
            CodexCollector(paths, offset_store)
            if self.config.providers.codex.enabled
            else None
        )
        self.claude_collector = (
            ClaudeCollector(paths, offset_store)
            if self.config.providers.claude.enabled
            else None
        )
        self.system_collector = SystemCollector(
            winprobe_interval_ms=self.config.app.winprobe_interval_ms,
            sensor_probe_enabled=self.config.app.sensor_probe_enabled,
        )
        self.claude_api = (
            ClaudeApiUsageCollector.from_env_file(
                cache_seconds=self.config.providers.claude.usage_api_interval_s
            )
            if self.config.providers.claude.enabled
            and self.config.providers.claude.usage_api_enabled
            else None
        )
        self._lock = RLock()
        self._claude_api_usage: ParsedUsage | None = None

        self._provider_rows: dict[str, dict[str, Any]] = {}
        self._provider_meta: dict[str, dict[str, Any]] = {}
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=self.config.ui.max_events)
        self._usage_rows: dict[str, deque[dict[str, Any]]] = {
            "claude": deque(maxlen=self.config.ui.sparkline_points),
            "codex": deque(maxlen=self.config.ui.sparkline_points),
        }
        self._cpu_history: deque[float] = deque(maxlen=600)

    def provider_enabled(self, provider: str) -> bool:
        section = getattr(self.config.providers, provider, None)
        return bool(getattr(section, "enabled", False))

    @property
    def enabled_ai_providers(self) -> list[str]:
        return [name for name in ("claude", "codex") if self.provider_enabled(name)]

    def run_claude_api_collection(self) -> None:
        """Fetch Claude usage limits from OAuth API when explicitly enabled."""
        if self.claude_api is None:
            return
        try:
            result = self.claude_api.fetch_if_due()
            if result is not None:
                with self._lock:
                    self._claude_api_usage = result
        except Exception:
            pass

    def run_light_collection(self) -> None:
        try:
            batch = self.system_collector.collect(include_temp=False, include_top=False)
            self._ingest_batch(batch)
        except Exception as exc:
            self._persist_error("system", str(exc))

    def run_heavy_collection(self) -> None:
        try:
            system_batch = self.system_collector.collect(include_temp=True, include_top=True)
            self._ingest_batch(system_batch)
        except Exception as exc:
            self._persist_error("system", str(exc))
        collectors = [self.codex_collector, self.claude_collector]
        for collector in [c for c in collectors if c is not None]:
            try:
                batch = collector.collect()
                self._ingest_batch(batch)
            except Exception as exc:
                self._persist_error(collector.name, str(exc))

    def run_full_collection(self) -> None:
        self.run_light_collection()
        self.run_heavy_collection()

    def _ingest_batch(self, batch: CollectBatch) -> None:
        with self._lock:
            if batch.provider_status is not None:
                provider_dict = batch.provider_status.model_dump(mode="json")
                name = str(provider_dict["name"])
                self._provider_rows[name] = provider_dict
                self._provider_meta[name] = redact_sensitive(dict(batch.provider_status.metadata))
                if name == "system":
                    cpu = batch.provider_status.metadata.get("cpu_percent")
                    if cpu is not None:
                        try:
                            self._cpu_history.append(float(cpu))
                        except (TypeError, ValueError):
                            pass

            for event in batch.events:
                event_dict = event.model_dump(mode="json")
                self._recent_events.appendleft(event_dict)

            for usage in batch.usage_samples:
                usage_dict = usage.model_dump(mode="json")
                bucket = self._usage_rows.get(usage.provider)
                if bucket is not None:
                    bucket.appendleft(usage_dict)

            if self.db is not None:
                self.db.ingest_batch(
                    provider_status=batch.provider_status,
                    events=batch.events,
                    usage_samples=batch.usage_samples,
                )

    def _persist_error(self, provider: str, error: str) -> None:
        from ai_meter.models import EventRecord

        error_event = EventRecord(
            provider=provider,
            timestamp=datetime.now(timezone.utc),
            event_type="collector_error",
            severity="error",
            title="Collector error",
            message=error[:240],
            accuracy="real",
            source="local",
            metadata={"provider": provider},
        )
        with self._lock:
            self._recent_events.appendleft(error_event.model_dump(mode="json"))
            if self.db is not None:
                self.db.add_event(error_event)

    def snapshot(self) -> DashboardSnapshot:
        with self._lock:
            system_meta = self._provider_meta.get("system", {})
            return DashboardSnapshot(
                timestamp=datetime.now(timezone.utc),
                provider_rows=list(self._provider_rows.values()),
                provider_meta=dict(self._provider_meta),
                recent_events=list(self._recent_events),
                claude_usage=list(self._usage_rows["claude"]),
                codex_usage=list(self._usage_rows["codex"]),
                system_meta=dict(system_meta),
                claude_api_usage=self._claude_api_usage,
                cpu_history=list(self._cpu_history),
            )
