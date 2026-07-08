from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from app.agent.provider import AnswerDraft
from app.query.contracts import QueryResult

NUMBER = re.compile(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?%?")


def build_evidence(result: QueryResult) -> list[dict]:
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


def validate_answer(draft: AnswerDraft, evidence: list[dict]) -> bool:
    by_id = {item["id"]: item["fact"] for item in evidence}
    for finding in draft.findings:
        if any(identity not in by_id for identity in finding.evidence_ids):
            return False
        claims = normalized_numbers(finding.text)
        supported = set().union(
            *(normalized_numbers(by_id[identity]) for identity in finding.evidence_ids)
        )
        if not claims <= supported:
            return False
    return True


def render_answer(draft: AnswerDraft) -> str:
    sections = [draft.summary.strip()]
    sections.extend(f"- {finding.text}" for finding in draft.findings)
    if draft.caveats:
        sections.append("Caveats: " + "; ".join(draft.caveats))
    return "\n".join(item for item in sections if item)


def fallback_answer(result: QueryResult) -> str:
    suffix = " The preview was truncated." if result.scope.preview_truncated else ""
    return f"The query returned {result.scope.rows_returned} preview rows.{suffix}"
