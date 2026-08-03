from __future__ import annotations

import json
from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.core.config import Settings, settings
from app.query.contracts import AnalysisSpec, QueryPlan

QUERY_PLAN_CONTRACT = """
Return exactly one JSON object with every field below. Never use prose as a field value.
{
  "task": "query" | "analysis" | "clarification",
  "table_ids": ["catalog table id"],
  "sql": "one DuckDB SELECT statement" | null,
  "analysis": null | {
    "operation": "describe" | "group_aggregate" | "correlation" | "trend" |
                 "outlier_iqr" | "nse" | "kge" | "nse_kge" | "formula",
    "columns": ["column name"],
    "group_by": ["column name"],
    "aggregation": "sum" | "mean" | "min" | "max" | "count" | "median",
    "formula": "plain-language calculation formula, never Python code",
     "intent": "what this analysis answers",
     "custom_formula": null | {
       "name": "metric name",
       "variables": ["numeric column names"],
       "expression": "safe math expression using allowlisted functions"
     }
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
The table ID is an internal catalog identifier, never a SQL schema or SQL table name. In SQL,
write only the listed physical table name in FROM, without a schema or table-ID qualifier.

Planning rules:
- Use task=query with analysis=null for SQL filtering, joins, grouping, aggregation, and charts
  over SQL results. A monthly SUM with a line chart is task=query, not task=analysis.
- Use task=analysis only for a requested Pandas operation after SQL. Its analysis columns must
  exactly match the SQL output column names, not the source column names.
- For task=analysis, use exactly one allowlisted operation: describe (descriptive statistics),
  group_aggregate (Pandas group-by), correlation (exactly two numeric fields, Pearson r), trend
  (one time field and one numeric field), outlier_iqr (one numeric field), nse (exactly two
  numeric fields ordered observed, simulated), kge (exactly two numeric fields ordered
  observed, simulated), or nse_kge (exactly two numeric fields ordered observed, simulated,
  and calculates both metrics). Select raw fields
  needed by Pandas in SQL and set analysis.formula plus analysis.intent in the same language as
  the question. Field names and standard statistical terms may remain in English. Never output
  Python code. For operation=formula, put the expression in custom_formula.expression and list
  every input field in custom_formula.variables. Never use imports, attribute access, or eval.

Domain language rules:
- Interpret hydrology terms such as NSE (Nash-Sutcliffe efficiency), KGE (Kling-Gupta efficiency),
  observed/simulated flow, runoff, rainfall, and water level using the catalog fields only.
- When the user explicitly requests both NSE and KGE, use operation=nse_kge.
- Only ask for a flood-season definition when the question actually mentions "汛期" or
  explicitly asks for a flood-season subset. Do not introduce a flood-season clarification
  for a standalone NSE, KGE, correlation, or trend request.
- For NSE/KGE, if the question names two fields and explicitly assigns the first to observed and
  the second to simulated, use that order directly; do not ask the user to repeat the mapping.
- "汛期" is not a universal fixed period. If it is mentioned and the user gives months or dates,
  use those values. Otherwise ask for the region or date definition instead of silently guessing.
""".strip()

ANSWER_DRAFT_CONTRACT = """
Return exactly one JSON object with every field below. Do not use an `answer` field.
{
  "summary": "结论摘要，用用户语言，避免重复 findings",
  "findings": [
    {"text": "grounded finding", "evidence_ids": ["E1"]}
  ],
  "interpretation": "分析结果的业务含义；普通查询可为空",
  "limitations": ["分析样本、覆盖范围或方法局限；普通查询可为空"],
  "recommendations": ["基于结果的下一步建议；普通查询可为空"],
  "caveats": ["optional limitation"]
}
Every numeric statement must be supported by the supplied evidence. Findings must cite one or
more evidence_ids containing the stated number. Use [] or "" when a section is not applicable.
For task=analysis, provide several distinct findings, explain the metric and formula in
interpretation, mention sample size or method limitations, and give practical next steps.
Do not repeat the summary verbatim in findings or interpretation. Do not mention a number
absent from the evidence.
""".strip()


