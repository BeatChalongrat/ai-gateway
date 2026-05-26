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
  Message,
  MessageListResponse,
  ModelListResponse,
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
  listMessages(convId: string) {
    return this.json<MessageListResponse>(
      `/conversations/${convId}/messages?limit=100`,
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
  ): Promise<Response> {
    return this.raw(`/conversations/${convId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, stream: true }),
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
