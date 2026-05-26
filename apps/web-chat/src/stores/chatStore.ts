// Conversation + message state. Streaming bookkeeping lives here
// too: when the user sends a message we:
//
//   1. Append the user message optimistically.
//   2. Insert a placeholder assistant message in status="streaming".
//   3. Open the SSE stream; each ``content_delta`` appends to the
//      placeholder's content (which is what the UI watches).
//   4. On ``message_complete`` set status="completed" + persist
//      token_usage.
//   5. On cancel (user) flip status to "cancelled".
//   6. On error flip status to "error" and bubble a toast.

"use client";

import { create } from "zustand";

import { api, ApiError } from "@/lib/api";
import { parseSSE } from "@/lib/stream";
import type {
  Conversation,
  Message,
  SSEContentDelta,
  SSEMessageComplete,
  SSEMessageStart,
} from "@/lib/types";

interface ChatState {
  conversations: Conversation[];
  conversationsLoaded: boolean;
  activeConversationId: string | null;
  /** message lists keyed by conversation id. */
  messages: Record<string, Message[]>;
  /** local conv→message-id pointer for whichever assistant message
   *  is currently being streamed in. Used by cancel + error paths. */
  streamingAssistantId: Record<string, string | undefined>;
  /** AbortController per active stream so cancel can interrupt. */
  abortControllers: Record<string, AbortController | undefined>;
  errorBanner: string | null;
  setError: (msg: string | null) => void;

