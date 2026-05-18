from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_meter.collectors.base import CollectBatch, Collector
from ai_meter.models import ProviderStatus, SystemSnapshot

try:
    import psutil
except ModuleNotFoundError:  # pragma: no cover
    psutil = None  # type: ignore

# Legacy output path from the disabled LibreHardwareMonitor sensor task.
_SERVICE_FILE = (
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ai-meter" / "sensor.json"
)
_SERVICE_FILE_MAX_AGE_S = 30.0  # ignore if older than this
_UNSAFE_SENSOR_PROBE_ENV = "AI_METER_ALLOW_VULNERABLE_SENSOR_PROBE"


class SystemCollector(Collector):
    name = "system"

    def __init__(
        self,
        winprobe_interval_ms: int = 250,
        sensor_probe_enabled: bool = False,
    ) -> None:
        self._native_sampler = NativeWinProbeSampler(winprobe_interval_ms)
        self._sensor_probe_enabled = bool(sensor_probe_enabled)
        self._cpu_name: str = self._get_cpu_name()
        self._cached_cpu_freq_mhz: float | None = None
        self._last_cpu_freq_read_at = 0.0
        self._last_probe_read_at = 0.0
        self._cached_cpu_temp_c: float | None = None
        self._cached_cpu_temp_source: str = "unknown"
        self._cached_gpu_temp_c: float | None = None
        self._cached_gpu_name: str | None = None
        self._cached_core_loads: list[dict[str, Any]] = []
        self._cached_needs_admin: bool = False
        self._last_temp_probe_meta: dict[str, Any] = {}
        self._last_top_read_at = 0.0
        self._cached_top_processes: list[dict[str, Any]] = []
        self._prev_cpu_times_per_core: list[Any] | None = None

    def probe(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            enabled=psutil is not None,
            status="active" if psutil is not None else "unknown",
            source="psutil" if psutil is not None else "unknown",
            last_seen_at=datetime.now(timezone.utc),
            metadata={"available": psutil is not None},
        )

    def collect(
        self,
        include_temp: bool = True,
        include_top: bool = True,
        include_core_loads: bool = True,
    ) -> CollectBatch:
        batch = CollectBatch(provider_status=self.probe())
        if psutil is None:
            return batch

        native = self._native_sampler.snapshot()
        if native is None and include_temp:
            time.sleep(1.0)
            native = self._native_sampler.snapshot()
        if native is not None:
            cpu_percent = float(native.get("cpu_percent", 0.0))
            ram_percent = float(native.get("ram_percent", 0.0))
            ram_used_mb = float(native.get("ram_used_mb", 0.0))
            ram_total_mb = float(native.get("ram_total_mb", 0.0))
            disk_percent = float(native.get("disk_percent", 0.0))
            net_sent_mb = float(native.get("net_sent_mb", 0.0))
            net_recv_mb = float(native.get("net_recv_mb", 0.0))
            metrics_source = str(native.get("source", "winprobe_cpp"))
        else:
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()
            cpu_percent = float(psutil.cpu_percent(interval=0.0))
            ram_percent = float(vm.percent)
            ram_used_mb = round(vm.used / (1024 * 1024), 1)
            ram_total_mb = round(vm.total / (1024 * 1024), 1)
            disk_percent = float(disk.percent)
            net_sent_mb = round(net.bytes_sent / (1024 * 1024), 1)
            net_recv_mb = round(net.bytes_recv / (1024 * 1024), 1)
            metrics_source = "psutil"

        top = self._top_processes() if include_top else self._cached_top_processes

        snapshot = SystemSnapshot(
            timestamp=datetime.now(timezone.utc),
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            ram_used_mb=ram_used_mb,
            ram_total_mb=ram_total_mb,
            disk_percent=disk_percent,
            net_sent_mb=net_sent_mb,
            net_recv_mb=net_recv_mb,
            top_processes=top,
        )
        if include_temp:
            self._refresh_sensor_probe()
        elif include_core_loads:
            self._refresh_core_loads_fast()

        meta: dict[str, Any] = snapshot.model_dump(mode="json")
        meta["metrics_source"] = metrics_source
        meta["native_winprobe"] = self._native_sampler.status()
        meta["sensor_probe_enabled"] = self._sensor_probe_enabled
        meta["sensor_probe_runtime_allowed"] = _unsafe_sensor_probe_allowed()
        meta["cpu_temp_c"] = self._cached_cpu_temp_c
        meta["cpu_temp_source"] = self._cached_cpu_temp_source
        meta["cpu_temp_probe"] = dict(self._last_temp_probe_meta)
        meta["gpu_temp_c"] = self._cached_gpu_temp_c
        meta["gpu_name"] = self._cached_gpu_name
        meta["core_loads"] = list(self._cached_core_loads)
        meta["needs_admin"] = self._cached_needs_admin
        meta["cpu_name"] = self._cpu_name
        meta["cpu_freq_mhz"] = self._cpu_freq_mhz()
        batch.provider_status.metadata = meta
        return batch

    def _cpu_freq_mhz(self) -> float | None:
        if psutil is None:
            return None
        now = time.monotonic()
        if self._cached_cpu_freq_mhz is not None and (now - self._last_cpu_freq_read_at) < 2.0:
            return self._cached_cpu_freq_mhz
        self._last_cpu_freq_read_at = now
        try:
            freq = psutil.cpu_freq()
            if freq is None:
                return self._cached_cpu_freq_mhz
            self._cached_cpu_freq_mhz = round(float(freq.current), 0)
        except Exception:
            return self._cached_cpu_freq_mhz
        return self._cached_cpu_freq_mhz

    def _get_cpu_name(self) -> str:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            name, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            winreg.CloseKey(key)
            name = str(name).strip()
            for suffix in (" with Radeon Graphics", " with Radeon Vega", " APU"):
                name = name.replace(suffix, "")
            return name
        except Exception:
            try:
                import platform
                return platform.processor()[:40]
            except Exception:
                return "CPU"

    def _refresh_core_loads_fast(self) -> None:
        """Refresh per-core loads from cpu_times deltas on the light loop cadence."""
        if psutil is None:
            return
        try:
            current_times = list(psutil.cpu_times(percpu=True))
        except Exception:
            return
        if not current_times:
            return

        previous_times = self._prev_cpu_times_per_core
        self._prev_cpu_times_per_core = current_times
        if previous_times is None or len(previous_times) != len(current_times):
            return

        core_loads: list[dict[str, Any]] = []
        for idx, (prev, cur) in enumerate(zip(previous_times, current_times), start=1):
            try:
                prev_total = float(sum(prev))
                cur_total = float(sum(cur))
                delta_total = cur_total - prev_total
                if delta_total <= 0.0:
                    continue

                prev_idle = float(getattr(prev, "idle", 0.0)) + float(getattr(prev, "iowait", 0.0))
                cur_idle = float(getattr(cur, "idle", 0.0)) + float(getattr(cur, "iowait", 0.0))
                delta_idle = max(0.0, cur_idle - prev_idle)
                value = ((delta_total - delta_idle) / delta_total) * 100.0
            except (TypeError, ValueError):
                continue
            value = max(0.0, min(100.0, value))
            core_loads.append(
                {
                    "name": f"CPU Core #{idx}",
                    "load_percent": value,
                }
            )
        if core_loads:
            self._cached_core_loads = core_loads

    def _top_processes(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._cached_top_processes and (now - self._last_top_read_at) < 3.0:
            return self._cached_top_processes

        if psutil is None:
            return []

        top: list[dict[str, Any]] = []
        for idx, proc in enumerate(psutil.process_iter(["pid", "name", "memory_info"])):
            if idx >= 24:
                break
            try:
                mem = getattr(proc.info.get("memory_info"), "rss", 0) / (1024 * 1024)
                top.append(
                    {
                        "pid": proc.info.get("pid"),
                        "name": proc.info.get("name"),
                        "cpu": 0.0,
                        "mem_mb": round(mem, 1),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        top = sorted(top, key=lambda p: float(p.get("mem_mb") or 0.0), reverse=True)[:8]
        self._cached_top_processes = top
        self._last_top_read_at = now
        return top

    def _refresh_sensor_probe(self) -> None:
        """Read sensor probe and update all cached sensor values. Rate-limited to 10s."""
        now = time.monotonic()
        if now - self._last_probe_read_at < 10.0:
            return
        self._last_probe_read_at = now

        obj: dict[str, Any] | None = None
        if self._sensor_probe_enabled and _unsafe_sensor_probe_allowed():
            # Prefer the service file (written by the legacy background task)
            obj = self._read_service_file() or self._run_sensor_probe()
        else:
            self._last_temp_probe_meta = {
                "available": False,
                "source": "sensor_probe:disabled",
                "sensor_count": 0,
                "needs_admin": False,
                "from_service": False,
                "service_file_present": _SERVICE_FILE.exists(),
                "error": (
                    "unsafe_sensor_probe_env_required"
                    if self._sensor_probe_enabled
                    else "sensor_probe_disabled"
                ),
            }

        if obj is None:
            # Fallback: try WMI for CPU temp only
            wmi_temp, wmi_source = self._read_wmi_temp_fallback()
            if wmi_temp is not None:
                self._cached_cpu_temp_c = wmi_temp
                self._cached_cpu_temp_source = _shorten_source(wmi_source)
            return

        from_service = bool(obj.get("_from_service"))
        self._last_temp_probe_meta = {
            "available": bool(obj.get("available")),
            "source": obj.get("source"),
            "sensor_count": obj.get("sensor_count"),
            "needs_admin": bool(obj.get("needs_admin")),
            "from_service": from_service,
            "error": obj.get("error"),
        }
        self._cached_needs_admin = bool(obj.get("needs_admin"))

        cpu_temp = obj.get("cpu_temp_c")
        if isinstance(cpu_temp, (int, float)) and 5.0 <= float(cpu_temp) <= 130.0:
            self._cached_cpu_temp_c = float(cpu_temp)
            self._cached_cpu_temp_source = _shorten_source(str(obj.get("source") or "lhm"))
        else:
            # CPU temp unavailable from probe — try WMI as fallback
            wmi_temp, wmi_source = self._read_wmi_temp_fallback()
            if wmi_temp is not None:
                self._cached_cpu_temp_c = wmi_temp
                self._cached_cpu_temp_source = _shorten_source(wmi_source)
            else:
                self._cached_cpu_temp_c = None
                self._cached_cpu_temp_source = _shorten_source(str(obj.get("source") or "unknown"))

        gpu_temp = obj.get("gpu_temp_c")
        if isinstance(gpu_temp, (int, float)) and 5.0 <= float(gpu_temp) <= 130.0:
            self._cached_gpu_temp_c = float(gpu_temp)
            self._cached_gpu_name = str(obj.get("gpu_name") or "GPU")
        else:
            self._cached_gpu_temp_c = None
            self._cached_gpu_name = None

        core_loads = obj.get("core_loads")
        if isinstance(core_loads, list) and core_loads:
            self._cached_core_loads = [
                {"name": str(c.get("name", "")), "load_percent": float(c.get("load_percent", 0.0))}
                for c in core_loads
                if isinstance(c, dict)
            ]
        else:
            self._cached_core_loads = []

    def _read_service_file(self) -> dict[str, Any] | None:
        """Read sensor data written by the legacy background scheduled task."""
        try:
            if not _SERVICE_FILE.exists():
                return None
            age = time.time() - _SERVICE_FILE.stat().st_mtime
            if age > _SERVICE_FILE_MAX_AGE_S:
                return None
            obj = json.loads(_SERVICE_FILE.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj["_from_service"] = True
                return obj
        except Exception:
            pass
        return None

    def _run_sensor_probe(self) -> dict[str, Any] | None:
        exe = _find_bundled_tool("ai-meter-sensor-probe.exe")
        if exe is None:
            self._last_temp_probe_meta = {"available": False, "error": "missing"}
            return None
        try:
            out = subprocess.run(
                [str(exe), "--once"],
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
                startupinfo=_hidden_startupinfo(),
            )
            payload = (out.stdout or "").strip()
            if not payload:
                return None
            obj = json.loads(payload)
            return obj if isinstance(obj, dict) else None
        except PermissionError:
            self._last_temp_probe_meta = {"available": False, "error": "permission_denied"}
            return None
        except subprocess.TimeoutExpired:
            self._last_temp_probe_meta = {"available": False, "error": "timeout"}
            return None
        except Exception as exc:
            self._last_temp_probe_meta = {"available": False, "error": exc.__class__.__name__}
            return None

    def _read_wmi_temp_fallback(self) -> tuple[float | None, str]:
        """PowerShell WMI fallback — works when running as admin."""
        script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$result = $null
$namespaces = @('root/LibreHardwareMonitor','root/OpenHardwareMonitor')
foreach ($ns in $namespaces) {
    try {
        $sensor = Get-CimInstance -Namespace $ns -ClassName Sensor |
            Where-Object {
                $_.SensorType -eq 'Temperature' -and
                ($_.Name -match 'Package|Core|Tctl|Tdie') -and
                ($_.Identifier -match 'cpu|k10|core')
            } |
            Sort-Object Value -Descending |
            Select-Object -First 1
        if ($sensor) {
            $result = [PSCustomObject]@{ temp_c = [double]$sensor.Value; source = 'wmi:lhm' }
            break
        }
    } catch {}
}
if (-not $result) {
    try {
        $zone = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature |
            Select-Object -First 1
        if ($zone) {
            $c = ([double]$zone.CurrentTemperature / 10.0) - 273.15
            if ($c -ge 5 -and $c -le 130) {
                $result = [PSCustomObject]@{ temp_c = [double]::Parse($c.ToString('F1')); source = 'wmi:acpi' }
            }
        }
    } catch {}
}
if ($result) { $result | ConvertTo-Json -Compress } else { '' }
"""
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            payload = (out.stdout or "").strip()
            if not payload:
                return None, "wmi:no_data"
            obj = json.loads(payload)
            temp = obj.get("temp_c")
            source = str(obj.get("source") or "wmi")
            if isinstance(temp, (int, float)) and 5.0 <= float(temp) <= 130.0:
                return float(temp), source
            return None, source
        except Exception:
            return None, "wmi:failed"


class NativeWinProbeSampler:
    def __init__(self, stream_interval_ms: int = 250) -> None:
        self._exe = _find_bundled_tool("ai-meter-winprobe.exe")
        self._stream_interval_ms = max(100, min(5000, int(stream_interval_ms)))
        self._latest: dict[str, Any] | None = None
        self._error: str | None = None
        self._started = False
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any] | None:
        if os.name != "nt" or self._exe is None:
            return None
        self._start()
        with self._lock:
            return dict(self._latest) if self._latest is not None else None

    def status(self) -> dict[str, Any]:
        return {
            "available": self._exe is not None,
            "path": str(self._exe) if self._exe is not None else None,
            "started": self._started,
            "stream_interval_ms": self._stream_interval_ms,
            "error": self._error,
        }

    def _start(self) -> None:
        if self._started:
            return
        self._started = True
        thread = threading.Thread(target=self._reader, daemon=True)
        thread.start()

    def _reader(self) -> None:
        if self._exe is None:
            return
        try:
            proc = subprocess.Popen(
                [str(self._exe), "--stream", str(self._stream_interval_ms)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                startupinfo=_hidden_startupinfo(),
            )
        except PermissionError:
            self._error = "permission_denied"
            return
        except Exception as exc:
            self._error = exc.__class__.__name__
            return

        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                with self._lock:
                    self._latest = obj


def _shorten_source(source: str) -> str:
    """Shorten long source strings for compact display."""
    replacements = {
        "LibreHardwareMonitorLib:": "lhm:",
        "root/wmi:MSAcpi_ThermalZoneTemperature": "wmi:acpi",
        "root/LibreHardwareMonitor": "wmi:lhm",
        "root/OpenHardwareMonitor": "wmi:ohm",
        "sensor_probe_missing": "probe:missing",
        "sensor_probe:": "probe:",
    }
    for long, short in replacements.items():
        source = source.replace(long, short)
    return source


def _unsafe_sensor_probe_allowed() -> bool:
    value = os.environ.get(_UNSAFE_SENSOR_PROBE_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _find_bundled_tool(name: str) -> Path | None:
    env_name = "AI_METER_SENSOR_PROBE" if "sensor" in name else "AI_METER_WINPROBE"
    env = os.environ.get(env_name)
    if env:
        candidate = Path(env)
        if candidate.exists():
            return candidate

    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(str(meipass)))
    roots.append(Path(__file__).resolve().parents[1])

    for root in roots:
        candidate = root / "ai_meter" / "bin" / "win-x64" / name
        if candidate.exists():
            return candidate
        candidate = root / "bin" / "win-x64" / name
        if candidate.exists():
            return candidate
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _hidden_startupinfo() -> subprocess.STARTUPINFO | None:
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo
