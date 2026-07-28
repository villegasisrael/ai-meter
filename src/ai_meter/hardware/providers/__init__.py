from ai_meter.hardware.providers.platform import PlatformSensorProvider

__all__ = ["AmdRyzenMasterProvider", "PlatformSensorProvider"]


def __getattr__(name: str) -> object:
    if name == "AmdRyzenMasterProvider":
        from ai_meter.hardware.providers.amd_ryzen import AmdRyzenMasterProvider

        return AmdRyzenMasterProvider
    raise AttributeError(name)
