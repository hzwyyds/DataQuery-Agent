import pytest

from app.analysis.chart import ChartError, ChartService
from app.analysis.service import AnalysisError, AnalysisService
from app.query.contracts import AnalysisSpec, ChartSpec, FormulaSpec, QueryResult, QueryScope


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
        AnalysisSpec(
            operation="correlation",
            columns=["sales", "returns"],
            formula="Pearson r(sales, returns)",
            intent="衡量销售额与退货量的线性关系",
        ),
        result,
    )
    assert analysis.metrics == {"correlation": pytest.approx(1.0), "pairs": 2}
    assert analysis.formula == "Pearson r(sales, returns)"
    assert analysis.intent == "衡量销售额与退货量的线性关系"
    assert analysis.input_rows == 2


def test_nse_and_kge_use_observed_then_simulated_pairs() -> None:
    result = query_result(
        [
            {"observed": 10, "simulated": 11},
            {"observed": 20, "simulated": 19},
            {"observed": 30, "simulated": 31},
            {"observed": 40, "simulated": 39},
        ]
    )

    nse = AnalysisService().run(
        AnalysisSpec(operation="nse", columns=["observed", "simulated"]), result
    )
    kge = AnalysisService().run(
        AnalysisSpec(operation="kge", columns=["observed", "simulated"]), result
    )

    assert nse.metrics["nse"] == pytest.approx(0.992)
    assert nse.metrics["pairs"] == 4
    assert kge.metrics["kge"] == pytest.approx(0.963165, abs=0.000001)
    assert {"correlation", "alpha", "beta", "pairs"} <= kge.metrics.keys()

    combined = AnalysisService().run(
        AnalysisSpec(operation="nse_kge", columns=["observed", "simulated"]), result
    )
    assert combined.metrics["nse"] == pytest.approx(nse.metrics["nse"])
    assert combined.metrics["kge"] == pytest.approx(kge.metrics["kge"])
    assert combined.metrics["pairs"] == 4
    assert "NSE" in combined.formula and "KGE" in combined.formula


def test_formula_analysis_uses_a_safe_domain_expression() -> None:
    result = query_result([{"measured": 10, "modeled": 11}, {"measured": 20, "modeled": 18}])
    analysis = AnalysisService().run(
        AnalysisSpec(
            operation="formula",
            custom_formula=FormulaSpec(
                name="平均绝对误差",
                variables=["measured", "modeled"],
                expression="mean(abs(measured - modeled))",
            ),
        ),
        result,
    )
    assert analysis.metrics["平均绝对误差"] == pytest.approx(1.5)
    assert analysis.formula == "mean(abs(measured - modeled))"

    with pytest.raises(AnalysisError, match="允许"):
        AnalysisService().run(
            AnalysisSpec(
                operation="formula",
                custom_formula=FormulaSpec(
                    name="unsafe", variables=["measured"], expression="__import__('os')"
                ),
            ),
            result,
        )


def test_analysis_plan_normalizes_optional_null_fields_from_llm() -> None:
    spec = AnalysisSpec.model_validate(
        {
            "operation": "nse_kge",
            "columns": ["observed", "simulated"],
            "group_by": None,
            "aggregation": None,
        }
    )
    assert spec.group_by == []
    assert spec.aggregation == "mean"


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


def test_chart_accepts_twelve_requested_series() -> None:
    columns = {f"station_{index}": index for index in range(12)}
    rows = [{"date": "2026-01-01", **columns}]
    chart = ChartService().build(
        ChartSpec(type="line", x="date", y=list(columns)),
        rows,
    )
    assert chart is not None
    assert len(chart.y) == 12


def test_chart_downsamples_to_500_points_and_discloses_scope() -> None:
    rows = [{"index": index, "value": index * 2} for index in range(700)]
    chart = ChartService().build(ChartSpec(type="scatter", x="index", y=["value"]), rows)

    assert chart is not None
    assert chart.source_points == 700
    assert chart.displayed_points == 500
    assert chart.downsampled is True
    assert chart.data[0] == rows[0]
    assert chart.data[-1] == rows[-1]
