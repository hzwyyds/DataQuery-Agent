from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from app.core.config import settings

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    index_status TEXT NOT NULL DEFAULT 'PENDING',
    index_error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_tables (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    physical_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    UNIQUE(workspace_id, physical_name)
);
CREATE TABLE IF NOT EXISTS catalog_columns (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    table_id TEXT NOT NULL REFERENCES catalog_tables(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    null_count INTEGER NOT NULL,
    distinct_count INTEGER NOT NULL,
    sample_values TEXT NOT NULL DEFAULT '[]',
    description TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '[]',
    UNIQUE(table_id, name)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    phase TEXT NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_sources_workspace ON sources(workspace_id);
CREATE INDEX IF NOT EXISTS idx_tables_workspace ON catalog_tables(workspace_id);
CREATE INDEX IF NOT EXISTS idx_columns_workspace ON catalog_columns(workspace_id);
CREATE INDEX IF NOT EXISTS idx_runs_workspace ON runs(workspace_id, created_at DESC);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Repository:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.database_path

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            await connection.close()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    async def create_workspace(self, name: str, description: str = "") -> dict:
        workspace_id = str(uuid4())
        timestamp = now()
        async with self.connect() as connection:
            await connection.execute(
                "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?)",
                (workspace_id, name, description, timestamp, timestamp),
            )
            await connection.commit()
        return await self.get_workspace(workspace_id)

    async def list_workspaces(self) -> list[dict]:
        async with self.connect() as connection:
            cursor = await connection.execute(
                """SELECT w.*, COUNT(DISTINCT s.id) AS source_count,
                          COUNT(DISTINCT t.id) AS table_count
                   FROM workspaces w
                   LEFT JOIN sources s ON s.workspace_id = w.id
                   LEFT JOIN catalog_tables t ON t.workspace_id = w.id
                   GROUP BY w.id ORDER BY w.updated_at DESC"""
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_workspace(self, workspace_id: str) -> dict:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("workspace not found")
        return dict(row)

    async def delete_workspace(self, workspace_id: str) -> None:
        await self.get_workspace(workspace_id)
        async with self.connect() as connection:
            await connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
            await connection.commit()

    async def add_source(
        self, workspace_id: str, source_id: str, original_name: str, stored_name: str, size: int
    ) -> dict:
        async with self.connect() as connection:
            await connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?, ?, 'PENDING', NULL, ?)",
                (source_id, workspace_id, original_name, stored_name, size, now()),
            )
            await connection.execute(
                "UPDATE workspaces SET updated_at = ? WHERE id = ?", (now(), workspace_id)
            )
            await connection.commit()
        return await self.get_source(source_id)

    async def get_source(self, source_id: str) -> dict:
        async with self.connect() as connection:
            cursor = await connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,))
            row = await cursor.fetchone()
        if row is None:
            raise KeyError("source not found")
        return dict(row)

    async def replace_catalog(self, source_id: str, tables: list[dict]) -> None:
        async with self.connect() as connection:
            await connection.execute("DELETE FROM catalog_tables WHERE source_id = ?", (source_id,))
            for table in tables:
                await connection.execute(
                    "INSERT INTO catalog_tables VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        table["id"],
                        table["workspace_id"],
                        source_id,
                        table["physical_name"],
                        table["display_name"],
                        table["row_count"],
                    ),
                )
                for column in table["columns"]:
                    await connection.execute(
                        """INSERT INTO catalog_columns
                           (id, workspace_id, table_id, name, data_type, ordinal, null_count,
                            distinct_count, sample_values, description, aliases)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '[]')""",
                        (
                            column["id"],
                            table["workspace_id"],
                            table["id"],
                            column["name"],
                            column["data_type"],
                            column["ordinal"],
                            column["null_count"],
                            column["distinct_count"],
                            json.dumps(column["sample_values"], default=str),
                        ),
                    )
            await connection.commit()

    async def catalog(self, workspace_id: str) -> list[dict]:
        async with self.connect() as connection:
            table_cursor = await connection.execute(
                "SELECT * FROM catalog_tables WHERE workspace_id = ? ORDER BY display_name",
                (workspace_id,),
            )
            tables = [dict(row) for row in await table_cursor.fetchall()]
            column_cursor = await connection.execute(
                """SELECT * FROM catalog_columns WHERE workspace_id = ?
                   ORDER BY table_id, ordinal""",
                (workspace_id,),
            )
            columns = [dict(row) for row in await column_cursor.fetchall()]
        grouped: dict[str, list[dict]] = {}
        for column in columns:
            column["sample_values"] = json.loads(column["sample_values"])
            column["aliases"] = json.loads(column["aliases"])
            grouped.setdefault(column["table_id"], []).append(column)
        for table in tables:
            table["columns"] = grouped.get(table["id"], [])
        return tables

    async def source_tables(self, source_id: str) -> list[dict]:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM catalog_tables WHERE source_id = ?", (source_id,)
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def delete_source(self, workspace_id: str, source_id: str) -> None:
        source = await self.get_source(source_id)
        if source["workspace_id"] != workspace_id:
            raise KeyError("source not found")
        async with self.connect() as connection:
            await connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            await connection.commit()

    async def update_source_index(
        self, source_id: str, status: str, error: str | None = None
    ) -> None:
        async with self.connect() as connection:
            await connection.execute(
                "UPDATE sources SET index_status = ?, index_error = ? WHERE id = ?",
                (status, error, source_id),
            )
            await connection.commit()

    async def update_column_annotation(
        self, workspace_id: str, column_id: str, description: str, aliases: list[str]
    ) -> dict:
        async with self.connect() as connection:
            cursor = await connection.execute(
                """UPDATE catalog_columns SET description = ?, aliases = ?
                   WHERE id = ? AND workspace_id = ?""",
                (description, json.dumps(aliases), column_id, workspace_id),
            )
            if cursor.rowcount != 1:
                raise KeyError("column not found")
            await connection.commit()
        for table in await self.catalog(workspace_id):
            for column in table["columns"]:
                if column["id"] == column_id:
                    return {**column, "source_id": table["source_id"]}
        raise KeyError("column not found")

    async def list_sources(self, workspace_id: str) -> list[dict]:
        async with self.connect() as connection:
            cursor = await connection.execute(
                "SELECT * FROM sources WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]


repository = Repository()
