from __future__ import annotations

import csv
import json
import os
import re
import shutil
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
_COMMAND_CACHE: dict[tuple[str, bool, tuple[str, ...]], Path | None] = {}
_COMMAND_CACHE_MISS = object()


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
        self._cached_gpu_temp_source: str = "unknown"
        self._cached_gpu_temps: list[dict[str, Any]] = []
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
        if native is None and include_temp and self._native_sampler.available:
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
        meta["gpu_temp_source"] = self._cached_gpu_temp_source
        meta["gpu_temps"] = list(self._cached_gpu_temps)
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
        if os.name != "nt":
            name = self._read_proc_cpu_name()
            if name:
                return name
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
                return (platform.processor() or platform.machine() or "CPU")[:40]
            except Exception:
                return "CPU"

    def _read_proc_cpu_name(self) -> str | None:
        cpuinfo = Path("/proc/cpuinfo")
        if not cpuinfo.exists():
            return None
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    _, _, value = line.partition(":")
                    name = value.strip()
                    if name:
                        return name[:40]
        except OSError:
            return None
        return None

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
            self._refresh_platform_temp_fallbacks()
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
            # CPU temp unavailable from probe; try platform fallback.
            fallback_temp, fallback_source = self._read_platform_temp_fallback()
            if fallback_temp is not None:
                self._cached_cpu_temp_c = fallback_temp
                self._cached_cpu_temp_source = _shorten_source(fallback_source)
            else:
                self._cached_cpu_temp_c = None
                self._cached_cpu_temp_source = _shorten_source(str(obj.get("source") or "unknown"))

        gpu_temp = obj.get("gpu_temp_c")
        if isinstance(gpu_temp, (int, float)) and 5.0 <= float(gpu_temp) <= 130.0:
            self._cached_gpu_temp_c = float(gpu_temp)
            self._cached_gpu_name = str(obj.get("gpu_name") or "GPU")
            self._cached_gpu_temp_source = _shorten_source(str(obj.get("source") or "probe"))
            self._cached_gpu_temps = [
                {
                    "name": self._cached_gpu_name,
                    "temp_c": self._cached_gpu_temp_c,
                    "source": self._cached_gpu_temp_source,
                }
            ]
        else:
            self._refresh_platform_gpu_temp_fallback()

        core_loads = obj.get("core_loads")
        if isinstance(core_loads, list) and core_loads:
            self._cached_core_loads = [
                {"name": str(c.get("name", "")), "load_percent": float(c.get("load_percent", 0.0))}
                for c in core_loads
                if isinstance(c, dict)
            ]
        else:
            self._cached_core_loads = []

    def read_temperature_snapshot(self) -> dict[str, Any]:
        """Read safe platform temperature sources for the hardware registry."""
        cpu_temp, cpu_source = self._read_platform_temp_fallback()
        gpu_temp, gpu_name, gpu_source, gpu_readings = (
            self._read_platform_gpu_temp_fallback()
        )
        return {
            "cpu_temp_c": cpu_temp,
            "cpu_source": cpu_source,
            "gpu_temp_c": gpu_temp,
            "gpu_name": gpu_name,
            "gpu_source": gpu_source,
            "gpu_readings": gpu_readings,
        }

    def _refresh_platform_temp_fallbacks(self) -> None:
        fallback_temp, fallback_source = self._read_platform_temp_fallback()
        if fallback_temp is not None:
            self._cached_cpu_temp_c = fallback_temp
            self._cached_cpu_temp_source = _shorten_source(fallback_source)
        else:
            self._cached_cpu_temp_c = None
            self._cached_cpu_temp_source = _shorten_source(fallback_source)
        self._refresh_platform_gpu_temp_fallback()

    def _refresh_platform_gpu_temp_fallback(self) -> None:
        gpu_temp, gpu_name, gpu_source, all_readings = self._read_platform_gpu_temp_fallback()
        self._cached_gpu_temps = all_readings
        if gpu_temp is None:
            self._cached_gpu_temp_c = None
            self._cached_gpu_name = None
            self._cached_gpu_temp_source = _shorten_source(gpu_source)
            return
        self._cached_gpu_temp_c = gpu_temp
        self._cached_gpu_name = gpu_name or "GPU"
        self._cached_gpu_temp_source = _shorten_source(gpu_source)

    def _read_platform_temp_fallback(self) -> tuple[float | None, str]:
        if os.name == "nt":
            return self._read_wmi_temp_fallback()
        return self._read_linux_temp_fallback()

    def _read_linux_temp_fallback(self) -> tuple[float | None, str]:
        temp, source = self._read_psutil_cpu_temp_fallback()
        if temp is not None:
            return temp, source
        return self._read_linux_hwmon_cpu_temp_fallback()

    def _read_psutil_cpu_temp_fallback(self) -> tuple[float | None, str]:
        if psutil is None or not hasattr(psutil, "sensors_temperatures"):
            return None, "linux:no_sensors"
        try:
            sensors = psutil.sensors_temperatures(fahrenheit=False)
        except Exception:
            return None, "linux:sensors_failed"
        readings = self._cpu_readings_from_psutil_sensors(sensors)
        reading = _best_temp_reading(readings)
        if reading is None:
            return None, "linux:no_data"
        return reading["temp_c"], reading["source"]

    def _cpu_readings_from_psutil_sensors(self, sensors: dict[str, Any]) -> list[dict[str, Any]]:
        readings: list[dict[str, Any]] = []
        best: tuple[int, float, str] | None = None
        hot_labels = ("package", "tctl", "tdie")
        acceptable_labels = ("cpu", "core")
        for chip, entries in sensors.items():
            if not isinstance(entries, (list, tuple)):
                continue
            for entry in entries:
                current = getattr(entry, "current", None)
                label = str(getattr(entry, "label", "") or chip)
                if not isinstance(current, (int, float)):
                    continue
                temp = float(current)
                if not 5.0 <= temp <= 130.0:
                    continue
                source = f"linux:{chip}:{label}"
                haystack = f"{chip} {label}".lower()
                if not _looks_like_cpu_temp_source(haystack):
                    continue
                if any(token in haystack for token in hot_labels):
                    score = 2
                elif any(token in haystack for token in acceptable_labels):
                    score = 1
                else:
                    score = 0
                if best is None or score > best[0] or (score == best[0] and temp > best[1]):
                    best = (score, temp, source)
        if best is not None:
            readings.append({"name": "CPU", "temp_c": best[1], "source": best[2]})
        return readings

    def _read_linux_hwmon_cpu_temp_fallback(self) -> tuple[float | None, str]:
        readings: list[dict[str, Any]] = []
        for sensor in _linux_hwmon_temperature_readings():
            haystack = f"{sensor.get('chip', '')} {sensor.get('label', '')}".lower()
            if not _looks_like_cpu_temp_source(haystack):
                continue
            readings.append(
                {
                    "name": "CPU",
                    "temp_c": sensor["temp_c"],
                    "source": f"linux:hwmon:{sensor.get('chip')}:{sensor.get('label')}",
                }
            )
        reading = _best_temp_reading(readings)
        if reading is None:
            return None, "linux:no_data"
        return reading["temp_c"], reading["source"]

    def _read_platform_gpu_temp_fallback(
        self,
    ) -> tuple[float | None, str | None, str, list[dict[str, Any]]]:
        readings: list[dict[str, Any]] = []
        nvidia_readings = self._read_nvidia_smi_gpu_temps()
        if nvidia_readings:
            readings = _dedupe_temp_readings(nvidia_readings)
            best = _best_temp_reading(readings)
            if best is not None:
                return best["temp_c"], best.get("name"), str(best.get("source") or "gpu"), readings
        readings.extend(self._read_amd_smi_gpu_temps())
        if os.name != "nt":
            readings.extend(self._read_rocm_smi_gpu_temps())
            readings.extend(self._read_linux_gpu_temp_fallback())
        else:
            readings.extend(self._read_wmi_gpu_temp_fallback())

        readings = _dedupe_temp_readings(readings)
        best = _best_temp_reading(readings)
        if best is None:
            source = "gpu:no_data"
            has_gpu_tool = (
                _find_command("nvidia-smi", extra_paths=_nvidia_smi_candidate_paths())
                or _find_command("amd-smi", extra_paths=_amd_smi_candidate_paths())
                or _find_command("rocm-smi", extra_paths=_rocm_smi_candidate_paths())
                or _find_command("rocm-smi.py", extra_paths=_rocm_smi_candidate_paths())
            )
            if not has_gpu_tool:
                source = (
                    "wmi:admin_required"
                    if os.name == "nt" and not _is_windows_admin()
                    else "gpu:no_tool"
                )
            return None, None, source, []
        return best["temp_c"], best.get("name"), str(best.get("source") or "gpu"), readings

    def _read_nvidia_smi_gpu_temps(self) -> list[dict[str, Any]]:
        exe = _find_command("nvidia-smi", extra_paths=_nvidia_smi_candidate_paths())
        if exe is None:
            return []
        try:
            out = subprocess.run(
                [
                    str(exe),
                    "--query-gpu=name,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
                startupinfo=_hidden_startupinfo(),
            )
        except Exception:
            return []
        return _parse_csv_gpu_temperatures(out.stdout, source="nvidia-smi")

    def _read_amd_smi_gpu_temps(self) -> list[dict[str, Any]]:
        exe = _find_command(
            "amd-smi",
            extra_paths=_amd_smi_candidate_paths(),
            include_path=os.name != "nt",
        )
        if exe is None:
            return []
        commands = (
            [str(exe), "metric", "--temperature", "--json"],
            [str(exe), "monitor", "--temperature", "--json"],
            [str(exe), "monitor", "--temperature"],
        )
        for command in commands:
            try:
                out = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=2.5,
                    check=False,
                    startupinfo=_hidden_startupinfo(),
                )
            except Exception:
                continue
            payload = (out.stdout or "").strip()
            if not payload:
                continue
            readings = _parse_gpu_tool_output(payload, source="amd-smi")
            if readings:
                return readings
        return []

    def _read_rocm_smi_gpu_temps(self) -> list[dict[str, Any]]:
        exe = _find_command("rocm-smi", extra_paths=_rocm_smi_candidate_paths())
        if exe is None:
            exe = _find_command("rocm-smi.py", extra_paths=_rocm_smi_candidate_paths())
        if exe is None:
            return []
        try:
            out = subprocess.run(
                [str(exe), "--showtemp", "--json"],
                capture_output=True,
                text=True,
                timeout=2.5,
                check=False,
                startupinfo=_hidden_startupinfo(),
            )
        except Exception:
            return []
        return _parse_gpu_tool_output(out.stdout or "", source="rocm-smi")

    def _read_linux_gpu_temp_fallback(self) -> list[dict[str, Any]]:
        readings: list[dict[str, Any]] = []
        if psutil is not None and hasattr(psutil, "sensors_temperatures"):
            try:
                sensors = psutil.sensors_temperatures(fahrenheit=False)
            except Exception:
                sensors = {}
            readings.extend(_gpu_readings_from_psutil_sensors(sensors))

        for sensor in _linux_hwmon_temperature_readings():
            haystack = f"{sensor.get('chip', '')} {sensor.get('label', '')}".lower()
            if not _looks_like_gpu_temp_source(haystack):
                continue
            chip = str(sensor.get("chip") or "GPU")
            readings.append(
                {
                    "name": _gpu_name_from_chip(chip),
                    "temp_c": sensor["temp_c"],
                    "source": f"linux:hwmon:{chip}:{sensor.get('label')}",
                }
            )
        return readings

    def _read_wmi_gpu_temp_fallback(self) -> list[dict[str, Any]]:
        if not _is_windows_admin():
            return []
        script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$items = @()
