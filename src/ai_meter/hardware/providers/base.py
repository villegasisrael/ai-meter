from __future__ import annotations

from abc import ABC, abstractmethod

from ai_meter.hardware.models import HardwareMetric, HardwareProviderStatus


class HardwareProvider(ABC):
    name: str

    @abstractmethod
    def probe(self) -> HardwareProviderStatus:
        raise NotImplementedError

    @abstractmethod
    def read(self) -> list[HardwareMetric]:
        raise NotImplementedError
