// chat-edge client (E3) — the subset of SranDesktop.Infrastructure/Api/
// ChatEdgeApiClient.cs the turn needs. Bearer = the portal access token the
// host owns (chat-edge accepts the portal JWT). The page created the
// conversation here, so this is where the host reads its project/model,
// loads history, writes the turn back (server-authoritative transcript —
// FIX 9), and renames it after the first turn (auto-title).

import type { Logger } from "./logger";

export interface ConversationDto {
  id: string;
  title?: string | null;
  model_id?: string | null;
  project_id?: string | null;
  message_count?: number;
  memory_enabled?: boolean | null;
}
export interface ProjectDto {
  id: string;
  name?: string | null;
  instructions?: string | null;
}
export interface MessageDto {
  id: string;
  role: string;
  content?: string | null;
  status?: string | null;
  client_id?: string | null;
  created_at?: string;
}
export interface MemoryDto {
  id: string;
  content: string;
  created_at?: string;
  /** FIX63 — the server's class for the memory (confirmed_instruction,
   * response_preference, inferred_preference, explicit_fact, …). The
   * prompt renders standing instructions and facts under different
   * headings, the way the web frame does; absent ⇒ treated as a fact. */
  memory_type?: string;
}
/** FIX27: one row of GET /conversations/{id}/files (handler.fileResponse). */
export interface FileRowDto {
  id: string;
  filename: string;
  content_type: string;
  file_size_bytes: number;
  extraction_status: string;
  message_id?: string;
}
export interface ImportMessageItem {
  client_id: string;
  role: "user" | "assistant";
  content: string;
  model_id: string | null;
  created_at: string;
  file_ids?: string[];
}

export class ChatEdgeClient {
  constructor(
    private readonly baseUrl: string,
    private readonly accessToken: () => string | null,
    private readonly log: Logger,
    private readonly timeoutMs = 15_000,
  ) {}

  getConversation(id: string): Promise<ConversationDto | null> {
    return this.get<ConversationDto>(`/api/v1/conversations/${encodeURIComponent(id)}`);
  }

  async listProjects(): Promise<ProjectDto[]> {
    const body = await this.get<{ projects?: ProjectDto[] } | ProjectDto[]>("/api/v1/projects");
    if (Array.isArray(body)) return body;
    return body?.projects ?? [];
  }

