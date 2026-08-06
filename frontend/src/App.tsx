import {
  AlertTriangle,
  BarChart3,
  Check,
  ChevronDown,
  ChevronRight,
  CirclePlus,
  Database,
  Download,
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
import { MarkdownText } from "./MarkdownText";
import type {
  CatalogColumn,
  CatalogTable,
  RagStatus,
  Run,
  RunEvent,
  RunPayload,
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

const MAX_FILE_BYTES = 50 * 1024 * 1024;

const TAB_LABELS = { answer: "回答", analysis: "分析", data: "数据", sql: "SQL" };
const STATUS_LABELS: Record<string, string> = {
  RUNNING: "运行中",
  COMPLETED: "已完成",
  FAILED: "失败",
};
const PHASE_LABELS: Record<string, string> = {
  retrieving: "检索目录",
  planning: "生成计划",
  validating: "校验 SQL",
  querying: "执行查询",
  analyzing: "Pandas 分析",
  answering: "生成回答",
  completed: "完成",
  failed: "失败",
};
const ANALYSIS_LABELS: Record<string, string> = {
  describe: "描述统计",
  group_aggregate: "分组聚合",
  correlation: "相关性分析",
  trend: "趋势分析",
  outlier_iqr: "IQR 异常值检测",
  nse: "Nash-Sutcliffe 效率系数（NSE）",
  kge: "Kling-Gupta 效率系数（KGE）",
  nse_kge: "NSE / KGE 效率系数",
};
const METRIC_LABELS: Record<string, string> = {
  aggregation: "聚合方式",
  alpha: "变异性比率 α",
  beta: "偏差比率 β",
  change: "变化量",
  correlation: "相关系数",
  direction: "趋势方向",
  first: "起始值",
  groups: "分组数",
  iqr: "四分位距",
  last: "结束值",
  lower_bound: "下界",
  outlier_count: "异常值数",
  pairs: "有效样本对",
  nse: "NSE",
  kge: "KGE",
  q1: "Q1",
  q3: "Q3",
  upper_bound: "上界",
};

const RETRIEVAL_MODE_LABELS: Record<string, string> = {
  HYBRID: "混合检索（词法 + 向量）",
  LEXICAL_FALLBACK: "词法检索降级",
  LEXICAL: "词法检索",
  VECTOR: "向量检索",
  NONE: "未执行检索",
};

function retrievalModeLabel(mode?: string) {
  if (!mode) return "等待中";
  return RETRIEVAL_MODE_LABELS[mode] ?? mode;
}

function isAnswerTab(tab: "answer" | "analysis" | "data" | "sql") {
  return tab === "answer";
}

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatIndexStatus(status: string) {
  return ({
    FAILED: "索引失败",
    INDEXING: "正在索引",
    PENDING: "等待索引",
    READY: "索引完成",
  } as Record<string, string>)[status] ?? status;
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
        <button className="icon-button" onClick={onClose} title="关闭编辑" aria-label="关闭编辑">
          <X size={15} />
        </button>
      </div>
      <label>
        字段说明
        <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} />
      </label>
      <label>
        同义词
        <input value={aliases} onChange={(event) => setAliases(event.target.value)} placeholder="营收，营业额" />
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
        {saving ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />} 保存
      </button>
    </div>
  );
}

