from __future__ import annotations

from pathlib import Path

from ai_meter.collectors.system import SystemCollector
from ai_meter.hardware.cpu_identity import CpuIdentity, detect_cpu_identity
from ai_meter.hardware.providers.base import HardwareProvider
from ai_meter.hardware.providers.platform import PlatformSensorProvider


def create_hardware_providers(
    system_collector: SystemCollector,
    amd_probe_path: str | Path | None = None,
    cpu_identity: CpuIdentity | None = None,
) -> list[HardwareProvider]:
    identity = cpu_identity or detect_cpu_identity()
    providers: list[HardwareProvider] = []
    if identity.vendor == "amd":
        from ai_meter.hardware.providers.amd_ryzen import AmdRyzenMasterProvider

        providers.append(AmdRyzenMasterProvider(amd_probe_path))
    providers.append(PlatformSensorProvider(system_collector, identity))
    return providers
