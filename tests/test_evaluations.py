import asyncio
import json
from pathlib import Path
from uuid import uuid4

from app.agent.grounding import build_evidence, validate_answer
from app.agent.provider import AnswerDraft, GroundedFinding
from app.core.config import Settings
from app.data.ingestion import IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage
from app.evaluation.metrics import agent_metrics, retrieval_metrics
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService

ROOT = Path(__file__).parents[1]


def evaluation_catalog() -> list[dict]:
    definitions = {
        "orders": {
            "id": "table-orders",
            "columns": {
                "sales_amount": [
                    "\u9500\u552e\u989d",
                    "\u8425\u6536",
                    "revenue",
                    "\u6210\u4ea4\u91d1\u989d",
                ],
                "order_date": [
                    "\u4e0b\u5355\u65e5\u671f",
                    "\u8ba2\u5355\u65f6\u95f4",
                    "order date",
                ],
                "region": ["\u533a\u57df", "\u5730\u533a", "market area"],
                "quantity": ["\u6570\u91cf", "\u4ef6\u6570", "units sold"],
                "discount_rate": [
                    "\u6298\u6263\u7387",
                    "\u4f18\u60e0\u6bd4\u4f8b",
                    "discount rate",
                ],
                "shipping_days": [
                    "\u914d\u9001\u5929\u6570",
                    "\u8fd0\u8f93\u65f6\u957f",
                    "delivery days",
                ],
                "channel": ["\u6e20\u9053", "\u6765\u6e90\u6e20\u9053", "sales channel"],
                "returned": ["\u662f\u5426\u9000\u8d27", "returned orders"],
                "order_id": ["\u8ba2\u5355\u7f16\u53f7", "order identifier"],
                "customer_id": ["\u5ba2\u6237\u7f16\u53f7", "customer id"],
                "product_id": ["\u5546\u54c1\u7f16\u53f7", "product identifier"],
            },
        },
        "returns": {
            "id": "table-returns",
            "columns": {
                "return_reason": ["\u9000\u8d27\u539f\u56e0", "return reason"],
                "refund_amount": [
                    "\u9000\u6b3e\u91d1\u989d",
                    "\u9000\u56de\u91d1\u989d",
                    "refund amount",
                ],
                "return_date": ["\u9000\u8d27\u65e5\u671f", "return date"],
                "return_id": ["\u9000\u8d27\u7f16\u53f7", "return identifier"],
            },
        },
        "products": {
            "id": "table-products",
            "columns": {
                "product_name": ["\u5546\u54c1\u540d\u79f0", "product name"],
                "category": ["\u54c1\u7c7b", "\u5206\u7c7b", "category"],
                "unit_price": ["\u5355\u4ef7", "unit price"],
                "supplier_name": ["\u4f9b\u5e94\u5546", "supplier name"],
                "stock_quantity": ["\u5e93\u5b58\u6570\u91cf", "stock quantity"],
            },
        },
    }
    catalog = []
    for table_name, definition in definitions.items():
        columns = []
        for ordinal, (name, aliases) in enumerate(definition["columns"].items()):
            columns.append(
                {
                    "id": f"column-{table_name}-{name}",
                    "name": name,
                    "data_type": "VARCHAR",
                    "ordinal": ordinal,
                    "description": "",
                    "aliases": aliases,
                    "sample_values": [],
                }
            )
        catalog.append(
            {
                "id": definition["id"],
                "display_name": table_name,
                "physical_name": table_name,
                "row_count": 1,
                "columns": columns,
            }
        )
    return catalog


def test_retrieval_evaluation_has_50_bilingual_cases_and_meets_targets() -> None:
    cases = json.loads((ROOT / "evaluations" / "retrieval_cases.json").read_text(encoding="utf-8"))
    catalog = evaluation_catalog()
    ranks = []
    for case in cases:
        matches = RAGService.lexical_matches(case["query"], catalog, 8)
        rank = next(
            (index for index, match in enumerate(matches, 1) if match["label"] == case["target"]),
            None,
        )
        ranks.append(rank)
    metrics = retrieval_metrics(ranks)
    assert len(cases) >= 50
    assert metrics["recall_at_5"] >= 0.90
    assert metrics["mrr"] >= 0.75


def test_agent_evaluation_contracts_guard_execute_and_ground_numbers(tmp_path: Path) -> None:
    cases = json.loads((ROOT / "evaluations" / "agent_cases.json").read_text(encoding="utf-8"))

    async def run() -> dict[str, float]:
        config = Settings(data_dir=tmp_path)
        repository = Repository(tmp_path / "metadata.sqlite3")
        storage = WorkspaceStorage(config)
        storage.ensure()
        await repository.initialize()
        workspace = await repository.create_workspace("Evaluation")
        paths = {}
        for filename in ("orders.csv", "returns.csv", "products.csv"):
            source_id = str(uuid4())
            source_dir = storage.source_dir(workspace["id"], source_id)
            source_dir.mkdir(parents=True)
            source_path = source_dir / filename
            source_path.write_bytes((ROOT / "evaluations" / "data" / filename).read_bytes())
            await repository.add_source(
                workspace["id"], source_id, filename, filename, source_path.stat().st_size
            )
            tables = await IngestionService(repository, storage).ingest(
                workspace["id"], source_id, source_path, filename
            )
            paths[filename.split(".")[0]] = tables[0]["physical_name"]

        executor = DuckDBQueryExecutor(storage)
        guard = SQLGuard()
        catalog = await repository.catalog(workspace["id"])
        schema = {
            table["physical_name"]: {
                column["name"]: column["data_type"] for column in table["columns"]
            }
            for table in catalog
        }
        results = []
        for case in cases:
            guarded = guard.validate(case["sql"].format(**paths), schema)
            if not guarded.allowed:
                results.append({"plan_valid": False, "executed": False, "grounded": False})
                continue
            result = await executor.execute(workspace["id"], guarded.normalized_sql)
            evidence = build_evidence(result)
            numeric = next(
                value
                for row in result.rows
                for value in row.values()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            )
            draft = AnswerDraft(
                summary="The result is grounded in the executed query.",
                findings=[
                    GroundedFinding(text=f"The measured value is {numeric}.", evidence_ids=["E1"])
                ],
            )
            results.append(
                {
                    "plan_valid": True,
                    "executed": bool(result.rows),
                    "grounded": validate_answer(draft, evidence),
                }
            )
        return agent_metrics(results)

    metrics = asyncio.run(run())
    assert len(cases) >= 40
    assert metrics["plan_validity"] >= 0.95
    assert metrics["execution_success"] >= 0.90
    assert metrics["numeric_groundedness"] == 1.0
