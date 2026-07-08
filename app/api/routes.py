from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response

from app.api.schemas import ColumnAnnotation, SourceView, WorkspaceCreate, WorkspaceView
from app.core.config import settings
from app.data.ingestion import SUPPORTED_SUFFIXES, IngestionService
from app.data.repository import repository
from app.data.storage import WorkspaceStorage, safe_filename
from app.rag.service import RAGService

router = APIRouter(prefix="/api/v1")
storage = WorkspaceStorage()
ingestion = IngestionService(repository, storage)
rag = RAGService(repository)


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
            status_code=415, detail="supported file types are CSV, XLSX, and Parquet"
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
                    raise HTTPException(status_code=413, detail="file exceeds the 50 MB limit")
                stream.write(chunk)
        if storage.workspace_bytes(workspace_id) > settings.max_workspace_bytes:
            raise HTTPException(status_code=413, detail="workspace exceeds the 200 MB limit")
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
