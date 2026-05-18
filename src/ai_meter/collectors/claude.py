from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_meter.collectors.base import CollectBatch, Collector
from ai_meter.models import EventRecord, ProviderStatus, UsageRecord
from ai_meter.paths import AppPaths

class ClaudeCollector(Collector):
    name = "claude"

    def __init__(self, paths: AppPaths, offset_store: Any) -> None:
        self.paths = paths
        self.offset_store = offset_store
        self._project_files_cache: list[Path] = []
        self._project_files_cached_at = 0.0

    def probe(self) -> ProviderStatus:
        home = self.paths.claude_home
        projects = home / "projects"
        history = home / "history.jsonl"
        stats = home / "stats-cache.json"
        found = any(p.exists() for p in [projects, history, stats])
        return ProviderStatus(
            name=self.name,
            enabled=True,
            status="active" if found else "unknown",
            source="projects_jsonl" if projects.exists() else "unknown",
            last_seen_at=datetime.now(timezone.utc),
            metadata={
                "claude_home": str(home),
                "projects_dir": projects.exists(),
                "history_jsonl": history.exists(),
                "stats_cache_json": stats.exists(),
            },
        )

    def collect(self) -> CollectBatch:
        batch = CollectBatch(provider_status=self.probe())
        account_meta = self._load_account_meta()
        if account_meta:
            batch.provider_status.metadata.update(account_meta)
        rows_by_file: list[tuple[Path, list[dict[str, Any]]]] = []
        for fp in self._project_files():
            try:
                rows_by_file.append((fp, self._read_new_jsonl(fp)))
            except OSError:
                continue
        batch.events.extend(self._collect_events(rows_by_file))
        batch.usage_samples.extend(self._collect_usage(rows_by_file))
        batch.usage_samples.extend(self._collect_stats_cache())
        return batch

    def _project_files(self) -> list[Path]:
        now = time.monotonic()
        if self._project_files_cache and (now - self._project_files_cached_at) < 20.0:
            return self._project_files_cache

        project_dir = self.paths.claude_home / "projects"
        if not project_dir.exists():
            return []
        files = list(project_dir.rglob("*.jsonl"))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        self._project_files_cache = files[:6]
        self._project_files_cached_at = now
        return self._project_files_cache

    def _read_new_jsonl(self, file_path: Path, max_lines: int = 60) -> list[dict[str, Any]]:
        key = f"file:{file_path}"
        offset = self.offset_store.get_offset(key)
        size = file_path.stat().st_size
        if offset == 0:
            objects = _read_tail_jsonl(file_path, max_bytes=256 * 1024, max_lines=max_lines)
            self.offset_store.set_offset(key, size)
            return objects
        if size < offset:
            offset = 0

        objects: list[dict[str, Any]] = []
        with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(offset)
            count = 0
            for line in handle:
                if count >= max_lines:
                    break
                count += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    objects.append(obj)
            new_offset = handle.tell()
        if new_offset != offset:
            self.offset_store.set_offset(key, new_offset)
        return objects

    def _collect_events(self, rows_by_file: list[tuple[Path, list[dict[str, Any]]]]) -> list[EventRecord]:
        events: list[EventRecord] = []
        for fp, rows in rows_by_file:
            for obj in rows:
                event_type = str(obj.get("type") or "raw_event")
                session_id = _find_str(obj, ["sessionId", "session_id", "toolUseID"])
                project = _find_str(obj, ["cwd", "project"])
                message = _event_summary(obj)
                events.append(
                    EventRecord(
                        provider="claude",
                        timestamp=_parse_ts(obj.get("timestamp")),
                        event_type=event_type,
                        severity="info",
                        title=_find_str(obj, ["subtype", "entrypoint", "userType"]),
                        message=message,
                        accuracy="real",
                        source="projects_jsonl",
                        project_path=project,
                        session_external_id=session_id,
                        metadata={"file": str(fp), "type": event_type},
                    )
                )
        return events

    def _collect_usage(self, rows_by_file: list[tuple[Path, list[dict[str, Any]]]]) -> list[UsageRecord]:
        usage: list[UsageRecord] = []
        for fp, rows in rows_by_file:
            for obj in rows:
                ub = _extract_usage(obj)
                if not ub:
                    continue
                session_id = _find_str(obj, ["sessionId", "session_id", "toolUseID"])
                usage.append(
                    UsageRecord(
                        provider="claude",
                        timestamp=_parse_ts(obj.get("timestamp")),
                        input_tokens=_as_int(ub.get("input_tokens")),
                        output_tokens=_as_int(ub.get("output_tokens")),
                        cache_creation_tokens=_as_int(ub.get("cache_creation_input_tokens")),
                        cache_read_tokens=_as_int(ub.get("cache_read_input_tokens")),
                        total_tokens=_usage_total(ub),
                        accuracy="real",
                        source="projects_jsonl",
                        project_path=_find_str(obj, ["cwd", "project"]),
                        session_external_id=session_id,
                        model=_find_str(obj, ["message.model", "model"]),
                        metadata={"file": str(fp)},
                    )
                )
        return usage

    def _collect_stats_cache(self) -> list[UsageRecord]:
        stats_file = self.paths.claude_home / "stats-cache.json"
        if not stats_file.exists():
            return []
        try:
            obj = json.loads(stats_file.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return []
        if not isinstance(obj, dict):
            return []
        model_usage = obj.get("modelUsage")
        if not isinstance(model_usage, dict):
            return []

        out: list[UsageRecord] = []
        ts = datetime.now(timezone.utc)
        for model, total in model_usage.items():
            t = _as_int(total)
            if t is None:
                continue
            out.append(
                UsageRecord(
                    provider="claude",
                    timestamp=ts,
                    total_tokens=t,
                    accuracy="estimated",
                    source="stats_cache",
                    model=str(model),
                    metadata={"aggregated": True},
                )
            )
        return out

    def _load_account_meta(self) -> dict[str, Any]:
        credentials_file = self.paths.claude_home / ".credentials.json"
        credentials_meta = self._load_credentials_account_meta(credentials_file)
        if credentials_meta:
            return credentials_meta

        backup_dir = self.paths.claude_home / "backups"
        backup_files = sorted(backup_dir.glob(".claude.json.backup.*"), reverse=True)
        if not backup_files:
            return {}
        try:
            obj = json.loads(backup_files[0].read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(obj, dict):
            return {}
        oauth = obj.get("oauthAccount")
        if not isinstance(oauth, dict):
            return {}
        return {
            "account": {
                "organization_type": oauth.get("organizationType"),
                "organization_rate_limit_tier": oauth.get("organizationRateLimitTier"),
                "has_extra_usage_enabled": oauth.get("hasExtraUsageEnabled"),
                "source": "claude_backup",
            }
        }

    def _load_credentials_account_meta(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(obj, dict):
            return {}
        oauth = obj.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            return {}

        subscription = oauth.get("subscriptionType")
        if not subscription:
            return {}
        return {
            "account": {
                "organization_type": f"claude_{subscription}",
                "organization_rate_limit_tier": oauth.get("rateLimitTier"),
                "source": "claude_credentials",
            }
        }


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_total(usage: dict[str, Any]) -> int | None:
    total = _as_int(usage.get("total_tokens"))
    if total is not None:
        return total
    parts = [
        _as_int(usage.get("input_tokens")),
        _as_int(usage.get("output_tokens")),
        _as_int(usage.get("cache_creation_input_tokens")),
        _as_int(usage.get("cache_read_input_tokens")),
    ]
    summed = sum(value or 0 for value in parts)
    return summed if summed > 0 else None


def _read_tail_jsonl(file_path: Path, max_bytes: int, max_lines: int) -> list[dict[str, Any]]:
    try:
        size = file_path.stat().st_size
        start = max(0, size - max_bytes)
        with file_path.open("rb") as handle:
            handle.seek(start)
            if start > 0:
                handle.readline()
            raw_lines = handle.readlines()
    except OSError:
        return []

    rows: list[dict[str, Any]] = []
    for raw in raw_lines[-max_lines:]:
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _find_str(data: dict[str, Any], paths: list[str]) -> str | None:
    for path in paths:
        current: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and isinstance(current, str) and current.strip():
            return current.strip()
    return None


def _short_json(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:180] if text else None


def _event_summary(obj: dict[str, Any]) -> str | None:
    direct_type = _find_str(obj, ["toolName", "command", "entrypoint"])
    if direct_type:
        return direct_type[:120]
    if obj.get("toolUseResult") is not None:
        return "tool result"
    if obj.get("lastPrompt") is not None or obj.get("content") is not None:
        return "content hidden"

    message = obj.get("message")
    if not isinstance(message, dict):
        return None

    role = message.get("role")
    if isinstance(role, str):
        return f"{role} message"
    content = message.get("content")
    if isinstance(content, str):
        return "text content"
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if isinstance(item_type, str) and item_type.strip():
                return f"{item_type.strip()} content"

    return None


def _extract_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = []
    message = obj.get("message")
    if isinstance(message, dict):
        candidates.extend([message.get("usage"), message.get("token_usage")])
        iterations = message.get("usage", {}).get("iterations") if isinstance(message.get("usage"), dict) else None
        if isinstance(iterations, list):
            for it in iterations:
                if isinstance(it, dict):
                    candidates.append(it)

    tool_use = obj.get("toolUseResult")
    if isinstance(tool_use, dict):
        candidates.extend([tool_use.get("usage"), tool_use])

    candidates.extend([obj.get("usage"), obj.get("token_usage")])

    for c in candidates:
        if isinstance(c, dict) and any(k in c for k in ["input_tokens", "output_tokens", "total_tokens"]):
            return c

    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if any(k in cur for k in ["input_tokens", "output_tokens", "total_tokens"]):
                return cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None
