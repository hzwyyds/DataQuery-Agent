import {
  AlertTriangle,
  BarChart3,
  Check,
  ChevronDown,
  ChevronRight,
  CirclePlus,
  Database,
  FileSpreadsheet,
  LoaderCircle,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, api } from "./api";
import type {
  CatalogColumn,
  CatalogTable,
  RagStatus,
  Run,
  RunEvent,
  Source,
  Workspace,
} from "./types";

const ChartView = lazy(() => import("./ChartView").then((module) => ({ default: module.ChartView })));

const phases = [
  "retrieving",
  "planning",
  "validating",
  "querying",
  "analyzing",
  "answering",
  "completed",
  "failed",
];

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function StatusDot({ status }: { status: string }) {
  return <span className={`status-dot status-${status.toLowerCase()}`} aria-hidden="true" />;
}

function ColumnEditor({
  column,
  onSave,
  onClose,
}: {
  column: CatalogColumn;
  onSave: (description: string, aliases: string[]) => Promise<void>;
  onClose: () => void;
}) {
  const [description, setDescription] = useState(column.description);
  const [aliases, setAliases] = useState(column.aliases.join(", "));
  const [saving, setSaving] = useState(false);

  return (
    <div className="annotation-editor">
      <div className="editor-heading">
        <strong>{column.name}</strong>
        <button className="icon-button" onClick={onClose} title="Close editor" aria-label="Close editor">
          <X size={15} />
        </button>
      </div>
      <label>
        Description
        <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} />
      </label>
      <label>
        Aliases
        <input value={aliases} onChange={(event) => setAliases(event.target.value)} placeholder="revenue, turnover" />
      </label>
      <button
        className="compact-button primary"
        disabled={saving}
        onClick={async () => {
          setSaving(true);
          try {
            await onSave(
              description,
              aliases.split(",").map((value) => value.trim()).filter(Boolean),
            );
          } finally {
            setSaving(false);
          }
        }}
      >
        {saving ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />} Save
      </button>
    </div>
  );
}

function DataTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (!rows.length) return <div className="empty-result">No rows returned.</div>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [catalog, setCatalog] = useState<CatalogTable[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [ragStatus, setRagStatus] = useState<RagStatus | null>(null);
  const [newWorkspace, setNewWorkspace] = useState("");
  const [question, setQuestion] = useState("");
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [expandedTables, setExpandedTables] = useState<string[]>([]);
  const [editingColumn, setEditingColumn] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tab, setTab] = useState<"answer" | "data" | "sql">("answer");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const workspace = workspaces.find((item) => item.id === workspaceId);

  const loadWorkspaces = useCallback(async () => {
    const items = await api.workspaces();
    setWorkspaces(items);
    setWorkspaceId((current) => current || items[0]?.id || "");
  }, []);

  const loadWorkspace = useCallback(async (id: string) => {
    if (!id) return;
    const [sourceItems, catalogResult, history, status] = await Promise.all([
      api.sources(id),
      api.catalog(id),
      api.runs(id),
      api.ragStatus(id),
    ]);
    setSources(sourceItems);
    setCatalog(catalogResult.tables);
    setRuns(history.runs);
    setRagStatus(status);
    setExpandedTables(catalogResult.tables.map((table) => table.id));
  }, []);

  useEffect(() => {
    loadWorkspaces().catch((cause: Error) => setError(cause.message));
  }, [loadWorkspaces]);

  useEffect(() => {
    setActiveRun(null);
    setEvents([]);
    setSelectedTables([]);
    loadWorkspace(workspaceId).catch((cause: Error) => setError(cause.message));
  }, [loadWorkspace, workspaceId]);

  const refreshRun = useCallback(async (runId: string) => {
    if (!workspaceId) return;
    const [run, history] = await Promise.all([api.run(workspaceId, runId), api.runs(workspaceId)]);
    setActiveRun(run);
    setRuns(history.runs);
    setBusy("");
  }, [workspaceId]);

  function subscribe(runId: string) {
    const stream = new EventSource(
      `${API_BASE}/api/v1/workspaces/${workspaceId}/runs/${runId}/events`,
    );
    phases.forEach((phase) => {
      stream.addEventListener(phase, (event) => {
        const parsed = JSON.parse((event as MessageEvent).data) as RunEvent;
        setEvents((current) => [...current.filter((item) => item.sequence !== parsed.sequence), parsed]);
        if (phase === "completed" || phase === "failed") {
          stream.close();
          refreshRun(runId).catch((cause: Error) => setError(cause.message));
        }
      });
    });
    stream.onerror = () => {
      stream.close();
      refreshRun(runId).catch((cause: Error) => setError(cause.message));
    };
  }

  async function createWorkspace() {
    if (!newWorkspace.trim()) return;
    setBusy("workspace");
    try {
      const created = await api.createWorkspace(newWorkspace.trim());
      setNewWorkspace("");
      await loadWorkspaces();
      setWorkspaceId(created.id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function upload(file?: File) {
    if (!file || !workspaceId) return;
    setBusy("upload");
    setError("");
    try {
      await api.upload(workspaceId, file);
      await Promise.all([loadWorkspace(workspaceId), loadWorkspaces()]);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy("");
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function ask() {
    if (!question.trim() || !workspaceId) return;
    setBusy("run");
    setError("");
    setEvents([]);
    setTab("answer");
    try {
      const run = await api.createRun(workspaceId, question.trim(), selectedTables);
      setActiveRun(run);
      subscribe(run.id);
    } catch (cause) {
      setBusy("");
      setError((cause as Error).message);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark"><Database size={18} /></span>
          <div><strong>DataQuery Agent</strong><span>Local analysis workbench</span></div>
        </div>
        <div className="service-state">
          <StatusDot status={ragStatus?.ready ? "ready" : ragStatus?.enabled ? "degraded" : "disabled"} />
          <span>{ragStatus?.ready ? "Semantic retrieval ready" : ragStatus?.enabled ? "Lexical fallback" : "RAG disabled"}</span>
          {workspaceId && (
            <button
              className="icon-button"
              title="Reindex catalog"
              aria-label="Reindex catalog"
              disabled={busy === "reindex"}
              onClick={async () => {
                setBusy("reindex");
                try {
                  await api.reindex(workspaceId);
                  await loadWorkspace(workspaceId);
                } catch (cause) {
                  setError((cause as Error).message);
                } finally {
                  setBusy("");
                }
              }}
            >
              <RefreshCw className={busy === "reindex" ? "spin" : ""} size={15} />
            </button>
          )}
        </div>
      </header>

      <aside className="workspace-rail">
        <div className="rail-heading"><span>Workspaces</span><span>{workspaces.length}</span></div>
        <nav className="workspace-list" aria-label="Workspaces">
          {workspaces.map((item) => (
            <button
              key={item.id}
              className={item.id === workspaceId ? "workspace-item active" : "workspace-item"}
              onClick={() => setWorkspaceId(item.id)}
            >
              <Database size={15} />
              <span><strong>{item.name}</strong><small>{item.table_count} tables</small></span>
            </button>
          ))}
        </nav>
        <div className="new-workspace">
          <input
            value={newWorkspace}
            onChange={(event) => setNewWorkspace(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && createWorkspace()}
            placeholder="New workspace"
            aria-label="New workspace name"
          />
          <button className="icon-button" onClick={createWorkspace} title="Create workspace" aria-label="Create workspace">
            {busy === "workspace" ? <LoaderCircle className="spin" size={16} /> : <CirclePlus size={16} />}
          </button>
        </div>
        <div className="history-block">
          <div className="rail-heading"><span>Recent runs</span></div>
          {runs.slice(0, 8).map((run) => (
            <button
              key={run.id}
              className="history-item"
              onClick={() => { setActiveRun(run); setEvents([]); setTab("answer"); }}
            >
              <StatusDot status={run.status} />
              <span><strong>{run.question}</strong><small>{formatTime(run.created_at)}</small></span>
            </button>
          ))}
        </div>
      </aside>

      <main className="workbench">
        {!workspace ? (
          <section className="first-workspace">
            <Database size={28} />
            <h1>Create a workspace</h1>
            <p>Start with a name, then upload a CSV, XLSX, or Parquet file.</p>
          </section>
        ) : (
          <>
            <header className="workspace-header">
              <div><p>Workspace</p><h1>{workspace.name}</h1></div>
              <div className="workspace-meta"><span>{sources.length} sources</span><span>{catalog.length} tables</span></div>
            </header>

            {error && (
              <div className="error-banner" role="alert">
                <AlertTriangle size={16} /><span>{error}</span>
                <button className="icon-button" onClick={() => setError("")} title="Dismiss" aria-label="Dismiss"><X size={15} /></button>
              </div>
            )}

            <div className="workspace-grid">
              <section className="catalog-pane" aria-label="Data catalog">
                <div className="pane-heading">
                  <div><p>Data catalog</p><span>{catalog.length} tables</span></div>
                  <input ref={fileInput} type="file" accept=".csv,.xlsx,.parquet" hidden onChange={(event) => upload(event.target.files?.[0])} />
                  <button className="icon-button emphasized" onClick={() => fileInput.current?.click()} title="Upload dataset" aria-label="Upload dataset">
                    {busy === "upload" ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
                  </button>
                </div>
                <div className="source-strip">
                  {sources.map((source) => (
                    <div className="source-row" key={source.id}>
                      <FileSpreadsheet size={14} />
                      <span><strong>{source.original_name}</strong><small>{formatBytes(source.size_bytes)} · {source.index_status}</small></span>
                      <button
                        className="icon-button danger"
                        title="Delete source"
                        aria-label={`Delete ${source.original_name}`}
                        onClick={async () => {
                          if (!confirm(`Delete ${source.original_name}?`)) return;
                          await api.deleteSource(workspaceId, source.id);
                          await Promise.all([loadWorkspace(workspaceId), loadWorkspaces()]);
                        }}
                      ><Trash2 size={14} /></button>
                    </div>
                  ))}
                </div>
                <div className="table-tree">
                  {catalog.map((table) => {
                    const expanded = expandedTables.includes(table.id);
                    const selected = selectedTables.includes(table.id);
                    return (
                      <div className="catalog-table" key={table.id}>
                        <div className="table-row">
                          <button
                            className="tree-toggle"
                            onClick={() => setExpandedTables((items) => expanded ? items.filter((id) => id !== table.id) : [...items, table.id])}
                            aria-label={expanded ? "Collapse table" : "Expand table"}
                          >{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
                          <label className="table-select">
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={() => setSelectedTables((items) => selected ? items.filter((id) => id !== table.id) : [...items, table.id])}
                            />
                            <span><strong>{table.display_name}</strong><small>{table.row_count.toLocaleString()} rows</small></span>
                          </label>
                        </div>
                        {expanded && (
                          <div className="column-list">
                            {table.columns.map((column) => (
                              <div className="column-block" key={column.id}>
                                <div className="column-row">
                                  <span><strong>{column.name}</strong><small>{column.data_type}</small></span>
                                  <button className="icon-button" onClick={() => setEditingColumn(column.id)} title="Edit field semantics" aria-label={`Edit ${column.name}`}><Pencil size={13} /></button>
                                </div>
                                {(column.description || column.aliases.length > 0) && <p>{column.description || column.aliases.join(", ")}</p>}
                                {editingColumn === column.id && (
                                  <ColumnEditor
                                    column={column}
                                    onClose={() => setEditingColumn(null)}
                                    onSave={async (description, aliases) => {
                                      await api.updateColumn(workspaceId, column.id, description, aliases);
                                      setEditingColumn(null);
                                      await loadWorkspace(workspaceId);
                                    }}
                                  />
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {!catalog.length && <div className="catalog-empty"><Upload size={20} /><span>Upload a dataset to build the catalog.</span></div>}
                </div>
              </section>

              <section className="query-pane">
                <div className="question-box">
                  <div className="question-label"><Sparkles size={15} /><span>Ask your data</span>{selectedTables.length > 0 && <small>{selectedTables.length} tables selected</small>}</div>
                  <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="例如：按月份汇总销售额并绘制趋势图"
                    rows={3}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) ask();
                    }}
                  />
                  <button className="run-button" onClick={ask} disabled={!question.trim() || !catalog.length || busy === "run"}>
                    {busy === "run" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} fill="currentColor" />} Run query
                  </button>
                </div>

                <div className="result-area">
                  <div className="result-tabs" role="tablist">
                    {(["answer", "data", "sql"] as const).map((name) => (
                      <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)} role="tab" aria-selected={tab === name}>{name}</button>
                    ))}
                    {activeRun && <span className={`run-status ${activeRun.status.toLowerCase()}`}><StatusDot status={activeRun.status} />{activeRun.status}</span>}
                  </div>

                  {!activeRun ? (
                    <div className="result-placeholder"><Search size={24} /><span>Query results will appear here.</span></div>
                  ) : activeRun.status === "FAILED" ? (
                    <div className="run-error"><AlertTriangle size={22} /><strong>{activeRun.error_code}</strong><span>{activeRun.error_message}</span></div>
                  ) : tab === "answer" ? (
                    <div className="answer-view">
                      <div className="answer-text">{activeRun.payload.answer || (activeRun.status === "RUNNING" ? "Running…" : "No answer was generated.")}</div>
                      {(activeRun.payload.warnings ?? []).map((warning) => <div className="warning-row" key={warning}><AlertTriangle size={14} />{warning}</div>)}
                      {activeRun.payload.analysis?.metrics && (
                        <div className="metrics-row">
                          {Object.entries(activeRun.payload.analysis.metrics).map(([key, value]) => <span key={key}><small>{key}</small><strong>{String(value)}</strong></span>)}
                        </div>
                      )}
                      {activeRun.payload.chart && (
                        <Suspense fallback={<div className="chart-loading">Loading chart…</div>}>
                          <ChartView chart={activeRun.payload.chart} />
                        </Suspense>
                      )}
                    </div>
                  ) : tab === "data" ? (
                    <div className="data-view">
                      <div className="scope-line">
                        <span>{activeRun.payload.scope?.rows_returned ?? 0} preview rows</span>
                        {activeRun.payload.scope?.preview_truncated && <span>Preview truncated</span>}
                      </div>
                      <DataTable columns={activeRun.payload.columns ?? []} rows={activeRun.payload.rows ?? []} />
                    </div>
                  ) : (
                    <pre className="sql-view"><code>{activeRun.payload.sql ?? "No SQL was executed."}</code></pre>
                  )}
                </div>

                <section className="trace-pane" aria-label="Agent trace">
                  <div className="trace-heading"><BarChart3 size={15} /><strong>Decision trace</strong><span>{activeRun?.payload.retrieval?.mode ?? "WAITING"}</span></div>
                  <div className="trace-grid">
                    <div className="phase-list">
                      {events.length ? events.map((event) => (
                        <div className="phase-row" key={event.sequence}><StatusDot status={event.level === "error" ? "failed" : "ready"} /><span><strong>{event.phase}</strong><small>{event.message}</small></span><time>{event.sequence}</time></div>
                      )) : <div className="trace-empty">Run phases will stream here.</div>}
                    </div>
                    <div className="retrieval-list">
                      {(activeRun?.payload.retrieval?.matches ?? []).map((match) => (
                        <div className="retrieval-row" key={`${match.entity_type}-${match.column_id || match.table_id}`}>
                          <span className="entity-type">{match.entity_type}</span>
                          <span><strong>{match.label}</strong><small>{match.retrieval_source}</small></span>
                          <code>{match.score.toFixed(4)}</code>
                        </div>
                      ))}
                      {!(activeRun?.payload.retrieval?.matches ?? []).length && <div className="trace-empty">Retrieved tables and columns will appear here.</div>}
                    </div>
                  </div>
                </section>
              </section>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
