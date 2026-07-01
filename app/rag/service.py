from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from app.core.config import Settings, settings
from app.data.repository import Repository
from app.rag.embedding import EmbeddingUnavailable, TEIEmbeddingClient
from app.rag.qdrant_repository import QdrantCatalogRepository


class RAGService:
    def __init__(
        self,
        repository: Repository,
        config: Settings = settings,
        embeddings: TEIEmbeddingClient | None = None,
        vectors: QdrantCatalogRepository | None = None,
    ):
        self.repository = repository
        self.config = config
        self.embeddings = embeddings or TEIEmbeddingClient(config)
        self.vectors = vectors or QdrantCatalogRepository(config)

    @staticmethod
    def document_payloads(workspace_id: str, source_id: str, tables: list[dict]) -> list[dict]:
        payloads: list[dict] = []
        for table in tables:
            column_text = ", ".join(
                f"{column['name']} ({column['data_type']})" for column in table["columns"]
            )
            content = (
                f"table {table['display_name']}; columns: {column_text}; rows: {table['row_count']}"
            )
            payloads.append(
                {
                    "entity_type": "table",
                    "table_id": table["id"],
                    "column_id": "",
                    "label": table["display_name"],
                    "content": content,
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                }
            )
            for column in table["columns"]:
                aliases = ", ".join(column.get("aliases") or [])
                description = column.get("description") or ""
                samples = ", ".join(str(value) for value in column["sample_values"])
                content = (
                    f"column {column['name']} in table {table['display_name']}; "
                    f"type: {column['data_type']}; description: {description}; "
                    f"aliases: {aliases}; examples: {samples}"
                )[:1200]
                payloads.append(
                    {
                        "entity_type": "column",
                        "table_id": table["id"],
                        "column_id": column["id"],
                        "label": column["name"],
                        "content": content,
                        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    }
                )
        return [
            {**payload, "workspace_id": workspace_id, "source_id": source_id}
            for payload in payloads
        ]

    async def index_source(self, workspace_id: str, source_id: str) -> dict:
        if not self.config.rag_enabled:
            await self.repository.update_source_index(source_id, "DISABLED")
            return {"status": "DISABLED"}
        await self.repository.update_source_index(source_id, "INDEXING")
        try:
            tables = [
                table
                for table in await self.repository.catalog(workspace_id)
                if table["source_id"] == source_id
            ]
            payloads = self.document_payloads(workspace_id, source_id, tables)
            vectors = await self.embeddings.embed_documents(
                [payload["content"] for payload in payloads]
            )
            if any(len(vector) != self.config.embedding_size for vector in vectors):
                raise EmbeddingUnavailable("embedding dimension does not match configuration")
            await self.vectors.replace_source(workspace_id, source_id, vectors, payloads)
            await self.repository.update_source_index(source_id, "READY")
            return {"status": "READY", "documents": len(payloads)}
        except Exception as exc:
            await self.repository.update_source_index(source_id, "FAILED", str(exc)[:300])
            if self.config.rag_required:
                raise
            return {"status": "FAILED", "warning": "RAG indexing failed"}

    @staticmethod
    def tokens(text: str) -> list[str]:
        chunks = [chunk for chunk in re.split(r"[^\w]+", text.casefold()) if chunk]
        return list(dict.fromkeys([*chunks, *[char for char in text if not char.isspace()]]))

    @classmethod
    def lexical_matches(cls, question: str, catalog: list[dict], limit: int) -> list[dict]:
        query_tokens = cls.tokens(question)
        scored: list[tuple[float, dict]] = []
        for table in catalog:
            table_text = f"{table['display_name']} {table.get('physical_name', '')}".casefold()
            score = sum(1 for token in query_tokens if token in table_text)
            scored.append(
                (
                    float(score),
                    {
                        "entity_type": "table",
                        "table_id": table["id"],
                        "column_id": "",
                        "label": table["display_name"],
                        "content": table_text,
                        "score": float(score),
                        "retrieval_source": "lexical",
                    },
                )
            )
            for column in table["columns"]:
                text = " ".join(
                    [column["name"], column.get("description", ""), *column.get("aliases", [])]
                ).casefold()
                score = sum(1 for token in query_tokens if token in text)
                scored.append(
                    (
                        float(score),
                        {
                            "entity_type": "column",
                            "table_id": table["id"],
                            "column_id": column["id"],
                            "label": column["name"],
                            "content": text,
                            "score": float(score),
                            "retrieval_source": "lexical",
                        },
                    )
                )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for score, item in scored if score > 0][:limit]

    @staticmethod
    def identity(item: dict) -> str:
        return f"{item.get('entity_type')}:{item.get('column_id') or item.get('table_id')}"

    async def retrieve(
        self,
        workspace_id: str,
        question: str,
        catalog: list[dict],
        selected_table_ids: list[str] | None = None,
    ) -> dict:
        scoped = (
            [table for table in catalog if table["id"] in selected_table_ids]
            if selected_table_ids
            else catalog
        )
        lexical = self.lexical_matches(question, scoped, self.config.retrieval_limit * 2)
        if not self.config.rag_enabled:
            return {
                "mode": "DISABLED",
                "matches": lexical[: self.config.retrieval_limit],
                "warnings": [],
            }
        try:
            vector = await self.embeddings.embed_query(question)
            semantic = await self.vectors.search(
                workspace_id,
                vector,
                selected_table_ids,
                self.config.retrieval_limit,
                self.config.retrieval_score_threshold,
            )
            rank_maps = [
                {self.identity(item): rank + 1 for rank, item in enumerate(items)}
                for items in (lexical, semantic)
            ]
            fused: dict[str, dict] = {}
            for items in (lexical, semantic):
                for item in items:
                    fused.setdefault(self.identity(item), item)
            scores = defaultdict(float)
            for ranks in rank_maps:
                for identity, rank in ranks.items():
                    scores[identity] += 1 / (60 + rank)
            matches = sorted(
                fused.values(), key=lambda item: scores[self.identity(item)], reverse=True
            )[: self.config.retrieval_limit]
            for item in matches:
                identity = self.identity(item)
                item["score"] = round(scores[identity], 6)
                if identity in rank_maps[0] and identity in rank_maps[1]:
                    item["retrieval_source"] = "hybrid"
            return {"mode": "HYBRID", "matches": matches, "warnings": []}
        except Exception:
            return {
                "mode": "LEXICAL_FALLBACK",
                "matches": lexical[: self.config.retrieval_limit],
                "warnings": ["RAG service unavailable; lexical catalog matching was used"],
            }

    async def status(self) -> dict:
        if not self.config.rag_enabled:
            return {"enabled": False, "qdrant": False, "embedding": False}
        qdrant, embedding = await self.vectors.health(), await self.embeddings.health()
        return {
            "enabled": True,
            "required": self.config.rag_required,
            "qdrant": qdrant,
            "embedding": embedding,
            "ready": qdrant and embedding,
        }
