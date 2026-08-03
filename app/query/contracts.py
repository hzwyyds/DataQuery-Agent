from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


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


class AnalysisResult(BaseModel):
    operation: str
    columns: list[str]
    formula: str = ""
    intent: str = ""
    input_rows: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class FormulaSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    variables: list[str] = Field(default_factory=list, max_length=8)
    expression: str = Field(min_length=1, max_length=500)


class ChartResult(BaseModel):
    type: Literal["line", "bar", "scatter"]
    x: str
    y: list[str]
    series: str | None = None
    data: list[dict[str, Any]]
    source_points: int
    displayed_points: int
    downsampled: bool


class AnalysisSpec(BaseModel):
    operation: Literal[
        "describe",
        "group_aggregate",
        "correlation",
        "trend",
        "outlier_iqr",
        "nse",
        "kge",
        "nse_kge",
        "formula",
    ]
    columns: list[str] = Field(default_factory=list, max_length=12)
    group_by: list[str] = Field(default_factory=list, max_length=4)
    aggregation: Literal["sum", "mean", "min", "max", "count", "median"] = "mean"
    formula: str = Field(default="", max_length=500)
    intent: str = Field(default="", max_length=300)
    custom_formula: FormulaSpec | None = None

    @field_validator("group_by", mode="before")
    @classmethod
    def normalize_group_by(cls, value: list[str] | None) -> list[str]:
        return value or []

    @field_validator("aggregation", mode="before")
    @classmethod
    def normalize_aggregation(cls, value: str | None) -> str:
        return value or "mean"


class ChartSpec(BaseModel):
    type: Literal["line", "bar", "scatter"]
    x: str
    y: list[str] = Field(min_length=1, max_length=12)
    series: str | None = None


class QueryPlan(BaseModel):
    task: Literal["query", "analysis", "clarification"]
    table_ids: list[str] = Field(default_factory=list, max_length=12)
    sql: str | None = Field(default=None, max_length=20_000)
    analysis: AnalysisSpec | None = None
    presentation: Literal["text", "table", "chart"] = "table"
    chart: ChartSpec | None = None
    clarification: str | None = Field(default=None, max_length=500)
