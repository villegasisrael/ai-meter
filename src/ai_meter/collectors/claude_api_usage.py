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
_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
# Public OAuth client id used by Claude Code (not a secret).
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_DEFAULT_CACHE_SECONDS = 900
_TIMEOUT_S = 10
_CREDENTIALS_NAME = ".credentials.json"


class ClaudeApiUsageCollector:
    def __init__(
        self,
        token: str,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
        expires_at_ms: int | None = None,
        token_source: str = "env",
        cache_path: Path | None = None,
        credentials_path: Path | None = None,
        refresh_token: str | None = None,
    ) -> None:
        # token = full Authorization header value, e.g. "Bearer sk-ant-oat01--..."
        self._token = token
        self._cache_seconds = max(60, int(cache_seconds))
        self._expires_at_ms = expires_at_ms
        self._token_source = token_source
        self._cache_path = Path(cache_path) if cache_path else None
        # Credentials file backing the OAuth token (Claude Code's
        # ~/.claude/.credentials.json). Tracked so we can (a) reload after the
        # user re-logs in and (b) refresh the access token automatically.
        self._credentials_path = Path(credentials_path) if credentials_path else None
        self._refresh_token = refresh_token
        self._credentials_mtime = self._current_credentials_mtime()
        self._last_fetch_mono = 0.0
        self._next_retry_mono = 0.0
        self._cached: dict[str, Any] | None = None
        self._parsed: ParsedUsage | None = None
        self._parsed_is_fresh = False
        self._last_error: str | None = None
        # Seed with the last successful result persisted on a previous run so
        # we can show a stale value (with age) instead of "unknown" when the
        # token has expired or the API is unreachable.
        self._load_cache()

    @classmethod
    def from_sources(
        cls,
        claude_home: Path | None = None,
        env_path: Path | None = None,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
        cache_path: Path | None = None,
    ) -> ClaudeApiUsageCollector | None:
        # An explicit TOKEN in .env wins (manual override); otherwise reuse the
        # OAuth token Claude Code already stored at ~/.claude/.credentials.json.
        collector = cls.from_env_file(
            env_path=env_path, cache_seconds=cache_seconds, cache_path=cache_path
        )
        if collector is not None:
            return collector
        return cls.from_credentials(
            claude_home, cache_seconds=cache_seconds, cache_path=cache_path
        )

    @classmethod
    def from_env_file(
        cls,
        env_path: Path | None = None,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
        cache_path: Path | None = None,
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
                    return cls(
                        token,
                        cache_seconds=cache_seconds,
                        token_source="env",
                        cache_path=cache_path,
                    )
        return None

    @classmethod
    def from_credentials(
        cls,
        claude_home: Path | None,
        cache_seconds: int = _DEFAULT_CACHE_SECONDS,
        cache_path: Path | None = None,
    ) -> ClaudeApiUsageCollector | None:
        if claude_home is None:
            return None
        path = Path(claude_home) / _CREDENTIALS_NAME
        if not path.exists():
            return None
        token, expires_at_ms, refresh_token = _parse_token_from_credentials(path)
        if not token:
            return None
        return cls(
            token,
            cache_seconds=cache_seconds,
            expires_at_ms=expires_at_ms,
            token_source="credentials",
            cache_path=cache_path,
            credentials_path=path,
            refresh_token=refresh_token,
        )

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            obj = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(obj, dict):
            return
        data = obj.get("data")
        fetched_epoch = _safe_float(obj.get("fetched_epoch"))
        if not isinstance(data, dict):
            return
        try:
            self._cached = data
            self._parsed = ParsedUsage.from_api(data, fetched_epoch=fetched_epoch)
            self._parsed_is_fresh = False
        except Exception:
            self._parsed = None

    def _save_cache(self, data: dict[str, Any]) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"fetched_epoch": datetime.now(timezone.utc).timestamp(), "data": data},
                ensure_ascii=False,
            )
            self._cache_path.write_text(payload, encoding="utf-8")
        except Exception:
            pass

    @property
    def is_stale(self) -> bool:
        """True when the current parsed value comes from cache, not a live fetch."""
        return self._parsed is not None and not self._parsed_is_fresh

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

    @property
    def token_source(self) -> str:
        return self._token_source

    @property
    def token_expired(self) -> bool:
        return self._is_token_expired()

    def _is_token_expired(self) -> bool:
        if self._expires_at_ms is None:
            return False
        now_ms = datetime.now(timezone.utc).timestamp() * 1000.0
        # Small safety margin so we stop using a token about to expire.
        return now_ms >= (self._expires_at_ms - 30_000)

    def fetch_if_due(self) -> ParsedUsage | None:
        # Pick up a re-login or a token Claude Code itself refreshed on disk.
        self._maybe_reload_credentials()
        if not self.is_due():
            return self._parsed
        return self._do_fetch()

    def current(self) -> ParsedUsage | None:
        return self._parsed

    def _current_credentials_mtime(self) -> float | None:
        if self._credentials_path is None:
            return None
        try:
            return self._credentials_path.stat().st_mtime
        except OSError:
            return None

    def _maybe_reload_credentials(self) -> None:
        """Re-read the credentials file if it changed (e.g. user re-logged in)."""
        if self._credentials_path is None:
            return
        mtime = self._current_credentials_mtime()
        if mtime is None or mtime == self._credentials_mtime:
            return
        token, expires_at_ms, refresh_token = _parse_token_from_credentials(
            self._credentials_path
        )
        self._credentials_mtime = mtime
        if not token:
            return
        if token != self._token:
            # A new access token landed — force a fresh fetch next tick.
            self._next_retry_mono = 0.0
            self._last_fetch_mono = 0.0
        self._token = token
        self._expires_at_ms = expires_at_ms
        if refresh_token:
            self._refresh_token = refresh_token

    def _refresh_access_token(self) -> bool:
        """Exchange the refresh token for a new access token and persist it.

        Returns True on success. Best-effort: any failure leaves state intact
        and surfaces via _last_error.
        """
        if not self._refresh_token:
            return False
        body = json.dumps(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
                "client_id": _OAUTH_CLIENT_ID,
            }
        ).encode()
        req = urllib.request.Request(
            _TOKEN_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "claude-code/2.0.32",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            self._last_error = f"token refresh failed: {str(exc)[:90]}"
            return False
        access = data.get("access_token")
        if not isinstance(access, str) or not access.strip():
            self._last_error = "token refresh failed: no access_token in response"
            return False
        self._token = access.strip()
        if not self._token.lower().startswith("bearer "):
            self._token = f"Bearer {self._token}"
        new_refresh = data.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh.strip():
            self._refresh_token = new_refresh.strip()
        expires_in = _safe_float(data.get("expires_in"))
        if expires_in is not None:
            self._expires_at_ms = int(
                (datetime.now(timezone.utc).timestamp() + expires_in) * 1000
            )
        self._persist_refreshed_credentials()
        return True

    def _persist_refreshed_credentials(self) -> None:
        """Write the refreshed token back so Claude Code keeps working too."""
        if self._credentials_path is None:
            return
        try:
            obj = json.loads(
                self._credentials_path.read_text(encoding="utf-8", errors="ignore")
            )
        except Exception:
            return
        if not isinstance(obj, dict) or not isinstance(obj.get("claudeAiOauth"), dict):
            return
        oauth = obj["claudeAiOauth"]
        bare = self._token[7:] if self._token.lower().startswith("bearer ") else self._token
        oauth["accessToken"] = bare
        if self._refresh_token:
            oauth["refreshToken"] = self._refresh_token
        if self._expires_at_ms is not None:
            oauth["expiresAt"] = self._expires_at_ms
        try:
            self._credentials_path.write_text(
                json.dumps(obj, ensure_ascii=False), encoding="utf-8"
            )
            self._credentials_mtime = self._current_credentials_mtime()
        except Exception:
            pass

    def _do_fetch(self) -> ParsedUsage | None:
        if self._is_token_expired():
            # Try to self-heal via the refresh token before giving up.
            if not self._refresh_access_token():
                self._last_error = "OAuth token expired (re-login in Claude Code)"
                self._next_retry_mono = time.monotonic() + self._cache_seconds
                return self._parsed
        data = self._request_usage()
        if data is None:
            return self._parsed  # error already recorded; return stale
        self._cached = data
        self._parsed = ParsedUsage.from_api(data)
        self._parsed_is_fresh = True
        self._last_fetch_mono = time.monotonic()
        self._next_retry_mono = 0.0
        self._last_error = None
        self._save_cache(data)
        return self._parsed

    def _request_usage(self, _allow_refresh: bool = True) -> dict[str, Any] | None:
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
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and _allow_refresh and self._refresh_access_token():
                # Auth rejected but refresh succeeded — retry once.
                return self._request_usage(_allow_refresh=False)
            if exc.code == 429:
                retry_after = _parse_retry_after_seconds(exc.headers.get("Retry-After"))
                self._next_retry_mono = time.monotonic() + retry_after
                self._last_error = f"HTTP 429: Too Many Requests (retry in {retry_after}s)"
            else:
                self._last_error = f"HTTP Error {exc.code}: {exc.reason}"
            return None
        except Exception as exc:
            self._last_error = str(exc)[:120]
            return None


class ParsedUsage:
    __slots__ = (
        "five_hour_pct",
        "five_hour_reset_secs",
        "seven_day_pct",
        "seven_day_reset_secs",
        "limits",
        "fetched_at",
        "fetched_epoch",
    )

    def __init__(
        self,
        five_hour_pct: float | None,
        five_hour_reset_secs: int | None,
        seven_day_pct: float | None,
        seven_day_reset_secs: int | None,
        limits: list[dict[str, Any]],
        fetched_at: str,
        fetched_epoch: float | None = None,
    ) -> None:
        self.five_hour_pct = five_hour_pct
        self.five_hour_reset_secs = five_hour_reset_secs
        self.seven_day_pct = seven_day_pct
        self.seven_day_reset_secs = seven_day_reset_secs
        self.limits = limits
        self.fetched_at = fetched_at
        self.fetched_epoch = fetched_epoch

    @classmethod
    def from_api(cls, data: dict[str, Any], fetched_epoch: float | None = None) -> ParsedUsage:
        fh = data.get("five_hour") or {}
        sd = data.get("seven_day") or {}
        limits = _parse_limits(data)
        now = datetime.now(timezone.utc)
        return cls(
            five_hour_pct=_safe_float(fh.get("utilization")),
            five_hour_reset_secs=_reset_secs(fh.get("resets_at")),
            seven_day_pct=_safe_float(sd.get("utilization")),
            seven_day_reset_secs=_reset_secs(sd.get("resets_at")),
            limits=limits,
            fetched_at=now.strftime("%H:%M:%S"),
            fetched_epoch=fetched_epoch if fetched_epoch is not None else now.timestamp(),
        )

    @property
    def age_seconds(self) -> int | None:
        if self.fetched_epoch is None:
            return None
        age = datetime.now(timezone.utc).timestamp() - self.fetched_epoch
        return max(0, int(age))


def _safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reset_secs(v: Any) -> int | None:
    if v is None:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((dt - datetime.now(timezone.utc)).total_seconds())
        return max(0, secs)
    except Exception:
        return None


def _parse_limits(data: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = [
        ("five_hour", "5h", "usage"),
        ("seven_day", "weekly", "usage"),
        ("seven_day_oauth_apps", "oauth", "usage"),
        ("seven_day_opus", "opus", "usage"),
        ("seven_day_sonnet", "sonnet", "usage"),
        ("sonnet_only", "sonnet", "usage"),
        ("seven_day_cowork", "cowork", "usage"),
        ("seven_day_design", "designs", "usage"),
        ("seven_day_routines", "routines", "usage"),
    ]
    limits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, label, kind in definitions:
        value = data.get(key)
        if not isinstance(value, dict):
            continue
        pct = _safe_float(value.get("utilization"))
        if pct is None:
            continue
        dedupe_key = f"{label}:{kind}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        limits.append(
            {
                "name": label,
                "kind": kind,
                "pct": pct,
                "reset_secs": _reset_secs(value.get("resets_at")),
            }
        )

    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled") is True:
        used = _safe_float(extra.get("used_credits"))
        limit = _safe_float(extra.get("monthly_limit"))
        pct = _safe_float(extra.get("utilization"))
        detail = _format_extra_usage(used, limit, extra.get("currency"))
        limits.append(
            {
                "name": "extra",
                "kind": "cost",
                "pct": pct,
                "reset_secs": _reset_secs(extra.get("resets_at")),
                "detail": detail,
            }
        )
    return limits


def _format_extra_usage(used: float | None, limit: float | None, currency: Any) -> str | None:
    if used is None:
        return None
    # Claude OAuth generally reports cents for subscription extra usage.
    currency_text = str(currency or "USD").strip() or "USD"
    used_major = used / 100.0
    if limit is None or limit == 0:
        return f"{currency_text} {used_major:.2f} spent"
    return f"{currency_text} {used_major:.2f}/{limit / 100.0:.2f}"


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


def _parse_token_from_credentials(
    path: Path,
) -> tuple[str | None, int | None, str | None]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None, None, None
    if not isinstance(obj, dict):
        return None, None, None
    oauth = obj.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None, None, None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token.strip():
        return None, None, None
    token = token.strip()
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    try:
        expires_at_ms = int(oauth.get("expiresAt"))
    except (TypeError, ValueError):
        expires_at_ms = None
    refresh = oauth.get("refreshToken")
    refresh_token = refresh.strip() if isinstance(refresh, str) and refresh.strip() else None
    return token, expires_at_ms, refresh_token


def _parse_retry_after_seconds(value: Any) -> int:
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return 300
    return max(60, min(1800, seconds))
