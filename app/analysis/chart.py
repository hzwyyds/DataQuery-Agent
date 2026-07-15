from __future__ import annotations

import math
from typing import Any

from app.query.contracts import ChartResult, ChartSpec


class ChartError(ValueError):
    pass


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class ChartService:
    max_points = 500

    def build(self, spec: ChartSpec | None, rows: list[dict]) -> ChartResult | None:
        if spec is None:
            return None
        if not rows:
            raise ChartError("chart requires at least one result row")
        fields = set(rows[0])
        requested = [spec.x, *spec.y, *([spec.series] if spec.series else [])]
        missing = [field for field in requested if field not in fields]
        if missing:
            raise ChartError(f"chart fields do not exist: {', '.join(missing)}")
        for field in spec.y:
            values = [row.get(field) for row in rows if row.get(field) is not None]
            if not values or not all(is_number(value) for value in values):
                raise ChartError(f"chart y field is not numeric: {field}")
        if spec.type == "scatter":
            x_values = [row.get(spec.x) for row in rows if row.get(spec.x) is not None]
            if not x_values or not all(is_number(value) for value in x_values):
                raise ChartError("scatter charts require a numeric x field")
        source_points = len(rows)
        if source_points <= self.max_points:
            data = rows
        else:
            indexes = [
                round(index * (source_points - 1) / (self.max_points - 1))
                for index in range(self.max_points)
            ]
            data = [rows[index] for index in indexes]
        return ChartResult(
            type=spec.type,
            x=spec.x,
            y=spec.y,
            series=spec.series,
            data=data,
            source_points=source_points,
            displayed_points=len(data),
            downsampled=source_points > self.max_points,
        )
