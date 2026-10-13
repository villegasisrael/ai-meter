"""Read account statistics through Codex; never start a thread or model turn.

Authentication stays inside Codex. Only initialize and account/usage/read are
sent to a short-lived app-server. Results stay in memory, including on failure.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from types import MappingProxyType
from typing import Any, Mapping, Sequence


class UsageUnavailable(Exception):
    """A safe, user-facing diagnostic (never raw subprocess output)."""


def discover_codex_command(configured_path: str = "") -> list[str] | None:
    """Prefer the desktop binary; fall back to the CLI without invoking a shell."""
    if configured_path:
        candidates = [Path(configured_path).expanduser()]
    else:
        candidates: list[Path] = []
        local = os.environ.get("LOCALAPPDATA")
        if os.name == "nt" and local:
            try:
                candidates = sorted(
                    (Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            except OSError:
                candidates = []
        executable = shutil.which("codex")
        if executable:
            candidates.append(Path(executable))
    for path in candidates:
        if not path.is_file():
            continue
        if os.name == "nt" and path.suffix.lower() in {".cmd", ".bat", ".ps1", ".js"}:
            script = path if path.suffix.lower() == ".js" else (
                path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            )
            node = shutil.which("node")
            if node and script.is_file():
                return [node, str(script)]
            continue
        return [str(path)]
    return None


def _token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass(frozen=True)
class AccountUsage:
    lifetime_tokens: int | None
    daily_tokens: Mapping[date, int]
    fetched_at: datetime

    @classmethod
    def from_result(cls, result: Any) -> AccountUsage:
        if not isinstance(result, dict):
            raise UsageUnavailable("respuesta de uso inválida")
        summary = result.get("summary") or {}
        buckets = result.get("dailyUsageBuckets")
        if not isinstance(summary, dict) or (buckets is not None and not isinstance(buckets, list)):
            raise UsageUnavailable("respuesta de uso inválida")
        daily: dict[date, int] = {}
        for bucket in buckets or []:
            if not isinstance(bucket, dict):
                continue
            try:
                day = date.fromisoformat(bucket.get("startDate", ""))
            except (ValueError, TypeError):
                continue
            tokens = _token_count(bucket.get("tokens"))
            if tokens is None:
                continue
            if day in daily and daily[day] != tokens:
                raise UsageUnavailable("respuesta de uso con fechas duplicadas")
            daily[day] = tokens
        return cls(
            lifetime_tokens=_token_count(summary.get("lifetimeTokens")),
            daily_tokens=MappingProxyType(daily),
            fetched_at=datetime.now(timezone.utc),
        )


@dataclass(frozen=True)
class AccountUsageState:
    usage: AccountUsage | None = None
    status: str = "loading"
    error: str | None = None
    interval_seconds: int = 900


class CodexAccountUsageCollector:
    def __init__(
        self,
        codex_home: Path,
        cache_seconds: int = 900,
        app_server_path: str = "",
        *,
        command: Sequence[str] | None = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.codex_home = codex_home
        self.cache_seconds = max(60, int(cache_seconds))
        self.command = list(command) if command is not None else discover_codex_command(app_server_path)
        self.timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._fetch_lock = Lock()
        self._closed = Event()
        self._process: subprocess.Popen[str] | None = None
        self._next_fetch = 0.0
        self._state = AccountUsageState(interval_seconds=self.cache_seconds)

    def snapshot(self) -> AccountUsageState:
        with self._lock:
            state = self._state
            if state.status == "connected" and time.monotonic() >= self._next_fetch:
                return AccountUsageState(state.usage, "stale", None, self.cache_seconds)
            return state

    def request_refresh(self) -> None:
        with self._lock:
            self._next_fetch = 0.0

    def fetch_if_due(self) -> AccountUsageState:
        if self._closed.is_set() or not self._fetch_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            with self._lock:
                if time.monotonic() < self._next_fetch:
                    return self._state
                self._next_fetch = time.monotonic() + self.cache_seconds
            try:
                usage = self._query_usage()
                state = AccountUsageState(usage, "connected", None, self.cache_seconds)
            except (UsageUnavailable, OSError, ValueError) as error:
                # Only our own sanitized diagnostics may reach the UI.
                message = str(error) if isinstance(error, UsageUnavailable) else "no se pudo iniciar Codex"
                with self._lock:
                    usage = self._state.usage
                state = AccountUsageState(
                    usage, "stale" if usage else "unavailable", message, self.cache_seconds
                )
            with self._lock:
                self._state = state
            return state
        finally:
            self._fetch_lock.release()

    def _query_usage(self) -> AccountUsage:
        if not self.command:
            raise UsageUnavailable("no se encontró Codex; configura app_server_path")
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.codex_home)
        process = subprocess.Popen(
            [*self.command, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        with self._lock:
            self._process = process
        replies: queue.Queue[Any] = queue.Queue()

        def read_replies() -> None:
            assert process.stdout is not None
            try:
                while True:
                    line = process.stdout.readline(2 * 1024 * 1024 + 1)
                    if not line:
                        break
                    if len(line) > 2 * 1024 * 1024:
                        replies.put(UsageUnavailable("respuesta de uso demasiado grande"))
                        break
                    try:
                        reply = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(reply, dict) and reply.get("id") in (1, 2):
                        replies.put(reply)
            finally:
                replies.put(None)

        reader = Thread(target=read_replies, name="ai-meter-codex-rpc", daemon=True)
        reader.start()
        deadline = time.monotonic() + self.timeout_seconds

        def send(message: dict[str, Any]) -> None:
            assert process.stdin is not None
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def response(request_id: int) -> Any:
            while not self._closed.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise UsageUnavailable("consulta agotada; se reintentará")
                try:
                    reply = replies.get(timeout=min(0.1, remaining))
                except queue.Empty:
                    continue
                if reply is None:
                    raise UsageUnavailable("Codex no respondió; revisa su inicio de sesión")
                if isinstance(reply, UsageUnavailable):
                    raise reply
                if reply.get("id") != request_id:
                    continue
                if "error" in reply:
                    error = reply["error"]
                    message = str(error.get("message", "")).lower() if isinstance(error, dict) else ""
                    if "auth" in message or "login" in message:
                        raise UsageUnavailable("inicia sesión en Codex")
                    if "method" in message or "unknown variant" in message:
                        raise UsageUnavailable("actualiza Codex para consultar el uso")
                    raise UsageUnavailable("consulta de uso no disponible")
                if "result" not in reply:
                    raise UsageUnavailable("respuesta de uso inválida")
                return reply["result"]
            raise UsageUnavailable("consulta cancelada")

        try:
            send({"id": 1, "method": "initialize", "params": {"clientInfo": {
                "name": "ai_meter", "title": "ai-meter account statistics", "version": "0.1.0"
            }}})
            response(1)
            send({"method": "initialized", "params": {}})
            send({"id": 2, "method": "account/usage/read"})
            return AccountUsage.from_result(response(2))
        finally:
            if process.stdin:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=0.5)
            if process.stdout:
                process.stdout.close()
            with self._lock:
                self._process = None

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
