from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.agent.grounding import (
    build_evidence,
    fallback_answer,
    render_answer,
    validate_answer,
)
from app.agent.provider import AgentProvider
from app.analysis.chart import ChartError, ChartService
from app.analysis.service import AnalysisError, AnalysisService
from app.data.repository import Repository
from app.query.contracts import AnalysisResult, ChartResult, QueryPlan, QueryResult
from app.query.executor import DuckDBQueryExecutor
from app.query.sql_guard import SQLGuard
from app.rag.service import RAGService

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    workspace_id: str
    question: str
    selected_table_ids: list[str]
    conversation_id: str | None
    catalog: list[dict]
    retrieval: dict
    planning_retrieval: dict
    plan: QueryPlan
    normalized_sql: str
    query_result: QueryResult
    analysis_input: QueryResult
    chart_input: QueryResult
    chart_source_points: int
    analysis_result: AnalysisResult
    chart: ChartResult | None
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
    analysis: AnalysisService
    charts: ChartService


def selected_catalog(state: AgentState) -> list[dict]:
    selected = state.get("selected_table_ids") or []
    return (
        [table for table in state["catalog"] if table["id"] in selected]
        if selected
        else state["catalog"]
    )


def preview_result(result: QueryResult, limit: int = 100) -> QueryResult:
    rows = result.rows[:limit]
    return result.model_copy(
        update={
            "rows": rows,
            "scope": result.scope.model_copy(
                update={
                    "rows_read": result.scope.rows_read,
                    "rows_returned": len(rows),
                    "preview_truncated": result.scope.rows_read > len(rows),
                }
            ),
        }
    )


def analysis_plan_error(plan: QueryPlan) -> str | None:
    if plan.task != "analysis":
        return None
    spec = plan.analysis
    if spec is None:
        return "analysis task is missing an analysis specification"
    expected_columns = {
        "correlation": 2,
        "trend": 2,
        "outlier_iqr": 1,
        "nse": 2,
        "kge": 2,
        "nse_kge": 2,
    }
    expected = expected_columns.get(spec.operation)
    if expected is not None and len(spec.columns) != expected:
        return f"{spec.operation} requires exactly {expected} analysis columns"
    if spec.operation == "group_aggregate":
        metrics = [column for column in spec.columns if column not in spec.group_by]
        if not spec.group_by or not metrics:
            return "group_aggregate requires group fields and numeric metric columns"
    if spec.operation == "formula" and spec.custom_formula is None:
        return "formula analysis requires custom_formula"
    return None


