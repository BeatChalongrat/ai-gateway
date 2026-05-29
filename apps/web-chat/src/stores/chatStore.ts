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
  FileAttachment,
  Message,
  SSEAnalysisStatus,
  SSEContentDelta,
  SSEMessageComplete,
  SSEMessageStart,
  SSEReasoningDelta,
} from "@/lib/types";

// ─── Pending-file state ──────────────────────────────────────
// Tracked client-side while uploads are in flight. After the user
// presses send, ready file ids ride along on the message request
// and the pending list is cleared.
export interface PendingFile {
  /** Client-only id used to address the card before the server
   *  knows about it. */
  id: string;
  file: File;
  /** Set once the upload completes. */
  serverId?: string;
  status: "uploading" | "ready" | "error";
  progress: number; // 0..100
  error?: string;
}

interface ChatState {
  conversations: Conversation[];
  conversationsLoaded: boolean;
  /** True once the first /conversations page has been fetched. */
  conversationsCursor: string | null;
  hasMoreConversations: boolean;
  loadingMoreConversations: boolean;
  /** Server-side search results. Populated by searchConversations;
   *  empty array when no search is active. */
  conversationsSearchResults: Conversation[];
  conversationsSearchActive: boolean;
  conversationsSearchLoading: boolean;
  activeConversationId: string | null;
  /** message lists keyed by conversation id. */
  messages: Record<string, Message[]>;
  /** True once loadMessages has completed for a conversation —
   *  success or failure. Lets the page distinguish three states
   *  cleanly:
   *    - not in map           → fetch hasn't completed → skeleton
   *    - true + messages []   → loaded, conversation is empty → EmptyState
   *    - true + messages [..] → loaded with content → ChatThread
   *  Without this flag a load-failure would either spin the skeleton
   *  forever (if we left messages undefined) OR fall through to
   *  EmptyState (if we wrote ``[]`` on error). Neither is correct;
   *  this discriminator fixes both. */
  messagesLoaded: Record<string, boolean>;
  /** local conv→message-id pointer for whichever assistant message
   *  is currently being streamed in. Used by cancel + error paths. */
  streamingAssistantId: Record<string, string | undefined>;
  /** AbortController per active stream so cancel can interrupt. */
  abortControllers: Record<string, AbortController | undefined>;
  /** has_more=true for older-page-available conversations. */
  hasMoreMessages: Record<string, boolean>;
  /** True while a paginate-older request is in flight. */
  loadingOlder: Record<string, boolean>;
  /** Files staged for the next user message, keyed by conv id. */
  pendingFiles: Record<string, PendingFile[]>;
  errorBanner: string | null;
  setError: (msg: string | null) => void;

  // ── pending-file ops ─────────────────────────────────────
  addPendingFile: (convId: string, file: File) => string;
  removePendingFile: (convId: string, pendingId: string) => void;
  updateFileProgress: (convId: string, pendingId: string, progress: number) => void;
  markFileReady: (convId: string, pendingId: string, serverId: string) => void;
  markFileError: (convId: string, pendingId: string, error: string) => void;
  clearPendingFiles: (convId: string) => void;

  // ── conversation ops ─────────────────────────────────────
  loadConversations: () => Promise<void>;
  loadMoreConversations: () => Promise<void>;
  searchConversations: (query: string) => Promise<void>;
  clearConversationsSearch: () => void;
  setActive: (id: string | null) => void;
  createConversation: (
    modelId: string,
    opts?: { temporary?: boolean },
  ) => Promise<Conversation>;
  renameConversation: (id: string, title: string) => Promise<void>;
  setConversationModel: (id: string, modelId: string) => Promise<void>;
  setConversationKB: (id: string, kbId: string | null) => Promise<void>;
  /** S1 — flip the per-conversation memory layer. Optimistic
   *  update with rollback so the header chip stays responsive. */
  setConversationMemory: (id: string, enabled: boolean) => Promise<void>;
  /** T4 — patch Thinking Mode preferences (any subset). Optimistic
   *  with rollback per the memory pattern. */
  setConversationReasoning: (
    id: string,
    patch: {
      reasoning_enabled?: boolean;
      reasoning_mode?: string;
      show_reasoning_summary?: boolean;
    },
  ) => Promise<void>;
  /** Marks the NEXT lazily-created conversation as a temporary
   *  chat. Read + cleared by createConversation(). Used by the
   *  sidebar's "Temporary chat" launcher: navigates to /chat,
   *  sets this flag, and the first send creates the row with
   *  memory_enabled=false. */
  nextConversationTemporary: boolean;
  setNextConversationTemporary: (v: boolean) => void;
  deleteConversation: (id: string) => Promise<void>;

