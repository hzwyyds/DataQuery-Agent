from __future__ import annotations

import argparse
import csv
import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(base_url: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
        method="POST" if body else "GET",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def wait_for_run(base_url: str, workspace_id: str, run_id: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = request_json(base_url, f"/api/v1/workspaces/{workspace_id}/runs/{run_id}")
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.5)
    raise TimeoutError(f"run {run_id} exceeded {timeout} seconds")


def csv_row_count(base_url: str, workspace_id: str, run_id: str) -> int:
    path = f"/api/v1/workspaces/{workspace_id}/runs/{run_id}/download?kind=result"
    with urlopen(f"{base_url.rstrip('/')}{path}", timeout=120) as response:
        text = response.read().decode("utf-8-sig")
    return sum(1 for _ in csv.DictReader(io.StringIO(text)))


def answer_has_depth(answer: str, minimum: int) -> bool:
    headings = ("结论", "关键发现", "分析解读", "局限", "建议", "计算公式")
    return len(answer.strip()) >= minimum and sum(item in answer for item in headings) >= 3


def evaluate_case(
    case: dict[str, Any], run: dict[str, Any], base_url: str, workspace_id: str
) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    payload = run.get("payload") or {}
    answer = str(payload.get("answer") or "")
    sql = str(payload.get("sql") or "")
    analysis = payload.get("analysis")
    chart = payload.get("chart")
    retrieval = payload.get("retrieval") or {}
    scope = payload.get("scope") or {}

    if run.get("status") != "COMPLETED":
        failures.append(f"status={run.get('status')}: {run.get('error_message')}")
        return failures, {"answer": answer, "sql": sql}
    if not answer.strip():
        failures.append("empty answer")
    if case["kind"] == "query" and (not sql or analysis):
        failures.append("expected a SQL query without Pandas analysis")
    if case["kind"] == "analysis":
        if not analysis:
            failures.append("missing analysis result")
        elif analysis.get("operation") != case["operation"]:
            failures.append(
                f"operation={analysis.get('operation')} expected={case['operation']}"
            )
        if not answer_has_depth(answer, case.get("min_answer_chars", 180)):
            failures.append("analysis answer is too shallow")
    if case["kind"] == "chart":
        if not chart:
            failures.append("missing chart")
        elif chart.get("type") != case["chart_type"]:
            failures.append(f"chart={chart.get('type')} expected={case['chart_type']}")
    if case["kind"] == "clarification":
        if sql or analysis or chart:
            failures.append("expected clarification without execution")
        for term in case.get("answer_terms", []):
            if term not in answer:
                failures.append(f"clarification missing term: {term}")
    else:
        for term in case.get("sql_terms", []):
            if term.casefold() not in sql.casefold():
                failures.append(f"SQL missing term: {term}")
        if not payload.get("evidence"):
            failures.append("missing execution evidence")
        if retrieval.get("mode") != "HYBRID":
            failures.append(f"retrieval mode={retrieval.get('mode')} expected=HYBRID")
        if int(scope.get("rows_returned") or 0) > 100:
            failures.append("table preview exceeds 100 rows")
    if analysis and int(analysis.get("input_rows") or 0) < case.get("min_input_rows", 0):
        failures.append(
            f"analysis input_rows={analysis.get('input_rows')} below {case['min_input_rows']}"
        )
    if chart and int(chart.get("source_points") or 0) < case.get("min_source_points", 0):
        failures.append(
            f"chart source_points={chart.get('source_points')} below {case['min_source_points']}"
        )
    if chart and chart.get("displayed_points") != chart.get("source_points"):
        failures.append("chart did not render the complete requested scope")
    if chart and chart.get("downsampled") is not False:
        failures.append("chart was downsampled")
    downloaded_rows = None
    if case.get("download_min_rows") and not failures:
        downloaded_rows = csv_row_count(base_url, workspace_id, run["id"])
        if downloaded_rows < case["download_min_rows"]:
            failures.append(
                f"download rows={downloaded_rows} below {case['download_min_rows']}"
            )
    return failures, {
        "answer": answer,
        "sql": sql,
        "analysis": analysis,
        "chart": chart,
        "scope": scope,
        "retrieval_mode": retrieval.get("mode"),
        "warnings": payload.get("warnings") or [],
        "downloaded_rows": downloaded_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 50 live Shigu Agent acceptance cases")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--table-id", required=True)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the named case; repeat for multiple cases",
    )
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/live-shigu-qa.json"),
    )
    args = parser.parse_args()
    cases_path = Path(__file__).with_name("live_shigu_cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if len(cases) != 50:
        raise ValueError(f"expected exactly 50 cases, got {len(cases)}")
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case["id"] in selected]
        missing = selected - {case["id"] for case in cases}
        if missing:
            raise ValueError(f"unknown case IDs: {', '.join(sorted(missing))}")

    results = []
    for index, case in enumerate(cases, 1):
        started = time.monotonic()
        try:
            created = request_json(
                args.base_url,
                f"/api/v1/workspaces/{args.workspace_id}/runs",
                {
                    "question": case["question"],
                    "selected_table_ids": [args.table_id],
                },
            )
            run = wait_for_run(
                args.base_url, args.workspace_id, created["id"], args.timeout
            )
            failures, evidence = evaluate_case(
                case, run, args.base_url, args.workspace_id
            )
        except Exception as exc:
            failures = [f"{type(exc).__name__}: {exc}"]
            evidence = {}
            run = {"id": None, "status": "EVALUATION_ERROR"}
        elapsed = round(time.monotonic() - started, 2)
        passed = not failures
        results.append(
            {
                "id": case["id"],
                "question": case["question"],
                "run_id": run.get("id"),
                "status": run.get("status"),
                "passed": passed,
                "failures": failures,
                "elapsed_seconds": elapsed,
                **evidence,
            }
        )
        print(
            f"[{index:02d}/{len(cases):02d}] {case['id']} {'PASS' if passed else 'FAIL'} "
            f"({elapsed:.2f}s){' | ' + '; '.join(failures) if failures else ''}",
            flush=True,
        )

    passed = sum(item["passed"] for item in results)
    warning_runs = sum(bool(item.get("warnings")) for item in results)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "workspace_id": args.workspace_id,
        "table_id": args.table_id,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "warning_runs": warning_runs,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total", "passed", "failed", "warning_runs")}))
    print(f"report={args.output.resolve()}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