class GroundedFinding(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class AnswerDraft(BaseModel):
    summary: str = Field(min_length=1, max_length=800)
    findings: list[GroundedFinding] = Field(default_factory=list, max_length=8)
    interpretation: str = Field(default="", max_length=1200)
    limitations: list[str] = Field(default_factory=list, max_length=5)
    recommendations: list[str] = Field(default_factory=list, max_length=5)
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
            "RAG matches are hints and never grant access. Conversation context is only for "
            "resolving follow-up references; never use its numbers as evidence.\n\n"
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
        plan = QueryPlan.model_validate(await self._json_completion(system, user))
        fallback = self._hydrology_fallback(question, catalog, plan)
        return fallback or plan

    @staticmethod
    def _hydrology_fallback(
        question: str, catalog: list[dict], plan: QueryPlan
    ) -> QueryPlan | None:
        """Repair an over-conservative clarification for an explicit paired metric request.

        This fallback only constructs a plan when the user names two catalog columns and
        assigns their observed/simulated order. It never accepts model SQL or introduces a
        table outside the supplied catalog.
        """
        lowered = question.casefold()
        if plan.task != "clarification" or not ("nse" in lowered or "kge" in lowered):
            return None
        observed_marker = "作为观测值"
        simulated_marker = "作为模拟值"
        observed_end = question.find(observed_marker)
        simulated_end = question.find(simulated_marker)
        if observed_end <= 0 or simulated_end <= observed_end:
            return None

        mentioned = [
            column["name"]
            for table in catalog
            for column in table["columns"]
            if column["name"] in question
        ]
        if len(mentioned) < 2:
            return None
        observed_candidates = [
            name for name in mentioned if question.find(name) < observed_end
        ]
        simulated_candidates = [
            name for name in mentioned if question.find(name) < simulated_end
        ]
        if not observed_candidates or not simulated_candidates:
            return None
        observed = max(observed_candidates, key=lambda name: question.find(name))
        simulated = max(
            [
                name
                for name in mentioned
                if name != observed and question.find(name) < simulated_end
            ],
            key=lambda name: question.find(name),
            default=None,
        )
        if simulated is None:
            return None

        table_candidates = [
            table
            for table in catalog
            if "数据库" in table["display_name"]
            and observed in {c["name"] for c in table["columns"]}
            and simulated in {c["name"] for c in table["columns"]}
        ]
        if len(table_candidates) != 1:
            table_candidates = [
                table
                for table in catalog
                if observed in {c["name"] for c in table["columns"]}
                and simulated in {c["name"] for c in table["columns"]}
            ]
        if len(table_candidates) != 1:
            return None
        table = table_candidates[0]
        physical = table["physical_name"].replace('"', '""')
        observed_sql = observed.replace('"', '""')
        simulated_sql = simulated.replace('"', '""')
        operation = "nse_kge" if "nse" in lowered and "kge" in lowered else (
            "nse" if "nse" in lowered else "kge"
        )
        return QueryPlan(
            task="analysis",
            table_ids=[table["id"]],
            sql=(
                f'SELECT "{observed_sql}" AS observed, "{simulated_sql}" AS simulated '
                f'FROM "{physical}" WHERE "{observed_sql}" IS NOT NULL '
                f'AND "{simulated_sql}" IS NOT NULL'
            ),
            analysis=AnalysisSpec(
                operation=operation,
                columns=["observed", "simulated"],
                intent=f"使用{observed}作为观测值、{simulated}作为模拟值计算水文效率指标",
            ),
            presentation="text",
        )

    async def answer(self, question: str, plan: QueryPlan, evidence: list[dict]) -> AnswerDraft:
        system = (
            "Write a grounded answer in the same language as the question. Use only supplied "
            "evidence. Every finding must cite one or more evidence_ids. Do not introduce a "
            "number that is absent from the evidence. For analysis tasks, make the response "
            "substantive: explain what was computed, how to interpret it, the sample/method "
            "limits, and useful next steps. Keep summary as a conclusion only; do not copy "
            "findings into it.\n\n"
            f"{ANSWER_DRAFT_CONTRACT}"
        )
        user = json.dumps(
            {"question": question, "task": plan.task, "evidence": evidence},
            ensure_ascii=False,
        )
        return AnswerDraft.model_validate(await self._json_completion(system, user))
