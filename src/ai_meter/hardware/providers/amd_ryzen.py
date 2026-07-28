from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_meter.hardware.models import HardwareMetric, HardwareProviderStatus
from ai_meter.hardware.providers.base import HardwareProvider

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only module
    winreg = None  # type: ignore[assignment]


_FLAT_METRICS: dict[str, tuple[str, str, str]] = {
    "cpu_temp_c": ("temperature", "CPU Temperature", "C"),
    "cpu_temperature_c": ("temperature", "CPU Temperature", "C"),
    "cpu_power_w": ("power", "CPU Power", "W"),
    "cpu_voltage_v": ("voltage", "CPU Voltage", "V"),
    "cpu_frequency_mhz": ("frequency", "CPU Frequency", "MHz"),
    "ppt_percent": ("utilization", "PPT", "%"),
    "tdc_percent": ("utilization", "TDC", "%"),
    "edc_percent": ("utilization", "EDC", "%"),
}


class AmdRyzenMasterProvider(HardwareProvider):
    """Read-only adapter for the ai-meter AMD SDK sidecar.

    ai-meter does not install the AMD SDK or a kernel driver. The executable
    implements ``--once`` and emits one JSON object.
    """

    name = "amd_ryzen_master"

    def __init__(
        self,
        executable: str | Path | None = None,
    ) -> None:
        self._configured_path = Path(executable) if executable else None
        self._executable = self._find_executable()
        self._last_error: str | None = None
        self._metric_count = 0

    def probe(self) -> HardwareProviderStatus:
        sdk_root = _amd_sdk_root()
        elevated = _is_elevated()
        available = self._executable is not None and elevated
        if self._executable is None:
            detail = "probe_not_built" if sdk_root is not None else "sdk_not_installed"
        elif not elevated:
            detail = "admin_required"
        elif self._last_error:
            detail = self._last_error
        else:
            detail = "ready"
        return HardwareProviderStatus(
            name=self.name,
            available=available,
            source="amd_ryzen_master_sdk",
            detail=detail,
            last_error=self._last_error,
            metric_count=self._metric_count,
            metadata={
                "path": str(self._executable) if self._executable is not None else None,
                "env": "AI_METER_AMD_PROBE",
                "sdk_path": str(sdk_root) if sdk_root is not None else None,
                "elevated": elevated,
            },
        )

    def read(self) -> list[HardwareMetric]:
        if self._executable is None:
            return []
        if not _is_elevated():
            self._last_error = "admin_required"
            self._metric_count = 0
            return []
        try:
            completed = subprocess.run(
                [str(self._executable), "--once"],
                capture_output=True,
                text=True,
                timeout=4.0,
                check=False,
                startupinfo=_hidden_startupinfo(),
            )
        except Exception as exc:
            self._last_error = exc.__class__.__name__
            self._metric_count = 0
            return []
        if completed.returncode != 0:
            stderr = " ".join((completed.stderr or "").strip().split())
            suffix = f":{stderr[:120]}" if stderr else ""
            self._last_error = f"exit_{completed.returncode}{suffix}"
            self._metric_count = 0
            return []
        try:
            payload = json.loads((completed.stdout or "").strip())
        except json.JSONDecodeError:
            self._last_error = "invalid_json"
            self._metric_count = 0
            return []
        if not isinstance(payload, dict):
            self._last_error = "invalid_payload"
            self._metric_count = 0
            return []

        return self._consume_payload(payload)

    def _consume_payload(self, payload: dict[str, Any]) -> list[HardwareMetric]:
        payload_error = str(payload.get("error") or "").strip()
        if payload_error:
            self._last_error = payload_error
            self._metric_count = 0
            return []
        metrics = self._parse_payload(payload)
        self._metric_count = len(metrics)
        self._last_error = None if metrics else "no_metrics"
        return metrics

    def _find_executable(self) -> Path | None:
        candidates: list[Path] = []
        env_path = os.environ.get("AI_METER_AMD_PROBE", "").strip()
        if self._configured_path is not None:
            candidates.append(self._configured_path)
        if env_path:
            candidates.append(Path(env_path))
        bundled = _find_bundled_probe()
        if bundled is not None:
            candidates.append(bundled)
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def _parse_payload(self, payload: dict[str, Any]) -> list[HardwareMetric]:
        timestamp = datetime.now(timezone.utc)
        raw_metrics = payload.get("metrics")
        if isinstance(raw_metrics, list):
            parsed = [
                metric
                for item in raw_metrics
                if isinstance(item, dict)
                for metric in [self._parse_metric_item(item, timestamp)]
                if metric is not None
            ]
            if parsed:
                return parsed

        metrics: list[HardwareMetric] = []
        for key, (kind, label, unit) in _FLAT_METRICS.items():
            value = _finite_number(payload.get(key))
            if value is None:
                continue
            metric_id = "cpu:0.temperature" if kind == "temperature" else f"cpu:0.{kind}.{key}"
            metrics.append(
                HardwareMetric(
                    id=metric_id,
                    device_id="cpu:0",
                    device_type="cpu",
                    kind=kind,
                    label=label,
                    value=value,
                    unit=unit,
                    source="amd_ryzen_master_sdk",
                    provider=self.name,
                    timestamp=timestamp,
                    stale_after_s=3.0,
                )
            )
        return metrics

    def _parse_metric_item(
        self,
        item: dict[str, Any],
        timestamp: datetime,
    ) -> HardwareMetric | None:
        value = _finite_number(item.get("value"))
        kind = str(item.get("kind") or "").strip().lower()
        if value is None or not kind:
            return None
        if kind == "temperature" and not 5.0 <= value <= 130.0:
            return None
        device_id = str(item.get("device_id") or "cpu:0")
        device_type = str(item.get("device_type") or "cpu")
        metric_id = str(item.get("id") or f"{device_id}.{kind}")
        return HardwareMetric(
            id=metric_id,
            device_id=device_id,
            device_type=device_type,
            kind=kind,
            label=str(item.get("label") or kind.replace("_", " ").title()),
            value=value,
            unit=str(item.get("unit") or ""),
            source="amd_ryzen_master_sdk",
            provider=self.name,
            timestamp=timestamp,
            stale_after_s=max(1.0, float(item.get("stale_after_s") or 3.0)),
            writable=False,
        )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo


def _find_bundled_probe() -> Path | None:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    module_root = Path(__file__).resolve().parents[2]
    roots.extend([module_root, module_root.parent, Path.cwd()])
    relative_paths = (
        Path("bin") / "win-x64" / "ai-meter-amd-probe.exe",
        Path("ai_meter") / "bin" / "win-x64" / "ai-meter-amd-probe.exe",
        Path("src") / "ai_meter" / "bin" / "win-x64" / "ai-meter-amd-probe.exe",
        Path("ai-meter-amd-probe.exe"),
    )
    for root in roots:
        for relative_path in relative_paths:
            candidate = root / relative_path
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


def _amd_sdk_root() -> Path | None:
    env_path = os.environ.get("AMDRMMONITORSDKPATH", "").strip()
    candidates = [Path(env_path)] if env_path else []
    if os.name == "nt" and winreg is not None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\AMD\RyzenMasterMonitoringSDK",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "InstallationPath")
                if value:
                    candidates.append(Path(str(value)))
        except OSError:
            pass
    candidates.append(Path(r"C:\Program Files\AMD\RyzenMasterMonitoringSDK"))
    for candidate in candidates:
        try:
            if (candidate / "bin" / "Platform.dll").is_file():
                return candidate
        except OSError:
            continue
    return None


def _is_elevated() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False
