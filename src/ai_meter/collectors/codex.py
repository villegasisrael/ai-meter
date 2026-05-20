from __future__ import annotations

import json
import sqlite3
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_meter.collectors.base import CollectBatch, Collector
from ai_meter.models import EventRecord, ProviderStatus, UsageRecord
from ai_meter.paths import AppPaths
from ai_meter.security import redact_sensitive

_SESSION_WINDOW_MINUTES = 5 * 60
_WEEKLY_WINDOW_MINUTES = 7 * 24 * 60


class CodexCollector(Collector):
    name = "codex"

    def __init__(self, paths: AppPaths, offset_store: Any) -> None:
        self.paths = paths
        self.offset_store = offset_store
        self._session_files_cache: list[Path] = []
        self._session_files_cached_at = 0.0

    def probe(self) -> ProviderStatus:
        home = self.paths.codex_home
        config = home / "config.toml"
        sessions_dir = home / "sessions"
        archived_sessions_dir = home / "archived_sessions"
        session_index = home / "session_index.jsonl"
        logs_sqlite = home / "logs_2.sqlite"
        auth = home / "auth.json"
        found = any(p.exists() for p in [sessions_dir, archived_sessions_dir, session_index, logs_sqlite, config])
        return ProviderStatus(
            name=self.name,
            enabled=True,
            status="active" if found else "unknown",
            source="sessions_jsonl" if sessions_dir.exists() else "logs_sqlite",
            last_seen_at=datetime.now(timezone.utc),
            metadata={
                "codex_home": str(home),
                "config_toml": config.exists(),
                "session_index_jsonl": session_index.exists(),
                "sessions_dir": sessions_dir.exists(),
                "archived_sessions_dir": archived_sessions_dir.exists(),
                "logs_sqlite": logs_sqlite.exists(),
                "auth_present": auth.exists(),
            },
        )

    def collect(self) -> CollectBatch:
        batch = CollectBatch(provider_status=self.probe())
        session_files = self._session_files()
        rate_limit_meta = self._collect_latest_session_rate_limits(session_files) or self._collect_rate_limits()
        if rate_limit_meta:
            batch.provider_status.metadata.update(rate_limit_meta)
        session_rows_by_file: list[tuple[Path, list[dict[str, Any]]]] = []
        for fp in session_files:
            try:
                session_rows_by_file.append((fp, self._read_new_jsonl(fp)))
            except OSError:
                continue
        batch.events.extend(self._collect_sessions_events(session_rows_by_file))
        batch.usage_samples.extend(self._collect_sessions_usage(session_rows_by_file))
        if not batch.usage_samples:
            batch.usage_samples.extend(self._collect_sqlite_usage())
        return batch

    def _session_files(self) -> list[Path]:
        now = time.monotonic()
        if self._session_files_cache and (now - self._session_files_cached_at) < 20.0:
            return self._session_files_cache

        files: list[Path] = []
        index_file = self.paths.codex_home / "session_index.jsonl"
        if index_file.exists():
            files.append(index_file)
        all_files: list[Path] = []
        for session_dir in (
            self.paths.codex_home / "sessions",
            self.paths.codex_home / "archived_sessions",
        ):
            if session_dir.exists():
                all_files.extend(session_dir.rglob("*.jsonl"))
        all_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        files.extend(all_files[:8])
        self._session_files_cache = files
        self._session_files_cached_at = now
        return files

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

    def _collect_sessions_events(self, rows_by_file: list[tuple[Path, list[dict[str, Any]]]]) -> list[EventRecord]:
        events: list[EventRecord] = []
        for fp, rows in rows_by_file:
            for obj in rows:
                ts = _parse_ts(obj.get("timestamp"))
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                content_type = _find_first_str(obj, ["type", "record_type", "payload.type"]) or "raw_event"
                title = _find_first_str(obj, ["name", "payload.model", "thread_name"])
                msg = _event_text(obj)
                project = _find_first_str(payload, ["cwd"]) or _find_first_str(obj, ["cwd"])
                session_id = _find_first_str(obj, ["session_id", "payload.session_id", "thread_name"])
                events.append(
                    EventRecord(
                        provider="codex",
                        timestamp=ts,
                        event_type=content_type,
                        severity="info",
                        title=title,
                        message=msg,
                        accuracy="real",
                        source="sessions_jsonl",
                        project_path=project,
                        session_external_id=session_id,
                        metadata=redact_sensitive({"file": str(fp), "type": content_type}),
                    )
                )
        return events

    def _collect_sessions_usage(self, rows_by_file: list[tuple[Path, list[dict[str, Any]]]]) -> list[UsageRecord]:
        usage: list[UsageRecord] = []
        for fp, rows in rows_by_file:
            current_model: str | None = None
            for obj in rows:
                model_from_context = _turn_context_model(obj)
                if model_from_context:
                    current_model = model_from_context
                    continue
                usage_blob = _extract_usage(obj)
                if not usage_blob:
                    continue
                ts = _parse_ts(obj.get("timestamp"))
                session_id = _find_first_str(obj, ["session_id", "payload.session_id", "thread_name"])
                model = _find_first_str(obj, ["payload.info.model", "payload.model", "model", "payload.model_provider"])
                if not model:
                    model = current_model
                usage.append(
                    UsageRecord(
                        provider="codex",
                        timestamp=ts,
                        input_tokens=_as_int(usage_blob.get("input_tokens")),
                        output_tokens=_as_int(usage_blob.get("output_tokens")),
                        cache_creation_tokens=_as_int(usage_blob.get("cache_creation_input_tokens")),
                        cache_read_tokens=_as_int(
                            usage_blob.get("cache_read_input_tokens") or usage_blob.get("cached_input_tokens")
                        ),
                        total_tokens=_usage_total(usage_blob),
                        accuracy="real",
                        source="sessions_jsonl",
                        session_external_id=session_id,
                        model=model,
                        metadata={"file": str(fp)},
                    )
                )
        return usage

    def _collect_sqlite_usage(self) -> list[UsageRecord]:
        db_file = self.paths.codex_home / "logs_2.sqlite"
        if not db_file.exists():
            return []

        usage: list[UsageRecord] = []
        key = f"sqlite:{db_file}:last_id"
        last_id = self.offset_store.get_offset(key)
        try:
            con = sqlite3.connect(str(db_file))
            cur = con.cursor()
            if last_id == 0:
                row = cur.execute("SELECT MAX(id) FROM logs").fetchone()
                max_existing_id = int(row[0] or 0)
                self.offset_store.set_offset(key, max_existing_id)
                con.close()
                return []
            rows = cur.execute(
                """
                SELECT id, ts, feedback_log_body
                FROM logs
                WHERE id > ? AND target IN ('codex_api::endpoint::responses_websocket','codex_api::sse::responses')
                ORDER BY id ASC LIMIT 120
                """,
                (last_id,),
            ).fetchall()
            max_id = last_id
            for row in rows:
                row_id, ts_raw, body = row
                max_id = max(max_id, int(row_id))
                if not body:
                    continue
                obj = _try_json_parse(str(body))
                if not isinstance(obj, dict):
                    continue
                ub = _extract_usage(obj)
                if not ub:
                    continue
                usage.append(
                    UsageRecord(
                        provider="codex",
                        timestamp=_parse_ts(ts_raw),
                        input_tokens=_as_int(ub.get("input_tokens")),
                        output_tokens=_as_int(ub.get("output_tokens")),
                        cache_creation_tokens=_as_int(ub.get("cache_creation_input_tokens")),
                        cache_read_tokens=_as_int(ub.get("cache_read_input_tokens") or ub.get("cached_input_tokens")),
                        total_tokens=_usage_total(ub),
                        accuracy="real",
                        source="logs_sqlite",
                        metadata={"row_id": row_id},
                    )
                )
            if max_id != last_id:
                self.offset_store.set_offset(key, max_id)
            con.close()
        except sqlite3.Error:
            return []
        return usage

    def _collect_rate_limits(self) -> dict[str, Any]:
        db_file = self.paths.codex_home / "logs_2.sqlite"
        if not db_file.exists():
            return {}
        try:
            con = sqlite3.connect(str(db_file))
            cur = con.cursor()
            row = cur.execute(
                """
                SELECT feedback_log_body
                FROM logs
                WHERE target = 'codex_api::endpoint::responses_websocket'
                  AND feedback_log_body LIKE '%"type":"codex.rate_limits"%'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            con.close()
        except sqlite3.Error:
            return {}

        if not row or not row[0]:
            return {}

        body = str(row[0])
        match = re.search(r"websocket event: (\{.*\})", body)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
        if payload.get("type") != "codex.rate_limits":
            return {}

        rate_limits = payload.get("rate_limits")
        if not isinstance(rate_limits, dict):
            return {}

        normalized = _normalize_rate_limits(rate_limits, "logs_sqlite", plan_type=payload.get("plan_type"))
        return {"rate_limits": normalized} if normalized else {}

    def _collect_latest_session_rate_limits(self, files: list[Path]) -> dict[str, Any]:
        latest: tuple[datetime, dict[str, Any]] | None = None
        for fp in files[:10]:
            for obj in _read_tail_jsonl(fp, max_bytes=256 * 1024, max_lines=400):
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                rate_limits = _find_rate_limits(payload) if isinstance(payload, dict) else None
                if not isinstance(rate_limits, dict):
                    continue
                normalized = _normalize_rate_limits(
                    rate_limits,
                    "sessions_jsonl",
                    plan_type=payload.get("plan_type") or rate_limits.get("plan_type"),
                )
                if not normalized:
                    continue
                ts = _parse_ts(obj.get("timestamp"))
                if latest is None or ts > latest[0]:
                    latest = (ts, normalized)

        if latest is None:
            return {}

        return {"rate_limits": latest[1]}


def _try_json_parse(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


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


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
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
        _as_int(usage.get("cache_read_input_tokens") or usage.get("cached_input_tokens")),
    ]
    summed = sum(value or 0 for value in parts)
    return summed if summed > 0 else None


def _find_first_str(data: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        current: Any = data
        ok = True
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and isinstance(current, str) and current.strip():
            return current.strip()
    return None


def _find_rate_limits(payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = payload.get("rate_limits")
    if isinstance(direct, dict):
        return direct
    singular = payload.get("rate_limit")
    if isinstance(singular, dict):
        out = dict(singular)
        for key in ("plan_type", "allowed", "limit_reached", "credits"):
            if key in payload and key not in out:
                out[key] = payload[key]
        return out
    return None


def _normalize_rate_limits(rate_limits: dict[str, Any], source: str, plan_type: Any = None) -> dict[str, Any] | None:
    primary = _normalize_rate_window(
        rate_limits.get("primary")
        or rate_limits.get("primary_window")
        or rate_limits.get("primaryWindow")
    )
    secondary = _normalize_rate_window(
        rate_limits.get("secondary")
        or rate_limits.get("secondary_window")
        or rate_limits.get("secondaryWindow")
    )
    primary, secondary = _normalize_window_order(primary, secondary)

    out: dict[str, Any] = {
        "plan_type": _first_present(plan_type, rate_limits.get("plan_type"), rate_limits.get("planType")),
        "allowed": _first_present(rate_limits.get("allowed")),
        "limit_reached": _first_present(rate_limits.get("limit_reached"), rate_limits.get("limitReached")),
        "primary": primary,
        "secondary": secondary,
        "source": source,
    }
    credits = rate_limits.get("credits")
    if isinstance(credits, dict):
        out["credits"] = {
            "has_credits": _first_present(credits.get("has_credits"), credits.get("hasCredits")),
            "unlimited": credits.get("unlimited"),
            "balance": credits.get("balance"),
        }
    if primary is None and secondary is None and out["plan_type"] is None:
        return None
    return out


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _normalize_rate_window(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    used = _as_float(raw.get("used_percent") if "used_percent" in raw else raw.get("usedPercent"))
    limit_window_seconds = _as_int(
        raw.get("limit_window_seconds")
        if "limit_window_seconds" in raw
        else raw.get("limitWindowSeconds")
    )
    window_duration_mins = _as_int(
        raw.get("window_duration_mins")
        if "window_duration_mins" in raw
        else raw.get("windowDurationMins") if "windowDurationMins" in raw else raw.get("window_minutes")
    )
    if limit_window_seconds is None and window_duration_mins is not None:
        limit_window_seconds = window_duration_mins * 60

    reset_at = _as_int(
        raw.get("reset_at")
        if "reset_at" in raw
        else raw.get("resetAt") if "resetAt" in raw else raw.get("resets_at")
    )
    reset_after_seconds = _as_int(
        raw.get("reset_after_seconds")
        if "reset_after_seconds" in raw
        else raw.get("resetAfterSeconds")
    )
    if reset_after_seconds is None and reset_at is not None:
        reset_after_seconds = max(0, reset_at - int(datetime.now(timezone.utc).timestamp()))

    out: dict[str, Any] = {}
    if used is not None:
        out["used_percent"] = used
    if limit_window_seconds is not None:
        out["limit_window_seconds"] = limit_window_seconds
    if reset_after_seconds is not None:
        out["reset_after_seconds"] = reset_after_seconds
    if reset_at is not None:
        out["reset_at"] = reset_at
    return out or None


def _normalize_window_order(
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    primary_role = _window_role(primary)
    secondary_role = _window_role(secondary)
    if primary and secondary:
        if primary_role == "weekly" and secondary_role in {"session", "unknown"}:
            return secondary, primary
        return primary, secondary
    if primary and primary_role == "weekly":
        return None, primary
    if secondary and secondary_role in {"session", "unknown"}:
        return secondary, None
    return primary, secondary


def _window_role(window: dict[str, Any] | None) -> str:
    if not isinstance(window, dict):
        return "unknown"
    seconds = _as_int(window.get("limit_window_seconds"))
    if seconds is None:
        return "unknown"
    minutes = seconds // 60
    if minutes == _SESSION_WINDOW_MINUTES:
        return "session"
    if minutes == _WEEKLY_WINDOW_MINUTES:
        return "weekly"
    return "unknown"


def _turn_context_model(obj: dict[str, Any]) -> str | None:
    content_type = _find_first_str(obj, ["type", "payload.type"])
    if content_type != "turn_context":
        return None
    return _find_first_str(obj, ["payload.model", "payload.info.model", "model"])


def _event_text(obj: dict[str, Any]) -> str | None:
    payload_type = _find_first_str(obj, ["payload.type", "type", "record_type"])
    if payload_type:
        return payload_type[:120]
    role = _find_first_str(obj, ["role", "payload.role"])
    if role:
        return f"{role} message"
    return "local event"


def _extract_usage(obj: dict[str, Any]) -> dict[str, Any] | None:
    # Common Codex session shapes.
    candidates: list[Any] = []
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else None
    if payload:
        info = payload.get("info") if isinstance(payload.get("info"), dict) else None
        if info:
            candidates.extend(
                [
                    info.get("last_token_usage"),
                    info.get("total_token_usage"),
                ]
            )
        candidates.extend([payload.get("usage"), payload.get("token_usage")])

    candidates.extend([obj.get("usage"), obj.get("token_usage")])

    for c in candidates:
        if isinstance(c, dict) and any(k in c for k in ["input_tokens", "output_tokens", "total_tokens"]):
            return c

    # Deep scan fallback.
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
