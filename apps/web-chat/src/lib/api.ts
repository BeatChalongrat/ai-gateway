// Fetch wrapper that:
//   • reads the Portal access token (cookie or URL ?token=)
//   • injects ``Authorization: Bearer <jwt>`` on every call
//   • redirects to the Portal /login on 401 (no client-side refresh —
//     refresh is the Portal's job)
//
// Chat Edge no longer mints its own tokens, so the previous
// register/login/logout helpers are removed. They were never called
// from production code after the Phase 1.5 migration.

import type {
  Conversation,
  ConversationListResponse,
  ErrorEnvelope,
  FeedbackResponse,
  FileAttachment,
  FileListResponse,
  KnowledgeBase,
  KnowledgeBaseDetailResponse,
  KnowledgeBaseListResponse,
  KnowledgeDocument,
  KnowledgeSearchResponse,
  Memory,
  MemoryEventListResponse,
  MemoryListResponse,
  Message,
  MessageListResponse,
  ModelListResponse,
  ShareExpiry,
  ShareLink,
  UserPreferences,
} from "./types";
import { clearPortalToken, getPortalToken, redirectToPortalLogin } from "./auth";

const API_BASE =
  typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL
    ? process.env.NEXT_PUBLIC_API_URL
    : "http://localhost:4000/api/v1";

