import pytest

from app.analysis.chart import ChartError, ChartService
from app.analysis.service import AnalysisError, AnalysisService
from app.query.contracts import AnalysisSpec, ChartSpec, QueryResult, QueryScope


def query_result(rows: list[dict], *, truncated: bool = False) -> QueryResult:
    return QueryResult(
        columns=list(rows[0]) if rows else [],
        rows=rows,
        scope=QueryScope(
            rows_read=len(rows) + int(truncated),
            rows_returned=len(rows),
            preview_truncated=truncated,
        ),
    )


def test_correlation_requires_two_explicit_columns() -> None:
    service = AnalysisService()
    result = query_result([{"sales": 10, "returns": 1}, {"sales": 20, "returns": 2}])

    with pytest.raises(AnalysisError, match="exactly two"):
        service.run(AnalysisSpec(operation="correlation", columns=["sales"]), result)

    analysis = service.run(
        AnalysisSpec(operation="correlation", columns=["sales", "returns"]), result
    )
    assert analysis.metrics == {"correlation": pytest.approx(1.0), "pairs": 2}


def test_outlier_iqr_returns_only_outlying_rows() -> None:
    rows = [{"order": index, "amount": value} for index, value in enumerate([1, 2, 2, 3, 100])]
    analysis = AnalysisService().run(
        AnalysisSpec(operation="outlier_iqr", columns=["amount"]), query_result(rows)
    )

    assert analysis.metrics["outlier_count"] == 1
    assert analysis.rows == [{"order": 4, "amount": 100}]


def test_chart_is_nullable_and_validates_fields() -> None:
    service = ChartService()
    rows = [{"month": "2026-01", "sales": 10}]

    assert service.build(None, rows) is None
    with pytest.raises(ChartError, match="fields do not exist"):
        service.build(ChartSpec(type="line", x="date", y=["sales"]), rows)


def test_chart_downsamples_to_500_points_and_discloses_scope() -> None:
    rows = [{"index": index, "value": index * 2} for index in range(700)]
    chart = ChartService().build(ChartSpec(type="scatter", x="index", y=["value"]), rows)

    assert chart is not None
    assert chart.source_points == 700
    assert chart.displayed_points == 500
    assert chart.downsampled is True
    assert chart.data[0] == rows[0]
    assert chart.data[-1] == rows[-1]
