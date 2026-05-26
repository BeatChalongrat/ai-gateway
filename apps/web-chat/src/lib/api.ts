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
  setConversationModel(id: string, model_id: string) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ model_id }),
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