  async listMessages(conversationId: string, limit = 200): Promise<MessageDto[]> {
    const body = await this.get<{ messages?: MessageDto[] } | MessageDto[]>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages?limit=${limit}`,
    );
    if (Array.isArray(body)) return body;
    return body?.messages ?? [];
  }

  async listGlobalMemories(limit = 100): Promise<MemoryDto[]> {
    const body = await this.get<{ memories?: MemoryDto[] }>(`/api/v1/memories?scope_type=global_user&status=active&limit=${limit}&offset=0`, 4_000);
    return body?.memories ?? [];
  }

  /** POST /api/v1/memories — FIX58. The desktop `remember` tool writes the
   *  SAME store the web client does, so a fact saved here is on the Memory
   *  page and in the next web chat.
   *
   *  Returns a discriminated result rather than null-on-error, because the
   *  reason matters to the caller: chat-edge refuses a save that trips the
   *  content guardrail, that is a duplicate, or whose text was masked before
   *  it reached the model, and each of those has to reach the model as its
   *  own honest sentence. Collapsing them into "failed" is how a model ends
   *  up telling the user it remembered something it did not. */
  async createMemory(input: {
    content: string;
    scopeType: "global_user" | "project";
    scopeId?: string | null;
    memoryType?: string;
    category?: string;
  }): Promise<{ ok: true; id: string; content: string } | { ok: false; status: number; detail: string }> {
    const token = this.accessToken();
    if (!token) return { ok: false, status: 0, detail: "not signed in" };
    const body: Record<string, unknown> = {
      content: input.content,
      scope_type: input.scopeType,
      memory_type: input.memoryType || "explicit_fact",
    };
    if (input.scopeId) body["scope_id"] = input.scopeId;
    if (input.category) body["category"] = input.category;
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/memories`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      if (!res.ok) {
        let detail = "";
        try {
          const text = await res.text();
          try {
            const parsed = JSON.parse(text) as { message?: string; error?: { message?: string } };
            detail = parsed.error?.message || parsed.message || text;
          } catch {
            detail = text;
          }
        } catch {
          /* body unreadable — the status still tells the caller something */
        }
        this.log.warn(`chat-edge POST /api/v1/memories → HTTP ${res.status} ${detail.slice(0, 200)}`);
        return { ok: false, status: res.status, detail: detail.slice(0, 300) };
      }
      const saved = (await res.json()) as { id?: string; content?: string };
      return { ok: true, id: saved.id ?? "", content: saved.content ?? input.content };
    } catch (err) {
      const detail = (err as Error).message;
      this.log.warn(`chat-edge POST /api/v1/memories failed: ${detail}`);
      return { ok: false, status: 0, detail };
    }
  }

  async importMessages(conversationId: string, messages: ImportMessageItem[]): Promise<{ imported: number; skipped: number } | null> {
    const res = await this.send("POST", `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/import`, { messages });
    if (!res) return null;
    return (await res.json()) as { imported: number; skipped: number };
  }

  /** POST /api/v1/code/run — the code interpreter (run_python, host-side in E4). */
  async runCode(code: string, fileIds: string[], signal: AbortSignal): Promise<{ exit: number; stdout?: string; stderr?: string; timed_out?: boolean; truncated?: boolean; images?: { filename: string; data_uri: string }[] } | null> {
    const token = this.accessToken();
    if (!token) return null;
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/code/run`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ code, files: fileIds }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(120_000)]),
      });
      if (!res.ok) {
        this.log.warn(`chat-edge POST /api/v1/code/run → HTTP ${res.status}`);
        return null;
      }
      return (await res.json()) as { exit: number; stdout?: string; stderr?: string; truncated?: boolean; images?: { filename: string; data_uri: string }[] };
    } catch (err) {
      this.log.warn(`chat-edge code/run failed: ${(err as Error).message}`);
      return null;
    }
  }

  /** GET /api/v1/search — FIX11 §2: search the user's OWN past conversations,
   * messages and saved memories. chat-edge scopes every query to the
   * authenticated (userID, tenantID) inside SearchService, so a session can
   * only ever see its own history — the same read surface as listMessages.
   * Host-side like run_python (Bearer = the portal token the host owns); the
   * sidecar never holds the JWT. */
  async searchPastConversations(
    query: string,
    scope: string,
    limit: number,
  ): Promise<{ query?: string; scope?: string; total?: number; results?: unknown[] } | null> {
    const qs = `q=${encodeURIComponent(query)}&scope=${encodeURIComponent(scope)}&limit=${limit}`;
    return this.get<{ query?: string; scope?: string; total?: number; results?: unknown[] }>(`/api/v1/search?${qs}`);
  }

  /** POST .../messages/{messageId}/edit — FIX11 §3: fork the conversation at a
   * user message and STREAM the new branch's assistant reply. chat-edge persists
   * the fork (parent_id/branch_index/is_active) server-side; the host relays the
   * SSE to the page. Bearer = the portal token the host owns; the sidecar never
   * holds it. Returns the streaming Response, or null on failure. */
  async editStream(conversationId: string, messageId: string, content: string, signal: AbortSignal): Promise<Response | null> {
    const token = this.accessToken();
    if (!token) return null;
    try {
      const res = await fetch(
        `${this.baseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/edit`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "text/event-stream" },
          body: JSON.stringify({ content }),
          signal,
        },
      );
      if (!res.ok) {
        this.log.warn(`chat-edge POST .../messages/${messageId}/edit → HTTP ${res.status}`);
        return null;
      }
      return res;
    } catch (err) {
      this.log.warn(`chat-edge edit stream failed: ${(err as Error).message}`);
      return null;
    }
  }

  async renameConversation(conversationId: string, title: string): Promise<boolean> {
    const res = await this.send("PATCH", `/api/v1/conversations/${encodeURIComponent(conversationId)}`, { title });
    return res !== null;
  }

  /** FIX3 §4: upload raw bytes (a scanned PDF the sidecar read + handed over) to
   * chat-edge for OCR. The host holds the session; the sidecar never does. Returns
   * the server file id, or null on failure. */
  async uploadFileBytes(conversationId: string, bytes: Buffer, name: string, mime: string): Promise<string | null> {
    const token = this.accessToken();
    if (!token) return null;
    try {
      const form = new FormData();
      form.append("file", new Blob([new Uint8Array(bytes)], { type: mime }), name);
      const res = await fetch(`${this.baseUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/files`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
        signal: AbortSignal.timeout(120_000),
      });
      if (!res.ok) {
        this.log.warn(`chat-edge OCR upload → HTTP ${res.status}`);
        return null;
      }
      const j = (await res.json()) as { id?: string };
      return j.id ?? null;
    } catch (err) {
      this.log.warn(`chat-edge OCR upload failed: ${(err as Error).message}`);
      return null;
    }
  }

  /** FIX27: the files of a conversation — the ONLY way an attachment handle is
   * resolved (a handle is a chat-edge file id, never a path). */
  async listFiles(conversationId: string): Promise<FileRowDto[] | null> {
    const r = await this.get<{ files?: FileRowDto[] }>(`/api/v1/conversations/${encodeURIComponent(conversationId)}/files`);
    return r && Array.isArray(r.files) ? r.files : null;
  }

  /** FIX27: the bytes of an uploaded image, for the vision block. Bounded
   * (25 MB, 60 s); the presigned-S3 307 is followed by fetch, which drops the
   * Authorization header on the cross-origin hop. */
  async downloadFileBytes(fileId: string): Promise<{ bytes: Buffer; mime: string } | null> {
    const token = this.accessToken();
    if (!token) return null;
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/files/${encodeURIComponent(fileId)}/download`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: AbortSignal.timeout(60_000),
      });
      if (!res.ok) {
        this.log.warn(`chat-edge file download ${fileId} → HTTP ${res.status}`);
        return null;
      }
      const len = Number(res.headers.get("content-length") ?? "0");
      if (len > 25 * 1024 * 1024) {
        this.log.warn(`chat-edge file download ${fileId}: ${len} bytes exceeds the 25 MB attachment bound`);
        return null;
      }
      const buf = Buffer.from(await res.arrayBuffer());
      if (buf.length > 25 * 1024 * 1024) return null;
      return { bytes: buf, mime: (res.headers.get("content-type") ?? "").split(";")[0]!.trim() };
    } catch (err) {
      this.log.warn(`chat-edge file download ${fileId} failed: ${(err as Error).message}`);
      return null;
    }
  }

  /** FIX3 §4: the extracted/OCR'd text of an uploaded file. */
  async getFileText(fileId: string): Promise<{ status: string; text: string; truncated: boolean; ocr: boolean } | null> {
    const r = await this.get<{ extraction_status: string; text: string; truncated: boolean; ocr: boolean }>(
      `/api/v1/files/${encodeURIComponent(fileId)}/text`,
    );
    return r ? { status: r.extraction_status, text: r.text ?? "", truncated: !!r.truncated, ocr: !!r.ocr } : null;
  }

  /** FIX2: create a conversation for a scheduled task's run (host-side firing). */
  async createConversation(modelId: string): Promise<string | null> {
    const res = await this.send("POST", `/api/v1/conversations`, { model_id: modelId });
    if (!res) return null;
    try {
      const j = (await res.json()) as { id?: string };
      return j.id ?? null;
    } catch {
      return null;
    }
  }

  /** FIX33 — the server's structured state for a conversation (web's summary). */
  async getConversationState(conversationId: string): Promise<Record<string, unknown> | null> {
    return this.get<Record<string, unknown>>(`/api/v1/conversations/${encodeURIComponent(conversationId)}/state`);
  }

  /** FIX33 A-W7 on the server: summarise now + set the boundary (web-side state). */
  async compactConversation(conversationId: string, instructions: string): Promise<boolean> {
    const res = await this.send("POST", `/api/v1/conversations/${encodeURIComponent(conversationId)}/compact`, { instructions });
    return res !== null;
  }

  private async get<T>(path: string, timeoutMs = this.timeoutMs): Promise<T | null> {
    const token = this.accessToken();
    if (!token) return null;
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!res.ok) {
        this.log.warn(`chat-edge GET ${path} → HTTP ${res.status}`);
        return null;
      }
      return (await res.json()) as T;
    } catch (err) {
      this.log.warn(`chat-edge GET ${path} failed: ${(err as Error).message}`);
      return null;
    }
  }

  private async send(method: "POST" | "PATCH", path: string, body: unknown): Promise<Response | null> {
    const token = this.accessToken();
    if (!token) return null;
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
      if (!res.ok) {
        let detail = "";
        try {
          detail = (await res.text()).slice(0, 200);
        } catch {
          /* ignore */
        }
        this.log.warn(`chat-edge ${method} ${path} → HTTP ${res.status} ${detail}`);
        return null;
      }
      return res;
    } catch (err) {
      this.log.warn(`chat-edge ${method} ${path} failed: ${(err as Error).message}`);
      return null;
    }
  }
}
