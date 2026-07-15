from contextlib import asynccontextmanager

import duckdb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.data.repository import repository
from app.data.storage import WorkspaceStorage
from app.rag.service import RAGService


@asynccontextmanager
async def lifespan(_: FastAPI):
    WorkspaceStorage().ensure()
    await repository.initialize()
    yield


app = FastAPI(title="DataQuery Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "dataquery-agent"}


@app.get("/ready")
async def ready():
    sqlite_ready = await repository.ping()
    try:
        connection = duckdb.connect(":memory:")
        duckdb_ready = connection.execute("SELECT 1").fetchone()[0] == 1
        connection.close()
    except Exception:
        duckdb_ready = False
    rag_status = await RAGService(repository).status()
    qdrant_ready = bool(rag_status.get("qdrant"))
    embedding_ready = bool(rag_status.get("embedding"))
    rag_ready = not rag_status.get("enabled") or (qdrant_ready and embedding_ready)
    payload = {
        "ready": sqlite_ready and duckdb_ready and rag_ready,
        "components": {
            "sqlite": sqlite_ready,
            "duckdb": duckdb_ready,
            "qdrant": qdrant_ready,
            "tei": embedding_ready,
            "rag_enabled": bool(rag_status.get("enabled")),
        },
    }
    return JSONResponse(payload, status_code=200 if payload["ready"] else 503)
