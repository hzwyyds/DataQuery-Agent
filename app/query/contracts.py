from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class QueryScope(BaseModel):
    rows_read: int = 0
    rows_returned: int = 0
    preview_truncated: bool = False
    displayed_points: int = 0
    downsampled: bool = False


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    scope: QueryScope


class AnalysisSpec(BaseModel):
    operation: Literal["describe", "group_aggregate", "correlation", "trend", "outlier_iqr"]
    columns: list[str] = Field(default_factory=list, max_length=12)
    group_by: list[str] = Field(default_factory=list, max_length=4)
    aggregation: Literal["sum", "mean", "min", "max", "count", "median"] = "mean"


class ChartSpec(BaseModel):
    type: Literal["line", "bar", "scatter"]
    x: str
    y: list[str] = Field(min_length=1, max_length=3)
    series: str | None = None


class QueryPlan(BaseModel):
    task: Literal["query", "analysis", "clarification"]
    table_ids: list[str] = Field(default_factory=list, max_length=12)
    sql: str | None = Field(default=None, max_length=20_000)
    analysis: AnalysisSpec | None = None
    presentation: Literal["text", "table", "chart"] = "table"
    chart: ChartSpec | None = None
    clarification: str | None = Field(default=None, max_length=500)
