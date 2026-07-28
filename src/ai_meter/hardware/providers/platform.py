from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ai_meter.collectors.system import SystemCollector
from ai_meter.hardware.cpu_identity import CpuIdentity, detect_cpu_identity
from ai_meter.hardware.models import HardwareMetric, HardwareProviderStatus
from ai_meter.hardware.providers.base import HardwareProvider


class PlatformSensorProvider(HardwareProvider):
    """Safe platform tools already supported by SystemCollector."""

    name = "platform"

    def __init__(
        self,
        system_collector: SystemCollector,
        cpu_identity: CpuIdentity | None = None,
    ) -> None:
        self._system = system_collector
        self._cpu_identity = cpu_identity or detect_cpu_identity()
        self._last_detail = "not_collected"
        self._last_error: str | None = None
        self._metric_count = 0
        self._sources: list[str] = []
        self._cpu_source = "unknown"

    def probe(self) -> HardwareProviderStatus:
        return HardwareProviderStatus(
            name=self.name,
            available=True,
            source="platform_tools",
            detail=self._last_detail,
            last_error=self._last_error,
            metric_count=self._metric_count,
            metadata={
                "sources": list(self._sources),
                "cpu_source": self._cpu_source,
                "cpu_vendor": self._cpu_identity.vendor,
                "cpu_vendor_id": self._cpu_identity.vendor_id,
                "cpu_name": self._cpu_identity.name,
            },
        )

    def read(self) -> list[HardwareMetric]:
        now = datetime.now(timezone.utc)
        metrics: list[HardwareMetric] = []
        sources: list[str] = []

        snapshot = self._system.read_temperature_snapshot()
        cpu_temp = snapshot.get("cpu_temp_c")
        cpu_source = str(snapshot.get("cpu_source") or "unknown")
        sources.append(cpu_source)
        if cpu_temp is not None:
            metrics.append(
                HardwareMetric(
                    id="cpu:0.temperature",
                    device_id="cpu:0",
                    device_type="cpu",
                    kind="temperature",
                    label="CPU",
                    value=float(cpu_temp),
                    unit="C",
                    source=cpu_source,
                    provider=self.name,
                    timestamp=now,
                    stale_after_s=15.0,
                )
            )

        gpu_source = str(snapshot.get("gpu_source") or "unknown")
        gpu_readings = snapshot.get("gpu_readings")
        if not isinstance(gpu_readings, list):
            gpu_readings = []
        if gpu_source:
            sources.append(gpu_source)
        for index, reading in enumerate(gpu_readings):
            metric = self._gpu_metric(reading, index, now)
            if metric is not None:
                metrics.append(metric)
                sources.append(metric.source)

        self._sources = list(dict.fromkeys(sources))
        self._cpu_source = cpu_source
        self._metric_count = len(metrics)
        self._last_error = None
        self._last_detail = self._detail(cpu_source, metrics)
        return metrics

    @staticmethod
    def _gpu_metric(
        reading: dict[str, Any],
        index: int,
        timestamp: datetime,
    ) -> HardwareMetric | None:
        value = reading.get("temp_c")
        if not isinstance(value, (int, float)) or not 5.0 <= float(value) <= 130.0:
            return None
        name = str(reading.get("name") or f"GPU {index}")
        source = str(reading.get("source") or "platform")
        return HardwareMetric(
            id=f"gpu:{index}.temperature",
            device_id=f"gpu:{index}",
            device_type="gpu",
            kind="temperature",
            label=name,
            value=float(value),
            unit="C",
            source=source,
            provider=PlatformSensorProvider.name,
            timestamp=timestamp,
            stale_after_s=15.0,
        )

    @staticmethod
    def _detail(cpu_source: str, metrics: list[HardwareMetric]) -> str:
        if metrics:
            return "active"
        if cpu_source == "wmi:admin_required":
            return "cpu_wmi_admin_required"
        return "no_supported_sensor_data"
