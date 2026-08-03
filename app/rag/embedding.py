from __future__ import annotations

import httpx

from app.core.config import Settings, settings


class EmbeddingUnavailable(RuntimeError):
    pass


class TEIEmbeddingClient:
    max_batch_size = 32

    def __init__(self, config: Settings = settings):
        self.config = config

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with httpx.AsyncClient(timeout=self.config.embedding_timeout_seconds) as client:
                vectors: list[list[float]] = []
                for start in range(0, len(texts), self.max_batch_size):
                    batch = texts[start : start + self.max_batch_size]
                    response = await client.post(
                        f"{self.config.embedding_url.rstrip('/')}/embed",
                        json={"inputs": batch},
                    )
                    response.raise_for_status()
                    batch_vectors = response.json()
                    if not isinstance(batch_vectors, list) or len(batch_vectors) != len(batch):
                        raise EmbeddingUnavailable("embedding service returned an invalid batch")
                    vectors.extend(batch_vectors)
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
