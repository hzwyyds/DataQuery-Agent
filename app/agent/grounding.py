from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from app.agent.provider import AnswerDraft
from app.query.contracts import AnalysisResult, QueryResult

NUMBER = re.compile(r"(?<![A-Za-z_\d])-?\d+(?:\.\d+)?%?")


def build_evidence(result: QueryResult, analysis: AnalysisResult | None = None) -> list[dict]:
    evidence = [
        {
            "id": "E0",
            "fact": (
                f"The query returned {result.scope.rows_returned} preview rows; "
                f"preview_truncated={str(result.scope.preview_truncated).lower()}."
            ),
        }
    ]
    for index, row in enumerate(result.rows[:20], 1):
        evidence.append(
            {
                "id": f"E{index}",
                "fact": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
            }
        )
    if analysis is not None:
        evidence.append(
            {
                "id": "A0",
                "fact": json.dumps(
                    {
                        "operation": analysis.operation,
                        "formula": analysis.formula,
                        "intent": analysis.intent,
                        "input_rows": analysis.input_rows,
                        "metrics": analysis.metrics,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
            }
        )
        for index, row in enumerate(analysis.rows[:20], 1):
            evidence.append(
                {
                    "id": f"A{index}",
                    "fact": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
                }
            )
    return evidence


def normalized_numbers(text: str) -> set[str]:
    normalized: set[str] = set()
    for value in NUMBER.findall(text):
        raw = value.removesuffix("%").lstrip("+")
        try:
            normalized.add(str(Decimal(raw).normalize()))
        except InvalidOperation:
            normalized.add(raw)
    return normalized


def numeric_tokens(text: str) -> list[tuple[Decimal, bool]]:
    tokens = []
    for value in NUMBER.findall(text):
        is_percent = value.endswith("%")
        raw = value.removesuffix("%").lstrip("+")
        try:
            number = Decimal(raw) / 100 if is_percent else Decimal(raw)
            tokens.append((number, "." not in raw and not is_percent))
        except InvalidOperation:
            continue
    return tokens


def numbers_supported(
    claim: str, evidence_text: str, *, allow_derived: bool = False
) -> bool:
    supported = numeric_tokens(evidence_text)
    for value, is_integer in numeric_tokens(claim):
        if is_integer:
            if not any(value == candidate for candidate, _ in supported):
                return False
            continue
        decimals = max(1, -value.as_tuple().exponent)
        tolerance = Decimal("0.5") * (Decimal(10) ** -decimals)
        if any(abs(value - candidate) <= tolerance for candidate, _ in supported):
            continue
        if allow_derived:
            values = [candidate for candidate, _ in supported]
            derived = []
            for left in values:
                for right in values:
                    derived.extend((left + right, left - right, left * right))
                    if right != 0:
                        derived.append(left / right)
            if any(abs(value - candidate) <= tolerance for candidate in derived):
                continue
        return False
    return True


def validate_answer(draft: AnswerDraft, evidence: list[dict]) -> bool:
    by_id = {item["id"]: item["fact"] for item in evidence}
    supported = "\n".join(by_id.values())
    # Conclusions stay strict. Explanatory sections may contain formula constants,
    # conventional thresholds, or suggested targets that are not result facts.
    if not numbers_supported(draft.summary, supported, allow_derived=True):
        return False
    for finding in draft.findings:
        if any(identity not in by_id for identity in finding.evidence_ids):
            return False
        finding_supported = "\n".join(by_id[identity] for identity in finding.evidence_ids)
        if not numbers_supported(finding.text, finding_supported, allow_derived=True):
            return False
    return True


def _normalized_text(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" -*•:：")


def _unique(items: list[str], seen: set[str]) -> list[str]:
    unique: list[str] = []
    for item in items:
        value = item.strip()
        key = _normalized_text(value)
        duplicate = key in seen or any(
            len(key) >= 24 and (key in previous or previous in key) for previous in seen
        )
        if not value or not key or duplicate:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def render_answer(draft: AnswerDraft) -> str:
    summary = draft.summary.strip()
    seen = {_normalized_text(summary)} if summary else set()
    findings = _unique([finding.text for finding in draft.findings], seen)
    interpretation = draft.interpretation.strip()
    if interpretation and _normalized_text(interpretation) in seen:
        interpretation = ""
    elif interpretation:
        seen.add(_normalized_text(interpretation))
    limitations = _unique(draft.limitations, seen)
    recommendations = _unique(draft.recommendations, seen)
    caveats = _unique(draft.caveats, seen)

    sections: list[str] = []
    if summary:
        sections.extend(["## 结论", summary])
    if findings:
        sections.extend(["## 关键发现", "\n".join(f"- {item}" for item in findings)])
    if interpretation:
        sections.extend(["## 分析解读", interpretation])
    if limitations or recommendations or caveats:
        sections.append("## 局限与建议")
        if limitations:
            sections.extend(["### 局限", "\n".join(f"- {item}" for item in limitations)])
        if caveats:
            sections.extend(["### 注意事项", "\n".join(f"- {item}" for item in caveats)])
        if recommendations:
            sections.extend(["### 建议", "\n".join(f"- {item}" for item in recommendations)])
    return "\n\n".join(item for item in sections if item)


def fallback_answer(result: QueryResult, analysis: AnalysisResult | None = None) -> str:
    if analysis is not None:
        return fallback_analysis_answer(analysis)
    suffix = " 预览已截断。" if result.scope.preview_truncated else ""
    rows = [
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        for row in result.rows[:20]
    ]
    rendered_rows = "\n".join(f"- {row}" for row in rows)
    return (
        f"查询返回 {result.scope.rows_returned} 行预览结果。{suffix}"
        + (f"\n计算结果：\n{rendered_rows}" if rendered_rows else "")
    )


def fallback_analysis_answer(analysis: AnalysisResult) -> str:
    operation_labels = {
        "describe": "描述统计",
        "group_aggregate": "分组聚合",
        "correlation": "Pearson 相关性",
        "trend": "趋势分析",
        "outlier_iqr": "IQR 异常值检测",
        "nse": "Nash-Sutcliffe 效率系数（NSE）",
        "kge": "Kling-Gupta 效率系数（KGE）",
        "nse_kge": "Nash-Sutcliffe 与 Kling-Gupta 效率系数（NSE / KGE）",
    }
    operation = operation_labels.get(analysis.operation, analysis.operation)
    sections = [
        "## 结论",
        f"已完成{operation}，Pandas 使用 {analysis.input_rows} 行输入数据计算。",
        "## 关键发现",
    ]
    if analysis.metrics:
        sections.append(
            "\n".join(
                f"- {key}：{json.dumps(value, ensure_ascii=False, default=str)}"
                for key, value in analysis.metrics.items()
            )
        )
    else:
        sections.append("- 结果明细见分析页中的计算表格。")
    if analysis.operation == "correlation" and "correlation" in analysis.metrics:
        coefficient = float(analysis.metrics["correlation"])
        strength = "弱"
        if abs(coefficient) >= 0.7:
            strength = "强"
        elif abs(coefficient) >= 0.3:
            strength = "中等"
        direction = "正" if coefficient > 0 else "负" if coefficient < 0 else "无"
        sections.extend(
            [
                "## 分析解读",
                (
                    f"Pearson 相关系数为 {coefficient:.6f}，表示两个字段呈"
                    f"{strength}{direction}线性关系。"
                    "相关性只描述线性关联，不能单独证明因果关系。"
                ),
                "## 计算公式",
                "$$r = \\frac{\\operatorname{cov}(X,Y)}{\\sigma_X\\sigma_Y}$$",
                f"本次字段：{', '.join(analysis.columns)}。{analysis.formula}".strip(),
            ]
        )
    elif analysis.formula:
        sections.extend(
            [
                "## 分析解读",
                analysis.intent or "结果由受限 Pandas 算子计算。",
                "## 计算公式",
                analysis.formula,
            ]
        )
    else:
        sections.extend(["## 分析解读", analysis.intent or "结果由受限 Pandas 算子计算。"])
    sections.extend(
        [
            "## 局限与建议",
            (
                f"### 局限\n- 当前分析基于 {analysis.input_rows} 行输入，缺失值处理和"
                "字段范围请结合数据页复核。"
            ),
            "### 建议\n- 结合筛选条件、分组结果或时间窗口进一步验证，再据此制定业务动作。",
        ]
    )
    return "\n\n".join(section for section in sections if section)
