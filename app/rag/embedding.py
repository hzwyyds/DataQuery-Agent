from __future__ import annotations

import httpx

from app.core.config import Settings, settings


class EmbeddingUnavailable(RuntimeError):
    pass


class TEIEmbeddingClient:
    def __init__(self, config: Settings = settings):
        self.config = config

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.config.embedding_timeout_seconds) as client:
                response = await client.post(
                    f"{self.config.embedding_url.rstrip('/')}/embed",
                    json={"inputs": texts},
                )
                response.raise_for_status()
                vectors = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise EmbeddingUnavailable("embedding service is unavailable") from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingUnavailable("embedding service returned an invalid batch")
        if any(not isinstance(vector, list) for vector in vectors):
            raise EmbeddingUnavailable("embedding service returned invalid vectors")
        return [[float(value) for value in vector] for vector in vectors]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self.config.embedding_url.rstrip('/')}/health")
                return response.is_success
        except httpx.HTTPError:
            return False
