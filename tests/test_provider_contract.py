from __future__ import annotations

from app.agent.provider import ANSWER_DRAFT_CONTRACT, QUERY_PLAN_CONTRACT, OpenAICompatibleProvider
from app.query.contracts import QueryPlan


def test_query_plan_contract_spells_out_allowed_values() -> None:
    assert '"task": "query" | "analysis" | "clarification"' in QUERY_PLAN_CONTRACT
    assert '"presentation": "text" | "table" | "chart"' in QUERY_PLAN_CONTRACT
    assert '"analysis": null' in QUERY_PLAN_CONTRACT
    assert '"chart": null' in QUERY_PLAN_CONTRACT
    assert "A monthly SUM with a line chart is task=query" in QUERY_PLAN_CONTRACT
    assert (
        '"formula": "plain-language calculation formula, never Python code"'
        in QUERY_PLAN_CONTRACT
    )
    assert "Never output" in QUERY_PLAN_CONTRACT
    assert "Python code." in QUERY_PLAN_CONTRACT
    assert "in the same language as" in QUERY_PLAN_CONTRACT


def test_answer_contract_rejects_unstructured_answer_key() -> None:
    assert "Do not use an `answer` field." in ANSWER_DRAFT_CONTRACT
    assert '"summary"' in ANSWER_DRAFT_CONTRACT
    assert '"evidence_ids"' in ANSWER_DRAFT_CONTRACT
    assert '"interpretation"' in ANSWER_DRAFT_CONTRACT
    assert '"limitations"' in ANSWER_DRAFT_CONTRACT
    assert '"recommendations"' in ANSWER_DRAFT_CONTRACT
    assert "Do not repeat the summary verbatim" in ANSWER_DRAFT_CONTRACT


def test_explicit_hydrology_pair_repairs_over_conservative_clarification() -> None:
    catalog = [
        {
            "id": "table-1",
            "display_name": "石鼓 / 数据库",
            "physical_name": "t_data",
            "columns": [{"name": "奔子栏流量"}, {"name": "石鼓流量"}],
        }
    ]
    plan = OpenAICompatibleProvider._hydrology_fallback(
        "请将数据库中的奔子栏流量作为观测值、石鼓流量作为模拟值，计算NSE和KGE。",
        catalog,
        QueryPlan(task="clarification", clarification="请补充字段"),
    )
    assert plan is not None
    assert plan.analysis is not None
    assert plan.analysis.operation == "nse_kge"
    assert plan.analysis.columns == ["observed", "simulated"]
    assert 'FROM "t_data"' in (plan.sql or "")
