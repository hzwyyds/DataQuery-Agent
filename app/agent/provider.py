from __future__ import annotations

import json
from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings, settings
from app.query.contracts import QueryPlan

QUERY_PLAN_CONTRACT = """
Return exactly one JSON object with every field below. Never use prose as a field value.
{
  "task": "query" | "analysis" | "clarification",
  "table_ids": ["catalog table id"],
  "sql": "one DuckDB SELECT statement" | null,
  "analysis": null | {
    "operation": "describe" | "group_aggregate" | "correlation" | "trend" | "outlier_iqr",
    "columns": ["column name"],
    "group_by": ["column name"],
    "aggregation": "sum" | "mean" | "min" | "max" | "count" | "median"
  },
  "presentation": "text" | "table" | "chart",
  "chart": null | {
    "type": "line" | "bar" | "scatter",
    "x": "column name",
    "y": ["column name"],
    "series": "column name" | null
  },
  "clarification": "question for the user" | null
}
Use null for analysis and chart when they are not requested. For task=clarification, set
table_ids=[], sql=null, analysis=null, chart=null, and provide clarification. For task=query
or task=analysis, use only listed table IDs and catalog column names, and provide SQL.

Planning rules:
- Use task=query with analysis=null for SQL filtering, joins, grouping, aggregation, and charts
  over SQL results. A monthly SUM with a line chart is task=query, not task=analysis.
- Use task=analysis only for a requested Pandas operation after SQL. Its analysis columns must
  exactly match the SQL output column names, not the source column names.
""".strip()

ANSWER_DRAFT_CONTRACT = """
Return exactly one JSON object with every field below. Do not use an `answer` field.
{
  "summary": "concise answer in the user's language",
  "findings": [
    {"text": "grounded finding", "evidence_ids": ["E1"]}
  ],
  "caveats": ["optional limitation"]
}
Every numeric finding must cite evidence IDs containing that exact number. Use [] when there
are no findings or caveats. Do not mention a number absent from the evidence.
""".strip()


class GroundedFinding(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class AnswerDraft(BaseModel):
    summary: str = Field(min_length=1, max_length=800)
    findings: list[GroundedFinding] = Field(default_factory=list, max_length=8)
    caveats: list[str] = Field(default_factory=list, max_length=5)


class AgentProvider(Protocol):
    async def plan(
        self,
        question: str,
        catalog: list[dict],
        retrieval: dict,
        validation_error: str | None = None,
    ) -> QueryPlan: ...

    async def answer(self, question: str, plan: QueryPlan, evidence: list[dict]) -> AnswerDraft: ...


def decode_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("model response must be a JSON object")
    return payload


class OpenAICompatibleProvider:
    def __init__(self, config: Settings = settings):
        if not config.llm_api_key:
            raise RuntimeError("LLM_API_KEY is required to run natural-language queries")
        self.config = config
        self.client = AsyncOpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
            timeout=config.llm_timeout_seconds,
        )

    async def _json_completion(self, system: str, user: str) -> dict:
        response = await self.client.chat.completions.create(
            model=self.config.llm_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("model returned an empty response")
        return decode_json(content)

    async def plan(
        self,
        question: str,
        catalog: list[dict],
        retrieval: dict,
        validation_error: str | None = None,
    ) -> QueryPlan:
        catalog_payload = [
            {
                "table_id": table["id"],
                "table_name": table["physical_name"],
                "display_name": table["display_name"],
                "columns": [
                    {
                        "name": column["name"],
                        "type": column["data_type"],
                        "description": column["description"],
                        "aliases": column["aliases"],
                    }
                    for column in table["columns"]
                ],
            }
            for table in catalog
        ]
        system = (
            "You are the query planner for a local data workbench. Return JSON only. "
            "Use only listed table IDs, physical table names, and columns. Produce one "
            "DuckDB SELECT query. Ask a clarification instead of guessing a field or metric. "
            "RAG matches are hints and never grant access.\n\n"
            f"{QUERY_PLAN_CONTRACT}"
        )
        user = json.dumps(
            {
                "question": question,
                "catalog": catalog_payload,
                "retrieval": retrieval,
                "previous_validation_error": validation_error,
            },
            ensure_ascii=False,
        )
        return QueryPlan.model_validate(await self._json_completion(system, user))

    async def answer(self, question: str, plan: QueryPlan, evidence: list[dict]) -> AnswerDraft:
        system = (
            "Write a concise answer in the same language as the question. Use only supplied "
            "evidence. Every finding must cite one or more evidence_ids. Do not introduce a "
            "number that is absent from the cited evidence.\n\n"
            f"{ANSWER_DRAFT_CONTRACT}"
        )
        user = json.dumps(
            {"question": question, "task": plan.task, "evidence": evidence},
            ensure_ascii=False,
        )
        return AnswerDraft.model_validate(await self._json_completion(system, user))