def build_agent_graph(
    runtime: AgentRuntime,
    on_phase: Callable[[str], Awaitable[None]] | None = None,
):
    async def emit(phase: str) -> None:
        if on_phase is not None:
            await on_phase(phase)

    async def retrieve(state: AgentState) -> dict:
        await emit("retrieving")
        catalog = await runtime.repository.catalog(state["workspace_id"])
        if not catalog:
            return {"catalog": [], "error": "Upload at least one dataset before asking a question"}
        retrieval = await runtime.rag.retrieve(
            state["workspace_id"],
            state["question"],
            catalog,
            state.get("selected_table_ids"),
        )
        recent_runs = (
            await runtime.repository.list_conversation_runs(state["conversation_id"], limit=6)
            if state.get("conversation_id")
            else []
        )
        conversation_context = [
            {
                "question": run["question"],
                "answer": str(run.get("payload", {}).get("answer", ""))[:1000],
            }
            for run in reversed(recent_runs)
            if run["status"] == "COMPLETED"
            and run.get("payload", {}).get("answer")
            and (
                run.get("payload", {}).get("sql")
                or run.get("payload", {}).get("analysis")
            )
        ][-3:]
        return {
            "catalog": catalog,
            "retrieval": retrieval,
            "planning_retrieval": {**retrieval, "conversation_context": conversation_context},
            "warnings": list(retrieval.get("warnings", [])),
        }

    async def plan(state: AgentState) -> dict:
        await emit("planning")
        if state.get("error"):
            return {}
        try:
            query_plan = await runtime.provider.plan(
                state["question"], selected_catalog(state), state["planning_retrieval"]
            )
        except (ValidationError, ValueError) as exc:
            try:
                query_plan = await runtime.provider.plan(
                    state["question"],
                    selected_catalog(state),
                    state["planning_retrieval"],
                    validation_error=f"The previous plan was invalid: {str(exc)[:500]}",
                )
            except (ValidationError, ValueError) as repair_error:
                if "chart" in str(repair_error).lower() and "at most" in str(repair_error).lower():
                    return {
                        "error": "当前图表请求包含过多指标，请减少雨量站数量或分两次提问。"
                    }
                return {"error": "查询计划格式无效，请缩小问题范围后重试。"}
            return {"plan": query_plan, "planning_attempts": 2}
        return {"plan": query_plan, "planning_attempts": 1}

    async def validate(state: AgentState) -> dict:
        await emit("validating")
        if state.get("error"):
            return {}
        query_plan = state["plan"]
        if query_plan.task == "clarification":
            return {"answer": query_plan.clarification or "Please clarify the requested field."}
        known_ids = {table["id"] for table in state["catalog"]}
        if not query_plan.table_ids or not set(query_plan.table_ids) <= known_ids:
            reason = "planner selected a table outside the workspace"
        elif spec_error := analysis_plan_error(query_plan):
            reason = spec_error
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
            guarded = runtime.guard.validate(
                query_plan.sql,
                schema,
                max_rows=100_000_000,
                apply_limit=False,
            )
            if guarded.allowed:
                return {"normalized_sql": guarded.normalized_sql}
            reason = guarded.reason or "SQL validation failed"
        if state.get("planning_attempts", 1) >= 2:
            return {"error": reason}
        repaired = await runtime.provider.plan(
            state["question"],
            selected_catalog(state),
            state["planning_retrieval"],
            validation_error=reason,
        )
        return {"plan": repaired, "planning_attempts": 2}

    async def execute(state: AgentState) -> dict:
        await emit("querying")
        if state.get("error") or not state.get("normalized_sql"):
            return {}
        max_rows = 100_000_000
        if state["plan"].task == "analysis":
            total_rows = await runtime.executor.count_rows(
                state["workspace_id"], state["normalized_sql"], max_rows=max_rows
            )
            result = await runtime.executor.execute(
                state["workspace_id"], state["normalized_sql"], max_rows=max_rows
            )
            return {
                "query_result": preview_result(result),
                "analysis_input": result,
                "chart_input": result,
                "chart_source_points": total_rows,
            }
        preview = await runtime.executor.execute(
            state["workspace_id"], state["normalized_sql"], max_rows=100
        )
        output: dict[str, Any] = {"query_result": preview}
        if state["plan"].chart is not None:
            chart_input, source_points = await runtime.executor.execute_chart(
                state["workspace_id"], state["normalized_sql"]
            )
            preview = preview.model_copy(
                update={
                    "scope": preview.scope.model_copy(
                        update={
                            "rows_read": source_points,
                            "preview_truncated": source_points > len(preview.rows),
                        }
                    )
                }
            )
            output.update(
                {
                    "query_result": preview,
                    "chart_input": chart_input,
                    "chart_source_points": source_points,
                }
            )
        return output

    async def analyze(state: AgentState) -> dict:
        await emit("analyzing")
        if state.get("error") or not state.get("query_result"):
            return {}
        try:
            analysis_result = None
            if state["plan"].task == "analysis":
                if state["plan"].analysis is None:
                    raise AnalysisError("analysis plan is missing an analysis specification")
                analysis_result = runtime.analysis.run(
                    state["plan"].analysis, state.get("analysis_input", state["query_result"])
                )
            chart_rows = (
                analysis_result.rows
                if analysis_result is not None and analysis_result.rows
                else state.get("chart_input", state["query_result"]).rows
            )
            chart = runtime.charts.build(
                state["plan"].chart,
                chart_rows,
                source_points=(
                    len(chart_rows)
                    if analysis_result is not None and analysis_result.rows
                    else state.get("chart_source_points")
                ),
            )
        except (AnalysisError, ChartError) as exc:
            return {"error": str(exc)}
        scope = state["query_result"].scope.model_copy(
            update={
                "displayed_points": chart.displayed_points if chart else 0,
                "downsampled": chart.downsampled if chart else False,
            }
        )
        query_result = state["query_result"].model_copy(update={"scope": scope})
        return {
            "query_result": query_result,
            "analysis_result": analysis_result,
            "chart": chart,
            "evidence": build_evidence(query_result, analysis_result),
        }

    async def synthesize(state: AgentState) -> dict:
        await emit("answering")
        if state.get("answer") or state.get("error") or not state.get("evidence"):
            return {}
        try:
            draft = await runtime.provider.answer(
                state["question"], state["plan"], state["evidence"]
            )
        except (ValidationError, ValueError) as exc:
            logger.info("Answer model output was invalid; using evidence fallback: %s", exc)
            return {
                "answer": fallback_answer(state["query_result"], state.get("analysis_result"))
            }
        if not validate_answer(draft, state["evidence"]):
            logger.info("Answer evidence validation rejected the model draft; using fallback")
            return {
                "answer": fallback_answer(state["query_result"], state.get("analysis_result"))
            }
        return {"answer": render_answer(draft)}

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
    builder.add_node("analyze", analyze)
    builder.add_node("synthesize", synthesize)
    builder.add_node("finish", lambda _state: {})
    builder.add_edge(START, "retrieve")
    builder.add_conditional_edges("retrieve", after_retrieve)
    builder.add_edge("plan", "validate")
    builder.add_conditional_edges("validate", after_validate)
    builder.add_edge("execute", "analyze")
    builder.add_edge("analyze", "synthesize")
    builder.add_edge("synthesize", END)
    builder.add_edge("finish", END)
    return builder.compile()


async def run_agent(
    runtime: AgentRuntime,
    workspace_id: str,
    question: str,
    selected_table_ids: list[str] | None = None,
    conversation_id: str | None = None,
    on_phase: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    graph = build_agent_graph(runtime, on_phase)
    state = await graph.ainvoke(
        {
            "workspace_id": workspace_id,
            "question": question,
            "selected_table_ids": selected_table_ids or [],
            "conversation_id": conversation_id,
        }
    )
    return state
