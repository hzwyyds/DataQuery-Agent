from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.agent.graph import AgentRuntime
from app.agent.provider import OpenAICompatibleProvider
from app.agent.runs import execute_run
from app.analysis.chart import ChartService
from app.analysis.service import AnalysisService
from app.api.schemas import (
    ColumnAnnotation,
    RunCreate,
    SourceView,
    WorkspaceCreate,
    WorkspaceView,
)
from app.core.config import settings
from app.data.ingestion import SUPPORTED_SUFFIXES, IngestionService
from app.data.repository import repository
from app.data.storage import WorkspaceStorage, safe_filename
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService

router = APIRouter(prefix="/api/v1")
storage = WorkspaceStorage()
ingestion = IngestionService(repository, storage)
rag = RAGService(repository)


def agent_runtime() -> AgentRuntime:
    return AgentRuntime(
        repository=repository,
        rag=rag,
        provider=OpenAICompatibleProvider(),
        executor=DuckDBQueryExecutor(storage),
        guard=SQLGuard(),
        analysis=AnalysisService(),
        charts=ChartService(),
    )


def not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0]))


@router.post("/workspaces", response_model=WorkspaceView, status_code=201)
async def create_workspace(payload: WorkspaceCreate):
    return await repository.create_workspace(payload.name, payload.description)


@router.get("/workspaces", response_model=list[WorkspaceView])
async def list_workspaces():
    return await repository.list_workspaces()


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceView)
async def get_workspace(workspace_id: str):
    try:
        workspace = await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    workspace["source_count"] = len(await repository.list_sources(workspace_id))
    workspace["table_count"] = len(await repository.catalog(workspace_id))
    return workspace


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str):
    try:
        await repository.delete_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    storage.remove_workspace(workspace_id)
    return Response(status_code=204)


@router.get("/workspaces/{workspace_id}/sources", response_model=list[SourceView])
async def list_sources(workspace_id: str):
    try:
        await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return await repository.list_sources(workspace_id)


@router.post("/workspaces/{workspace_id}/files", response_model=SourceView, status_code=201)
async def upload_file(workspace_id: str, file: UploadFile = File(...)):
    try:
        await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    original_name = file.filename or "dataset"
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415, detail="仅支持 CSV、TSV、XLS、XLSX 和 Parquet 文件"
        )
    source_id = str(uuid4())
    stored_name = safe_filename(original_name)
    source_dir = storage.source_dir(workspace_id, source_id)
    source_dir.mkdir(parents=True, exist_ok=False)
    target = source_dir / stored_name
    size = 0
    try:
        with target.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_file_bytes:
                    raise HTTPException(status_code=413, detail="单个文件不能超过 50 MB")
                stream.write(chunk)
        if storage.workspace_bytes(workspace_id) > settings.max_workspace_bytes:
            raise HTTPException(status_code=413, detail="工作区总文件不能超过 200 MB")
        await repository.add_source(workspace_id, source_id, original_name, stored_name, size)
        await ingestion.ingest(workspace_id, source_id, target, original_name)
        await rag.index_source(workspace_id, source_id)
        return await repository.get_source(source_id)
    except Exception:
        storage.remove_source(workspace_id, source_id)
        try:
            await repository.delete_source(workspace_id, source_id)
        except KeyError:
            pass
        raise
    finally:
        await file.close()


@router.get("/workspaces/{workspace_id}/catalog")
async def catalog(workspace_id: str):
    try:
        await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return {"tables": await repository.catalog(workspace_id)}


@router.delete("/workspaces/{workspace_id}/sources/{source_id}", status_code=204)
async def delete_source(workspace_id: str, source_id: str):
    try:
        source = await repository.get_source(source_id)
        if source["workspace_id"] != workspace_id:
            raise KeyError("source not found")
        tables = await repository.source_tables(source_id)
        await ingestion.drop_source_tables(workspace_id, tables)
        if rag.config.rag_enabled:
            await rag.vectors.delete_source(workspace_id, source_id)
        await repository.delete_source(workspace_id, source_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    storage.remove_source(workspace_id, source_id)
    return Response(status_code=204)


@router.get("/workspaces/{workspace_id}/rag/status")
async def rag_status(workspace_id: str):
    try:
        await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return await rag.status()


@router.post("/workspaces/{workspace_id}/rag/reindex")
async def reindex_workspace(workspace_id: str):
    try:
        await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    sources = await repository.list_sources(workspace_id)
    results = [await rag.index_source(workspace_id, source["id"]) for source in sources]
    return {"sources": len(results), "results": results}


@router.patch("/workspaces/{workspace_id}/catalog/columns/{column_id}")
async def update_column_annotation(workspace_id: str, column_id: str, payload: ColumnAnnotation):
    aliases = list(dict.fromkeys(item.strip()[:80] for item in payload.aliases if item.strip()))
    try:
        column = await repository.update_column_annotation(
            workspace_id, column_id, payload.description.strip(), aliases
        )
    except KeyError as exc:
        raise not_found(exc) from exc
    await rag.index_source(workspace_id, column.pop("source_id"))
    return column


@router.post("/workspaces/{workspace_id}/runs", status_code=202)
async def create_run(workspace_id: str, payload: RunCreate, background: BackgroundTasks):
    try:
        run = await repository.create_run(
            workspace_id, payload.question.strip(), payload.selected_table_ids
        )
    except KeyError as exc:
        raise not_found(exc) from exc
    try:
        runtime = agent_runtime()
    except RuntimeError:
        await repository.fail_run(
            run["id"], "CONFIGURATION_ERROR", "The language model is not configured"
        )
        await repository.append_event(
            run["id"],
            "failed",
            "The language model is not configured",
            {"error_code": "CONFIGURATION_ERROR"},
            level="error",
        )
        return await repository.get_run(workspace_id, run["id"])
    background.add_task(
        execute_run,
        repository,
        runtime,
        run["id"],
        workspace_id,
        payload.question.strip(),
        payload.selected_table_ids,
    )
    return run


@router.get("/workspaces/{workspace_id}/runs")
async def list_runs(workspace_id: str, limit: int = 50):
    try:
        await repository.get_workspace(workspace_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    return {"runs": await repository.list_runs(workspace_id, limit)}


@router.get("/workspaces/{workspace_id}/runs/{run_id}")
async def get_run(workspace_id: str, run_id: str):
    try:
        return await repository.get_run(workspace_id, run_id)
    except KeyError as exc:
        raise not_found(exc) from exc


@router.get("/workspaces/{workspace_id}/runs/{run_id}/events")
async def stream_run_events(
    workspace_id: str,
    run_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    try:
        await repository.get_run(workspace_id, run_id)
    except KeyError as exc:
        raise not_found(exc) from exc
    try:
        cursor = max(after, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc

    async def events():
        nonlocal cursor
        while True:
            batch = await repository.list_events(run_id, cursor)
            for event in batch:
                cursor = event["sequence"]
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\nevent: {event['phase']}\ndata: {data}\n\n"
            run = await repository.get_run(workspace_id, run_id)
            if run["status"] in {"COMPLETED", "FAILED"} and not batch:
                break
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