  // ── message ops ──────────────────────────────────────────
  loadMessages: (convId: string) => Promise<void>;
  loadOlderMessages: (convId: string) => Promise<void>;
  sendMessage: (convId: string, content: string, fileIds?: string[]) => Promise<void>;
  cancelGeneration: (convId: string) => Promise<void>;
  regenerate: (convId: string, messageId: string) => Promise<void>;
  editMessage: (convId: string, messageId: string, newContent: string) => Promise<void>;
  switchBranch: (convId: string, messageId: string, branchIndex: number) => Promise<void>;
  setFeedback: (
    convId: string,
    messageId: string,
    rating: "thumbs_up" | "thumbs_down",
  ) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  conversationsLoaded: false,
  conversationsCursor: null,
  hasMoreConversations: false,
  loadingMoreConversations: false,
  conversationsSearchResults: [],
  conversationsSearchActive: false,
  conversationsSearchLoading: false,
  activeConversationId: null,
  messages: {},
  messagesLoaded: {},
  streamingAssistantId: {},
  abortControllers: {},
  hasMoreMessages: {},
  loadingOlder: {},
  pendingFiles: {},
  errorBanner: null,
  nextConversationTemporary: false,
  setNextConversationTemporary(v) {
    set({ nextConversationTemporary: v });
  },

