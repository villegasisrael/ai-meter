from __future__ import annotations


class MemoryOffsetStore:
    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}

    def get_offset(self, key: str) -> int:
        return int(self._offsets.get(key, 0))

    def set_offset(self, key: str, offset: int) -> None:
        self._offsets[key] = int(offset)
