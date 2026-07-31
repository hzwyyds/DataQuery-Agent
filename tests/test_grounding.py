from app.agent.grounding import fallback_analysis_answer, render_answer, validate_answer
from app.agent.provider import AnswerDraft, GroundedFinding
from app.query.contracts import AnalysisResult

EVIDENCE = [{"id": "E1", "fact": '{"region": "East", "total": 120.5}'}]


def test_grounding_accepts_supported_numbers() -> None:
    draft = AnswerDraft(
        summary="East leads.",
        findings=[GroundedFinding(text="East total is 120.5.", evidence_ids=["E1"])],
    )

    assert validate_answer(draft, EVIDENCE)


def test_grounding_rejects_unsupported_numbers_and_evidence() -> None:
    invented = AnswerDraft(
        summary="Invented.",
        findings=[GroundedFinding(text="East total is 999.", evidence_ids=["E1"])],
    )
    unknown = AnswerDraft(
        summary="Unknown.",
        findings=[GroundedFinding(text="East total is 120.5.", evidence_ids=["E9"])],
    )

    assert not validate_answer(invented, EVIDENCE)
    assert not validate_answer(unknown, EVIDENCE)


def test_render_answer_deduplicates_summary_and_finding() -> None:
    draft = AnswerDraft(
        summary="East total is 120.5.",
        findings=[
            GroundedFinding(text="East total is 120.5.", evidence_ids=["E1"]),
            GroundedFinding(text="West is lower than East.", evidence_ids=["E1"]),
        ],
        interpretation="The result suggests East leads the returned regions.",
        limitations=["Only the returned preview is covered."],
        recommendations=["Check the underlying data before operational decisions."],
    )

    rendered = render_answer(draft)

    assert rendered.count("East total is 120.5.") == 1
    assert "## 关键发现" in rendered
    assert "## 分析解读" in rendered
    assert "### 局限" in rendered
    assert "### 建议" in rendered


def test_grounding_rejects_unsupported_numbers_in_analysis_narrative() -> None:
    draft = AnswerDraft(
        summary="相关性为 0.8。",
        interpretation="该指标基于 3 个样本。",
    )

    assert not validate_answer(draft, EVIDENCE)


def test_analysis_fallback_contains_formula_interpretation_and_limits() -> None:
    answer = fallback_analysis_answer(
        AnalysisResult(
            operation="correlation",
            columns=["sales", "discount"],
            formula="Pearson r(sales, discount)",
            input_rows=12,
            metrics={"correlation": 0.1968500901, "pairs": 12},
        )
    )

    assert "## 分析解读" in answer
    assert "## 计算公式" in answer
    assert "相关性只描述线性关联" in answer
    assert "当前分析基于 12 行" in answer
    assert "\\frac" in answer
