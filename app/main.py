from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.data.repository import repository
from app.data.storage import WorkspaceStorage


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
