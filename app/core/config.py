from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.getenv("DATAQUERY_DATA_DIR", PROJECT_ROOT / "data"))
    max_file_bytes: int = int(os.getenv("MAX_FILE_BYTES", 50 * 1024 * 1024))
    max_workspace_bytes: int = int(os.getenv("MAX_WORKSPACE_BYTES", 200 * 1024 * 1024))
    rag_enabled: bool = os.getenv("RAG_ENABLED", "false").lower() in {"1", "true", "yes"}
    rag_required: bool = os.getenv("RAG_REQUIRED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    qdrant_url: str = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
    embedding_url: str = os.getenv("EMBEDDING_URL", "http://127.0.0.1:8081")
    embedding_size: int = int(os.getenv("EMBEDDING_SIZE", "512"))
    embedding_timeout_seconds: float = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "20"))
    retrieval_limit: int = int(os.getenv("RETRIEVAL_LIMIT", "8"))
    retrieval_score_threshold: float = float(os.getenv("RETRIEVAL_SCORE_THRESHOLD", "0.35"))

    @property
    def database_path(self) -> Path:
        return self.data_dir / "dataquery.sqlite3"


settings = Settings()
