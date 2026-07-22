# Evaluation Suite

`retrieval_cases.json` contains 50 bilingual catalog retrieval cases. The default test suite
uses explicit catalog aliases to validate lexical ranking, thresholds, workspace isolation, and
fallback behavior without an external service. `agent_cases.json` contains 40 golden query cases
that validate SQL plans, guarded execution, and numerical evidence grounding on the bundled retail
fixtures.

Run the portable suite:

```powershell
uv run pytest -q tests/test_evaluations.py tests/test_rag.py
```

Run the real vector evaluation after Qdrant and TEI are healthy:

```powershell
uv run python evaluations/run_retrieval_live.py
```

The live command writes a fresh ignored JSON report under `artifacts/`; metrics in this repository
are never inherited from the upstream project. The model-backed planner/answer evaluation requires
`LLM_API_KEY` and is intentionally not part of the offline test suite.
