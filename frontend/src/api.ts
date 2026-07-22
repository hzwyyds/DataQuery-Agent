import type {
  CatalogColumn,
  CatalogTable,
  RagStatus,
  Run,
  Source,
  Workspace,
} from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      message = body.detail ?? message;
    } catch {
      // Keep the stable HTTP fallback when the response is not JSON.
    }
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
  createRun: (workspaceId: string, question: string, selectedTableIds: string[]) =>
    request<Run>(`/api/v1/workspaces/${workspaceId}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, selected_table_ids: selectedTableIds }),
    }),
  runs: (workspaceId: string) =>
    request<{ runs: Run[] }>(`/api/v1/workspaces/${workspaceId}/runs`),
  run: (workspaceId: string, runId: string) =>
    request<Run>(`/api/v1/workspaces/${workspaceId}/runs/${runId}`),
};
