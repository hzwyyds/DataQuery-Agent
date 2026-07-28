# Architecture Notes

## Trust Boundaries

| Component | Reads | Produces |
| --- | --- | --- |
| Catalog RAG | Field metadata, aliases, limited representative values | Relevant table/column candidates |
| Planner | Full catalog and retrieved candidates | Structured `QueryPlan` |
| SQL guard | QueryPlan SQL and complete workspace schema | Normalized read-only query or rejection |
| DuckDB/Pandas | Authorized workspace tables | Rows, metrics, chart input, evidence |
| Answer model | Question and evidence records | Cited prose only |

Retrieved fields do not grant permissions. The SQL guard validates the full workspace catalog again
before DuckDB opens the query.

## Run Lifecycle

`POST /api/v1/workspaces/{workspace_id}/runs` creates a durable run. The worker emits
`retrieving`, `planning`, `validating`, `querying`, `analyzing`, and `answering` while LangGraph
nodes run, then writes `completed` or `failed`. Clients reconnect to the SSE endpoint with
`Last-Event-ID` or `after` to replay only missed events.

The saved result contains answer, SQL, retrieval matches, evidence, analysis metrics, chart
contract, scope, warnings, and stable error details. Detailed stack traces remain local logs.
