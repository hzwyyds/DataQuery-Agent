from __future__ import annotations

from app.agent.provider import ANSWER_DRAFT_CONTRACT, QUERY_PLAN_CONTRACT


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
