import asyncio
from pathlib import Path
from uuid import uuid4

from app.agent.graph import AgentRuntime
from app.agent.provider import AnswerDraft, GroundedFinding
from app.agent.runs import execute_run
from app.analysis.chart import ChartService
from app.analysis.service import AnalysisService
from app.core.config import Settings
from app.data.ingestion import IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage
from app.query.contracts import QueryPlan
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService


class RunProvider:
    def __init__(self, table_id: str, physical_name: str):
        self.table_id = table_id
        self.physical_name = physical_name

    async def plan(self, _question, _catalog, _retrieval, validation_error=None):
        assert validation_error is None
        return QueryPlan(
            task="query",
            table_ids=[self.table_id],
            sql=f'SELECT region, amount FROM "{self.physical_name}" ORDER BY amount DESC',
        )

    async def answer(self, _question, _plan, evidence):
        row = next(item for item in evidence if '"amount": 12' in item["fact"])
        return AnswerDraft(
            summary="North has the highest amount.",
            findings=[GroundedFinding(text="The highest amount is 12.", evidence_ids=[row["id"]])],
        )


def test_run_persists_result_and_resumable_phase_events(tmp_path: Path) -> None:
    async def run() -> None:
        config = Settings(data_dir=tmp_path)
        repository = Repository(tmp_path / "metadata.sqlite3")
        storage = WorkspaceStorage(config)
        storage.ensure()
        await repository.initialize()
        workspace = await repository.create_workspace("Runs")
        source_id = str(uuid4())
        source_dir = storage.source_dir(workspace["id"], source_id)
        source_dir.mkdir(parents=True)
        csv_path = source_dir / "orders.csv"
        csv_path.write_text("region,amount\nNorth,12\nSouth,8\n", encoding="utf-8")
        await repository.add_source(
            workspace["id"], source_id, csv_path.name, csv_path.name, csv_path.stat().st_size
        )
        await IngestionService(repository, storage).ingest(
            workspace["id"], source_id, csv_path, csv_path.name
        )
        table = (await repository.catalog(workspace["id"]))[0]
        record = await repository.create_run(workspace["id"], "Highest amount", [])
        runtime = AgentRuntime(
            repository=repository,
            rag=RAGService(repository, config),
            provider=RunProvider(table["id"], table["physical_name"]),
            executor=DuckDBQueryExecutor(storage),
            guard=SQLGuard(),
            analysis=AnalysisService(),
            charts=ChartService(),
        )

        await execute_run(repository, runtime, record["id"], workspace["id"], "Highest amount", [])

        completed = await repository.get_run(workspace["id"], record["id"])
        assert completed["status"] == "COMPLETED"
        assert "North has the highest amount." in completed["payload"]["answer"]
        events = await repository.list_events(record["id"])
        assert [event["phase"] for event in events] == [
            "retrieving",
            "planning",
            "validating",
            "querying",
            "analyzing",
            "answering",
            "completed",
        ]
        assert [event["sequence"] for event in await repository.list_events(record["id"], 5)] == [
            6,
            7,
        ]

    asyncio.run(run())


def test_runs_are_bound_to_explicit_conversations(tmp_path: Path) -> None:
    async def run() -> None:
        repository = Repository(tmp_path / "metadata.sqlite3")
        await repository.initialize()
        workspace = await repository.create_workspace("Conversations")
        first = await repository.create_conversation(workspace["id"], "雨量分析")
        second = await repository.create_conversation(workspace["id"], "流量校验")
        first_run = await repository.create_run(workspace["id"], "第一问", [], first["id"])
        await repository.create_run(workspace["id"], "第二问", [], second["id"])
        runs = await repository.list_conversation_runs(first["id"])
        assert [item["id"] for item in runs] == [first_run["id"]]
        assert first_run["conversation_id"] == first["id"]

    asyncio.run(run())
