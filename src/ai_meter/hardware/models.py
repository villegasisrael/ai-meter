from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HardwareMetric(BaseModel):
    """One normalized, read-only hardware observation."""

    id: str
    device_id: str
    device_type: str
    kind: str
    label: str
    value: float
    unit: str
    source: str
    provider: str
    timestamp: datetime
    stale_after_s: float = 3.0
    writable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class HardwareProviderStatus(BaseModel):
    name: str
    available: bool
    source: str
    detail: str = ""
    last_error: str | None = None
    metric_count: int = 0
    read_only: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
