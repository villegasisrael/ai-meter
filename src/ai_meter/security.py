from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|auth|authorization|bearer|access[_-]?token|refresh[_-]?token|id[_-]?token|token|cookie|session|secret|password|client[_-]?secret)",
    re.IGNORECASE,
)

LONG_SECRET_LIKE_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def _redact_string(value: str) -> str:
    masked = value
    if "bearer " in value.lower():
        return "[REDACTED]"
    masked = LONG_SECRET_LIKE_RE.sub("[REDACTED]", masked)
    return masked


def redact_sensitive(data: Any) -> Any:
    if isinstance(data, Mapping):
        out: dict[str, Any] = {}
        for key, value in data.items():
            if SENSITIVE_KEY_RE.search(str(key)):
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = redact_sensitive(value)
        return out
    if isinstance(data, str):
        return _redact_string(data)
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return [redact_sensitive(item) for item in data]
    return data
