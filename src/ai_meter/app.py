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
    cpu_avg: float | None = None
    cpu_peak: float | None = None


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
            ClaudeApiUsageCollector.from_sources(
                claude_home=self.paths.claude_home,
                cache_seconds=self.config.providers.claude.usage_api_interval_s,
                cache_path=getattr(self.paths, "data_dir", None) and self.paths.data_dir / "claude_usage_cache.json",
            )
            if self.config.providers.claude.enabled
            and self.config.providers.claude.usage_api_enabled
            else None
        )
        self._lock = RLock()
        self._claude_api_usage: ParsedUsage | None = (
            self.claude_api.current() if self.claude_api is not None else None
        )
        # Tracks which "<scope>:<limit>" thresholds have already fired an alert
        # so we emit one event per crossing instead of every refresh.
        self._alerted_limits: set[str] = set()

        self._provider_rows: dict[str, dict[str, Any]] = {}
        self._provider_meta: dict[str, dict[str, Any]] = {}
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=self.config.ui.max_events)
        self._usage_rows: dict[str, deque[dict[str, Any]]] = {
            "claude": deque(maxlen=self.config.ui.sparkline_points),
            "codex": deque(maxlen=self.config.ui.sparkline_points),
        }
        self._cpu_history: deque[float] = deque(maxlen=600)
        # Running stats over the whole session (not bounded by the deque).
        self._cpu_sum: float = 0.0
        self._cpu_count: int = 0
        self._cpu_peak: float = 0.0

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
                if not self.claude_api.is_stale:
                    self._check_claude_api_alerts(result)
        except Exception:
            pass

    def reload_claude_api(self) -> bool:
        """Rebuild the Claude usage collector from disk (e.g. after a re-login).

        Returns True if a collector was (re)created. Used by the TUI to pick up
        fresh credentials immediately instead of waiting for the mtime watch.
        """
        if not (
            self.config.providers.claude.enabled
            and self.config.providers.claude.usage_api_enabled
        ):
            return False
        collector = ClaudeApiUsageCollector.from_sources(
            claude_home=self.paths.claude_home,
            cache_seconds=self.config.providers.claude.usage_api_interval_s,
            cache_path=getattr(self.paths, "data_dir", None)
            and self.paths.data_dir / "claude_usage_cache.json",
        )
        with self._lock:
            self.claude_api = collector
            if collector is not None:
                self._claude_api_usage = collector.current() or self._claude_api_usage
        return collector is not None

    def _check_claude_api_alerts(self, usage: ParsedUsage) -> None:
        threshold = float(self.config.app.alert_threshold_pct)
        if not self.config.app.alert_enabled or threshold <= 0:
            return
        checks: list[tuple[str, float | None, int | None]] = [
            ("5h", usage.five_hour_pct, usage.five_hour_reset_secs),
            ("weekly", usage.seven_day_pct, usage.seven_day_reset_secs),
        ]
        for raw in usage.limits or []:
            if isinstance(raw, dict) and raw.get("pct") is not None:
                checks.append((str(raw.get("name") or "limit"), raw.get("pct"), raw.get("reset_secs")))
        for name, pct, reset_secs in checks:
            self._evaluate_threshold("claude", name, pct, threshold, reset_secs)

    def _evaluate_threshold(
        self,
        provider: str,
        scope: str,
        pct: Any,
        threshold: float,
        reset_secs: int | None = None,
    ) -> None:
        try:
            value = float(pct)
        except (TypeError, ValueError):
            return
        key = f"{provider}:{scope}"
        if value >= threshold:
            if key in self._alerted_limits:
                return
            self._alerted_limits.add(key)
            reset_txt = f" (resets in ~{reset_secs // 60}m)" if reset_secs else ""
            self._emit_alert(
                provider,
                title=f"{scope} usage at {value:.0f}%",
                message=f"{provider} {scope} limit reached {value:.0f}% (>= {threshold:.0f}%){reset_txt}",
            )
        elif value < threshold:
            # Re-arm once usage drops back below the threshold.
            self._alerted_limits.discard(key)

    def _emit_alert(self, provider: str, title: str, message: str) -> None:
        from ai_meter.models import EventRecord

        event = EventRecord(
            provider=provider,
            timestamp=datetime.now(timezone.utc),
            event_type="usage_alert",
            severity="warning",
            title=title,
            message=message,
            accuracy="real",
            source="local",
            metadata={"provider": provider, "kind": "threshold"},
        )
        with self._lock:
            self._recent_events.appendleft(event.model_dump(mode="json"))
            if self.db is not None:
                self.db.add_event(event)

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
                            cpu_val = float(cpu)
                        except (TypeError, ValueError):
                            cpu_val = None
                        if cpu_val is not None:
                            self._cpu_history.append(cpu_val)
                            self._cpu_sum += cpu_val
                            self._cpu_count += 1
                            if cpu_val > self._cpu_peak:
                                self._cpu_peak = cpu_val
                elif name == "codex":
                    self._check_codex_alerts(batch.provider_status.metadata)

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

    def _check_codex_alerts(self, metadata: dict[str, Any]) -> None:
        threshold = float(self.config.app.alert_threshold_pct)
        if not self.config.app.alert_enabled or threshold <= 0:
            return
        rate_limits = metadata.get("rate_limits") if isinstance(metadata, dict) else None
        if not isinstance(rate_limits, dict):
            return
        for key, scope in (("primary", "5h"), ("secondary", "weekly")):
            window = rate_limits.get(key)
            if isinstance(window, dict):
                self._evaluate_threshold(
                    "codex",
                    scope,
                    window.get("used_percent"),
                    threshold,
                    window.get("reset_after_seconds"),
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
                cpu_avg=(self._cpu_sum / self._cpu_count) if self._cpu_count else None,
                cpu_peak=self._cpu_peak if self._cpu_count else None,
            )
