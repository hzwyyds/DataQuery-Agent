export type Workspace = {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  source_count: number;
  table_count: number;
};

export type Source = {
  id: string;
  workspace_id: string;
  original_name: string;
  size_bytes: number;
  index_status: string;
  index_error: string | null;
  created_at: string;
};

export type CatalogColumn = {
  id: string;
  name: string;
  data_type: string;
  null_count: number;
  distinct_count: number;
  sample_values: unknown[];
  description: string;
  aliases: string[];
};

export type CatalogTable = {
  id: string;
  source_id: string;
  physical_name: string;
  display_name: string;
  row_count: number;
  columns: CatalogColumn[];
};

export type RetrievalMatch = {
  entity_type: "table" | "column";
  table_id: string;
  column_id?: string;
  label: string;
  score: number;
  retrieval_source: "lexical" | "vector" | "hybrid";
  content: string;
};

export type ChartResult = {
  type: "line" | "bar" | "scatter";
  x: string;
  y: string[];
  series: string | null;
  data: Record<string, unknown>[];
  source_points: number;
  displayed_points: number;
  downsampled: boolean;
};

export type RunPayload = {
  answer?: string;
  sql?: string | null;
  columns?: string[];
  rows?: Record<string, unknown>[];
  retrieval?: { mode: string; matches: RetrievalMatch[] };
  analysis?: {
    operation: string;
    columns: string[];
    formula: string;
    intent: string;
    input_rows: number;
    rows: Record<string, unknown>[];
    metrics: Record<string, unknown>;
  } | null;
  evidence?: { id: string; fact: string }[];
  chart?: ChartResult | null;
  scope?: {
    rows_read: number;
    rows_returned: number;
    preview_truncated: boolean;
    displayed_points: number;
    downsampled: boolean;
  } | null;
  warnings?: string[];
  error?: string | null;
  selected_table_ids?: string[];
};

export type Run = {
  id: string;
  workspace_id: string;
  question: string;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  payload: RunPayload;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

export type RunEvent = {
  sequence: number;
  phase: string;
  level: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type RagStatus = {
  enabled: boolean;
  ready?: boolean;
  qdrant: boolean;
  embedding: boolean;
};
