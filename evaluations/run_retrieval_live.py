from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import Settings
from app.data.repository import Repository
from app.evaluation.metrics import retrieval_metrics
from app.rag.service import RAGService

ROOT = Path(__file__).parents[1]
WORKSPACE_ID = "evaluation-retail-v1"
SOURCE_ID = "evaluation-retail-source-v1"


def catalog() -> list[dict]:
    columns = {
        "orders": {
            "sales_amount": [
                "\u9500\u552e\u989d",
                "\u8425\u6536",
                "revenue",
                "\u6210\u4ea4\u91d1\u989d",
            ],
            "order_date": ["\u4e0b\u5355\u65e5\u671f", "\u8ba2\u5355\u65f6\u95f4", "order date"],
            "region": ["\u533a\u57df", "\u5730\u533a", "market area"],
            "quantity": ["\u6570\u91cf", "\u4ef6\u6570", "units sold"],
            "discount_rate": ["\u6298\u6263\u7387", "\u4f18\u60e0\u6bd4\u4f8b", "discount rate"],
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
        "returns": {
            "return_reason": ["\u9000\u8d27\u539f\u56e0", "return reason"],
            "refund_amount": [
                "\u9000\u6b3e\u91d1\u989d",
                "\u9000\u56de\u91d1\u989d",
                "refund amount",
            ],
            "return_date": ["\u9000\u8d27\u65e5\u671f", "return date"],
            "return_id": ["\u9000\u8d27\u7f16\u53f7", "return identifier"],
        },
        "products": {
            "product_name": ["\u5546\u54c1\u540d\u79f0", "product name"],
            "category": ["\u54c1\u7c7b", "\u5206\u7c7b", "category"],
            "unit_price": ["\u5355\u4ef7", "unit price"],
            "supplier_name": ["\u4f9b\u5e94\u5546", "supplier name"],
            "stock_quantity": ["\u5e93\u5b58\u6570\u91cf", "stock quantity"],
        },
    }
    tables = []
    for table_name, fields in columns.items():
        tables.append(
            {
                "id": f"table-{table_name}",
                "source_id": SOURCE_ID,
                "display_name": table_name,
                "physical_name": table_name,
                "row_count": 12,
                "columns": [
                    {
                        "id": f"column-{table_name}-{name}",
                        "name": name,
                        "data_type": "VARCHAR",
                        "description": "",
                        "aliases": aliases,
                        "sample_values": [],
                    }
                    for name, aliases in fields.items()
                ],
            }
        )
    return tables


async def evaluate(output: Path) -> None:
    config = Settings(rag_enabled=True, rag_required=True)
    repository = Repository(config.database_path)
    service = RAGService(repository, config)
    if not all((await service.vectors.health(), await service.embeddings.health())):
        raise RuntimeError(
            "Qdrant and TEI must be reachable before running the live retrieval evaluation"
        )
    tables = catalog()
    payloads = service.document_payloads(WORKSPACE_ID, SOURCE_ID, tables)
    vectors = await service.embeddings.embed_documents([payload["content"] for payload in payloads])
    if any(len(vector) != config.embedding_size for vector in vectors):
        raise RuntimeError("TEI embedding dimension does not match EMBEDDING_SIZE")
    vector_repo = service.vectors
    await vector_repo.replace_source(WORKSPACE_ID, SOURCE_ID, vectors, payloads)
    try:
        cases = json.loads((ROOT / "evaluations" / "retrieval_cases.json").read_text("utf-8"))
        ranks = []
        for case in cases:
            retrieval = await service.retrieve(WORKSPACE_ID, case["query"], tables)
            ranks.append(
                next(
                    (
                        index
                        for index, match in enumerate(retrieval["matches"], 1)
                        if match["label"] == case["target"]
                    ),
                    None,
                )
            )
        report = {"mode": "live_tei_qdrant", "cases": len(cases), **retrieval_metrics(ranks)}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        await vector_repo.delete_source(WORKSPACE_ID, SOURCE_ID)
        await vector_repo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "retrieval-live.json")
    args = parser.parse_args()
    asyncio.run(evaluate(args.output))
