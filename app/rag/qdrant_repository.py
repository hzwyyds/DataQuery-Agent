from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings, settings

COLLECTION = "dataquery_catalog_v1"


class QdrantCatalogRepository:
    def __init__(self, config: Settings = settings, client: AsyncQdrantClient | None = None):
        self.config = config
        self.client = client or AsyncQdrantClient(url=config.qdrant_url, check_compatibility=False)

    async def ensure_collection(self) -> None:
        if await self.client.collection_exists(COLLECTION):
            return
        await self.client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(
                size=self.config.embedding_size,
                distance=models.Distance.COSINE,
            ),
        )

    @staticmethod
    def point_id(workspace_id: str, payload: dict) -> str:
        content_hash = (
            payload.get("content_hash")
            or hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
        )
        key = ":".join(
            [
                workspace_id,
                payload["source_id"],
                payload["entity_type"],
                payload.get("table_id", ""),
                payload.get("column_id", ""),
                content_hash,
            ]
        )
        return str(uuid5(NAMESPACE_URL, f"dataquery:{key}"))

    def _scope_filter(self, workspace_id: str, source_id: str | None = None):
        must = [
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id))
        ]
        if source_id:
            must.append(
                models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id))
            )
        return models.Filter(must=must)

    async def replace_source(
        self, workspace_id: str, source_id: str, vectors: list[list[float]], payloads: list[dict]
    ) -> None:
        await self.ensure_collection()
        await self.client.delete(
            collection_name=COLLECTION,
            points_selector=models.FilterSelector(
                filter=self._scope_filter(workspace_id, source_id)
            ),
            wait=True,
        )
        points = [
            models.PointStruct(
                id=self.point_id(workspace_id, payload),
                vector=vector,
                payload={**payload, "workspace_id": workspace_id, "source_id": source_id},
            )
            for vector, payload in zip(vectors, payloads, strict=True)
        ]
        for offset in range(0, len(points), 64):
            await self.client.upsert(
                collection_name=COLLECTION, points=points[offset : offset + 64], wait=True
            )

    async def delete_source(self, workspace_id: str, source_id: str) -> None:
        if await self.client.collection_exists(COLLECTION):
            await self.client.delete(
                collection_name=COLLECTION,
                points_selector=models.FilterSelector(
                    filter=self._scope_filter(workspace_id, source_id)
                ),
                wait=True,
            )

    async def search(
        self,
        workspace_id: str,
        vector: list[float],
        selected_table_ids: list[str] | None,
        limit: int,
        score_threshold: float,
    ) -> list[dict]:
        await self.ensure_collection()
        must = [
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id))
        ]
        if selected_table_ids:
            must.append(
                models.FieldCondition(key="table_id", match=models.MatchAny(any=selected_table_ids))
            )
        result = await self.client.query_points(
            collection_name=COLLECTION,
            query=vector,
            query_filter=models.Filter(must=must),
            limit=min(limit, 30),
            score_threshold=score_threshold,
            with_payload=True,
        )
        return [
            {
                **(point.payload or {}),
                "score": float(point.score or 0),
                "retrieval_source": "vector",
            }
            for point in result.points
        ]

    async def health(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()
