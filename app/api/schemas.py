from __future__ import annotations

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)


class WorkspaceView(BaseModel):
    id: str
    name: str
    description: str
    created_at: str
    updated_at: str
    source_count: int = 0
    table_count: int = 0


class SourceView(BaseModel):
    id: str
    workspace_id: str
    original_name: str
    size_bytes: int
    index_status: str
    index_error: str | None = None
    created_at: str


class ColumnAnnotation(BaseModel):
    description: str = Field(default="", max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=20)