function DataTable({ columns, rows }: { columns: string[]; rows: Record<string, unknown>[] }) {
  if (!rows.length) return <div className="empty-result">没有可展示的结果行。</div>;
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

function AnalysisView({ analysis }: { analysis: NonNullable<RunPayload["analysis"]> }) {
  return (
    <div className="analysis-view">
      <div className="analysis-summary">
        <div><small>分析方法</small><strong>{ANALYSIS_LABELS[analysis.operation] ?? analysis.operation}</strong></div>
        <div><small>输入行数</small><strong>{analysis.input_rows.toLocaleString()}</strong></div>
      </div>
      <div className="analysis-detail"><small>LLM 分析意图</small><MarkdownText>{analysis.intent || "根据问题选择受限分析算子，并由 Pandas 计算。"}</MarkdownText></div>
      <div className="analysis-detail"><small>计算公式</small><MarkdownText>{analysis.formula || "由 Pandas 按受限算子计算"}</MarkdownText></div>
      <div className="metrics-row">
        {Object.entries(analysis.metrics).map(([key, value]) => (
          <span key={key}><small>{METRIC_LABELS[key] ?? key}</small><strong>{String(value)}</strong></span>
        ))}
      </div>
      <DataTable columns={analysis.columns} rows={analysis.rows} />
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
  const [editingWorkspace, setEditingWorkspace] = useState<string | null>(null);
  const [workspaceDraft, setWorkspaceDraft] = useState("");
  const [question, setQuestion] = useState("");
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [expandedTables, setExpandedTables] = useState<string[]>([]);
  const [editingColumn, setEditingColumn] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [tab, setTab] = useState<"answer" | "analysis" | "data" | "sql">("answer");
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
    const [run, history, persistedEvents] = await Promise.all([
      api.run(workspaceId, runId),
      api.runs(workspaceId),
      api.events(workspaceId, runId),
    ]);
    setActiveRun(run);
    setRuns(history.runs);
    setEvents(persistedEvents);
    setBusy("");
  }, [workspaceId]);

  const selectRun = useCallback(async (runId: string) => {
    if (!workspaceId) return;
    try {
      const [run, persistedEvents] = await Promise.all([
        api.run(workspaceId, runId),
        api.events(workspaceId, runId),
      ]);
      setActiveRun(run);
      setEvents(persistedEvents);
      setQuestion(run.question);
      setSelectedTables(run.payload.selected_table_ids ?? []);
      setTab("answer");
    } catch (cause) {
      setError((cause as Error).message);
    }
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

  async function renameWorkspace(workspaceIdToRename: string) {
    const name = workspaceDraft.trim();
    if (name.length < 2) {
      setError("工作区名称至少需要 2 个字符。");
      return;
    }
    setBusy("workspace-rename");
    setError("");
    try {
      await api.updateWorkspace(workspaceIdToRename, name);
      setEditingWorkspace(null);
      setWorkspaceDraft("");
      await loadWorkspaces();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy("");
    }
  }

  async function upload(file?: File) {
    if (!file || !workspaceId) return;
    if (file.size > MAX_FILE_BYTES) {
      setError("单个文件最大为 50 MB，请压缩、拆分或筛选数据后再上传。");
      if (fileInput.current) fileInput.current.value = "";
      return;
    }
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
          <div><strong>DataQuery Agent</strong><span>本地数据分析台</span></div>
        </div>
        <div className="service-state">
          <StatusDot status={ragStatus?.ready ? "ready" : ragStatus?.enabled ? "degraded" : "disabled"} />
          <span>{ragStatus?.ready ? "语义检索已就绪" : ragStatus?.enabled ? "已降级为词法检索" : "RAG 未启用"}</span>
          {workspaceId && (
            <button
              className="icon-button"
              title="重新建立目录索引"
              aria-label="重新建立目录索引"
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
        <section className="rail-section workspace-section" aria-label="工作区列表">
          <div className="rail-heading"><span>工作区</span><span>{workspaces.length}</span></div>
          <nav className="workspace-list" aria-label="工作区">
            {workspaces.map((item) => {
              const editing = editingWorkspace === item.id;
              return (
                <div className={item.id === workspaceId ? "workspace-row active" : "workspace-row"} key={item.id}>
                  {editing ? (
                    <form
                      className="workspace-editor"
                      onSubmit={(event) => {
                        event.preventDefault();
                        renameWorkspace(item.id);
                      }}
                    >
                      <input
                        autoFocus
                        aria-label={`修改 ${item.name} 的名称`}
                        maxLength={120}
                        value={workspaceDraft}
                        onChange={(event) => setWorkspaceDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") {
                            setEditingWorkspace(null);
                            setWorkspaceDraft("");
                          }
                        }}
                      />
                      <button className="workspace-action" type="submit" disabled={busy === "workspace-rename" || workspaceDraft.trim().length < 2} title="保存名称" aria-label="保存工作区名称">
                        {busy === "workspace-rename" ? <LoaderCircle className="spin" size={14} /> : <Check size={14} />}
                      </button>
                      <button className="workspace-action" type="button" onClick={() => { setEditingWorkspace(null); setWorkspaceDraft(""); }} title="取消修改" aria-label="取消修改工作区名称">
                        <X size={14} />
                      </button>
                    </form>
                  ) : (
                    <>
                      <button
                        className={item.id === workspaceId ? "workspace-item active" : "workspace-item"}
                        onClick={() => setWorkspaceId(item.id)}
                        title={item.name}
                      >
                        <Database size={15} />
                        <span><strong>{item.name}</strong><small>{item.table_count} 张表</small></span>
                      </button>
                      <button
                        className="workspace-action workspace-rename"
                        onClick={() => { setEditingWorkspace(item.id); setWorkspaceDraft(item.name); }}
                        title="修改工作区名称"
                        aria-label={`修改工作区名称：${item.name}`}
                      >
                        <Pencil size={13} />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
          </nav>
          <div className="new-workspace">
            <input
              value={newWorkspace}
              onChange={(event) => setNewWorkspace(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && createWorkspace()}
              placeholder="新建工作区"
              aria-label="新建工作区名称"
            />
            <button className="icon-button" onClick={createWorkspace} title="创建工作区" aria-label="创建工作区">
              {busy === "workspace" ? <LoaderCircle className="spin" size={16} /> : <CirclePlus size={16} />}
            </button>
          </div>
        </section>
        <section className="rail-section history-section" aria-label="最近运行">
          <div className="rail-heading"><span>最近运行</span><span>{runs.length}</span></div>
          <nav className="history-list" aria-label="最近运行列表">
            {runs.map((run) => (
              <button
                key={run.id}
                className={run.id === activeRun?.id ? "history-item active" : "history-item"}
                onClick={() => selectRun(run.id)}
                title={run.question}
                aria-label={`载入历史运行：${run.question}`}
              >
                <StatusDot status={run.status} />
                <span><strong>{run.question}</strong><small>{formatTime(run.created_at)}</small></span>
              </button>
            ))}
          </nav>
        </section>
      </aside>

      <main className="workbench">
        {!workspace ? (
          <section className="first-workspace">
            <Database size={28} />
            <h1>创建工作区</h1>
            <p>输入工作区名称，然后上传 CSV、TSV、XLS、XLSX 或 Parquet 数据文件。</p>
          </section>
        ) : (
          <>
            <header className="workspace-header">
              <div><p>工作区</p><h1>{workspace.name}</h1></div>
              <div className="workspace-meta"><span>{sources.length} 个文件</span><span>{catalog.length} 张表</span></div>
            </header>

            {error && (
              <div className="error-banner" role="alert">
                <AlertTriangle size={16} /><span>{error}</span>
                <button className="icon-button" onClick={() => setError("")} title="关闭提示" aria-label="关闭提示"><X size={15} /></button>
              </div>
            )}

            <div className="workspace-grid">
              <section className="catalog-pane" aria-label="数据目录">
                <div className="pane-heading">
                  <div><p>数据目录</p><span>{catalog.length} 张表 · 单文件最大 50 MB</span></div>
                  <input ref={fileInput} type="file" accept=".csv,.tsv,.xls,.xlsx,.parquet" hidden onChange={(event) => upload(event.target.files?.[0])} />
                  <button className="icon-button emphasized" onClick={() => fileInput.current?.click()} title="上传数据文件" aria-label="上传数据文件">
                    {busy === "upload" ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}
                  </button>
                </div>
                <div className="source-strip">
                  {sources.map((source) => (
                    <div className="source-row" key={source.id}>
                      <FileSpreadsheet size={14} />
                      <span><strong>{source.original_name}</strong><small>{formatBytes(source.size_bytes)} · {formatIndexStatus(source.index_status)}</small></span>
                      <button
                        className="icon-button danger"
                        title="删除数据文件"
                        aria-label={`删除 ${source.original_name}`}
                        onClick={async () => {
                          if (!confirm(`确认删除 ${source.original_name}？`)) return;
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
                            aria-label={expanded ? "收起数据表" : "展开数据表"}
                          >{expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
                          <label className="table-select">
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={() => setSelectedTables((items) => selected ? items.filter((id) => id !== table.id) : [...items, table.id])}
                            />
                            <span><strong>{table.display_name}</strong><small>{table.row_count.toLocaleString()} 行</small></span>
                          </label>
                        </div>
                        {expanded && (
                          <div className="column-list">
                            {table.columns.map((column) => (
                              <div className="column-block" key={column.id}>
                                <div className="column-row">
                                  <span><strong>{column.name}</strong><small>{column.data_type}</small></span>
                                  <button className="icon-button" onClick={() => setEditingColumn(column.id)} title="编辑字段语义" aria-label={`编辑 ${column.name}`}><Pencil size={13} /></button>
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
                  {!catalog.length && <div className="catalog-empty"><Upload size={20} /><span>上传数据文件以建立目录。支持 CSV、TSV、XLS、XLSX、Parquet，单文件最大 50 MB。</span></div>}
                </div>
              </section>

              <section className="query-pane">
                <div className="question-box">
                  <div className="question-label"><Sparkles size={15} /><span>提问或发起分析</span>{selectedTables.length > 0 && <small>已选择 {selectedTables.length} 张表</small>}</div>
                  <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="例如：分析销售额与折扣率的相关性，并说明计算公式"
                    rows={3}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) ask();
                    }}
                  />
                  <button className="run-button" onClick={ask} disabled={!question.trim() || !catalog.length || busy === "run"}>
                    {busy === "run" ? <LoaderCircle className="spin" size={16} /> : <Play size={16} fill="currentColor" />} 开始运行
                  </button>
                </div>

                <div className="result-area">
                  <div className="result-tabs" role="tablist">
                    {(["answer", "analysis", "data", "sql"] as const).map((name) => (
                      <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)} role="tab" aria-selected={tab === name} disabled={name === "analysis" && !activeRun?.payload.analysis}>{TAB_LABELS[name]}</button>
                    ))}
                    {activeRun && <span className={`run-status ${activeRun.status.toLowerCase()}`}><StatusDot status={activeRun.status} />{STATUS_LABELS[activeRun.status]}</span>}
                    {activeRun?.status === "COMPLETED" && (tab === "analysis" ? activeRun.payload.analysis : activeRun.payload.columns?.length) && (
                      <a
                        className="icon-button"
                        href={api.downloadUrl(workspaceId, activeRun.id, tab === "analysis" ? "analysis" : "result")}
                        title={tab === "analysis" ? "下载分析结果 CSV" : "下载查询结果 CSV"}
                        aria-label={tab === "analysis" ? "下载分析结果 CSV" : "下载查询结果 CSV"}
                      >
                        <Download size={15} />
                      </a>
                    )}
                  </div>

                  {!activeRun ? (
                    <div className="result-placeholder"><Search size={24} /><span>查询、分析、图表和证据会显示在这里。</span></div>
                  ) : activeRun.status === "FAILED" ? (
                    <div className="run-error"><AlertTriangle size={22} /><strong>{activeRun.error_code}</strong><span>{activeRun.error_message}</span></div>
                  ) : Boolean(activeRun) && isAnswerTab(tab) ? (
                    <div className="answer-view">
                      <MarkdownText className="answer-text">{activeRun.payload.answer || (activeRun.status === "RUNNING" ? "正在运行…" : "未生成回答。")}</MarkdownText>
                      {(activeRun.payload.warnings ?? []).map((warning) => <div className="warning-row" key={warning}><AlertTriangle size={14} />{warning}</div>)}
                      {activeRun.payload.chart && (
                        <Suspense fallback={<div className="chart-loading">正在加载图表…</div>}>
                          <ChartView chart={activeRun.payload.chart} />
                        </Suspense>
                      )}
                    </div>
                  ) : tab === "analysis" && activeRun.payload.analysis ? (
                    <AnalysisView analysis={activeRun.payload.analysis} />
                  ) : tab === "data" ? (
                    <div className="data-view">
                      <div className="scope-line">
                        <span>{activeRun.payload.scope?.rows_returned ?? 0} 行预览数据</span>
                        {activeRun.payload.scope?.preview_truncated && <span>预览已截断</span>}
                      </div>
                      <DataTable columns={activeRun.payload.columns ?? []} rows={activeRun.payload.rows ?? []} />
                    </div>
                  ) : (
                    <pre className="sql-view"><code>{activeRun.payload.sql ?? "未执行 SQL。"}</code></pre>
                  )}
                </div>

                <section className="trace-pane" aria-label="Agent 决策轨迹">
                  <div className="trace-heading"><BarChart3 size={15} /><strong>决策轨迹</strong><span title="混合检索同时使用关键词匹配和 Qdrant 向量语义匹配">{retrievalModeLabel(activeRun?.payload.retrieval?.mode)}</span></div>
                  <div className="trace-grid">
                    <div className="phase-list">
                      {events.length ? events.map((event) => (
                        <div className="phase-row" key={event.sequence}><StatusDot status={event.level === "error" ? "failed" : "ready"} /><span><strong>{PHASE_LABELS[event.phase] ?? event.phase}</strong><small>{event.message}</small></span><time>{event.sequence}</time></div>
                      )) : <div className="trace-empty">运行阶段会在这里实时显示。</div>}
                    </div>
                    <div className="retrieval-list">
                      {(activeRun?.payload.retrieval?.matches ?? []).map((match) => (
                        <div className="retrieval-row" key={`${match.entity_type}-${match.column_id || match.table_id}`}>
                          <span className="entity-type">{match.entity_type === "table" ? "数据表" : "字段"}</span>
                          <span><strong>{match.label}</strong><small>{match.retrieval_source === "vector" ? "向量检索" : match.retrieval_source === "hybrid" ? "混合检索" : "词法检索"}</small></span>
                          <code>{match.score.toFixed(4)}</code>
                        </div>
                      ))}
                      {!(activeRun?.payload.retrieval?.matches ?? []).length && <div className="trace-empty">检索到的表和字段会显示在这里。</div>}
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
