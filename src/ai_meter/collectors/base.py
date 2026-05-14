from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ai_meter.models import EventRecord, ProviderStatus, UsageRecord


@dataclass
class CollectBatch:
    provider_status: ProviderStatus | None = None
    events: list[EventRecord] = field(default_factory=list)
    usage_samples: list[UsageRecord] = field(default_factory=list)


class Collector(ABC):
    name: str

    @abstractmethod
    def probe(self) -> ProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def collect(self) -> CollectBatch:
        raise NotImplementedError