$namespaces = @('root/LibreHardwareMonitor','root/OpenHardwareMonitor')
foreach ($ns in $namespaces) {
    try {
        $sensors = Get-CimInstance -Namespace $ns -ClassName Sensor |
            Where-Object {
                $_.SensorType -eq 'Temperature' -and
                (
                    $_.Identifier -match 'gpu|nvidia|amd|ati|radeon' -or
                    $_.Name -match 'GPU|Hot Spot|Junction|Memory'
                )
            }
        foreach ($sensor in $sensors) {
            if ($sensor.Value -ge 5 -and $sensor.Value -le 130) {
                $items += [PSCustomObject]@{
                    name = if ($sensor.Identifier -match 'nvidia') { 'NVIDIA GPU' } elseif ($sensor.Identifier -match 'amd|ati|radeon') { 'AMD GPU' } else { 'GPU' }
                    temp_c = [double]$sensor.Value
                    source = $ns + ':' + $sensor.Name
                }
            }
        }
        if ($items.Count -gt 0) { break }
    } catch {}
}
if ($items.Count -gt 0) { $items | ConvertTo-Json -Compress } else { '' }
"""
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
                startupinfo=_hidden_startupinfo(),
            )
        except Exception:
            return []
        payload = (out.stdout or "").strip()
        if not payload:
            return []
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            return []
        rows = obj if isinstance(obj, list) else [obj]
        readings: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            temp = _parse_temp_value(row.get("temp_c"))
            if temp is None:
                continue
            readings.append(
                {
                    "name": str(row.get("name") or "GPU"),
                    "temp_c": temp,
                    "source": str(row.get("source") or "wmi:gpu"),
                }
            )
        return readings

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
        """PowerShell WMI fallback; works when running as admin."""
        if not _is_windows_admin():
            return None, "wmi:admin_required"
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
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
                startupinfo=_hidden_startupinfo(),
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
        if not self.available:
            return None
        self._start()
        with self._lock:
            return dict(self._latest) if self._latest is not None else None

    @property
    def available(self) -> bool:
        return os.name == "nt" and self._exe is not None

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
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
        with self._lock:
            if self._process is proc:
                self._process = None


def _parse_csv_gpu_temperatures(text: str, source: str) -> list[dict[str, Any]]:
    readings: list[dict[str, Any]] = []
    for row in csv.reader((text or "").splitlines()):
        if len(row) < 2:
            continue
        name = row[0].strip() or "GPU"
        temp = _parse_temp_value(row[1])
        if temp is None:
            continue
        readings.append({"name": name, "temp_c": temp, "source": source})
    return readings


def _parse_gpu_tool_output(text: str, source: str) -> list[dict[str, Any]]:
    payload = (text or "").strip()
    if not payload:
        return []
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return _parse_gpu_temperature_table(payload, source)
    readings: list[dict[str, Any]] = []
    _collect_gpu_json_readings(obj, source=source, readings=readings)
    return readings


def _collect_gpu_json_readings(
    obj: Any,
    source: str,
    readings: list[dict[str, Any]],
    name_hint: str | None = None,
) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_gpu_json_readings(item, source, readings, name_hint)
        return
    if not isinstance(obj, dict):
        return

    local_name = name_hint
    for key, value in obj.items():
        key_l = str(key).lower()
        if any(token in key_l for token in ("product", "name", "card", "gpu")) and isinstance(value, str):
            if "temp" not in key_l and value.strip():
                local_name = value.strip()
                break

    for key, value in obj.items():
        key_text = str(key)
        key_l = key_text.lower()
        if isinstance(value, (dict, list)):
            next_name = local_name
            if re.match(r"^(card|gpu)\s*\d+$", key_l) or re.match(r"^(card|gpu)\d+$", key_l):
                next_name = key_text.upper().replace("CARD", "AMD GPU ")
            _collect_gpu_json_readings(value, source, readings, next_name)
            continue

        if "temp" not in key_l and "gpu_t" not in key_l and "edge" not in key_l and "junction" not in key_l:
            continue
        if any(token in key_l for token in ("limit", "critical", "throttle", "shutdown")):
            continue
        temp = _parse_temp_value(value)
        if temp is None:
            continue
        name = local_name or "GPU"
        readings.append({"name": name, "temp_c": temp, "source": f"{source}:{key_text}"})


def _parse_gpu_temperature_table(text: str, source: str) -> list[dict[str, Any]]:
    readings: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith(("gpu ", "=", "-")):
            continue
        if not re.match(r"^\d+\s+", stripped):
            continue
        matches = re.findall(r"([-+]?\d+(?:\.\d+)?)\s*(?:\u00b0\s*)?C\b", stripped, flags=re.IGNORECASE)
        if not matches:
            continue
        temp = _parse_temp_value(matches[0])
        if temp is None:
            continue
        gpu_id = stripped.split()[0]
        readings.append({"name": f"AMD GPU {gpu_id}", "temp_c": temp, "source": source})
    return readings


def _gpu_readings_from_psutil_sensors(sensors: dict[str, Any]) -> list[dict[str, Any]]:
    readings: list[dict[str, Any]] = []
    for chip, entries in sensors.items():
        if not isinstance(entries, (list, tuple)):
            continue
        for entry in entries:
            label = str(getattr(entry, "label", "") or chip)
            haystack = f"{chip} {label}".lower()
            if not _looks_like_gpu_temp_source(haystack):
                continue
            temp = _parse_temp_value(getattr(entry, "current", None))
            if temp is None:
                continue
            readings.append(
                {
                    "name": _gpu_name_from_chip(str(chip)),
                    "temp_c": temp,
                    "source": f"linux:{chip}:{label}",
                }
            )
    return readings


def _linux_hwmon_temperature_readings() -> list[dict[str, Any]]:
    root = Path("/sys/class/hwmon")
    if not root.exists():
        return []
    readings: list[dict[str, Any]] = []
    try:
        hwmons = list(root.glob("hwmon*"))
    except OSError:
        return []
    for hwmon in hwmons:
        try:
            chip = (hwmon / "name").read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            chip = hwmon.name
        try:
            inputs = list(hwmon.glob("temp*_input"))
        except OSError:
            continue
        for input_file in inputs:
            match = re.match(r"temp(\d+)_input", input_file.name)
            if match is None:
                continue
            idx = match.group(1)
            try:
                raw = input_file.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            temp = _parse_temp_value(raw)
            if temp is None:
                continue
            if temp > 1000.0:
                temp = round(temp / 1000.0, 1)
            try:
                label = (hwmon / f"temp{idx}_label").read_text(
                    encoding="utf-8",
                    errors="ignore",
                ).strip()
            except OSError:
                label = f"temp{idx}"
            readings.append({"chip": chip, "label": label, "temp_c": temp})
    return readings


def _looks_like_cpu_temp_source(text: str) -> bool:
    haystack = text.lower()
    if any(token in haystack for token in ("amdgpu", "radeon", "nvidia", "nouveau", "gpu", "nvme", "wifi", "iwlwifi", "battery")):
        return False
    return any(
        token in haystack
        for token in (
            "k10temp",
            "coretemp",
            "zenpower",
            "cpu",
            "package",
            "tctl",
            "tdie",
            "tccd",
            "ccd",
            "acpitz",
            "x86_pkg_temp",
        )
    )


def _looks_like_gpu_temp_source(text: str) -> bool:
    haystack = text.lower()
    if "gpu" in haystack:
        return True
    return any(token in haystack for token in ("amdgpu", "radeon", "nvidia", "nouveau"))


def _gpu_name_from_chip(chip: str) -> str:
    lower = chip.lower()
    if "amdgpu" in lower or "radeon" in lower:
        return "AMD GPU"
    if "nvidia" in lower or "nouveau" in lower:
        return "NVIDIA GPU"
    return chip or "GPU"


def _parse_temp_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        temp = float(value)
    else:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
        if match is None:
            return None
        try:
            temp = float(match.group(0))
        except ValueError:
            return None
    if temp > 1000.0:
        temp = round(temp / 1000.0, 1)
    if not 5.0 <= temp <= 130.0:
        return None
    return round(temp, 1)


def _best_temp_reading(readings: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [r for r in readings if isinstance(r.get("temp_c"), (int, float))]
    if not valid:
        return None
    return max(valid, key=lambda row: float(row.get("temp_c") or 0.0))


def _dedupe_temp_readings(readings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for reading in readings:
        temp = _parse_temp_value(reading.get("temp_c"))
        if temp is None:
            continue
        name = str(reading.get("name") or "GPU")
        source = str(reading.get("source") or "gpu")
        key = (name.lower(), source.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"name": name, "temp_c": temp, "source": source})
    return deduped


def _find_command(
    name: str,
    extra_paths: list[Path] | None = None,
    include_path: bool = True,
) -> Path | None:
    extra = tuple(str(path) for path in (extra_paths or []))
    key = (name.lower() if os.name == "nt" else name, bool(include_path), extra)
    cached = _COMMAND_CACHE.get(key, _COMMAND_CACHE_MISS)
    if cached is not _COMMAND_CACHE_MISS:
        return cached

    if include_path:
        found = shutil.which(name)
        if found:
            result = Path(found)
            _COMMAND_CACHE[key] = result
            return result
        if os.name == "nt" and not name.lower().endswith(".exe"):
            found = shutil.which(f"{name}.exe")
            if found:
                result = Path(found)
                _COMMAND_CACHE[key] = result
                return result
    for candidate in extra_paths or []:
        if candidate.exists():
            _COMMAND_CACHE[key] = candidate
            return candidate
    _COMMAND_CACHE[key] = None
    return None


def _nvidia_smi_candidate_paths() -> list[Path]:
    if os.name != "nt":
        return []
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        os.environ.get("SystemRoot"),
    ]
    candidates: list[Path] = []
    for root in [Path(r) for r in roots if r]:
        candidates.extend(
            [
                root / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
                root / "System32" / "nvidia-smi.exe",
            ]
        )
    return candidates


def _amd_smi_candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("AI_METER_AMD_SMI")
    if env:
        candidates.append(Path(env))
    if os.name == "nt":
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]
        for root_text in [r for r in roots if r]:
            root = Path(root_text) / "AMD" / "ROCm"
            candidates.append(root / "bin" / "amd-smi.exe")
    else:
        candidates.extend([Path("/opt/rocm/bin/amd-smi"), Path("/usr/bin/amd-smi")])
    return candidates


def _rocm_smi_candidate_paths() -> list[Path]:
    if os.name == "nt":
        return []
    return [
        Path("/opt/rocm/bin/rocm-smi"),
        Path("/opt/rocm/bin/rocm-smi.py"),
        Path("/usr/bin/rocm-smi"),
        Path("/usr/bin/rocm-smi.py"),
    ]


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


def _is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


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
