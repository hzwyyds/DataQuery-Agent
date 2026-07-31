from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.query.contracts import AnalysisResult, AnalysisSpec, QueryResult


class AnalysisError(ValueError):
    pass


def records(frame: pd.DataFrame, limit: int = 500) -> list[dict[str, Any]]:
    return frame.head(limit).replace({np.nan: None}).to_dict(orient="records")


class AnalysisService:
    max_rows = 100_000

    def run(self, spec: AnalysisSpec, result: QueryResult) -> AnalysisResult:
        if result.scope.preview_truncated and result.scope.rows_returned >= self.max_rows:
            raise AnalysisError("analysis exceeds 100,000 rows; filter or aggregate the query")
        frame = pd.DataFrame(result.rows)
        self.require_columns(frame, spec.columns)
        return getattr(self, f"_{spec.operation}")(frame, spec)

    @staticmethod
    def require_columns(frame: pd.DataFrame, columns: list[str]) -> None:
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise AnalysisError(f"analysis columns do not exist: {', '.join(missing)}")

    @staticmethod
    def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        if values.empty:
            raise AnalysisError(f"column is not numeric: {column}")
        return values

    @staticmethod
    def default_formula(spec: AnalysisSpec) -> str:
        columns = ", ".join(spec.columns) or "数值字段"
        if spec.operation == "describe":
            return f"对 {columns} 计算 count、mean、median、min、max、std"
        if spec.operation == "group_aggregate":
            return f"按 {', '.join(spec.group_by)} 分组，对 {columns} 计算 {spec.aggregation}"
        if spec.operation == "correlation":
            return f"Pearson r({spec.columns[0]}, {spec.columns[1]})"
        if spec.operation == "trend":
            return f"按时间排序，变化量 = last({spec.columns[1]}) - first({spec.columns[1]})"
        return f"IQR = Q3({columns}) - Q1({columns})；异常值在 [Q1 - 1.5IQR, Q3 + 1.5IQR] 外"

    def result_metadata(self, spec: AnalysisSpec, frame: pd.DataFrame) -> dict[str, Any]:
        return {
            "formula": spec.formula.strip() or self.default_formula(spec),
            "intent": spec.intent.strip(),
            "input_rows": int(len(frame)),
        }

    def _describe(self, frame: pd.DataFrame, spec: AnalysisSpec) -> AnalysisResult:
        columns = spec.columns or [
            column
            for column in frame.columns
            if pd.to_numeric(frame[column], errors="coerce").notna().any()
        ]
        if not columns:
            raise AnalysisError("describe requires at least one numeric column")
        rows = []
        for column in columns:
            values = self.numeric(frame, column)
            rows.append(
                {
                    "column": column,
                    "count": int(values.count()),
                    "mean": float(values.mean()),
                    "median": float(values.median()),
                    "min": float(values.min()),
                    "max": float(values.max()),
                    "std": float(values.std(ddof=0)),
                }
            )
        return AnalysisResult(
            operation=spec.operation,
            columns=["column", "count", "mean", "median", "min", "max", "std"],
            **self.result_metadata(spec, frame),
            rows=rows,
        )

    def _group_aggregate(self, frame: pd.DataFrame, spec: AnalysisSpec) -> AnalysisResult:
        if not spec.group_by or not spec.columns:
            raise AnalysisError("group_aggregate requires group_by and metric columns")
        self.require_columns(frame, [*spec.group_by, *spec.columns])
        working = frame[[*spec.group_by, *spec.columns]].copy()
        for column in spec.columns:
            working[column] = pd.to_numeric(working[column], errors="coerce")
        output = (
            working.groupby(spec.group_by, dropna=False)[spec.columns]
            .agg(spec.aggregation)
            .reset_index()
        )
        return AnalysisResult(
            operation=spec.operation,
            columns=[str(column) for column in output.columns],
            **self.result_metadata(spec, frame),
            rows=records(output),
            metrics={"groups": int(len(output)), "aggregation": spec.aggregation},
        )

    def _correlation(self, frame: pd.DataFrame, spec: AnalysisSpec) -> AnalysisResult:
        if len(spec.columns) != 2:
            raise AnalysisError("correlation requires exactly two numeric columns")
        left = self.numeric(frame, spec.columns[0])
        right = pd.to_numeric(frame.loc[left.index, spec.columns[1]], errors="coerce")
        paired = pd.DataFrame({spec.columns[0]: left, spec.columns[1]: right}).dropna()
        if len(paired) < 2:
            raise AnalysisError("correlation requires at least two complete pairs")
        return AnalysisResult(
            operation=spec.operation,
            columns=spec.columns,
            **self.result_metadata(spec, frame),
            rows=records(paired),
            metrics={
                "correlation": float(paired.corr().iloc[0, 1]),
                "pairs": int(len(paired)),
            },
        )

    def _trend(self, frame: pd.DataFrame, spec: AnalysisSpec) -> AnalysisResult:
        if len(spec.columns) != 2:
            raise AnalysisError("trend requires a time column and one numeric column")
        time_column, value_column = spec.columns
        working = (
            pd.DataFrame(
                {
                    time_column: pd.to_datetime(frame[time_column], errors="coerce"),
                    value_column: pd.to_numeric(frame[value_column], errors="coerce"),
                }
            )
            .dropna()
            .sort_values(time_column)
        )
        if len(working) < 2:
            raise AnalysisError("trend requires at least two valid observations")
        first = float(working[value_column].iloc[0])
        last = float(working[value_column].iloc[-1])
        change = last - first
        direction = "up" if change > 0 else "down" if change < 0 else "flat"
        working[time_column] = working[time_column].dt.strftime("%Y-%m-%dT%H:%M:%S")
        return AnalysisResult(
            operation=spec.operation,
            columns=[time_column, value_column],
            **self.result_metadata(spec, frame),
            rows=records(working),
            metrics={"first": first, "last": last, "change": change, "direction": direction},
        )

    def _outlier_iqr(self, frame: pd.DataFrame, spec: AnalysisSpec) -> AnalysisResult:
        if len(spec.columns) != 1:
            raise AnalysisError("outlier_iqr requires exactly one numeric column")
        column = spec.columns[0]
        values = self.numeric(frame, column)
        q1, q3 = float(values.quantile(0.25)), float(values.quantile(0.75))
        iqr = q3 - q1
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outliers = frame.loc[values[(values < lower) | (values > upper)].index]
        return AnalysisResult(
            operation=spec.operation,
            columns=[str(name) for name in outliers.columns],
            **self.result_metadata(spec, frame),
            rows=records(outliers),
            metrics={
                "q1": q1,
                "q3": q3,
                "iqr": iqr,
                "lower_bound": lower,
                "upper_bound": upper,
                "outlier_count": int(len(outliers)),
            },
        )
