from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ai_meter.models import EventRecord, ProviderStatus, UsageRecord
from ai_meter.security import redact_sensitive


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'unknown',
    source TEXT,
    last_seen_at TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT,
    git_branch TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    external_id TEXT,
    project_id INTEGER,
    model TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'unknown',
    metadata_json TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS usage_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    session_id INTEGER,
    timestamp TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_creation_tokens INTEGER,
    cache_read_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost REAL,
    usage_percent REAL,
    reset_at TEXT,
    accuracy TEXT NOT NULL DEFAULT 'estimated',
    source TEXT NOT NULL DEFAULT 'unknown',
    metadata_json TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    session_id INTEGER,
    project_id INTEGER,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT,
    message TEXT,
    accuracy TEXT NOT NULL DEFAULT 'real',
    source TEXT NOT NULL DEFAULT 'unknown',
    metadata_json TEXT,
    FOREIGN KEY(session_id) REFERENCES sessions(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS file_offsets (
    path TEXT PRIMARY KEY,
    offset INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_provider ON events(provider);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_samples(timestamp);
CREATE INDEX IF NOT EXISTS idx_sessions_provider ON sessions(provider);
CREATE INDEX IF NOT EXISTS idx_projects_path ON projects(path);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.db_path))
        try:
            con.row_factory = sqlite3.Row
            yield con
            con.commit()
        finally:
            con.close()

    def _ensure_schema(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA_SQL)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _upsert_provider_status_in_conn(self, con: sqlite3.Connection, status: ProviderStatus) -> None:
        payload = json.dumps(redact_sensitive(status.metadata), ensure_ascii=False)
        con.execute(
            """
            INSERT INTO providers(name, enabled, status, source, last_seen_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                enabled = excluded.enabled,
                status = excluded.status,
                source = excluded.source,
                last_seen_at = excluded.last_seen_at,
                metadata_json = excluded.metadata_json
            """,
            (
                status.name,
                1 if status.enabled else 0,
                status.status,
                status.source,
                (status.last_seen_at or datetime.now(timezone.utc)).isoformat(),
                payload,
            ),
        )

    def upsert_provider_status(self, status: ProviderStatus) -> None:
        with self.connect() as con:
            self._upsert_provider_status_in_conn(con, status)

    def _ensure_project(self, con: sqlite3.Connection, path: str | None) -> int | None:
        if not path:
            return None
        now = self._now()
        name = Path(path).name
        con.execute(
            """
            INSERT INTO projects(path, name, git_branch, first_seen_at, last_seen_at, metadata_json)
            VALUES(?, ?, NULL, ?, ?, '{}')
            ON CONFLICT(path) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (path, name, now, now),
        )
        row = con.execute("SELECT id FROM projects WHERE path = ?", (path,)).fetchone()
        return int(row[0]) if row else None

    def _ensure_session(
        self,
        con: sqlite3.Connection,
        provider: str,
        session_external_id: str | None,
        project_id: int | None,
        source: str,
        model: str | None = None,
    ) -> int | None:
        if not session_external_id:
            return None
        existing = con.execute(
            """
            SELECT id FROM sessions
            WHERE provider = ? AND external_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (provider, session_external_id),
        ).fetchone()
        if existing:
            session_id = int(existing[0])
            con.execute(
                """
                UPDATE sessions
                SET project_id = COALESCE(?, project_id),
                    model = COALESCE(?, model),
                    source = ?,
                    status = 'active'
                WHERE id = ?
                """,
                (project_id, model, source, session_id),
            )
            return session_id
        now = self._now()
        con.execute(
            """
            INSERT INTO sessions(provider, external_id, project_id, model, started_at, status, source, metadata_json)
            VALUES(?, ?, ?, ?, ?, 'active', ?, '{}')
            """,
            (provider, session_external_id, project_id, model, now, source),
        )
        row = con.execute("SELECT last_insert_rowid() as id").fetchone()
        return int(row[0]) if row else None

    def _add_event_in_conn(
        self,
        con: sqlite3.Connection,
        event: EventRecord,
        project_cache: dict[str, int],
        session_cache: dict[tuple[str, str], int],
    ) -> None:
        project_id: int | None = None
        if event.project_path:
            if event.project_path in project_cache:
                project_id = project_cache[event.project_path]
            else:
                project_id = self._ensure_project(con, event.project_path)
                if project_id is not None:
                    project_cache[event.project_path] = project_id

        session_id: int | None = None
        if event.session_external_id:
            skey = (event.provider, event.session_external_id)
            if skey in session_cache:
                session_id = session_cache[skey]
            else:
                session_id = self._ensure_session(
                    con,
                    provider=event.provider,
                    session_external_id=event.session_external_id,
                    project_id=project_id,
                    source=event.source,
                    model=None,
                )
                if session_id is not None:
                    session_cache[skey] = session_id

        con.execute(
            """
            INSERT INTO events(
                provider, session_id, project_id, timestamp, event_type, severity,
                title, message, accuracy, source, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.provider,
                session_id,
                project_id,
                event.timestamp.isoformat(),
                event.event_type,
                event.severity,
                event.title,
                event.message,
                event.accuracy,
                event.source,
                json.dumps(redact_sensitive(event.metadata), ensure_ascii=False),
            ),
        )

    def _add_usage_in_conn(
        self,
        con: sqlite3.Connection,
        usage: UsageRecord,
        project_cache: dict[str, int],
        session_cache: dict[tuple[str, str], int],
    ) -> None:
        project_id: int | None = None
        if usage.project_path:
            if usage.project_path in project_cache:
                project_id = project_cache[usage.project_path]
            else:
                project_id = self._ensure_project(con, usage.project_path)
                if project_id is not None:
                    project_cache[usage.project_path] = project_id

        session_id: int | None = None
        if usage.session_external_id:
            skey = (usage.provider, usage.session_external_id)
            if skey in session_cache:
                session_id = session_cache[skey]
            else:
                session_id = self._ensure_session(
                    con,
                    provider=usage.provider,
                    session_external_id=usage.session_external_id,
                    project_id=project_id,
                    source=usage.source,
                    model=usage.model,
                )
                if session_id is not None:
                    session_cache[skey] = session_id

        con.execute(
            """
            INSERT INTO usage_samples(
                provider, session_id, timestamp, input_tokens, output_tokens,
                cache_creation_tokens, cache_read_tokens, total_tokens,
                estimated_cost, usage_percent, reset_at, accuracy, source, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                usage.provider,
                session_id,
                usage.timestamp.isoformat(),
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_creation_tokens,
                usage.cache_read_tokens,
                usage.total_tokens,
                usage.estimated_cost,
                usage.usage_percent,
                usage.reset_at.isoformat() if usage.reset_at else None,
                usage.accuracy,
                usage.source,
                json.dumps(redact_sensitive(usage.metadata), ensure_ascii=False),
            ),
        )

    def ingest_batch(
        self,
        provider_status: ProviderStatus | None,
        events: list[EventRecord],
        usage_samples: list[UsageRecord],
    ) -> None:
        with self.connect() as con:
            if provider_status is not None:
                self._upsert_provider_status_in_conn(con, provider_status)
            project_cache: dict[str, int] = {}
            session_cache: dict[tuple[str, str], int] = {}
            for event in events:
                self._add_event_in_conn(con, event, project_cache, session_cache)
            for usage in usage_samples:
                self._add_usage_in_conn(con, usage, project_cache, session_cache)

    def add_event(self, event: EventRecord) -> None:
        self.ingest_batch(None, [event], [])

    def add_usage(self, usage: UsageRecord) -> None:
        self.ingest_batch(None, [], [usage])

    def get_offset(self, key: str) -> int:
        with self.connect() as con:
            row = con.execute("SELECT offset FROM file_offsets WHERE path = ?", (key,)).fetchone()
            return int(row[0]) if row else 0

    def set_offset(self, key: str, offset: int) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO file_offsets(path, offset, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    offset = excluded.offset,
                    updated_at = excluded.updated_at
                """,
                (key, offset, self._now()),
            )

    def latest_usage_by_provider(self, provider: str, limit: int = 60) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """
                SELECT timestamp, input_tokens, output_tokens, total_tokens, accuracy, source
                FROM usage_samples
                WHERE provider = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (provider, limit),
            ).fetchall()

    def recent_events(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                """
                SELECT timestamp, provider, event_type, severity, title, message, accuracy, source
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def provider_status_rows(self) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute(
                "SELECT name, enabled, status, source, last_seen_at, metadata_json FROM providers ORDER BY name"
            ).fetchall()

    def export_rows(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        with self.connect() as con:
            for table in ["providers", "projects", "sessions", "usage_samples", "events"]:
                rows = con.execute(f"SELECT * FROM {table}").fetchall()
                out[table] = [dict(row) for row in rows]
        return out
