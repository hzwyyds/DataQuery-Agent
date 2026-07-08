import asyncio
from pathlib import Path
from uuid import uuid4

from app.agent.graph import AgentRuntime, run_agent
from app.agent.provider import AnswerDraft, GroundedFinding
from app.core.config import Settings
from app.data.ingestion import IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage
from app.query.contracts import QueryPlan
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService


class FakeProvider:
    def __init__(self, table_id: str, physical_name: str, repair: bool = False):
        self.table_id = table_id
        self.physical_name = physical_name
        self.repair = repair
        self.plan_calls = 0

    async def plan(self, _question, _catalog, _retrieval, validation_error=None):
        self.plan_calls += 1
        if self.repair and validation_error is None:
            return QueryPlan(task="query", table_ids=["outside"], sql="SELECT * FROM missing")
        return QueryPlan(
            task="query",
            table_ids=[self.table_id],
            sql=(
                "SELECT region, SUM(sales_amount) AS total "
                f'FROM "{self.physical_name}" GROUP BY region'
            ),
        )

    async def answer(self, _question, _plan, evidence):
        row = next(item for item in evidence if "East" in item["fact"])
        return AnswerDraft(
            summary="East is present in the result.",
            findings=[GroundedFinding(text="East total is 120.0.", evidence_ids=[row["id"]])],
        )


def test_agent_runs_guarded_query_and_repairs_invalid_plan(tmp_path: Path) -> None:
    async def run() -> None:
        config = Settings(data_dir=tmp_path)
        repository = Repository(tmp_path / "metadata.sqlite3")
        storage = WorkspaceStorage(config)
        storage.ensure()
        await repository.initialize()
        workspace = await repository.create_workspace("Agent")
        source_id = str(uuid4())
        source_dir = storage.source_dir(workspace["id"], source_id)
        source_dir.mkdir(parents=True)
        csv_path = source_dir / "orders.csv"
        csv_path.write_text("region,sales_amount\nEast,120\nWest,80\n", encoding="utf-8")
        await repository.add_source(
            workspace["id"], source_id, csv_path.name, csv_path.name, csv_path.stat().st_size
        )
        await IngestionService(repository, storage).ingest(
            workspace["id"], source_id, csv_path, csv_path.name
        )
        table = (await repository.catalog(workspace["id"]))[0]
        provider = FakeProvider(table["id"], table["physical_name"], repair=True)
        runtime = AgentRuntime(
            repository=repository,
            rag=RAGService(repository, config),
            provider=provider,
            executor=DuckDBQueryExecutor(storage),
            guard=SQLGuard(),
        )

        result = await run_agent(runtime, workspace["id"], "Sales by region")
        assert result["answer"].startswith("East is present")
        assert result["query_result"].rows[0]["region"] in {"East", "West"}
        assert provider.plan_calls == 2

    asyncio.run(run())
