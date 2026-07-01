# DataQuery Agent

DataQuery Agent is a local-first natural-language data analysis workbench. It
turns uploaded tabular files into guarded SQL queries, constrained statistics,
grounded answers, and charts.

This project is a substantial refactor of
[didilili/shopkeeper-agent](https://github.com/didilili/shopkeeper-agent). See
`NOTICE.md` for the exact upstream revision and attribution.

## Status

The repository is being rebuilt in reviewable, independently tested commits.

## Minimal Development Check

```powershell
uv sync --dev
uv run pytest
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The API exposes `GET /health`. The frontend lives in `frontend/`.
