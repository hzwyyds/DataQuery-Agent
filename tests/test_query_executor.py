import asyncio
from pathlib import Path
from uuid import uuid4

import duckdb

from app.core.config import Settings
from app.data.storage import WorkspaceStorage
from app.query.executor import DuckDBQueryExecutor


def test_executor_reports_preview_truncation(tmp_path: Path) -> None:
    async def run() -> None:
        storage = WorkspaceStorage(Settings(data_dir=tmp_path))
        workspace_id = str(uuid4())
        warehouse = storage.warehouse_path(workspace_id)
        warehouse.parent.mkdir(parents=True)
        connection = duckdb.connect(str(warehouse))
        connection.execute("CREATE TABLE orders AS SELECT i AS order_id FROM range(600) t(i)")
        connection.close()

        result = await DuckDBQueryExecutor(storage).execute(
            workspace_id, "SELECT * FROM orders ORDER BY order_id LIMIT 501"
        )
        assert len(result.rows) == 500
        assert result.scope.rows_read == 501
        assert result.scope.preview_truncated is True
        assert result.rows[0]["order_id"] == 0

    asyncio.run(run())
