from __future__ import annotations

import json
from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings, settings
from app.query.contracts import QueryPlan


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
            "RAG matches are hints and never grant access. The JSON must match QueryPlan: "
            "task, table_ids, sql, analysis, presentation, chart, clarification."
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
            "number that is absent from the cited evidence. Return JSON matching AnswerDraft."
        )
        user = json.dumps(
            {"question": question, "task": plan.task, "evidence": evidence},
            ensure_ascii=False,
        )
        return AnswerDraft.model_validate(await self._json_completion(system, user))