  // ────────────────────────────────────────────────────────
  // Pending-file actions. All purely client-side; the upload
  // network call lives in the components so it can pass an
  // AbortController + progress callback directly.
  // ────────────────────────────────────────────────────────
  addPendingFile(convId, file) {
    const id = `pend-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: [
          ...(s.pendingFiles[convId] ?? []),
          { id, file, status: "uploading", progress: 0 },
        ],
      },
    }));
    return id;
  },
  removePendingFile(convId, pendingId) {
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: (s.pendingFiles[convId] ?? []).filter(
          (p) => p.id !== pendingId,
        ),
      },
    }));
  },
  updateFileProgress(convId, pendingId, progress) {
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: (s.pendingFiles[convId] ?? []).map((p) =>
          p.id === pendingId ? { ...p, progress } : p,
        ),
      },
    }));
  },
  markFileReady(convId, pendingId, serverId) {
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: (s.pendingFiles[convId] ?? []).map((p) =>
          p.id === pendingId
            ? { ...p, status: "ready", progress: 100, serverId }
            : p,
        ),
      },
    }));
  },
  markFileError(convId, pendingId, error) {
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: (s.pendingFiles[convId] ?? []).map((p) =>
          p.id === pendingId ? { ...p, status: "error", error } : p,
        ),
      },
    }));
  },
  clearPendingFiles(convId) {
    set((s) => {
      const next = { ...s.pendingFiles };
      delete next[convId];
      return { pendingFiles: next };
    });
  },

  setError(msg) {
    set({ errorBanner: msg });
  },

  // ────────────────────────────────────────────────────────
  async loadConversations() {
    try {
      const res = await api.listConversationsPage({ limit: 50 });
      set({
        conversations: res.conversations,
        conversationsLoaded: true,
        conversationsCursor: res.next_cursor,
        hasMoreConversations: res.has_more,
      });
    } catch (err) {
      set({ conversationsLoaded: true });
      if (err instanceof ApiError && err.status !== 401) {
        set({ errorBanner: "Couldn't load conversations." });
      }
    }
  },

  async loadMoreConversations() {
    const state = get();
    if (state.loadingMoreConversations) return;
    if (!state.hasMoreConversations || !state.conversationsCursor) return;
    set({ loadingMoreConversations: true });
    try {
      const res = await api.listConversationsPage({
        limit: 50,
        cursor: state.conversationsCursor,
      });
      set((s) => {
        // Dedup by id — defensive against double-clicks fetching
        // the same page twice.
        const have = new Set(s.conversations.map((c) => c.id));
        const next = res.conversations.filter((c) => !have.has(c.id));
        return {
          conversations: [...s.conversations, ...next],
          conversationsCursor: res.next_cursor,
          hasMoreConversations: res.has_more,
          loadingMoreConversations: false,
        };
      });
    } catch {
      set({ loadingMoreConversations: false });
    }
  },

  async searchConversations(query) {
    const q = query.trim();
    if (!q) {
      set({
        conversationsSearchActive: false,
        conversationsSearchResults: [],
        conversationsSearchLoading: false,
      });
      return;
    }
    set({ conversationsSearchActive: true, conversationsSearchLoading: true });
    try {
      const res = await api.listConversationsSearch(q, 50);
      set({
        conversationsSearchResults: res.conversations,
        conversationsSearchLoading: false,
      });
    } catch {
      set({ conversationsSearchLoading: false });
    }
  },

  clearConversationsSearch() {
    set({
      conversationsSearchActive: false,
      conversationsSearchResults: [],
      conversationsSearchLoading: false,
    });
  },

  setActive(id) {
    set({ activeConversationId: id });
  },

  async createConversation(modelId, opts) {
    let c = await api.createConversation(modelId);
    // S1 — temporary chat. Two trigger paths land here: the explicit
    // opts.temporary flag (called from /chat landing when the user
    // clicked the temp-chat launcher) or the persisted
    // nextConversationTemporary store flag (set by the sidebar
    // before navigation, consumed on the next create). Either way
    // we PATCH memory_enabled=false on the fresh row so the very
    // first send already skips memory + tools.
    const wantTemp = opts?.temporary === true || get().nextConversationTemporary;
    if (wantTemp) {
      try {
        c = await api.setConversationMemory(c.id, false);
      } catch {
        // Non-fatal — the user can still toggle from the header.
      }
      set({ nextConversationTemporary: false });
    }
    // Brand-new conversation is, by definition, fully loaded with
    // zero messages — flip the discriminator so the page skips the
    // skeleton and renders EmptyState immediately.
    set((s) => ({
      conversations: [c, ...s.conversations],
      messages: { ...s.messages, [c.id]: [] },
      messagesLoaded: { ...s.messagesLoaded, [c.id]: true },
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
    //
    // `existed` covers the "user landed on /chat/[id] directly and
    // the conversation list hasn't fetched yet" case: there's no
    // entry to flip optimistically, but the PATCH still goes
    // through and the success branch upserts the row using the
    // server response. Without this the model pill would stay
    // stale until the next sidebar refresh.
    const existing = get().conversations.find((c) => c.id === id);
    const prev = existing?.model_id;
    if (existing) {
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, model_id: modelId } : c,
        ),
      }));
    }
    try {
      const updated = await api.setConversationModel(id, modelId);
      set((s) => {
        const found = s.conversations.some((c) => c.id === id);
        return {
          conversations: found
            ? s.conversations.map((c) => (c.id === id ? { ...c, ...updated } : c))
            : [updated, ...s.conversations],
        };
      });
    } catch (err) {
      if (existing && prev !== undefined) {
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === id ? { ...c, model_id: prev } : c,
          ),
        }));
      }
      throw err;
    }
  },

  async setConversationMemory(id, enabled) {
    // Optimistic toggle so the header chip + sidebar BrainOff icon
    // flip the instant the user clicks. Rollback on failure.
    const prev = get().conversations.find((c) => c.id === id)?.memory_enabled;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, memory_enabled: enabled } : c,
      ),
    }));
    try {
      const updated = await api.setConversationMemory(id, enabled);
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, ...updated } : c,
        ),
      }));
    } catch (err) {
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, memory_enabled: prev ?? true } : c,
        ),
      }));
      throw err;
    }
  },

  async setConversationReasoning(id, patch) {
    // Optimistic — snapshot all three fields so a rollback fully
    // restores the prior state even when the user toggled
    // multiple fields in quick succession.
    const prev = get().conversations.find((c) => c.id === id);
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, ...patch } : c,
      ),
    }));
    try {
      const updated = await api.setConversationReasoning(id, patch);
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, ...updated } : c,
        ),
      }));
    } catch (err) {
      if (prev) {
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === id
              ? {
                  ...c,
                  reasoning_enabled: prev.reasoning_enabled,
                  reasoning_mode: prev.reasoning_mode,
                  show_reasoning_summary: prev.show_reasoning_summary,
                }
              : c,
          ),
        }));
      }
      throw err;
    }
  },

  async setConversationKB(id, kbId) {
    // Optimistic update: flip the binding immediately so the KB chip
    // by the input updates without a network round-trip; rollback
    // on failure.
    const prev = get().conversations.find((c) => c.id === id)?.knowledge_base_id;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, knowledge_base_id: kbId } : c,
      ),
    }));
    try {
      const updated = await api.setConversationKB(id, kbId);
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, ...updated } : c,
        ),
      }));
    } catch (err) {
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, knowledge_base_id: prev ?? null } : c,
        ),
      }));
      throw err;
    }
  },

  async deleteConversation(id) {
    await api.deleteConversation(id);
    set((s) => {
      const next = { ...s.messages };
      delete next[id];
      const nextLoaded = { ...s.messagesLoaded };
      delete nextLoaded[id];
      return {
        conversations: s.conversations.filter((c) => c.id !== id),
        messages: next,
        messagesLoaded: nextLoaded,
        activeConversationId:
          s.activeConversationId === id ? null : s.activeConversationId,
      };
    });
  },

  // ────────────────────────────────────────────────────────
  async loadMessages(convId) {
    try {
      const res = await api.listMessages(convId, { limit: 50 });
      set((s) => ({
        messages: { ...s.messages, [convId]: res?.messages ?? [] },
        messagesLoaded: { ...s.messagesLoaded, [convId]: true },
        hasMoreMessages: {
          ...s.hasMoreMessages,
          [convId]: res?.has_more ?? false,
        },
      }));
    } catch (err) {
      // Mark the slot loaded so the skeleton clears, but populate
      // the array based on the failure shape so the page UI
      // distinguishes the three states cleanly:
      //   - 404 → conversation truly gone, show empty + banner
      //   - other error → leave any previously-loaded array intact,
      //                   surface a retry-able banner
      // 401s never reach this catch — ApiClient bounces to portal-
      // login first.
      set((s) => ({
        messages: { ...s.messages, [convId]: s.messages[convId] ?? [] },
        messagesLoaded: { ...s.messagesLoaded, [convId]: true },
        hasMoreMessages: {
          ...s.hasMoreMessages,
          [convId]: s.hasMoreMessages[convId] ?? false,
        },
        errorBanner:
          err instanceof ApiError && err.status === 404
            ? "Conversation not found."
            : err instanceof ApiError
              ? `Couldn't load messages (${err.code}).`
              : "Couldn't load messages.",
      }));
    }
  },

  async loadOlderMessages(convId) {
    const current = get().messages[convId] ?? [];
    if (current.length === 0) return;
    if (get().loadingOlder[convId]) return;
    if (get().hasMoreMessages[convId] === false) return;
    const cursor = current[0].id;
    set((s) => ({
      loadingOlder: { ...s.loadingOlder, [convId]: true },
    }));
    try {
      const res = await api.listMessages(convId, { limit: 50, before: cursor });
      set((s) => {
        // Skip ids already present (defensive — pagination can
        // return the cursor row itself depending on the server's
        // strict-vs-loose < semantics).
        const have = new Set((s.messages[convId] ?? []).map((m) => m.id));
        const prepend = res.messages.filter((m) => !have.has(m.id));
        return {
          messages: {
            ...s.messages,
            [convId]: [...prepend, ...(s.messages[convId] ?? [])],
          },
          hasMoreMessages: {
            ...s.hasMoreMessages,
            [convId]: res.has_more,
          },
          loadingOlder: { ...s.loadingOlder, [convId]: false },
        };
      });
    } catch {
      set((s) => ({
        loadingOlder: { ...s.loadingOlder, [convId]: false },
      }));
    }
  },

  // ────────────────────────────────────────────────────────
  async sendMessage(convId, content, fileIds) {
    const trimmed = content.trim();
    if (!trimmed && (!fileIds || fileIds.length === 0)) return;
    // Offline guard. SSE streams need a live socket — bail early
    // with a user-visible toast rather than letting fetch hang.
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      get().setError(
        "You're offline. Messages will send when you reconnect.",
      );
      return;
    }
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

    // Build optimistic file shells from the current pendingFiles
    // list so the user immediately sees badges/thumbnails under
    // their message even before the server reloads message history.
    //
    // We search EVERY bucket and match by serverId because chips
    // can be staged under two different keys depending on the entry
    // point:
    //   - Conv page: ChatInput stages into pendingFiles[convId]
    //     (its render bucket equals the URL conv id).
    //   - Landing page: ChatInput stages into pendingFiles[DRAFT]
    //     because its conversationId prop stays null for the
    //     instance's whole lifetime — only ensureConv knows the
    //     real id, and it materialises here, not in the staging
    //     bucket. A naive pendingFiles[convId] lookup misses every
    //     landing send and the user bubble renders an empty pill
    //     with no files visible.
    // Filtering by fileIds keeps unrelated still-uploading rows
    // (from a previous send that the user abandoned) off this
    // message.
    const fileIdSet = new Set(fileIds ?? []);
    const optimisticFiles: FileAttachment[] =
      fileIdSet.size === 0
        ? []
        : Object.values(get().pendingFiles)
            .flat()
            .filter((p) => p.serverId && fileIdSet.has(p.serverId))
            .map((p) => ({
              id: p.serverId!,
              filename: p.file.name,
              content_type: p.file.type || "application/octet-stream",
              file_size_bytes: p.file.size,
              extraction_status: "completed" as const,
              created_at: new Date().toISOString(),
            }));

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
            files: optimisticFiles.length > 0 ? optimisticFiles : undefined,
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
      const res = await api.openMessageStream(
        convId,
        trimmed,
        ctrl.signal,
        fileIds,
      );
      if (!res.ok) {
        await handleStreamPreError(res, set, get, convId, tempAsstId);
        return;
      }
      await consumeStream(res, ctrl.signal, set, get, convId, tempAsstId);
      // Stream completed cleanly. If this was the conversation's
      // first turn, ask the backend to AI-generate a title from the
      // user/assistant pair. Fire-and-forget — failure here falls
      // back to the local truncation heuristic so the sidebar
      // doesn't stay "New conversation" if the gateway round-trip
      // stutters (offline, 5xx, etc.).
      if (isFirstMessage) {
        void api
          .generateTitle(convId)
          .then((res) => {
            if (!res?.title) return;
            set((s) => ({
              conversations: s.conversations.map((c) =>
                c.id === convId ? { ...c, title: res.title } : c,
              ),
            }));
          })
          .catch(() => {
            const fallback = makeAutoTitle(trimmed);
            if (!fallback) return;
            void get()
              .renameConversation(convId, fallback)
              .catch(() => {
                /* non-fatal */
              });
          });
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

  async editMessage(convId, messageId, newContent) {
    // Edit is a fork-and-stream operation. The server creates the
    // new branch's user + assistant rows; we mirror the streaming
    // optimistic-UI logic from sendMessage so the user sees their
    // edit + the AI response immediately.
    const trimmed = newContent.trim();
    if (!trimmed) return;
    const existing = get().messages[convId] ?? [];
    const idx = existing.findIndex((m) => m.id === messageId);
    if (idx === -1) return;

    const convModelId =
      get().conversations.find((c) => c.id === convId)?.model_id ?? null;
    const tempAsstId = `tmp-edit-asst-${Date.now()}`;

    // Optimistic: replace the edited user message's content + clear
    // everything after it. The server-side fork sends back the same
    // shape via the SSE stream.
    set((s) => {
      const list = s.messages[convId] ?? [];
      const truncated = list.slice(0, idx);
      truncated.push({
        ...list[idx],
        content: trimmed,
      });
      truncated.push({
        id: tempAsstId,
        role: "assistant",
        content: "",
        status: "streaming",
        model_id: convModelId,
        created_at: new Date().toISOString(),
        isStreaming: true,
      });
      return {
        messages: { ...s.messages, [convId]: truncated },
        streamingAssistantId: { ...s.streamingAssistantId, [convId]: tempAsstId },
      };
    });

    const ctrl = new AbortController();
    set((s) => ({
      abortControllers: { ...s.abortControllers, [convId]: ctrl },
    }));

    try {
      const res = await api.openEditStream(convId, messageId, trimmed, ctrl.signal);
      if (!res.ok) {
        await handleStreamPreError(res, set, get, convId, tempAsstId);
        return;
      }
      await consumeStream(res, ctrl.signal, set, get, convId, tempAsstId);
      // Re-fetch messages so the new branch_index / branch_count
      // info from the server replaces the optimistic shape.
      try {
        await get().loadMessages(convId);
      } catch {
        /* non-fatal */
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finalizeMessage(set, get, convId, tempAsstId, "cancelled");
        return;
      }
      get().setError("Network error during edit.");
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

  async switchBranch(convId, messageId, branchIndex) {
    try {
      await api.switchBranch(convId, messageId, branchIndex);
      // Toggling is_active server-side rewrote which messages are
      // current — re-fetch to pick up the new chain.
      await get().loadMessages(convId);
    } catch {
      get().setError("Couldn't switch branch.");
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
  // RAF-batched delta buffer. content_delta events from fast models
  // can fire 50+ times/sec; flushing every token to React state
  // collapses the main thread. We buffer deltas in a plain string
  // and commit once per animation frame (~60Hz) so React renders
  // the streaming message at most once per frame.
  let pendingDelta = "";
  let rafHandle: number | null = null;
  // T4 — Thinking Mode. Mirror the content RAF batching pattern
  // for reasoning fragments so a fast reasoning stream doesn't
  // monopolise the React tree either. ``thinkingStartedAt`` is
  // captured on ``analysis_status: started`` so the duration can
  // be reported on ``completed``.
  let pendingReasoning = "";
  let thinkingStartedAt: number | null = null;
  // ``runRaf`` lets tests / non-browser environments fall back to
  // setTimeout when requestAnimationFrame isn't available.
  const runRaf =
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame
      : (cb: () => void) => window.setTimeout(cb, 16) as unknown as number;
  const cancelRaf =
    typeof cancelAnimationFrame === "function"
      ? cancelAnimationFrame
      : (h: number) => window.clearTimeout(h);

  const flush = () => {
    if (!pendingDelta && !pendingReasoning) return;
    const contentChunk = pendingDelta;
    const reasoningChunk = pendingReasoning;
    pendingDelta = "";
    pendingReasoning = "";
    set((s) => {
      const list = s.messages[convId] ?? [];
      const id = realAssistantId ?? tempAsstId;
      return {
        messages: {
          ...s.messages,
          [convId]: list.map((m) => {
            if (m.id !== id) return m;
            const next = { ...m };
            if (contentChunk) {
              next.content = m.content + contentChunk;
            }
            if (reasoningChunk) {
              next.reasoning_content =
                (m.reasoning_content ?? "") + reasoningChunk;
            }
            return next;
          }),
        },
      };
    });
  };

  const scheduleFlush = () => {
    if (rafHandle != null) return;
    rafHandle = runRaf(() => {
      rafHandle = null;
      flush();
    });
  };

  try {
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
        pendingDelta += data.delta;
        scheduleFlush();
      } else if (evt.event === "reasoning_delta") {
        // T4 — thinking fragment. Same RAF batching as content
        // deltas (reasoning models like o3 can emit hundreds of
        // small fragments per turn).
        const data = evt.data as SSEReasoningDelta;
        pendingReasoning += data.delta;
        scheduleFlush();
      } else if (evt.event === "analysis_status") {
        // T4 — phase marker. ``started`` flips the per-message
        // is_thinking flag so the ThinkingBlock renders with
        // bouncing dots; ``completed`` flips it off + records the
        // total analysis duration for the "Analyzed Xs" header.
        const data = evt.data as SSEAnalysisStatus;
        if (data.status === "started") {
          thinkingStartedAt = Date.now();
          set((s) => {
            const list = s.messages[convId] ?? [];
            const id = realAssistantId ?? tempAsstId;
            return {
              messages: {
                ...s.messages,
                [convId]: list.map((m) =>
                  m.id === id ? { ...m, is_thinking: true } : m,
                ),
              },
            };
          });
        } else if (data.status === "completed") {
          const duration =
            thinkingStartedAt !== null
              ? Date.now() - thinkingStartedAt
              : undefined;
          // Flush any pending reasoning fragment BEFORE flipping the
          // flag so the final paint includes every fragment under
          // the "Analyzed" header rather than orphaning the tail.
          if (rafHandle != null) {
            cancelRaf(rafHandle);
            rafHandle = null;
          }
          flush();
          set((s) => {
            const list = s.messages[convId] ?? [];
            const id = realAssistantId ?? tempAsstId;
            return {
              messages: {
                ...s.messages,
                [convId]: list.map((m) =>
                  m.id === id
                    ? {
                        ...m,
                        is_thinking: false,
                        thinking_duration_ms: duration,
                      }
                    : m,
                ),
              },
            };
          });
        }
      } else if (evt.event === "message_complete") {
        // Flush any buffered tail BEFORE writing the terminal state
        // so the final content paint includes every token.
        if (rafHandle != null) {
          cancelRaf(rafHandle);
          rafHandle = null;
        }
        flush();
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
        if (rafHandle != null) {
          cancelRaf(rafHandle);
          rafHandle = null;
        }
        flush();
        const id = realAssistantId ?? tempAsstId;
        finalizeMessage(set, get, convId, id, "error");
        get().setError("AI service error during streaming.");
      }
    }
  } finally {
    // Any deltas left in the buffer at this point belong to an
    // abort / error path. Flushing them here keeps the persisted UI
    // consistent with what the server already wrote to the DB.
    if (rafHandle != null) {
      cancelRaf(rafHandle);
      rafHandle = null;
    }
    flush();
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