  // ── conversation ops ─────────────────────────────────────
  loadConversations: () => Promise<void>;
  setActive: (id: string | null) => void;
  createConversation: (modelId: string) => Promise<Conversation>;
  renameConversation: (id: string, title: string) => Promise<void>;
  setConversationModel: (id: string, modelId: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;

  // ── message ops ──────────────────────────────────────────
  loadMessages: (convId: string) => Promise<void>;
  sendMessage: (convId: string, content: string) => Promise<void>;
  cancelGeneration: (convId: string) => Promise<void>;
  regenerate: (convId: string, messageId: string) => Promise<void>;
  setFeedback: (
    convId: string,
    messageId: string,
    rating: "thumbs_up" | "thumbs_down",
  ) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  conversationsLoaded: false,
  activeConversationId: null,
  messages: {},
  streamingAssistantId: {},
  abortControllers: {},
  errorBanner: null,

  setError(msg) {
    set({ errorBanner: msg });
  },

  // ────────────────────────────────────────────────────────
  async loadConversations() {
    try {
      const res = await api.listConversations();
      set({ conversations: res.conversations, conversationsLoaded: true });
    } catch (err) {
      set({ conversationsLoaded: true });
      if (err instanceof ApiError && err.status !== 401) {
        set({ errorBanner: "Couldn't load conversations." });
      }
    }
  },

  setActive(id) {
    set({ activeConversationId: id });
  },

  async createConversation(modelId) {
    const c = await api.createConversation(modelId);
    set((s) => ({
      conversations: [c, ...s.conversations],
      messages: { ...s.messages, [c.id]: [] },
      activeConversationId: c.id,
    }));
    return c;
  },

  async renameConversation(id, title) {
    const updated = await api.renameConversation(id, title);
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, ...updated } : c,
      ),
    }));
  },

  async setConversationModel(id, modelId) {
    // Optimistic: flip the sidebar entry immediately so the model
    // pill in the chat header / input updates without waiting on
    // the network. On failure the catch path rolls back.
    const prev = get().conversations.find((c) => c.id === id)?.model_id;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, model_id: modelId } : c,
      ),
    }));
    try {
      const updated = await api.setConversationModel(id, modelId);
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, ...updated } : c,
        ),
      }));
    } catch (err) {
      if (prev !== undefined) {
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === id ? { ...c, model_id: prev } : c,
          ),
        }));
      }
      throw err;
    }
  },

  async deleteConversation(id) {
    await api.deleteConversation(id);
    set((s) => {
      const next = { ...s.messages };
      delete next[id];
      return {
        conversations: s.conversations.filter((c) => c.id !== id),
        messages: next,
        activeConversationId:
          s.activeConversationId === id ? null : s.activeConversationId,
      };
    });
  },

  // ────────────────────────────────────────────────────────
  async loadMessages(convId) {
    try {
      const res = await api.listMessages(convId);
      // Server sometimes carries content as content; map to local
      // Message shape (already matches because handler renames the
      // column to ``content`` on the wire).
      set((s) => ({
        messages: { ...s.messages, [convId]: res.messages },
      }));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        set({ errorBanner: "Conversation not found." });
      }
    }
  },

  // ────────────────────────────────────────────────────────
  async sendMessage(convId, content) {
    const trimmed = content.trim();
    if (!trimmed) return;
    const tempUserId = `tmp-user-${Date.now()}`;
    const tempAsstId = `tmp-asst-${Date.now()}`;

    // Auto-title heuristic: if this is the first message in the
    // conversation we'll PATCH a generated title once the stream
    // completes successfully (handled below in the try block).
    const isFirstMessage = (get().messages[convId] ?? []).length === 0;

    // Capture the conversation's currently bound model at send time
    // and stamp it on the assistant placeholder. The server-side
    // message_id arrives via message_start, but its model_id only
    // surfaces on the next /messages reload — stamping locally keeps
    // the per-message model badge + switch divider consistent during
    // streaming.
    const convModelId =
      get().conversations.find((c) => c.id === convId)?.model_id ?? null;

    set((s) => ({
      messages: {
        ...s.messages,
        [convId]: [
          ...(s.messages[convId] ?? []),
          {
            id: tempUserId,
            role: "user",
            content: trimmed,
            status: "completed",
            created_at: new Date().toISOString(),
          },
          {
            id: tempAsstId,
            role: "assistant",
            content: "",
            status: "streaming",
            model_id: convModelId,
            created_at: new Date().toISOString(),
            isStreaming: true,
          },
        ],
      },
      streamingAssistantId: { ...s.streamingAssistantId, [convId]: tempAsstId },
    }));

    const ctrl = new AbortController();
    set((s) => ({
      abortControllers: { ...s.abortControllers, [convId]: ctrl },
    }));

    try {
      const res = await api.openMessageStream(convId, trimmed, ctrl.signal);
      if (!res.ok) {
        await handleStreamPreError(res, set, get, convId, tempAsstId);
        return;
      }
      await consumeStream(res, ctrl.signal, set, get, convId, tempAsstId);
      // Stream completed cleanly. If this was the conversation's
      // first turn, generate a title from the user's prompt and
      // persist it. Fire-and-forget — failure here is non-fatal.
      if (isFirstMessage) {
        const title = makeAutoTitle(trimmed);
        if (title) {
          void get()
            .renameConversation(convId, title)
            .catch(() => {
              /* non-fatal */
            });
        }
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finalizeMessage(set, get, convId, tempAsstId, "cancelled");
        return;
      }
      get().setError("Network error during streaming.");
      finalizeMessage(set, get, convId, tempAsstId, "error");
    } finally {
      set((s) => {
        const aborts = { ...s.abortControllers };
        delete aborts[convId];
        const sids = { ...s.streamingAssistantId };
        delete sids[convId];
        return { abortControllers: aborts, streamingAssistantId: sids };
      });
      // Refresh sidebar so updated_at + auto-title flow through.
      try {
        const c = await api.getConversation(convId);
        set((s) => ({
          conversations: s.conversations.map((x) =>
            x.id === convId ? { ...x, ...c } : x,
          ),
        }));
      } catch {
        /* non-fatal */
      }
    }
  },

  async cancelGeneration(convId) {
    const ctrl = get().abortControllers[convId];
    try {
      await api.cancelGeneration(convId);
    } catch {
      /* idempotent; ignore */
    }
    if (ctrl) ctrl.abort();
  },

  async regenerate(convId, messageId) {
    const existing = get().messages[convId] ?? [];
    const idx = existing.findIndex((m) => m.id === messageId);
    if (idx === -1) return;

    const convModelId =
      get().conversations.find((c) => c.id === convId)?.model_id ?? null;

    // Mark the old one regenerated, insert a fresh placeholder.
    const tempAsstId = `tmp-regen-${Date.now()}`;
    set((s) => {
      const list = s.messages[convId] ?? [];
      const updated = list.map((m, i) =>
        i === idx ? { ...m, status: "regenerated" as const } : m,
      );
      updated.push({
        id: tempAsstId,
        role: "assistant",
        content: "",
        status: "streaming",
        model_id: convModelId,
        created_at: new Date().toISOString(),
        isStreaming: true,
      });
      return {
        messages: { ...s.messages, [convId]: updated },
        streamingAssistantId: { ...s.streamingAssistantId, [convId]: tempAsstId },
      };
    });

    const ctrl = new AbortController();
    set((s) => ({
      abortControllers: { ...s.abortControllers, [convId]: ctrl },
    }));

    try {
      const res = await api.openRegenerateStream(convId, messageId, ctrl.signal);
      if (!res.ok) {
        await handleStreamPreError(res, set, get, convId, tempAsstId);
        return;
      }
      await consumeStream(res, ctrl.signal, set, get, convId, tempAsstId);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finalizeMessage(set, get, convId, tempAsstId, "cancelled");
        return;
      }
      get().setError("Network error during regenerate.");
      finalizeMessage(set, get, convId, tempAsstId, "error");
    } finally {
      set((s) => {
        const aborts = { ...s.abortControllers };
        delete aborts[convId];
        const sids = { ...s.streamingAssistantId };
        delete sids[convId];
        return { abortControllers: aborts, streamingAssistantId: sids };
      });
    }
  },

  async setFeedback(convId, messageId, rating) {
    set((s) => {
      const list = s.messages[convId] ?? [];
      return {
        messages: {
          ...s.messages,
          [convId]: list.map((m) =>
            m.id === messageId ? { ...m, feedback: rating } : m,
          ),
        },
      };
    });
    try {
      await api.setFeedback(messageId, rating);
    } catch {
      get().setError("Couldn't save feedback. Try again.");
    }
  },
}));

