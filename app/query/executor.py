from __future__ import annotations

import asyncio
import csv
import io
from decimal import Decimal
from typing import Any

import duckdb

from app.data.storage import WorkspaceStorage
from app.query.contracts import QueryResult, QueryScope


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class QueryTimeout(RuntimeError):
    pass


class QueryTooLarge(RuntimeError):
    pass


class DuckDBQueryExecutor:
    def __init__(self, storage: WorkspaceStorage):
        self.storage = storage

    def connect(self, workspace_id: str) -> duckdb.DuckDBPyConnection:
        warehouse = self.storage.warehouse_path(workspace_id)
        if not warehouse.is_file():
            raise FileNotFoundError("workspace warehouse does not exist")
        connection = duckdb.connect(str(warehouse), read_only=True)
        connection.execute("SET enable_external_access = false")
        connection.execute("SET memory_limit = '512MB'")
        connection.execute("SET threads = 2")
        return connection

    async def execute(
        self,
        workspace_id: str,
        sql: str,
        *,
        max_rows: int = 500,
        timeout_seconds: float = 15,
    ) -> QueryResult:
        connection = self.connect(workspace_id)

        def run() -> tuple[list[str], list[tuple]]:
            cursor = connection.execute(sql)
            columns = [item[0] for item in cursor.description]
            return columns, cursor.fetchmany(max_rows + 1)

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            columns, raw_rows = await asyncio.wait_for(task, timeout_seconds)
        except TimeoutError as exc:
            connection.interrupt()
            try:
                await asyncio.wait_for(task, 2)
            except Exception:
                pass
            raise QueryTimeout(f"query exceeded {timeout_seconds:g} seconds") from exc
        finally:
            connection.close()
        truncated = len(raw_rows) > max_rows
        rows = raw_rows[:max_rows]
        serialized = [
            {column: json_value(value) for column, value in zip(columns, row, strict=True)}
            for row in rows
        ]
        return QueryResult(
            columns=columns,
            rows=serialized,
            scope=QueryScope(
                rows_read=len(raw_rows),
                rows_returned=len(serialized),
                preview_truncated=truncated,
            ),
        )

    async def count_rows(
        self, workspace_id: str, sql: str, *, max_rows: int, timeout_seconds: float = 15
    ) -> int:
        connection = self.connect(workspace_id)
        statement = f"SELECT COUNT(*) FROM ({sql.rstrip(';')}) AS dataquery_count"

        def run() -> int:
            return int(connection.execute(statement).fetchone()[0])

        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            count = await asyncio.wait_for(task, timeout_seconds)
        except TimeoutError as exc:
            connection.interrupt()
            raise QueryTimeout(f"query exceeded {timeout_seconds:g} seconds") from exc
        finally:
            connection.close()
        if count > max_rows:
            raise QueryTooLarge(
                "当前分析需要读取超过 1 亿行，已停止执行；请先按时间、区域或指标筛选，或先聚合"
            )
        return count

    async def execute_chart(
        self,
        workspace_id: str,
        sql: str,
        *,
        max_points: int = 500,
        max_rows: int = 100_000_000,
    ) -> tuple[QueryResult, int]:
        source_points = await self.count_rows(workspace_id, sql, max_rows=max_rows)
        if source_points <= max_points:
            return (
                await self.execute(workspace_id, sql, max_rows=max_points),
                source_points,
            )
        sampled_sql = f"""
        WITH dataquery_source AS ({sql.rstrip(";")}),
        dataquery_numbered AS (
            SELECT *,
                   ROW_NUMBER() OVER () AS __dataquery_row,
                   COUNT(*) OVER () AS __dataquery_total
            FROM dataquery_source
        ),
        dataquery_bucketed AS (
            SELECT *,
                   FLOOR((__dataquery_row - 1) * {max_points} / __dataquery_total)
                       AS __dataquery_bucket
            FROM dataquery_numbered
        ),
        dataquery_sampled AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY __dataquery_bucket ORDER BY __dataquery_row
                   ) AS __dataquery_bucket_row
            FROM dataquery_bucketed
        )
        SELECT * EXCLUDE (
            __dataquery_row,
            __dataquery_total,
            __dataquery_bucket,
            __dataquery_bucket_row
        )
        FROM dataquery_sampled
        WHERE __dataquery_bucket_row = 1
        ORDER BY __dataquery_row
        """
        sampled = await self.execute(workspace_id, sampled_sql, max_rows=max_points)
        return sampled, source_points

    def stream_csv(self, workspace_id: str, sql: str, batch_size: int = 10_000):
        connection = self.connect(workspace_id)
        try:
            cursor = connection.execute(sql)
            columns = [item[0] for item in cursor.description]
            header = io.StringIO()
            csv.writer(header).writerow(columns)
            yield "\ufeff" + header.getvalue()
            while rows := cursor.fetchmany(batch_size):
                stream = io.StringIO()
                writer = csv.writer(stream)
                writer.writerows(
                    [json_value(value) for value in row]
                    for row in rows
                )
                yield stream.getvalue()
        finally:
            connection.close()

    async def explain(self, workspace_id: str, sql: str) -> list[list[str]]:
        connection = self.connect(workspace_id)
        try:
            rows = await asyncio.to_thread(lambda: connection.execute(f"EXPLAIN {sql}").fetchall())
            return [[str(value) for value in row] for row in rows]
        finally:
            connection.close()
