"""Fetches real-time Claude usage limits from the Anthropic OAuth usage API.

No model tokens are consumed — this is a rate-limit metadata endpoint only.
Requires an OAuth bearer token (sk-ant-oat01--...) from Claude Code.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_DEFAULT_CACHE_SECONDS = 900
_TIMEOUT_S = 10


class ClaudeApiUsageCollector:
    def __init__(self, token: str, cache_seconds: int = _DEFAULT_CACHE_SECONDS) -> None:
        # token = full Authorization header value, e.g. "Bearer sk-ant-oat01--..."
        self._token = token
        self._cache_seconds = max(60, int(cache_seconds))
        self._last_fetch_mono = 0.0
        self._next_retry_mono = 0.0
        self._cached: dict[str, Any] | None = None
        self._parsed: ParsedUsage | None = None
        self._last_error: str | None = None

    @classmethod
    def from_env_file(
        cls,
        env_path: Path | None = None,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
    ) -> ClaudeApiUsageCollector | None:
        candidates: list[Path | None] = [env_path] if env_path else []
        candidates += [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[3] / ".env",
        ]
        for path in candidates:
            if path and path.exists():
                token = _parse_token_from_env(path)
                if token:
                    return cls(token, cache_seconds=cache_seconds)
        return None

    @property
    def has_data(self) -> bool:
        return self._parsed is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def cache_seconds(self) -> int:
        return self._cache_seconds

    def is_due(self) -> bool:
        now = time.monotonic()
        if now < self._next_retry_mono:
            return False
        return now - self._last_fetch_mono >= self._cache_seconds

    @property
    def retry_after_seconds(self) -> int:
        remaining = int(self._next_retry_mono - time.monotonic())
        return max(0, remaining)

    def fetch_if_due(self) -> ParsedUsage | None:
        if not self.is_due():
            return self._parsed
        return self._do_fetch()

    def current(self) -> ParsedUsage | None:
        return self._parsed

    def _do_fetch(self) -> ParsedUsage | None:
        req = urllib.request.Request(
            _USAGE_URL,
            headers={
                "Authorization": self._token,
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "claude-code/2.0.32",
                "Accept": "application/json, text/plain, */*",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
            self._cached = data
            self._parsed = ParsedUsage.from_api(data)
            self._last_fetch_mono = time.monotonic()
            self._next_retry_mono = 0.0
            self._last_error = None
            return self._parsed
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = _parse_retry_after_seconds(exc.headers.get("Retry-After"))
                self._next_retry_mono = time.monotonic() + retry_after
                self._last_error = f"HTTP 429: Too Many Requests (retry in {retry_after}s)"
            else:
                self._last_error = f"HTTP Error {exc.code}: {exc.reason}"
            return self._parsed  # return stale on error
        except Exception as exc:
            self._last_error = str(exc)[:120]
            return self._parsed  # return stale on error


class ParsedUsage:
    __slots__ = (
        "five_hour_pct",
        "five_hour_reset_secs",
        "seven_day_pct",
        "seven_day_reset_secs",
        "fetched_at",
    )

    def __init__(
        self,
        five_hour_pct: float | None,
        five_hour_reset_secs: int | None,
        seven_day_pct: float | None,
        seven_day_reset_secs: int | None,
        fetched_at: str,
    ) -> None:
        self.five_hour_pct = five_hour_pct
        self.five_hour_reset_secs = five_hour_reset_secs
        self.seven_day_pct = seven_day_pct
        self.seven_day_reset_secs = seven_day_reset_secs
        self.fetched_at = fetched_at

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ParsedUsage:
        fh = data.get("five_hour") or {}
        sd = data.get("seven_day") or {}
        return cls(
            five_hour_pct=_safe_float(fh.get("utilization")),
            five_hour_reset_secs=_reset_secs(fh.get("resets_at")),
            seven_day_pct=_safe_float(sd.get("utilization")),
            seven_day_reset_secs=_reset_secs(sd.get("resets_at")),
            fetched_at=datetime.now(timezone.utc).strftime("%H:%M:%S"),
        )


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reset_secs(v: Any) -> int | None:
    if v is None:
        return None
    try:
        dt = datetime.fromisoformat(str(v))
        secs = int((dt - datetime.now(timezone.utc)).total_seconds())
        return max(0, secs)
    except Exception:
        return None


def _parse_token_from_env(path: Path) -> str | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "TOKEN":
                value = value.strip()
                if value.lower().startswith("bearer "):
                    return value
        return None
    except Exception:
        return None


def _parse_retry_after_seconds(value: Any) -> int:
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return 300
    return max(60, min(1800, seconds))
