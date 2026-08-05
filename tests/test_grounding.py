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


def test_grounding_accepts_rounded_decimals_but_not_wrong_integers() -> None:
    evidence = [{"id": "A0", "fact": '{"score": 0.9553753017, "pairs": 4900}'}]
    rounded = AnswerDraft(
        summary="分数为 0.9554。",
        findings=[GroundedFinding(text="使用了 4900 个样本对。", evidence_ids=["A0"])],
    )
    wrong_count = AnswerDraft(
        summary="分数为 0.9554。",
        findings=[GroundedFinding(text="使用了 4901 个样本对。", evidence_ids=["A0"])],
    )
    assert validate_answer(rounded, evidence)
    assert not validate_answer(wrong_count, evidence)


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


def test_grounding_allows_explanatory_constants_when_conclusion_is_grounded() -> None:
    draft = AnswerDraft(
        summary="East total is 120.5.",
        interpretation="A conventional threshold such as 0 may be explained here.",
    )
    assert validate_answer(draft, EVIDENCE)


def test_grounding_accepts_ratio_rendered_as_percentage() -> None:
    evidence = [{"id": "A0", "fact": '{"ratio": 0.95}'}]
    draft = AnswerDraft(summary="The completion rate is 95%.")
    assert validate_answer(draft, evidence)


def test_grounding_accepts_one_step_decimal_derivations_in_cited_findings() -> None:
    evidence = [
        {
            "id": "A1",
            "fact": '{"max": 6529.75, "min": 240.208333, "mean": 1365.768995, '
            '"std": 1128.792679}',
        }
    ]
    draft = AnswerDraft(
        summary="流量离散程度较高。",
        findings=[
            GroundedFinding(text="极差为 6289.54。", evidence_ids=["A1"]),
            GroundedFinding(text="变异系数约为 82.6%。", evidence_ids=["A1"]),
        ],
    )
    unsupported = AnswerDraft(
        summary="流量离散程度较高。",
        findings=[GroundedFinding(text="派生值为 777.77。", evidence_ids=["A1"])],
    )

    assert validate_answer(draft, evidence)
    assert not validate_answer(unsupported, evidence)


def test_grounding_reads_iso_date_parts_as_positive_numbers() -> None:
    evidence = [
        {"id": "E1", "fact": '{"日期": "2011-01-02T00:00:00", "流量": 451.5}'}
    ]
    draft = AnswerDraft(
        summary="首条证据日期为 2011 年 1 月 2 日。",
        findings=[
            GroundedFinding(
                text="2011 年 1 月 2 日的流量为 451.5。", evidence_ids=["E1"]
            )
        ],
    )
    assert validate_answer(draft, evidence)


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