// ─── Helpers ──────────────────────────────────────────────────

type Set = (
  partial:
    | ChatState
    | Partial<ChatState>
    | ((state: ChatState) => ChatState | Partial<ChatState>),
) => void;
type Get = () => ChatState;

async function consumeStream(
  res: Response,
  signal: AbortSignal,
  set: Set,
  get: Get,
  convId: string,
  tempAsstId: string,
) {
  let realAssistantId: string | null = null;
  for await (const evt of parseSSE(res, signal)) {
    if (evt.event === "message_start") {
      const data = evt.data as SSEMessageStart;
      realAssistantId = data.message_id;
      // Swap the temp id with the real one so feedback / regenerate
      // can act on the persisted row.
      set((s) => {
        const list = s.messages[convId] ?? [];
        return {
          messages: {
            ...s.messages,
            [convId]: list.map((m) =>
              m.id === tempAsstId ? { ...m, id: data.message_id } : m,
            ),
          },
          streamingAssistantId: {
            ...s.streamingAssistantId,
            [convId]: data.message_id,
          },
        };
      });
    } else if (evt.event === "content_delta") {
      const data = evt.data as SSEContentDelta;
      set((s) => {
        const list = s.messages[convId] ?? [];
        const id = realAssistantId ?? tempAsstId;
        return {
          messages: {
            ...s.messages,
            [convId]: list.map((m) =>
              m.id === id ? { ...m, content: m.content + data.delta } : m,
            ),
          },
        };
      });
    } else if (evt.event === "message_complete") {
      const data = evt.data as SSEMessageComplete;
      const id = realAssistantId ?? tempAsstId;
      set((s) => {
        const list = s.messages[convId] ?? [];
        return {
          messages: {
            ...s.messages,
            [convId]: list.map((m) =>
              m.id === id
                ? {
                    ...m,
                    status: m.status === "cancelled" ? "cancelled" : "completed",
                    isStreaming: false,
                    token_usage: data.token_usage ?? null,
                  }
                : m,
            ),
          },
        };
      });
    } else if (evt.event === "error") {
      const id = realAssistantId ?? tempAsstId;
      finalizeMessage(set, get, convId, id, "error");
      get().setError("AI service error during streaming.");
    }
  }
}

async function handleStreamPreError(
  res: Response,
  set: Set,
  get: Get,
  convId: string,
  tempAsstId: string,
) {
  const text = await res.text();
  let msg = res.statusText;
  try {
    const env = JSON.parse(text) as { error?: { message?: string } };
    msg = env.error?.message ?? msg;
  } catch {
    /* ignore */
  }
  if (res.status === 429) {
    get().setError("Too many messages — slow down for a minute.");
  } else if (res.status === 502) {
    get().setError("AI service is temporarily unavailable.");
  } else if (res.status !== 401) {
    get().setError(msg);
  }
  finalizeMessage(set, get, convId, tempAsstId, "error");
}

// ─── Pure helpers used by selectors ───────────────────────────

// hasMultipleModels reports whether the assistant messages in a
// conversation use more than one distinct model_id. Used by the
// MessageBubble to decide whether to clutter the timeline with
// per-message model badges (skipped when every reply is the same
// model).
export function hasMultipleModels(messages: Message[]): boolean {
  const seen = new Set<string>();
  for (const m of messages) {
    if (m.role !== "assistant") continue;
    if (!m.model_id) continue;
    seen.add(m.model_id);
    if (seen.size > 1) return true;
  }
  return false;
}

// getModelSwitchPoints returns the indices of messages where the
// assistant's model_id flips relative to the previous assistant
// message. Used by ChatThread to inject the model-switch divider
// just BEFORE those indices.
export function getModelSwitchPoints(messages: Message[]): number[] {
  const out: number[] = [];
  let prevModel: string | undefined;
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (m.role !== "assistant" || !m.model_id) continue;
    if (prevModel !== undefined && m.model_id !== prevModel) {
      out.push(i);
    }
    prevModel = m.model_id;
  }
  return out;
}

// Derive a sidebar-friendly title from the user's first prompt:
// strip markdown formatting, collapse whitespace, truncate to 50
// chars at the last word boundary, append an ellipsis when cut.
function makeAutoTitle(raw: string): string {
  let s = raw
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/~~(.*?)~~/g, "$1")
    .replace(/^>\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!s) return "";
  if (s.length <= 50) return s;
  const truncated = s.slice(0, 50);
  const lastSpace = truncated.lastIndexOf(" ");
  const cut = lastSpace > 20 ? lastSpace : 50;
  return `${s.slice(0, cut).trim()}…`;
}

function finalizeMessage(
  set: Set,
  _get: Get,
  convId: string,
  messageId: string,
  status: "completed" | "cancelled" | "error",
) {
  set((s) => {
    const list = s.messages[convId] ?? [];
    return {
      messages: {
        ...s.messages,
        [convId]: list.map((m) =>
          m.id === messageId ? { ...m, status, isStreaming: false } : m,
        ),
      },
    };
  });
}
