from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pandas as pd

from app.data.repository import Repository
from app.data.storage import WorkspaceStorage

SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".parquet"}
_IDENTIFIER = re.compile(r"[^A-Za-z0-9_]+")


def quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def slug(value: str) -> str:
    normalized = _IDENTIFIER.sub("_", value).strip("_").lower()
    return normalized[:40] or "table"


def json_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


class IngestionService:
    def __init__(self, repository: Repository, storage: WorkspaceStorage):
        self.repository = repository
        self.storage = storage

    async def ingest(
        self, workspace_id: str, source_id: str, path: Path, original_name: str
    ) -> list[dict]:
        tables = await asyncio.to_thread(
            self._ingest_sync, workspace_id, source_id, path, original_name
        )
        await self.repository.replace_catalog(source_id, tables)
        return tables

    def _ingest_sync(
        self, workspace_id: str, source_id: str, path: Path, original_name: str
    ) -> list[dict]:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError("supported file types are CSV, XLSX, and Parquet")
        warehouse = self.storage.warehouse_path(workspace_id)
        warehouse.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(warehouse))
        created: list[str] = []
        result: list[dict] = []
        try:
            if suffix == ".xlsx":
                workbook = pd.ExcelFile(path)
                entries = [
                    (sheet, pd.read_excel(path, sheet_name=sheet)) for sheet in workbook.sheet_names
                ]
                entries = [(name, frame) for name, frame in entries if not frame.empty]
                if not entries:
                    raise ValueError("the workbook does not contain a non-empty sheet")
                for index, (sheet, frame) in enumerate(entries, 1):
                    physical = f"t_{source_id.replace('-', '')[:12]}_{index}_{slug(sheet)}"
                    connection.register("incoming_frame", frame)
                    connection.execute(
                        f"CREATE OR REPLACE TABLE {quote(physical)} AS SELECT * FROM incoming_frame"
                    )
                    connection.unregister("incoming_frame")
                    created.append(physical)
                    result.append(
                        self._profile(
                            connection,
                            workspace_id,
                            source_id,
                            physical,
                            f"{Path(original_name).stem} / {sheet}",
                        )
                    )
            else:
                physical = f"t_{source_id.replace('-', '')[:12]}_{slug(Path(original_name).stem)}"
                reader = "read_csv_auto" if suffix == ".csv" else "read_parquet"
                options = ", header = true" if suffix == ".csv" else ""
                escaped = str(path.resolve()).replace("'", "''")
                connection.execute(
                    f"CREATE OR REPLACE TABLE {quote(physical)} AS "
                    f"SELECT * FROM {reader}('{escaped}'{options})"
                )
                created.append(physical)
                result.append(
                    self._profile(
                        connection, workspace_id, source_id, physical, Path(original_name).stem
                    )
                )
            return result
        except Exception:
            for table in created:
                connection.execute(f"DROP TABLE IF EXISTS {quote(table)}")
            raise
        finally:
            connection.close()

    def _profile(
        self,
        connection: duckdb.DuckDBPyConnection,
        workspace_id: str,
        source_id: str,
        physical_name: str,
        display_name: str,
    ) -> dict:
        table_id = str(uuid4())
        row_count = int(
            connection.execute(f"SELECT COUNT(*) FROM {quote(physical_name)}").fetchone()[0]
        )
        schema = connection.execute(f"PRAGMA table_info({quote(physical_name)})").fetchall()
        columns: list[dict] = []
        for ordinal, row in enumerate(schema):
            name = str(row[1])
            data_type = str(row[2])
            column_sql = quote(name)
            null_count, distinct_count = connection.execute(
                f"SELECT COUNT(*) FILTER (WHERE {column_sql} IS NULL), "
                f"COUNT(DISTINCT {column_sql}) FROM {quote(physical_name)}"
            ).fetchone()
            samples = connection.execute(
                f"SELECT DISTINCT {column_sql} FROM {quote(physical_name)} "
                f"WHERE {column_sql} IS NOT NULL LIMIT 5"
            ).fetchall()
            columns.append(
                {
                    "id": str(uuid4()),
                    "name": name,
                    "data_type": data_type,
                    "ordinal": ordinal,
                    "null_count": int(null_count),
                    "distinct_count": int(distinct_count),
                    "sample_values": [json_value(item[0]) for item in samples],
                }
            )
        return {
            "id": table_id,
            "workspace_id": workspace_id,
            "source_id": source_id,
            "physical_name": physical_name,
            "display_name": display_name,
            "row_count": row_count,
            "columns": columns,
        }

    async def drop_source_tables(self, workspace_id: str, tables: list[dict]) -> None:
        warehouse = self.storage.warehouse_path(workspace_id)
        if not warehouse.exists() or not tables:
            return

        def drop() -> None:
            connection = duckdb.connect(str(warehouse))
            try:
                for table in tables:
                    connection.execute(f"DROP TABLE IF EXISTS {quote(table['physical_name'])}")
            finally:
                connection.close()

        await asyncio.to_thread(drop)
