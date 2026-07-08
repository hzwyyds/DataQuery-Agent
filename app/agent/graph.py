from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.grounding import (
    build_evidence,
    fallback_answer,
    render_answer,
    validate_answer,
)
from app.agent.provider import AgentProvider
from app.data.repository import Repository
from app.query.contracts import QueryPlan, QueryResult
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService


class AgentState(TypedDict, total=False):
    workspace_id: str
    question: str
    selected_table_ids: list[str]
    catalog: list[dict]
    retrieval: dict
    plan: QueryPlan
    normalized_sql: str
    query_result: QueryResult
    evidence: list[dict]
    answer: str
    warnings: list[str]
    error: str
    planning_attempts: int


@dataclass(frozen=True)
class AgentRuntime:
    repository: Repository
    rag: RAGService
    provider: AgentProvider
    executor: DuckDBQueryExecutor
    guard: SQLGuard


def selected_catalog(state: AgentState) -> list[dict]:
    selected = state.get("selected_table_ids") or []
    return (
        [table for table in state["catalog"] if table["id"] in selected]
        if selected
        else state["catalog"]
    )


def build_agent_graph(runtime: AgentRuntime):
    async def retrieve(state: AgentState) -> dict:
        catalog = await runtime.repository.catalog(state["workspace_id"])
        if not catalog:
            return {"catalog": [], "error": "Upload at least one dataset before asking a question"}
        retrieval = await runtime.rag.retrieve(
            state["workspace_id"],
            state["question"],
            catalog,
            state.get("selected_table_ids"),
        )
        return {
            "catalog": catalog,
            "retrieval": retrieval,
            "warnings": list(retrieval.get("warnings", [])),
        }

    async def plan(state: AgentState) -> dict:
        if state.get("error"):
            return {}
        query_plan = await runtime.provider.plan(
            state["question"], selected_catalog(state), state["retrieval"]
        )
        return {"plan": query_plan, "planning_attempts": 1}

    async def validate(state: AgentState) -> dict:
        if state.get("error"):
            return {}
        query_plan = state["plan"]
        if query_plan.task == "clarification":
            return {"answer": query_plan.clarification or "Please clarify the requested field."}
        known_ids = {table["id"] for table in state["catalog"]}
        if not query_plan.table_ids or not set(query_plan.table_ids) <= known_ids:
            reason = "planner selected a table outside the workspace"
        elif not query_plan.sql:
            reason = "planner did not provide SQL"
        else:
            tables = [table for table in state["catalog"] if table["id"] in query_plan.table_ids]
            schema = {
                table["physical_name"]: {
                    column["name"]: column["data_type"] for column in table["columns"]
                }
                for table in tables
            }
            guarded = runtime.guard.validate(query_plan.sql, schema)
            if guarded.allowed:
                return {"normalized_sql": guarded.normalized_sql}
            reason = guarded.reason or "SQL validation failed"
        if state.get("planning_attempts", 1) >= 2:
            return {"error": reason}
        repaired = await runtime.provider.plan(
            state["question"],
            selected_catalog(state),
            state["retrieval"],
            validation_error=reason,
        )
        return {"plan": repaired, "planning_attempts": 2}

    async def execute(state: AgentState) -> dict:
        if state.get("error") or not state.get("normalized_sql"):
            return {}
        result = await runtime.executor.execute(state["workspace_id"], state["normalized_sql"])
        return {"query_result": result, "evidence": build_evidence(result)}

    async def synthesize(state: AgentState) -> dict:
        if state.get("answer") or state.get("error") or not state.get("query_result"):
            return {}
        draft = await runtime.provider.answer(state["question"], state["plan"], state["evidence"])
        answer = (
            render_answer(draft)
            if validate_answer(draft, state["evidence"])
            else fallback_answer(state["query_result"])
        )
        return {"answer": answer}

    def after_retrieve(state: AgentState) -> Literal["plan", "finish"]:
        return "finish" if state.get("error") else "plan"

    def after_validate(state: AgentState) -> Literal["validate", "execute", "finish"]:
        if state.get("error") or state.get("answer"):
            return "finish"
        if not state.get("normalized_sql"):
            return "validate"
        return "execute"

    builder = StateGraph(AgentState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("plan", plan)
    builder.add_node("validate", validate)
    builder.add_node("execute", execute)
    builder.add_node("synthesize", synthesize)
    builder.add_node("finish", lambda _state: {})
    builder.add_edge(START, "retrieve")
    builder.add_conditional_edges("retrieve", after_retrieve)
    builder.add_edge("plan", "validate")
    builder.add_conditional_edges("validate", after_validate)
    builder.add_edge("execute", "synthesize")
    builder.add_edge("synthesize", END)
    builder.add_edge("finish", END)
    return builder.compile()


async def run_agent(
    runtime: AgentRuntime,
    workspace_id: str,
    question: str,
    selected_table_ids: list[str] | None = None,
) -> dict[str, Any]:
    graph = build_agent_graph(runtime)
    state = await graph.ainvoke(
        {
            "workspace_id": workspace_id,
            "question": question,
            "selected_table_ids": selected_table_ids or [],
        }
    )
    return state
