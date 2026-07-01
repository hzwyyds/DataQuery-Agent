import asyncio
from pathlib import Path
from uuid import uuid4

from qdrant_client import AsyncQdrantClient

from app.core.config import Settings
from app.data.ingestion import IngestionService
from app.data.repository import Repository
from app.data.storage import WorkspaceStorage
from app.rag.embedding import EmbeddingUnavailable
from app.rag.qdrant_repository import COLLECTION, QdrantCatalogRepository
from app.rag.service import RAGService


class FakeEmbeddings:
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, float(index), 0.0, 0.0] for index, _ in enumerate(texts)]

    async def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    async def health(self) -> bool:
        return True


class FailingEmbeddings(FakeEmbeddings):
    async def embed_query(self, _text: str) -> list[float]:
        raise EmbeddingUnavailable("offline")


def payload(source_id: str, label: str) -> dict:
    return {
        "workspace_id": "unused",
        "source_id": source_id,
        "entity_type": "column",
        "table_id": f"table-{source_id}",
        "column_id": f"column-{source_id}",
        "label": label,
        "content": label,
        "content_hash": label,
    }


def test_qdrant_filter_isolates_workspaces_and_replace_is_idempotent(tmp_path: Path) -> None:
    async def run() -> None:
        config = Settings(data_dir=tmp_path, embedding_size=4)
        client = AsyncQdrantClient(":memory:")
        vectors = QdrantCatalogRepository(config, client)
        await vectors.replace_source("workspace-a", "a", [[1, 0, 0, 0]], [payload("a", "sales")])
        await vectors.replace_source("workspace-b", "b", [[1, 0, 0, 0]], [payload("b", "secret")])
        await vectors.replace_source("workspace-a", "a", [[1, 0, 0, 0]], [payload("a", "sales")])

        result = await vectors.search("workspace-a", [1, 0, 0, 0], None, 8, 0)
        assert {item["workspace_id"] for item in result} == {"workspace-a"}
        assert {item["label"] for item in result} == {"sales"}
        points, _ = await client.scroll(collection_name=COLLECTION, limit=20)
        assert len(points) == 2
        await client.close()

    asyncio.run(run())


def test_index_and_retrieve_catalog_with_explicit_fallback(tmp_path: Path) -> None:
    async def run() -> None:
        config = Settings(
            data_dir=tmp_path,
            embedding_size=4,
            rag_enabled=True,
            retrieval_score_threshold=0,
        )
        repository = Repository(tmp_path / "metadata.sqlite3")
        storage = WorkspaceStorage(config)
        storage.ensure()
        await repository.initialize()
        workspace = await repository.create_workspace("Retail workspace")
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
        client = AsyncQdrantClient(":memory:")
        vector_repo = QdrantCatalogRepository(config, client)
        service = RAGService(repository, config, FakeEmbeddings(), vector_repo)

        indexed = await service.index_source(workspace["id"], source_id)
        assert indexed["status"] == "READY"
        catalog = await repository.catalog(workspace["id"])
        retrieval = await service.retrieve(workspace["id"], "sales amount", catalog)
        assert retrieval["mode"] == "HYBRID"
        assert any(item["label"] == "sales_amount" for item in retrieval["matches"])

        fallback = RAGService(repository, config, FailingEmbeddings(), vector_repo)
        degraded = await fallback.retrieve(workspace["id"], "sales amount", catalog)
        assert degraded["mode"] == "LEXICAL_FALLBACK"
        assert degraded["warnings"]
        await client.close()

    asyncio.run(run())

