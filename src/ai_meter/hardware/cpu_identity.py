from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


CpuVendor = Literal["amd", "intel", "unknown"]


@dataclass(frozen=True)
class CpuIdentity:
    vendor: CpuVendor
    vendor_id: str
    name: str


def detect_cpu_identity() -> CpuIdentity:
    """Detect CPU identity once without starting a vendor-specific provider."""
    vendor_id = ""
    name = ""

    if os.name == "nt":
        vendor_id, name = _read_windows_registry()
    else:
        vendor_id, name = _read_proc_cpuinfo()

    processor_identifier = os.environ.get("PROCESSOR_IDENTIFIER", "").strip()
    if not name:
        name = (platform.processor() or "CPU").strip()
    vendor = classify_cpu_vendor(vendor_id, processor_identifier, name)
    return CpuIdentity(
        vendor=vendor,
        vendor_id=vendor_id or processor_identifier or "unknown",
        name=name or "CPU",
    )


def classify_cpu_vendor(*values: str) -> CpuVendor:
    normalized = " ".join(str(value).strip().lower() for value in values if value)
    if "authenticamd" in normalized or "amd ryzen" in normalized:
        return "amd"
    if "genuineintel" in normalized or "intel(r)" in normalized or "intel64" in normalized:
        return "intel"
    return "unknown"


def _read_windows_registry() -> tuple[str, str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            vendor_id = str(winreg.QueryValueEx(key, "VendorIdentifier")[0]).strip()
            name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            return vendor_id, name
    except (ImportError, OSError):
        return "", ""


def _read_proc_cpuinfo() -> tuple[str, str]:
    path = Path("/proc/cpuinfo")
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "", ""

    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values.setdefault(key.strip().lower(), value.strip())
    vendor_id = values.get("vendor_id") or values.get("cpu implementer") or ""
    name = values.get("model name") or values.get("hardware") or ""
    return vendor_id, name
