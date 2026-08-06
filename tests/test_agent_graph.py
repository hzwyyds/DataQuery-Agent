import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from app.agent.graph import AgentRuntime, analysis_plan_error, run_agent
from app.agent.provider import AnswerDraft, GroundedFinding
from app.analysis.chart import ChartService
from app.analysis.service import AnalysisService
from app.core.config import Settings
from app.data.ingestion import IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage
from app.query.contracts import AnalysisSpec, QueryPlan
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService


class FakeProvider:
    def __init__(
        self, table_id: str, physical_name: str, repair: bool = False, analysis: bool = False
    ):
        self.table_id = table_id
        self.physical_name = physical_name
        self.repair = repair
        self.analysis = analysis
        self.malformed_answer = False
        self.plan_calls = 0
        self.last_retrieval = None

    async def plan(self, _question, _catalog, _retrieval, validation_error=None):
        self.plan_calls += 1
        self.last_retrieval = _retrieval
        if self.repair and validation_error is None:
            return QueryPlan(task="query", table_ids=["outside"], sql="SELECT * FROM missing")
        if self.analysis:
            return QueryPlan(
                task="analysis",
                table_ids=[self.table_id],
                sql=f'SELECT sales_amount, discount_rate FROM "{self.physical_name}"',
                analysis=AnalysisSpec(
                    operation="correlation",
                    columns=["sales_amount", "discount_rate"],
                    formula="Pearson r(sales_amount, discount_rate)",
                    intent="衡量销售额与折扣率的线性关系",
                ),
            )
        return QueryPlan(
            task="query",
            table_ids=[self.table_id],
            sql=(
                "SELECT region, SUM(sales_amount) AS total "
                f'FROM "{self.physical_name}" GROUP BY region'
            ),
        )

    async def answer(self, _question, _plan, evidence):
        if self.malformed_answer:
            raise ValueError("model response missed required answer fields")
        if self.analysis:
            return AnswerDraft(summary="相关性分析已完成。")
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
        for index in range(6):
            previous = await repository.create_run(
                workspace["id"], f"Previous question {index + 1}", []
            )
            await repository.complete_run(
                previous["id"], {"answer": f"Previous answer {index + 1}"}
            )
        provider = FakeProvider(table["id"], table["physical_name"], repair=True)
        runtime = AgentRuntime(
            repository=repository,
            rag=RAGService(repository, config),
            provider=provider,
            executor=DuckDBQueryExecutor(storage),
            guard=SQLGuard(),
            analysis=AnalysisService(),
            charts=ChartService(),
        )

        result = await run_agent(runtime, workspace["id"], "Sales by region")
        assert "East is present in the result." in result["answer"]
        assert result["query_result"].rows[0]["region"] in {"East", "West"}
        assert provider.plan_calls == 2
        assert [item["question"] for item in provider.last_retrieval["recent_run_context"]] == [
            "Previous question 2",
            "Previous question 3",
            "Previous question 4",
            "Previous question 5",
            "Previous question 6",
        ]

        provider.malformed_answer = True
        fallback = await run_agent(runtime, workspace["id"], "Sales by region")
        assert fallback["answer"].startswith("查询返回 2 行预览结果。")
        assert '"region": "East"' in fallback["answer"]
        assert '"total": 120' in fallback["answer"]
        assert fallback["warnings"] == []

    asyncio.run(run())


def test_agent_runs_structured_pandas_analysis(tmp_path: Path) -> None:
    async def run() -> None:
        config = Settings(data_dir=tmp_path)
        repository = Repository(tmp_path / "metadata.sqlite3")
        storage = WorkspaceStorage(config)
        storage.ensure()
        await repository.initialize()
        workspace = await repository.create_workspace("Analysis")
        source_id = str(uuid4())
        source_dir = storage.source_dir(workspace["id"], source_id)
        source_dir.mkdir(parents=True)
        csv_path = source_dir / "orders.csv"
        csv_path.write_text(
            "sales_amount,discount_rate\n100,0.1\n200,0.2\n300,0.3\n", encoding="utf-8"
        )
        await repository.add_source(
            workspace["id"], source_id, csv_path.name, csv_path.name, csv_path.stat().st_size
        )
        await IngestionService(repository, storage).ingest(
            workspace["id"], source_id, csv_path, csv_path.name
        )
        table = (await repository.catalog(workspace["id"]))[0]
        runtime = AgentRuntime(
            repository=repository,
            rag=RAGService(repository, config),
            provider=FakeProvider(table["id"], table["physical_name"], analysis=True),
            executor=DuckDBQueryExecutor(storage),
            guard=SQLGuard(),
            analysis=AnalysisService(),
            charts=ChartService(),
        )

        result = await run_agent(runtime, workspace["id"], "分析销售额与折扣率的相关性")

        assert result["analysis_result"].formula == "Pearson r(sales_amount, discount_rate)"
        assert result["analysis_result"].intent == "衡量销售额与折扣率的线性关系"
        assert result["analysis_result"].metrics["correlation"] == pytest.approx(1.0)
        assert result["analysis_result"].input_rows == 3

    asyncio.run(run())


def test_analysis_plan_validation_rejects_wrong_fixed_arity() -> None:
    invalid_trend = QueryPlan(
        task="analysis",
        table_ids=["table-1"],
        sql="SELECT date, value, month FROM measurements",
        analysis=AnalysisSpec(
            operation="trend", columns=["date", "value", "month"]
        ),
    )
    invalid_formula = QueryPlan(
        task="analysis",
        table_ids=["table-1"],
        sql="SELECT value FROM measurements",
        analysis=AnalysisSpec(operation="formula", columns=["value"]),
    )
    valid_trend = invalid_trend.model_copy(
        update={
            "analysis": AnalysisSpec(operation="trend", columns=["date", "value"])
        }
    )

    assert analysis_plan_error(invalid_trend) == "trend requires exactly 2 analysis columns"
    assert analysis_plan_error(invalid_formula) == "formula analysis requires custom_formula"
    assert analysis_plan_error(valid_trend) is None