class ApiClient {
  // -----------------------------------------------------------
  // Low-level fetch.
  // -----------------------------------------------------------
  async raw(path: string, init: RequestInit = {}): Promise<Response> {
    const headers = new Headers(init.headers);
    if (!headers.has("Content-Type") && init.body) {
      headers.set("Content-Type", "application/json");
    }
    const token = getPortalToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      // We don't need credentials: include — chat-edge cookies are
      // not part of the contract any more.
    });
    if (res.status === 401) {
      // The Portal's refresh flow lives on port 3001. Wipe the
      // local token (so a stale cached value can't keep retrying)
      // and bounce the user.
      clearPortalToken();
      redirectToPortalLogin();
    }
    return res;
  }

  // -----------------------------------------------------------
  // JSON helper.
  // -----------------------------------------------------------
  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await this.raw(path, init);
    const text = await res.text();
    const body = text ? (safeParse(text) as T | ErrorEnvelope) : null;
    if (!res.ok) {
      const env = body as ErrorEnvelope | null;
      throw new ApiError(
        env?.error?.code ?? `HTTP_${res.status}`,
        env?.error?.message ?? res.statusText,
        res.status,
      );
    }
    return body as T;
  }

  // ----- Models ---------------------------------------------
  models() {
    return this.json<ModelListResponse>("/models");
  }

  // ----- Conversations --------------------------------------
  listConversations(cursor?: string) {
    const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return this.json<ConversationListResponse>(`/conversations${q}`);
  }
  createConversation(model_id: string) {
    return this.json<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify({ model_id }),
    });
  }
  getConversation(id: string) {
    return this.json<Conversation>(`/conversations/${id}`);
  }
  renameConversation(id: string, title: string) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
  }
  /** Asks the backend to summarize the conversation's first turn
   *  into a sidebar-friendly title and persist it. Frontend fires
   *  this once after the first assistant stream completes. */
  generateTitle(id: string) {
    return this.json<{ title: string }>(
      `/conversations/${id}/generate-title`,
      { method: "POST" },
    );
  }
  setConversationModel(id: string, model_id: string) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ model_id }),
    });
  }
  /** Bind a knowledge base to a conversation, or unbind by passing
   *  null. The server validates that the KB belongs to the caller's
   *  tenant before persisting. */
  setConversationKB(id: string, knowledge_base_id: string | null) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ knowledge_base_id }),
    });
  }
  /** S1 — toggle the cross-conversation memory layer for THIS
   *  conversation. False = "temporary chat" (no memory injection,
   *  no save/list tools). Returns the updated conversation. */
  setConversationMemory(id: string, enabled: boolean) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ memory_enabled: enabled }),
    });
  }
  /** T4 — patch Thinking Mode preferences for a conversation.
   *  Any subset of the three fields can be sent; the server uses
   *  COALESCE to keep unset fields at their current value. */
  setConversationReasoning(
    id: string,
    patch: {
      reasoning_enabled?: boolean;
      reasoning_mode?: string;
      show_reasoning_summary?: boolean;
    },
  ) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }
  deleteConversation(id: string) {
    return this.raw(`/conversations/${id}`, { method: "DELETE" });
  }
  cancelGeneration(convId: string) {
    return this.json<{ cancelled: boolean }>(
      `/conversations/${convId}/cancel`,
      { method: "POST" },
    );
  }

  // ----- Messages -------------------------------------------
  listMessages(convId: string, opts?: { limit?: number; before?: string }) {
    const params = new URLSearchParams();
    params.set("limit", String(opts?.limit ?? 50));
    if (opts?.before) params.set("before", opts.before);
    return this.json<MessageListResponse>(
      `/conversations/${convId}/messages?${params.toString()}`,
    );
  }
  listConversationsSearch(q: string, limit = 50) {
    const params = new URLSearchParams();
    params.set("q", q);
    params.set("limit", String(limit));
    return this.json<ConversationListResponse>(
      `/conversations?${params.toString()}`,
    );
  }
  listConversationsPage(opts?: { limit?: number; cursor?: string }) {
    const params = new URLSearchParams();
    params.set("limit", String(opts?.limit ?? 50));
    if (opts?.cursor) params.set("cursor", opts.cursor);
    return this.json<ConversationListResponse>(
      `/conversations?${params.toString()}`,
    );
  }
  sendMessageNonStream(convId: string, content: string) {
    return this.json<{ message: Message }>(
      `/conversations/${convId}/messages`,
      { method: "POST", body: JSON.stringify({ content, stream: false }) },
    );
  }
  async openMessageStream(
    convId: string,
    content: string,
    signal: AbortSignal,
    fileIds?: string[],
  ): Promise<Response> {
    const body: Record<string, unknown> = { content, stream: true };
    if (fileIds && fileIds.length > 0) body.file_ids = fileIds;
    return this.raw(`/conversations/${convId}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    });
  }
  async openRegenerateStream(
    convId: string,
    messageId: string,
    signal: AbortSignal,
  ): Promise<Response> {
    return this.raw(
      `/conversations/${convId}/messages/${messageId}/regenerate`,
      { method: "POST", signal },
    );
  }

  // ----- Feedback -------------------------------------------
  setFeedback(messageId: string, rating: "thumbs_up" | "thumbs_down") {
    return this.json<FeedbackResponse>(`/messages/${messageId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating }),
    });
  }

  // ----- Files ----------------------------------------------
  listFiles(convId: string) {
    return this.json<FileListResponse>(`/conversations/${convId}/files`);
  }
  deleteFile(fileId: string) {
    return this.raw(`/files/${fileId}`, { method: "DELETE" });
  }
  /** Absolute URL for the download endpoint. Auth rides on the
   *  ``portal_access_token`` cookie — cookies aren't scoped by port
   *  on localhost, so the cookie set by the Portal flows to the
   *  chat-edge origin automatically. */
  downloadUrl(fileId: string): string {
    return `${API_BASE}/files/${fileId}/download`;
  }

  // ----- Excel Smart Workspace (Phase 4) --------------------
  /** Run the deep inspector against an xlsx upload. The structure
   *  is also cached server-side in inspection_json so a subsequent
   *  message turn picks it up via the system prompt. */
  inspectExcel(fileId: string) {
    return this.json<Record<string, unknown>>(
      `/files/${fileId}/inspect`,
      { method: "POST" },
    );
  }
  /** Execute an AI-generated :::excel-plan against the source
   *  workbook. The server runs the executor on a fresh copy of the
   *  source, validates the output, and persists a new generated
   *  conversation_files row. Returns the new file metadata plus the
   *  validator report so the result card can render both. */
  executeExcelPlan(
    convId: string,
    body: { source_file_id: string; output_filename?: string; plan: unknown },
  ) {
    return this.json<import("./types").ExcelExecuteResponse>(
      `/conversations/${convId}/excel/execute`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  listExcelVersions(convId: string, fileId: string) {
    return this.json<{ versions: import("./types").FileVersion[] }>(
      `/conversations/${convId}/files/${fileId}/versions`,
    );
  }

  /** Phase 4 Session 5 — Artifact Workspace API.
   *  All endpoints are conversation-scoped + tenant-checked
   *  server-side; the frontend just passes ids. */
  listGeneratedFiles(convId: string) {
    return this.json<{ files: import("./types").GeneratedFileEntry[] }>(
      `/conversations/${convId}/generated-files`,
    );
  }

  /** Generic file version timeline — same shape as listExcelVersions
   *  but works for docx / pdf / pptx as well. The backend route is
   *  format-agnostic; the helper name reflects the new use case. */
  listFileVersions(convId: string, fileId: string) {
    return this.json<{ versions: import("./types").FileVersion[] }>(
      `/conversations/${convId}/files/${fileId}/versions`,
    );
  }

  /** Rollback creates a new version (additive) carrying the
   *  target version's content forward. Returns the new file row. */
  rollbackFile(convId: string, lineageFileId: string, targetVersionId: string) {
    return this.json<{
      file: import("./types").FileAttachment;
      message: string;
    }>(
      `/conversations/${convId}/files/${lineageFileId}/rollback`,
      {
        method: "POST",
        body: JSON.stringify({ target_version_id: targetVersionId }),
      },
    );
  }

  compareVersions(convId: string, v1: string, v2: string) {
    const params = new URLSearchParams({ v1, v2 });
    return this.json<import("./types").ComparisonResponse>(
      `/conversations/${convId}/file-compare?${params.toString()}`,
    );
  }

  /** Phase 4 Session 2 — execute a docx/pdf doc plan. Same shape
   *  as executeExcelPlan but the plan + format live at the top
   *  level (no nested wrapping). source_file_id is optional for
   *  the create-from-scratch path. */
  executeDocumentPlan(
    convId: string,
    body: {
      source_file_id?: string;
      output_filename?: string;
      format: "docx" | "pdf" | "pptx";
      plan: unknown;
    },
  ) {
    return this.json<import("./types").DocExecuteResponse>(
      `/conversations/${convId}/document/execute`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  // ----- Memory (S1) ----------------------------------------
  /** List the caller's saved memories. Filters mirror the server
   *  side: scope_type / memory_type / status, with paging via
   *  limit + offset. Defaults match the API (active only, pinned
   *  first via the server's ORDER BY). */
  listMemories(params?: {
    scope_type?: string;
    memory_type?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }) {
    const search = new URLSearchParams();
    if (params?.scope_type) search.set("scope_type", params.scope_type);
    if (params?.memory_type) search.set("memory_type", params.memory_type);
    if (params?.status) search.set("status", params.status);
    if (params?.limit != null) search.set("limit", String(params.limit));
    if (params?.offset != null) search.set("offset", String(params.offset));
    const qs = search.toString();
    return this.json<MemoryListResponse>(
      `/memories${qs ? `?${qs}` : ""}`,
    );
  }
  createMemory(data: {
    scope_type?: string;
    memory_type: string;
    content: string;
    category?: string;
  }) {
    return this.json<Memory>("/memories", {
      method: "POST",
      body: JSON.stringify({
        scope_type: data.scope_type ?? "global_user",
        memory_type: data.memory_type,
        content: data.content,
        category: data.category,
      }),
    });
  }
  updateMemory(
    id: string,
    data: {
      content?: string;
      category?: string;
      memory_type?: string;
      importance_score?: number;
    },
  ) {
    return this.json<Memory>(`/memories/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  }
  deleteMemory(id: string) {
    return this.raw(`/memories/${id}`, { method: "DELETE" });
  }
  pinMemory(id: string, pinned: boolean) {
    return this.json<Memory>(`/memories/${id}/pin`, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    });
  }
  confirmMemory(id: string, confirmed: boolean) {
    return this.json<Memory>(`/memories/${id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    });
  }
  /** Bulk soft-delete. Pass a scope_type to limit (e.g. only
   *  conversation-scoped memories); omit to clear everything. */
  clearAllMemories(scopeType?: string) {
    const qs = scopeType ? `?scope_type=${encodeURIComponent(scopeType)}` : "";
    return this.json<{ deleted_count: number }>(`/memories/all${qs}`, {
      method: "DELETE",
    });
  }
  getMemoryEvents(id: string) {
    return this.json<MemoryEventListResponse>(`/memories/${id}/events`);
  }
  /** S8 — pending preference-suggestion count. Drives the badge
   *  on the sidebar Memory menu item + the dot on the chat
   *  header's MemoryIndicator. Polled every ~2 minutes. */
  getMemoryPendingCount() {
    return this.json<{ count: number }>("/memories/pending-count");
  }

  // ----- Account data management (S9) ----------------------
  /** Generate a JSON export of every memory the caller owns.
   *  Returns metadata + an ID the download endpoint consumes. */
  exportMemories() {
    return this.json<{
      export_id: string;
      filename: string;
      size_bytes: number;
      expires_at: string;
    }>("/account/export-memories", { method: "POST" });
  }
  /** Absolute URL the browser hits to stream the export file —
   *  hits /account/exports/{id}/download with the bearer token
   *  carried via the same auth layer the rest of the client uses. */
  exportDownloadUrl(exportID: string): string {
    return `${API_BASE}/account/exports/${encodeURIComponent(exportID)}/download`;
  }
  /** S9 GDPR right-to-erasure. Confirm string MUST match
   *  exactly: "DELETE ALL MY MEMORIES" (the backend rejects
   *  anything else with 400). */
  purgeMemories(confirm: string) {
    return this.json<{
      memories_deleted: number;
      events_deleted: number;
      states_deleted: number;
      sources_deleted: number;
      exports_deleted: number;
      export_files_unlinked: number;
    }>("/account/purge-memories", {
      method: "DELETE",
      body: JSON.stringify({ confirm }),
    });
  }

  // ----- Preferences ---------------------------------------
  getPreferences() {
    return this.json<UserPreferences>("/user/preferences");
  }
  putPreferences(prefs: Partial<UserPreferences>) {
    return this.json<UserPreferences>("/user/preferences", {
      method: "PUT",
      body: JSON.stringify(prefs),
    });
  }

  // ----- Export ---------------------------------------------
  exportUrl(convId: string, format: "markdown" | "json"): string {
    return `${API_BASE}/conversations/${convId}/export?format=${format}`;
  }
  async exportText(convId: string, format: "markdown" | "json"): Promise<string> {
    const res = await this.raw(`/conversations/${convId}/export?format=${format}`);
    if (!res.ok) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText, res.status);
    }
    return await res.text();
  }

  // ----- Sharing --------------------------------------------
  createShare(convId: string, expiresIn: ShareExpiry) {
    return this.json<ShareLink>(`/conversations/${convId}/share`, {
      method: "POST",
      body: JSON.stringify({ expires_in: expiresIn }),
    });
  }
  getShare(convId: string) {
    return this.json<ShareLink>(`/conversations/${convId}/share`);
  }
  revokeShare(convId: string) {
    return this.raw(`/conversations/${convId}/share`, { method: "DELETE" });
  }

  // ----- Branching ------------------------------------------
  async openEditStream(
    convId: string,
    messageId: string,
    content: string,
    signal: AbortSignal,
  ): Promise<Response> {
    return this.raw(`/conversations/${convId}/messages/${messageId}/edit`, {
      method: "POST",
      body: JSON.stringify({ content }),
      signal,
    });
  }
  switchBranch(convId: string, messageId: string, branchIndex: number) {
    return this.raw(
      `/conversations/${convId}/messages/${messageId}/switch-branch`,
      {
        method: "POST",
        body: JSON.stringify({ branch_index: branchIndex }),
      },
    );
  }

  // ----- Voice -----------------------------------------------
  /** Forward an audio blob to /voice/transcribe. Returns the
   *  Whisper transcript text. 503 means OPENAI_API_KEY isn't set
   *  on the server — caller should fall back to browser native. */
  async transcribeAudio(audio: Blob, language?: string): Promise<string> {
    const form = new FormData();
    // Whisper picks a decoder by extension — name the file after
    // the blob's mime so the server's extension mapper stays sane.
    const ext = mimeToExt(audio.type);
    form.append("audio", audio, `audio.${ext}`);
    if (language && language !== "auto") form.append("language", language);
    const res = await this.raw("/voice/transcribe", {
      method: "POST",
      body: form,
    });
    const text = await res.text();
    const body = text ? (safeParse(text) as { text?: string } | ErrorEnvelope | null) : null;
    if (!res.ok) {
      const env = body as ErrorEnvelope | null;
      throw new ApiError(
        env?.error?.code ?? `HTTP_${res.status}`,
        env?.error?.message ?? res.statusText,
        res.status,
      );
    }
    return (body as { text?: string } | null)?.text ?? "";
  }
  /** Hit /voice/synthesize and return the audio bytes as a Blob. */
  async synthesizeSpeech(opts: {
    text: string;
    voice: string;
    speed: number;
  }): Promise<Blob> {
    const res = await this.raw("/voice/synthesize", {
      method: "POST",
      body: JSON.stringify(opts),
    });
    if (!res.ok) {
      const text = await res.text();
      const env = text ? (safeParse(text) as ErrorEnvelope | null) : null;
      throw new ApiError(
        env?.error?.code ?? `HTTP_${res.status}`,
        env?.error?.message ?? res.statusText,
        res.status,
      );
    }
    return await res.blob();
  }

  // ----- Knowledge bases (RAG) ------------------------------
  listKnowledgeBases() {
    return this.json<KnowledgeBaseListResponse>("/knowledge-bases");
  }
  createKnowledgeBase(data: {
    name: string;
    description?: string;
    chunk_size?: number;
    chunk_overlap?: number;
  }) {
    return this.json<KnowledgeBase>("/knowledge-bases", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }
  getKnowledgeBase(id: string) {
    return this.json<KnowledgeBaseDetailResponse>(`/knowledge-bases/${id}`);
  }
  updateKnowledgeBase(
    id: string,
    patch: { name?: string; description?: string; is_active?: boolean },
  ) {
    return this.json<KnowledgeBase>(`/knowledge-bases/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
  }
  deleteKnowledgeBase(id: string) {
    return this.raw(`/knowledge-bases/${id}`, { method: "DELETE" });
  }
  deleteKBDocument(kbId: string, docId: string) {
    return this.raw(`/knowledge-bases/${kbId}/documents/${docId}`, {
      method: "DELETE",
    });
  }
  reindexKBDocument(kbId: string, docId: string) {
    return this.json<KnowledgeDocument>(
      `/knowledge-bases/${kbId}/documents/${docId}/reindex`,
      { method: "POST" },
    );
  }
  searchKnowledgeBase(kbId: string, query: string, top_k = 5) {
    return this.json<KnowledgeSearchResponse>(
      `/knowledge-bases/${kbId}/search`,
      { method: "POST", body: JSON.stringify({ query, top_k }) },
    );
  }
  /** XHR-backed KB document upload with progress callbacks. Same
   *  ergonomics as uploadFile() — used by the KB management UI. */
  uploadKBDocument(
    kbId: string,
    file: File,
    opts?: {
      signal?: AbortSignal;
      onProgress?: (percent: number) => void;
    },
  ): Promise<KnowledgeDocument> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/knowledge-bases/${kbId}/documents`);
      const token = getPortalToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && opts?.onProgress) {
          opts.onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          clearPortalToken();
          redirectToPortalLogin();
          reject(new ApiError("UNAUTHORIZED", "Session expired", 401));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as KnowledgeDocument);
          } catch {
            reject(
              new ApiError(
                "DECODE_ERROR",
                "Bad response from server",
                xhr.status,
              ),
            );
          }
          return;
        }
        let code = `HTTP_${xhr.status}`;
        let message = xhr.statusText || "Upload failed";
        try {
          const env = JSON.parse(xhr.responseText) as ErrorEnvelope;
          if (env.error?.code) code = env.error.code;
          if (env.error?.message) message = env.error.message;
        } catch {
          /* non-json */
        }
        reject(new ApiError(code, message, xhr.status));
      };
      xhr.onerror = () => {
        reject(new ApiError("NETWORK_ERROR", "Network error", 0));
      };
      xhr.onabort = () => {
        reject(new ApiError("ABORTED", "Upload cancelled", 0));
      };
      if (opts?.signal) {
        if (opts.signal.aborted) {
          xhr.abort();
        } else {
          opts.signal.addEventListener("abort", () => xhr.abort());
        }
      }
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }

  /** XHR-backed upload with progress callbacks. fetch+ReadableStream
   *  upload progress isn't reliable in all browsers in 2026 so we
   *  use XHR to keep the progress bar honest. */
  uploadFile(
    convId: string,
    file: File,
    opts?: {
      signal?: AbortSignal;
      onProgress?: (percent: number) => void;
    },
  ): Promise<FileAttachment> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/conversations/${convId}/files`);
      const token = getPortalToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && opts?.onProgress) {
          opts.onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          // Same redirect-to-portal contract as the JSON path.
          clearPortalToken();
          redirectToPortalLogin();
          reject(new ApiError("UNAUTHORIZED", "Session expired", 401));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as FileAttachment);
          } catch (err) {
            reject(
              new ApiError(
                "DECODE_ERROR",
                "Bad response from server",
                xhr.status,
              ),
            );
          }
          return;
        }
        let code = `HTTP_${xhr.status}`;
        let message = xhr.statusText || "Upload failed";
        try {
          const env = JSON.parse(xhr.responseText) as ErrorEnvelope;
          if (env.error?.code) code = env.error.code;
          if (env.error?.message) message = env.error.message;
        } catch {
          /* non-json body */
        }
        reject(new ApiError(code, message, xhr.status));
      };
      xhr.onerror = () => {
        reject(new ApiError("NETWORK_ERROR", "Network error", 0));
      };
      xhr.onabort = () => {
        reject(new ApiError("ABORTED", "Upload cancelled", 0));
      };
      if (opts?.signal) {
        if (opts.signal.aborted) {
          xhr.abort();
        } else {
          opts.signal.addEventListener("abort", () => xhr.abort());
        }
      }
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

// Map a MediaRecorder mime string back to a file extension. The
// chat-edge handler renames the upload anyway, but giving Whisper a
// well-named file makes upstream errors easier to diagnose.
function mimeToExt(mime: string): string {
  const lc = mime.toLowerCase();
  if (lc.startsWith("audio/webm")) return "webm";
  if (lc.startsWith("audio/mp4")) return "mp4";
  if (lc.startsWith("audio/mpeg") || lc === "audio/mp3") return "mp3";
  if (lc.startsWith("audio/wav") || lc === "audio/x-wav") return "wav";
  if (lc.startsWith("audio/ogg")) return "ogg";
  return "webm";
}

export const api = new ApiClient();

// ── Public share-view fetcher ────────────────────────────────
// Doesn't go through ApiClient because the /shared route must NOT
// carry Authorization headers (the auth middleware is not in the
// route's chain on the server side, but sending the header would
// leak the user's token to a public surface).
export interface PublicSharedFile {
  id: string;
  filename: string;
  content_type: string;
  file_size_bytes: number;
}
export interface PublicSharedMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  model_id?: string | null;
  created_at: string;
  files?: PublicSharedFile[];
}
export interface PublicSharedView {
  conversation: {
    title: string;
    model_id: string;
    created_at: string;
    updated_at: string;
  };
  messages: PublicSharedMessage[];
  expires_at?: string;
}

export async function fetchPublicShared(
  token: string,
): Promise<PublicSharedView | null> {
  const res = await fetch(`${API_BASE}/shared/${encodeURIComponent(token)}`, {
    // No credentials, no Authorization — pristine public request.
    credentials: "omit",
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new ApiError(`HTTP_${res.status}`, res.statusText, res.status);
  }
  return (await res.json()) as PublicSharedView;
}
