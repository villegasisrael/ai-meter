from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    name: str
    enabled: bool = True
    status: str = "unknown"
    source: str = "unknown"
    last_seen_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventRecord(BaseModel):
    provider: str
    timestamp: datetime
    event_type: str
    severity: str = "info"
    title: str | None = None
    message: str | None = None
    accuracy: str = "real"
    source: str = "unknown"
    project_path: str | None = None
    session_external_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UsageRecord(BaseModel):
    provider: str
    timestamp: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    usage_percent: float | None = None
    reset_at: datetime | None = None
    accuracy: str = "real"
    source: str = "unknown"
    project_path: str | None = None
    session_external_id: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemSnapshot(BaseModel):
    timestamp: datetime
    cpu_percent: float
    ram_percent: float
    ram_used_mb: float
    ram_total_mb: float
    disk_percent: float
    net_sent_mb: float
    net_recv_mb: float
    top_processes: list[dict[str, Any]] = Field(default_factory=list)


@dataclass
class RuntimeState:
    paused: bool = False
    last_error: str | None = None
