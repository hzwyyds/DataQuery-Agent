import type {
  CatalogColumn,
  CatalogTable,
  Conversation,
  RagStatus,
  Run,
  RunEvent,
  Source,
  Workspace,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail ?? message;
    } catch {
      // Keep the stable HTTP fallback when the response is not JSON.
    }
    if (response.status === 413) message = "文件超过 50 MB 限制，或工作区超过 200 MB 限制";
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  workspaces: () => request<Workspace[]>("/api/v1/workspaces"),
  createWorkspace: (name: string) =>
    request<Workspace>("/api/v1/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  conversations: (workspaceId: string) =>
    request<{ conversations: Conversation[] }>("/api/v1/workspaces/" + workspaceId + "/conversations"),
  createConversation: (workspaceId: string, title = "新会话") =>
    request<Conversation>("/api/v1/workspaces/" + workspaceId + "/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  sources: (workspaceId: string) =>
    request<Source[]>(`/api/v1/workspaces/${workspaceId}/sources`),
  catalog: (workspaceId: string) =>
    request<{ tables: CatalogTable[] }>(`/api/v1/workspaces/${workspaceId}/catalog`),
  upload: (workspaceId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Source>(`/api/v1/workspaces/${workspaceId}/files`, {
      method: "POST",
      body,
    });
  },
  deleteSource: (workspaceId: string, sourceId: string) =>
    request<void>(`/api/v1/workspaces/${workspaceId}/sources/${sourceId}`, {
      method: "DELETE",
    }),
  updateColumn: (
    workspaceId: string,
    columnId: string,
    description: string,
    aliases: string[],
  ) =>
    request<CatalogColumn>(
      `/api/v1/workspaces/${workspaceId}/catalog/columns/${columnId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description, aliases }),
      },
    ),
  ragStatus: (workspaceId: string) =>
    request<RagStatus>(`/api/v1/workspaces/${workspaceId}/rag/status`),
  reindex: (workspaceId: string) =>
    request(`/api/v1/workspaces/${workspaceId}/rag/reindex`, { method: "POST" }),
  createRun: (workspaceId: string, question: string, selectedTableIds: string[], conversationId?: string) =>
    request<Run>(`/api/v1/workspaces/${workspaceId}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, selected_table_ids: selectedTableIds, conversation_id: conversationId }),
    }),
  runs: (workspaceId: string) =>
    request<{ runs: Run[] }>(`/api/v1/workspaces/${workspaceId}/runs?limit=200`),
  run: (workspaceId: string, runId: string) =>
    request<Run>(`/api/v1/workspaces/${workspaceId}/runs/${runId}`),
  events: (workspaceId: string, runId: string) =>
    request<RunEvent[]>(`/api/v1/workspaces/${workspaceId}/runs/${runId}/events/history`),
  downloadUrl: (workspaceId: string, runId: string, kind: "result" | "analysis") =>
    `${API_BASE}/api/v1/workspaces/${workspaceId}/runs/${runId}/download?kind=${kind}`,
};
