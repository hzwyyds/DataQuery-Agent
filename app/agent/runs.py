from __future__ import annotations

import logging
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.agent.graph import AgentRuntime, run_agent
from app.data.repository import Repository
from app.query.executor import QueryTimeout, QueryTooLarge

logger = logging.getLogger(__name__)

PHASE_MESSAGES = {
    "retrieving": "正在检索相关表和字段",
    "planning": "正在生成受约束的查询与分析计划",
    "validating": "正在校验表、字段和 SQL",
    "querying": "正在执行受保护的 DuckDB 查询",
    "analyzing": "正在使用 Pandas 计算受限分析",
    "answering": "正在基于证据生成回答",
}


def result_payload(state: dict[str, Any]) -> dict:
    query_result = state.get("query_result")
    plan = state.get("plan")
    return jsonable_encoder(
        {
            "answer": state.get("answer", ""),
            "sql": state.get("normalized_sql") or (plan.sql if plan else None),
            "columns": query_result.columns if query_result else [],
            "rows": query_result.rows if query_result else [],
            "retrieval": state.get("retrieval", {"mode": "NONE", "matches": []}),
            "analysis": state.get("analysis_result"),
            "evidence": state.get("evidence", []),
            "chart": state.get("chart"),
            "scope": query_result.scope if query_result else None,
            "warnings": state.get("warnings", []),
            "error": state.get("error"),
        }
    )


async def execute_run(
    repository: Repository,
    runtime: AgentRuntime,
    run_id: str,
    workspace_id: str,
    question: str,
    selected_table_ids: list[str],
    conversation_id: str | None = None,
) -> None:
    async def record_phase(phase: str) -> None:
        await repository.append_event(run_id, phase, PHASE_MESSAGES[phase])

    try:
        state = await run_agent(
            runtime,
            workspace_id,
            question,
            selected_table_ids,
            conversation_id=conversation_id,
            on_phase=record_phase,
        )
        if state.get("error"):
            await repository.fail_run(run_id, "AGENT_ERROR", state["error"])
            await repository.append_event(
                run_id,
                "failed",
                state["error"],
                {"error_code": "AGENT_ERROR", "message": state["error"]},
                level="error",
            )
            return
            await repository.fail_run(
                run_id, "AGENT_ERROR", "分析计划无法完成，请调整问题或字段范围"
            )
            await repository.append_event(
                run_id,
                "failed",
                "运行失败",
                {"error_code": "AGENT_ERROR"},
                level="error",
            )
            return
        payload = result_payload(state)
        await repository.complete_run(run_id, payload)
        await repository.append_event(run_id, "completed", "运行完成")
    except QueryTooLarge as exc:
        await repository.fail_run(run_id, "QUERY_TOO_LARGE", str(exc))
        await repository.append_event(
            run_id, "failed", str(exc), {"error_code": "QUERY_TOO_LARGE"}, level="error"
        )
    except QueryTimeout:
        await repository.fail_run(run_id, "QUERY_TIMEOUT", "查询超过时间限制")
        await repository.append_event(
            run_id,
            "failed",
            "查询超过时间限制",
            {"error_code": "QUERY_TIMEOUT"},
            level="error",
        )
    except RuntimeError as exc:
        code = "CONFIGURATION_ERROR" if "LLM_API_KEY" in str(exc) else "PROVIDER_ERROR"
        message = (
            "未配置语言模型"
            if code == "CONFIGURATION_ERROR"
            else "语言模型请求失败"
        )
        logger.exception("Run %s failed", run_id)
        await repository.fail_run(run_id, code, message)
        await repository.append_event(
            run_id, "failed", message, {"error_code": code}, level="error"
        )
    except Exception:
        logger.exception("Run %s failed", run_id)
        await repository.fail_run(run_id, "RUN_FAILED", "运行发生未预期错误，请查看本地日志")
        await repository.append_event(
            run_id,
            "failed",
            "运行发生未预期错误",
            {"error_code": "RUN_FAILED"},
            level="error",
        )
