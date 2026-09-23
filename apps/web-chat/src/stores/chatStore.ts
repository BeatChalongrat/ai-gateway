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

import { bumpUsageVersion } from "@/hooks/useUsage";
import { useMemoryPendingStore } from "@/hooks/useMemoryPendingCount";
import { api, ApiError } from "@/lib/api";
import { HOSTED_MODE } from "@/lib/auth";
import { bridge, usingBridgeTransport } from "@/lib/bridge";
import { clearDraft, loadDraft, migrateDraft, NEW_CHAT_DRAFT_KEY, saveDraft } from "@/lib/draftStore";
import { discardUpload, forgetUpload } from "@/lib/uploadRegistry";
import { outboxAccepted, outboxGiveBack, outboxIsUndelivered, outboxPeek, outboxPut } from "@/lib/sendOutbox";
import { tOutside } from "@/lib/i18n";
import { toast } from "@/components/ui/Toast";
import { localizeErrorCode } from "@/lib/errorMessages";
import { mergeMemorySaved, parseMemoryUsed } from "@/lib/memoryMarkers";
import { EOF_WITHOUT_TERMINAL_CAUSE, EOF_WITHOUT_TERMINAL_CODE, isStopReason } from "@/lib/terminalCause";
import { parseSSE } from "@/lib/stream";
import type { ContextInfo } from "@/lib/types";
import type { TurnScrollRequest } from "@/lib/turnAnchor";
// One-way dependency: the chat store nudges the canvas store after a
// turn (Phase 8). canvasStore must never import chatStore back.
import { useCanvasStore } from "@/stores/canvasStore";
import type {
  Conversation,
  RoutingTier,
  ConversationSearchHit,
  FileAttachment,
  Message,
  PendingConsent,
  PendingQuestion,
  PendingQuestionItem,
  QuestionAnswerPayload,
  PlanStep,
  Project,
  SSEAnalysisStatus,
  SSECodeExecution,
  SSEToolActivity,
  SSEContentDelta,
  SSEError,
  SSEFallbackInfo,
  SSEProviderStall,
  SSEImageNotice,
  SSEReasoningNotice,
  SSEMessageComplete,
  SSEMessageFiles,
  SSEMessageSources,
  SSEMessageStart,
  SSEReasoningDelta,
} from "@/lib/types";

// ─── Pending-file state ──────────────────────────────────────
// Tracked client-side while uploads are in flight. After the user
// presses send, ready file ids ride along on the message request
// and the pending list is cleared.
const IMAGE_GENERATION_TIMEOUT_MS = 180_000;
// Hosted-mode reload recovery: how long to wait after posting the resume
// ui.ready for the host to replay. The host replays its whole buffer
// synchronously on receipt, so this only needs to cover the postMessage round
// trip; kept generous for a busy host.
const HOSTED_RESUME_PROBE_MS = 700;

// Collision-proof optimistic ids. `Date.now()` collided when two sends
// landed in the same millisecond (rapid double-send / same-tick calls),
// which made every later map-by-id update touch BOTH placeholder rows.
// crypto.randomUUID() is available in all target browsers and jsdom.
const tmpId = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;

/** FIX127 D1 — one message waiting for the current answer to finish. */
export interface QueuedMessage {
  id: string;
  text: string;
  fileIds: string[];
}

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
  /** Server-side text extraction state, tracked separately from
   *  `status` because the two are now independent: the upload
   *  resolves as soon as the bytes land, while extraction (OCR on a
   *  scanned PDF can take minutes) continues in the background. A
   *  chip can therefore be "ready" AND still extracting — which is
   *  exactly the state where sending is allowed but the composer
   *  should say what is happening. */
  extractionStatus?: FileAttachment["extraction_status"];
}

interface ChatState {
  conversations: Conversation[];
  conversationsLoaded: boolean;
  /** FIX86 — the last list load failed (not "you have none"). */
  conversationsLoadFailed: boolean;
  /** True once the first /conversations page has been fetched. */
  conversationsCursor: string | null;
  hasMoreConversations: boolean;
  loadingMoreConversations: boolean;
  /** Server-side search results. Populated by searchConversations;
   *  empty array when no search is active. */
  conversationsSearchResults: Conversation[];
  /** Content matches from the S6 full-text engine (message bodies),
   *  one row per conversation, excluding conversations already in
   *  conversationsSearchResults (title matches win the dedup). */
  conversationsSearchHits: ConversationSearchHit[];
  conversationsSearchActive: boolean;
  conversationsSearchLoading: boolean;
  activeConversationId: string | null;
  /** message lists keyed by conversation id. */
  messages: Record<string, Message[]>;
  /** FIX121 C3 — per conversation, the send that has left the page and has
   *  not yet got its response: its id and what it said. Not rendered by
   *  anything; it lives here so that it flips synchronously (zustand's set
   *  is synchronous) and resets with the store. */
  sendsOpening: Record<string, { requestId: string; signature: string } | undefined>;
  /** FIX122 Part C — per conversation, the text of a send that failed
   *  before the server took it, on its way back to the composer. ChatInput
   *  puts it in the box (when the box is empty) and consumes it. The same
   *  text is already in the conversation's draft (lib/sendOutbox), which is
   *  what a composer that mounts later reads. */
  returnedDrafts: Record<string, { id: string; text: string } | undefined>;
  consumeReturnedDraft: (convId: string, id: string) => void;
  /** FIX124 R2 — "load the answer": read the conversation from the server
   *  (the row that stopped waiting is replaced by what really happened) and
   *  reattach if the turn is still being written. */
  reloadAnswer: (convId: string) => Promise<void>;
  turnScrollRequests: Record<string, TurnScrollRequest | undefined>;
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
  /** T2 — true while an active stream has received NO bytes (real
   *  events OR server heartbeats) for STREAM_IDLE.timeoutMs. Drives
   *  the "still working — retry?" notice; cleared the moment data
   *  flows again or the stream ends. */
  stalledStreams: Record<string, boolean>;
  /** Server-declared provider stall: chat-edge is holding the upstream
   *  connection and has had NO model output for its threshold. Kept
   *  SEPARATE from stalledStreams on purpose — heartbeats keep arriving
   *  during a provider stall, so the byte-based clock would erase this
   *  the instant the next ": ping" landed. Cleared only by the server's
   *  recovery frame, by retry, or by stream end. */
  providerStalledStreams: Record<string, boolean>;
  /** FIX33 A-W6 — the context meter's numbers per conversation (web: the
   *  /context-info dry run; desktop: the host's context_budget events). */
  contextBudgetByConv: Record<string, ContextInfo>;
  setContextBudget: (convId: string, info: ContextInfo) => void;
  /** has_more=true for older-page-available conversations. */
  hasMoreMessages: Record<string, boolean>;
  /** True while a paginate-older request is in flight. */
  loadingOlder: Record<string, boolean>;
  /** Files staged for the next user message, keyed by conv id. */
  pendingFiles: Record<string, PendingFile[]>;
  /** Attachment shells handed over by the composer at the instant of
   *  send, keyed by server file id.
   *
   *  Why this exists: `submit()` clears its pending-file bucket
   *  synchronously right after calling `onSend`, but on the landing
   *  page `onSend` awaits `ensureConv()` before it reaches
   *  `sendMessage` — so by the time sendMessage looked for the chips
   *  they were already gone and the user's own message rendered with
   *  no attachments (the file was linked server-side all along; only
   *  the transcript was missing it). Handing the metadata over
   *  explicitly removes the ordering dependency entirely. */
  outgoingFiles: Record<string, FileAttachment>;
  errorBanner: string | null;
  setError: (msg: string | null) => void;
  /** FIX127 A3 — a message sent into a conversation that was deleted
   *  elsewhere. The text is already in the new-chat draft; this is the
   *  banner's "send in a new chat" hand-off. */
  strandedSend: { text: string } | null;
  clearStrandedSend: () => void;

  /** FIX127 D1 — messages typed while an answer streams wait here, per
   *  conversation, in order. Each one is sent as its OWN turn when the
   *  current answer finishes (one turn in flight per chat — M5 — is kept:
   *  the queue drains one message per finished turn, never in parallel).
   *  They are sent one by one, not merged into a single prompt as Open
   *  WebUI does: merging would put words in the transcript the person
   *  never composed, and every turn's rescue, charge and title logic is
   *  per message. */
  queuedMessages: Record<string, QueuedMessage[]>;
  enqueueMessage: (convId: string, text: string, fileIds?: string[]) => string;
  updateQueuedMessage: (convId: string, id: string, text: string) => void;
  removeQueuedMessage: (convId: string, id: string) => void;
  /** Stop the current answer, then send this queued message. */
  sendQueuedNow: (convId: string, id: string) => Promise<void>;

  // ── pending-file ops ─────────────────────────────────────
  addPendingFile: (convId: string, file: File) => string;
  /** `keepFile`: the chip leaves the composer but the upload stays on the
   *  server — it now belongs to a queued message (FIX127 D1). */
  removePendingFile: (convId: string, pendingId: string, opts?: { keepFile?: boolean }) => void;
  updateFileProgress: (convId: string, pendingId: string, progress: number) => void;
  markFileReady: (
    convId: string,
    pendingId: string,
    serverId: string,
    extractionStatus?: FileAttachment["extraction_status"],
  ) => void;
  markFileError: (convId: string, pendingId: string, error: string) => void;
  /** Background text extraction finished (or moved on) for a chip
   *  that has already finished uploading. */
  setFileExtractionStatus: (
    convId: string,
    pendingId: string,
    extractionStatus: FileAttachment["extraction_status"],
  ) => void;
  clearPendingFiles: (convId: string) => void;
  /** Hand the composer's staged attachment metadata to the store just
   *  before `onSend`, so the optimistic user bubble can render chips
   *  no matter how long the send path takes to reach sendMessage. */
  stageOutgoingFiles: (files: FileAttachment[]) => void;

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
  /** FIX35 Part C — Auto router cost ceiling for one conversation. */
  setConversationRoutingTier: (id: string, tier: RoutingTier | "") => Promise<void>;
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
  /** Pin / unpin a conversation (migration 021). Optimistic update —
   *  the sidebar re-buckets into / out of the PINNED section
   *  immediately; rolls back on failure. */
  togglePinConversation: (id: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;

  // ── archive (migration 032) ──────────────────────────────
  /** Archive / un-archive. NOT a delete — the chat only leaves the
   *  sidebar list. Optimistic: the row disappears (or comes back)
   *  immediately; rolls back on failure. */
  /** FIX84 row 6 — resolves true only when the server accepted the move.
   *  The optimistic list update is rolled back on failure and the error
   *  banner raised here; callers must NOT announce success unless this
   *  returns true (the archive page used to toast "moved back" on top of
   *  the failure banner). */
  setConversationArchived: (id: string, archived: boolean) => Promise<boolean>;
  /** Archived conversations, loaded on demand by the archive view.
   *  Kept out of `conversations` so the sidebar's grouping/pagination
   *  logic never has to filter them out. */
  archivedConversations: Conversation[];
  archivedLoaded: boolean;
  archivedLoading: boolean;
  loadArchivedConversations: () => Promise<void>;

  // ── project ops (Phase 4) ────────────────────────────────
  /** Projects visible to the current user. */
  projects: Project[];
  projectsLoaded: boolean;
  /** Set when the last loadProjects failed, so the page can render
   *  an error state with retry instead of an empty list. */
  projectsError: string | null;
  loadProjects: () => Promise<void>;
  /** Create a project. Throws on failure so the page can toast. */
  createProject: (name: string, instructions: string) => Promise<Project>;
  /** Patch name / instructions. Throws on failure. */
  updateProject: (
    id: string,
    patch: { name?: string; instructions?: string },
  ) => Promise<Project>;
  /** Attach / detach ("null") the project's knowledge base. Throws
   *  on failure. */
  setProjectKnowledgeBase: (
    id: string,
    kbId: string | null,
  ) => Promise<Project>;
  /** Delete a project; its conversations are un-assigned (server SET
   *  NULL), mirrored locally. Optimistic with rollback; rethrows so
   *  the page can toast. */
  deleteProject: (id: string) => Promise<void>;
  /** Assign a conversation to a project, or `null` to un-assign.
   *  Optimistic (conversations[].project_id + project counts);
   *  rolls back on failure. */
  moveConversationToProject: (
    convId: string,
    projectId: string | null,
  ) => Promise<void>;
  /** When set, the NEXT lazily-created conversation is assigned to
   *  this project right after creation. Read + cleared by
   *  createConversation() — same contract as
   *  nextConversationTemporary. Used by the project page's "New chat
   *  in this project" button. */
  nextConversationProjectId: string | null;
  setNextConversationProjectId: (v: string | null) => void;

  // ── message ops ──────────────────────────────────────────
  /** A 404/410 on a conversation means it was deleted elsewhere
   *  (desktop delete propagation, another tab). Drop it from the
   *  sidebar + clear active/message state and surface a specific
   *  "deleted" banner instead of a generic AI-service error. */
  handleConversationGone: (convId: string) => void;
  loadMessages: (convId: string) => Promise<void>;
  loadOlderMessages: (convId: string) => Promise<void>;
  sendMessage: (convId: string, content: string, fileIds?: string[], imageMode?: boolean, videoMode?: boolean) => Promise<void>;
  cancelGeneration: (convId: string) => Promise<void>;
  /** Phase 5 — reattach to a generation that is still running
   *  server-side (page refresh / network drop mid-stream). Replays
   *  the already-generated prefix and continues through the normal
   *  streaming pipeline. */
  resumeStream: (convId: string, messageId: string) => Promise<void>;
  /** Hosted mode (desktop shell): after a reload the host — not chat-edge —
   *  holds any in-flight turn, and nothing streaming is persisted to attach
   *  to. Probe the host over the bridge; if a turn replays, materialize a
   *  streaming row and render it. No-op in a plain browser. */
  hostedResumeProbe: (convId: string) => void;
  /** Desktop shell (Phase 4) — attach an in-thread consent prompt to the
   *  conversation's streaming assistant message. */
  addPendingConsent: (convId: string, consent: PendingConsent) => void;
  /** Desktop shell (Phase 4) — answer a consent prompt over the bridge and
   *  clear the card. */
  resolveConsent: (
    convId: string,
    promptId: string,
    decision: "allow" | "deny",
    scope: "once" | "session" | "always",
  ) => void;
  /** FIX73 (desktop shell) — attach the model's question card to the
   *  conversation's streaming assistant message. */
  addPendingQuestion: (convId: string, question: PendingQuestion) => void;
  /** FIX73 — answer or skip the question over the bridge and clear the card. */
  resolveQuestion: (convId: string, answer: QuestionAnswerPayload) => void;
  /** T2 — user-driven recovery from a stalled stream: drops the local
   *  connection and reattaches through the existing resume machinery
   *  (the generation itself keeps running server-side). */
  retryStalledStream: (convId: string) => void;
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
  conversationsLoadFailed: false,
  archivedConversations: [],
  archivedLoaded: false,
  archivedLoading: false,
  conversationsCursor: null,
  hasMoreConversations: false,
  loadingMoreConversations: false,
  conversationsSearchResults: [],
  conversationsSearchHits: [],
  conversationsSearchActive: false,
  conversationsSearchLoading: false,
  activeConversationId: null,
  messages: {},
  sendsOpening: {},
  returnedDrafts: {},
  async reloadAnswer(convId) {
    if (get().streamingAssistantId[convId]) return;
    if (!(await adoptServerRows(set, convId))) {
      get().setError(tOutside("error.offline"));
      return;
    }
    const last = (get().messages[convId] ?? []).at(-1);
    if (last && last.role === "assistant" && last.status === "streaming") void get().resumeStream(convId, last.id);
  },
  consumeReturnedDraft(convId, id) {
    if (get().returnedDrafts[convId]?.id !== id) return;
    set((s) => ({ returnedDrafts: { ...s.returnedDrafts, [convId]: undefined } }));
  },
  turnScrollRequests: {},
  messagesLoaded: {},
  streamingAssistantId: {},
  abortControllers: {},
  stalledStreams: {},
  providerStalledStreams: {},
  contextBudgetByConv: {},
  setContextBudget(convId, info) {
    set((s) => ({ contextBudgetByConv: { ...s.contextBudgetByConv, [convId]: info } }));
  },
  hasMoreMessages: {},
  loadingOlder: {},
  pendingFiles: {},
  outgoingFiles: {},
  errorBanner: null,
  strandedSend: null,
  queuedMessages: {},
  nextConversationTemporary: false,
  setNextConversationTemporary(v) {
    set({ nextConversationTemporary: v });
  },
  projects: [],
  projectsLoaded: false,
  projectsError: null,
  nextConversationProjectId: null,
  setNextConversationProjectId(v) {
    set({ nextConversationProjectId: v });
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
  removePendingFile(convId, pendingId, opts) {
    // FIX127 A1 — removing the chip removes the file: abort the transfer
    // and take the server row back (lib/uploadRegistry). Until FIX127 this
    // was a pure filter and the removed file stayed in the model's context.
    const entry = (get().pendingFiles[convId] ?? []).find(
      (p) => p.id === pendingId,
    );
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: (s.pendingFiles[convId] ?? []).filter(
          (p) => p.id !== pendingId,
        ),
      },
    }));
    if (!entry) return;
    if (opts?.keepFile) forgetUpload(entry.id);
    else discardUpload({ id: entry.id, serverId: entry.serverId });
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
  markFileReady(convId, pendingId, serverId, extractionStatus) {
    set((s) => ({
      pendingFiles: {
        ...s.pendingFiles,
        [convId]: (s.pendingFiles[convId] ?? []).map((p) =>
          p.id === pendingId
            ? {
                ...p,
                status: "ready",
                progress: 100,
                serverId,
                extractionStatus: extractionStatus ?? p.extractionStatus,
              }
            : p,
        ),
      },
    }));
  },
  setFileExtractionStatus(convId, pendingId, extractionStatus) {
    set((s) => {
      // Late arrival: extraction polling outlives the chip (submit()
      // clears the bucket while the server is still reading a scan).
      // Bail rather than writing an empty bucket back into the map.
      const bucket = s.pendingFiles[convId];
      if (!bucket?.some((p) => p.id === pendingId)) return {};
      return {
        pendingFiles: {
          ...s.pendingFiles,
          [convId]: bucket.map((p) =>
            p.id === pendingId ? { ...p, extractionStatus } : p,
          ),
        },
      };
    });
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
    // The chips are being SENT (or the bucket is torn down): keep the files,
    // drop the upload bookkeeping only.
    for (const p of get().pendingFiles[convId] ?? []) forgetUpload(p.id);
    set((s) => {
      const next = { ...s.pendingFiles };
      delete next[convId];
      return { pendingFiles: next };
    });
  },
  stageOutgoingFiles(files) {
    if (files.length === 0) return;
    set((s) => {
      const next = { ...s.outgoingFiles };
      for (const f of files) next[f.id] = f;
      return { outgoingFiles: next };
    });
  },

  clearStrandedSend() {
    set({ strandedSend: null });
  },

  // ── FIX127 D1 — the message queue ──
  enqueueMessage(convId, text, fileIds = []) {
    const id = `q-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    set((s) => ({ queuedMessages: { ...s.queuedMessages, [convId]: [...(s.queuedMessages[convId] ?? []), { id, text, fileIds }] } }));
    return id;
  },
  updateQueuedMessage(convId, id, text) {
    set((s) => ({ queuedMessages: { ...s.queuedMessages, [convId]: (s.queuedMessages[convId] ?? []).map((q) => (q.id === id ? { ...q, text } : q)) } }));
  },
  removeQueuedMessage(convId, id) {
    set((s) => ({ queuedMessages: { ...s.queuedMessages, [convId]: (s.queuedMessages[convId] ?? []).filter((q) => q.id !== id) } }));
  },
  async sendQueuedNow(convId, id) {
    const q = (get().queuedMessages[convId] ?? []).find((x) => x.id === id);
    if (!q) return;
    get().removeQueuedMessage(convId, id);
    if (get().abortControllers[convId] || get().streamingAssistantId[convId]) {
      await get().cancelGeneration(convId);
    }
    await get().sendMessage(convId, q.text, q.fileIds);
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
        conversationsLoadFailed: false,
        conversationsCursor: res.next_cursor,
        hasMoreConversations: res.has_more,
      });
    } catch (err) {
      set({ conversationsLoaded: true, conversationsLoadFailed: true });
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
        conversationsSearchHits: [],
        conversationsSearchLoading: false,
      });
      return;
    }
    set({ conversationsSearchActive: true, conversationsSearchLoading: true });

    // Two engines, fired together: the legacy title substring match
    // (keeps rename/pin/menu-capable conversation rows) plus the S6
    // full-text search over message bodies. Each may fail
    // independently — a dead /search endpoint must never take the
    // title search down with it, so allSettled instead of all.
    const [titles, fts] = await Promise.allSettled([
      api.listConversationsSearch(q, 50),
      api.searchMessages(q, 20),
    ]);

    if (titles.status === "rejected" && fts.status === "rejected") {
      // Both engines down — stop the spinner, keep whatever was on
      // screen rather than blanking the sidebar.
      set({ conversationsSearchLoading: false });
      return;
    }

    const titleConvs =
      titles.status === "fulfilled" ? titles.value.conversations : [];
    const titleIds = new Set(titleConvs.map((c) => c.id));

    const hits: ConversationSearchHit[] = [];
    if (fts.status === "fulfilled") {
      const seen = new Set<string>();
      for (const r of fts.value.results) {
        if (r.type !== "message" || !r.conversation_id) continue;
        // One row per conversation; title matches already render as
        // full conversation rows above, so skip those too.
        if (titleIds.has(r.conversation_id) || seen.has(r.conversation_id))
          continue;
        seen.add(r.conversation_id);
        const content = r.content.trim();
        hits.push({
          conversation_id: r.conversation_id,
          conversation_title: r.conversation_title?.trim() || "Untitled",
          snippet:
            content.length > 140 ? `${content.slice(0, 140)}…` : content,
          date: r.date,
        });
      }
    }

    set({
      conversationsSearchResults: titleConvs,
      conversationsSearchHits: hits,
      conversationsSearchLoading: false,
    });
  },

  clearConversationsSearch() {
    set({
      conversationsSearchActive: false,
      conversationsSearchResults: [],
      conversationsSearchHits: [],
      conversationsSearchLoading: false,
    });
  },

  setActive(id) {
    set({ activeConversationId: id });
  },

  async createConversation(modelId, opts) {
    const wantTemp = opts?.temporary === true || get().nextConversationTemporary;
    let c = await api.createConversation(modelId, { temporary: wantTemp });
    // S1 — temporary chat. Two trigger paths land here: the explicit
    // opts.temporary flag (called from /chat landing when the user
    // clicked the temp-chat launcher) or the persisted
    // nextConversationTemporary store flag (set by the sidebar
    // before navigation, consumed on the next create). Either way
    // we PATCH memory_enabled=false on the fresh row so the very
    // first send already skips memory + tools.
    if (wantTemp && c.memory_enabled !== false) {
      // The server already starts a temporary chat with memory off
      // (FIX127 B1); this PATCH only covers a row that came back otherwise.
      try {
        c = await api.setConversationMemory(c.id, false);
      } catch {
        // Non-fatal — the user can still toggle from the header.
      }
      set({ nextConversationTemporary: false });
    }
    // Phase 4 — "New chat in this project". Same consume-the-flag
    // contract as nextConversationTemporary: the project page sets
    // the flag before navigating to /chat, and the lazy create that
    // follows assigns the fresh row to the project so the very first
    // send already carries the project's instructions + KB.
    const wantProjectId = get().nextConversationProjectId;
    if (wantProjectId) {
      try {
        await api.moveConversationToProject(c.id, wantProjectId);
        c = { ...c, project_id: wantProjectId };
        // Keep the local project count honest without a refetch.
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === wantProjectId
              ? { ...p, conversation_count: p.conversation_count + 1 }
              : p,
          ),
        }));
      } catch {
        // Non-fatal — the user can still move it from the sidebar.
      }
      set({ nextConversationProjectId: null });
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
    // FIX122 — a rename is the person's; a name still being generated for
    // this conversation must not be shown over it (nameConversationOnce).
    userRenames.set(id, (userRenames.get(id) ?? 0) + 1);
    const updated = await api.renameConversation(id, title);
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, ...updated } : c,
      ),
    }));
  },

  async togglePinConversation(id) {
    const existing = get().conversations.find((c) => c.id === id);
    const prevPinned = existing?.is_pinned ?? false;
    const nextPinned = !prevPinned;
    // Optimistic flip so the sidebar re-buckets immediately.
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, is_pinned: nextPinned } : c,
      ),
    }));
    try {
      const updated = await api.setConversationPinned(id, nextPinned);
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, ...updated } : c,
        ),
      }));
    } catch {
      // Roll back the optimistic flip.
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === id ? { ...c, is_pinned: prevPinned } : c,
        ),
      }));
      get().setError("Couldn't update pin. Please try again.");
    }
  },

  async setConversationArchived(id, archived) {
    const existing =
      get().conversations.find((c) => c.id === id) ??
      get().archivedConversations.find((c) => c.id === id);
    if (!existing) return false;

    // Optimistic move between the two lists. Archiving must feel like
    // filing, not loading: the row leaves the sidebar at once.
    set((s) => ({
      conversations: archived
        ? s.conversations.filter((c) => c.id !== id)
        : [{ ...existing, is_archived: false }, ...s.conversations.filter((c) => c.id !== id)],
      archivedConversations: archived
        ? [{ ...existing, is_archived: true }, ...s.archivedConversations.filter((c) => c.id !== id)]
        : s.archivedConversations.filter((c) => c.id !== id),
    }));

    try {
      await api.setConversationArchived(id, archived);
      return true;
    } catch {
      // Put it back exactly where it was.
      set((s) => ({
        conversations: archived
          ? [{ ...existing, is_archived: false }, ...s.conversations.filter((c) => c.id !== id)]
          : s.conversations.filter((c) => c.id !== id),
        archivedConversations: archived
          ? s.archivedConversations.filter((c) => c.id !== id)
          : [{ ...existing, is_archived: true }, ...s.archivedConversations.filter((c) => c.id !== id)],
      }));
      get().setError(tOutside("archive.error.failed"));
      return false;
    }
  },

  async loadArchivedConversations() {
    if (get().archivedLoading) return;
    set({ archivedLoading: true });
    try {
      const res = await api.listArchivedConversations();
      set({
        archivedConversations: res?.conversations ?? [],
        archivedLoaded: true,
        archivedLoading: false,
      });
    } catch {
      set({ archivedLoading: false, archivedLoaded: true });
      get().setError(tOutside("archive.error.loadFailed"));
    }
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

  async setConversationRoutingTier(id, tier) {
    // FIX35 Part C — optimistic, revert on failure (same shape as the
    // reasoning toggle).
    const prev = get().conversations.find((c) => c.id === id)?.routing_tier ?? "";
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, routing_tier: tier } : c)),
    }));
    try {
      await api.setConversationRoutingTier(id, tier);
    } catch {
      set((s) => ({
        conversations: s.conversations.map((c) => (c.id === id ? { ...c, routing_tier: prev } : c)),
      }));
      get().setError(tOutside("tier.toast.failed"));
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
    // Capture project membership BEFORE the await — mirrors
    // moveConversationToProject, which reads project_id via get() before
    // mutating. Reading it only inside the post-await set() risks a null
    // if the conversation list is mutated during the request window.
    const beforeProjectId = get().conversations.find((c) => c.id === id)?.project_id ?? null;
    await api.deleteConversation(id);
    // A deleted conversation must not resurrect its unsent draft (W1).
    clearDraft(id);
    set((s) => {
      const next = { ...s.messages };
      delete next[id];
      const nextLoaded = { ...s.messagesLoaded };
      delete nextLoaded[id];
      // If the deleted conversation belonged to a project, decrement that
      // project's count so the sidebar "(N)" stays accurate without a
      // refetch — mirrors the count adjust in moveConversationToProject.
      const insideProjectId = s.conversations.find((c) => c.id === id)?.project_id ?? null;
      // Prefer the pre-await value; fall back to the in-set lookup.
      const deletedProjectId = beforeProjectId ?? insideProjectId;
      return {
        conversations: s.conversations.filter((c) => c.id !== id),
        projects: deletedProjectId
          ? s.projects.map((p) =>
              p.id === deletedProjectId
                ? { ...p, conversation_count: Math.max(0, p.conversation_count - 1) }
                : p,
            )
          : s.projects,
        messages: next,
        messagesLoaded: nextLoaded,
        activeConversationId:
          s.activeConversationId === id ? null : s.activeConversationId,
      };
    });
  },

  // ── projects (Phase 4) ───────────────────────────────────
  async loadProjects() {
    try {
      const res = await api.listProjects();
      set({
        projects: res?.projects ?? [],
        projectsLoaded: true,
        projectsError: null,
      });
    } catch {
      set({
        projectsLoaded: true,
        projectsError: tOutside("projects.error.loadFailed"),
      });
    }
  },

  async createProject(name, instructions) {
    const res = await api.createProject(name, instructions);
    set((s) => ({ projects: [res.project, ...s.projects] }));
    return res.project;
  },

  async updateProject(id, patch) {
    const res = await api.updateProject(id, patch);
    set((s) => ({
      projects: s.projects.map((p) =>
        p.id === id ? { ...p, ...res.project } : p,
      ),
    }));
    return res.project;
  },

  async setProjectKnowledgeBase(id, kbId) {
    const res = await api.setProjectKnowledgeBase(id, kbId);
    set((s) => ({
      projects: s.projects.map((p) =>
        p.id === id ? { ...p, ...res.project } : p,
      ),
    }));
    return res.project;
  },

  async deleteProject(id) {
    const prevProjects = get().projects;
    const prevConversations = get().conversations;
    // Optimistic: drop the project and un-assign its conversations
    // locally (server does the same via ON DELETE SET NULL — chats
    // are NOT deleted).
    set((s) => ({
      projects: s.projects.filter((p) => p.id !== id),
      conversations: s.conversations.map((c) =>
        c.project_id === id ? { ...c, project_id: null } : c,
      ),
    }));
    try {
      await api.deleteProject(id);
    } catch (err) {
      set({ projects: prevProjects, conversations: prevConversations });
      throw err;
    }
  },

  async moveConversationToProject(convId, projectId) {
    const existing = get().conversations.find((c) => c.id === convId);
    const prevProjectId = existing?.project_id ?? null;
    if (prevProjectId === projectId) return;
    // Optimistic: re-assign + adjust project counts so the UI
    // reflects the move immediately.
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === convId ? { ...c, project_id: projectId } : c,
      ),
      projects: s.projects.map((p) => {
        if (p.id === prevProjectId) {
          return { ...p, conversation_count: Math.max(0, p.conversation_count - 1) };
        }
        if (p.id === projectId) {
          return { ...p, conversation_count: p.conversation_count + 1 };
        }
        return p;
      }),
    }));
    try {
      await api.moveConversationToProject(convId, projectId);
    } catch {
      // Roll back both the membership and the counts.
      set((s) => ({
        conversations: s.conversations.map((c) =>
          c.id === convId ? { ...c, project_id: prevProjectId } : c,
        ),
        projects: s.projects.map((p) => {
          if (p.id === projectId) {
            return { ...p, conversation_count: Math.max(0, p.conversation_count - 1) };
          }
          if (p.id === prevProjectId) {
            return { ...p, conversation_count: p.conversation_count + 1 };
          }
          return p;
        }),
      }));
      get().setError(tOutside("projects.error.moveFailed"));
    }
  },

  // ────────────────────────────────────────────────────────
  handleConversationGone(convId) {
    // FIX127 A3 — never lose what the user typed: a draft under the dead
    // id moves to the new-chat composer, which is where the page lands.
    migrateDraft(convId, NEW_CHAT_DRAFT_KEY);
    set((s) => {
      const nextMessages = { ...s.messages };
      delete nextMessages[convId];
      const nextLoaded = { ...s.messagesLoaded };
      delete nextLoaded[convId];
      const nextHasMore = { ...s.hasMoreMessages };
      delete nextHasMore[convId];
      // Keep the project "(N)" count accurate, same as deleteConversation.
      const goneProjectId =
        s.conversations.find((c) => c.id === convId)?.project_id ?? null;
      return {
        conversations: s.conversations.filter((c) => c.id !== convId),
        projects: goneProjectId
          ? s.projects.map((p) =>
              p.id === goneProjectId
                ? { ...p, conversation_count: Math.max(0, p.conversation_count - 1) }
                : p,
            )
          : s.projects,
        messages: nextMessages,
        messagesLoaded: nextLoaded,
        hasMoreMessages: nextHasMore,
        activeConversationId:
          s.activeConversationId === convId ? null : s.activeConversationId,
        errorBanner: tOutside("error.conversationDeleted"),
      };
    });
  },

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
      // FIX122 Part C — a send the page died under (reload, crash) before
      // the server answered it: if the server never got it, the text goes
      // back to the composer; if it did, the entry is simply dropped. Never
      // while a send of THIS page is still opening — that entry is live.
      const interrupted = outboxPeek(convId);
      if (interrupted && !get().sendsOpening[convId]) {
        if (outboxIsUndelivered(interrupted, res?.messages ?? [])) giveBackToComposer(set, convId);
        else outboxAccepted(convId, interrupted.requestId);
      }
      // Phase 5 — the last assistant row still says "streaming": the
      // generation is (probably) alive server-side and this client
      // just wasn't attached (refresh mid-stream / reopened tab).
      // Reattach in the background; resumeStream owns all fallbacks.
      const list = res?.messages ?? [];
      const last = list[list.length - 1];
      if (last && last.role === "assistant" && last.status === "streaming") {
        void get().resumeStream(convId, last.id);
      } else if (HOSTED_MODE) {
        // Hosted mode: the host holds any in-flight turn and persists nothing
        // streaming to attach to, so the "last row is streaming" test above
        // can't fire. Probe the host directly (plan addendum, reload recovery).
        get().hostedResumeProbe(convId);
      }
    } catch (err) {
      // 404/410 → the conversation was deleted elsewhere: drop it from
      // the sidebar, clear active/message state, and show the specific
      // "deleted" banner. 401s never reach this catch — ApiClient
      // bounces to portal-login first.
      if (err instanceof ApiError && (err.status === 404 || err.status === 410)) {
        get().handleConversationGone(convId);
        return;
      }
      // Other errors: mark the slot loaded so the skeleton clears, keep
      // any previously-loaded array intact, surface a retry-able banner.
      set((s) => ({
        messages: { ...s.messages, [convId]: s.messages[convId] ?? [] },
        messagesLoaded: { ...s.messagesLoaded, [convId]: true },
        hasMoreMessages: {
          ...s.hasMoreMessages,
          [convId]: s.hasMoreMessages[convId] ?? false,
        },
        errorBanner: tOutside("error.conversationLoadFailed"),
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
  async sendMessage(convId, content, fileIds, imageMode, videoMode) {
    const trimmed = content.trim();
    if (!trimmed && (!fileIds || fileIds.length === 0)) return;
    // Offline guard. SSE streams need a live socket — bail early
    // with a user-visible toast rather than letting fetch hang.
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      get().setError(
        tOutside("error.offline"),
      );
      return;
    }
    // FIX121 C3 — one user action is one send. Enter and a click (or two
    // clicks) inside one React commit both pass the composer's `canSend`,
    // because `streaming` is derived state that only turns true a commit
    // later; each call then POSTed its own turn and BOTH persisted — the
    // supersede below only relabels the first row, it never stopped the
    // first request. An IDENTICAL send (same text, same files) for this
    // conversation while the first has no response yet is the same keystroke
    // arriving twice, and is dropped here, where it cannot cost a turn. A
    // different message is a different action and goes through as before.
    const signature = `${trimmed}\u0000${(fileIds ?? []).join(",")}`;
    if (get().sendsOpening[convId]?.signature === signature) return;
    const tempUserId = tmpId("tmp-user");
    const tempAsstId = tmpId("tmp-asst");
    // The id of THIS send, minted before anything leaves the page: the run
    // id on every frame and row, and the server's idempotency key.
    const requestId = crypto.randomUUID();
    set((s) => ({ sendsOpening: { ...s.sendsOpening, [convId]: { requestId, signature } } }));
    // FIX122 Part C — on the machine before it is on the wire: the composer
    // is already empty, so until the server answers this is the only copy.
    outboxPut(convId, requestId, trimmed);

    // FIX114 — one streaming row per conversation, and this path owns it.
    // A send while the previous turn is still open used to leave that
    // turn's row streaming for good: the desktop host cancels the old run
    // and, by its own rule, writes it no terminal ("SUPERSEDED — no
    // terminal from the old run"); the bridge closes the old sink; nothing
    // on this side ever told the row. Measured on the owner's e48: the
    // superseded row kept its "Working…" under the finished answer that
    // replaced it. The row is retired here as cancelled — it keeps what it
    // had and says it was cut short — before the new rows are added.
    const inflight = get().streamingAssistantId[convId];
    if (inflight) finalizeMessage(set, get, convId, inflight, "cancelled");

    // FIX124 — a chat is named after the first turn that is ANSWERED, which
    // need not be the first turn: asking only after turn one left a chat whose
    // first answer failed as "New conversation" for good.
    const answeredBefore = hasAnsweredTurn(get, convId);
    let replyCompleted = false;
    showPlaceholderTitle(set, get, convId, trimmed);

    // Capture the conversation's currently bound model at send time
    // and stamp it on the assistant placeholder. The server-side
    // message_id arrives via message_start, but its model_id only
    // surfaces on the next /messages reload — stamping locally keeps
    // the per-message model badge + switch divider consistent during
    // streaming.
    const convModelId =
      get().conversations.find((c) => c.id === convId)?.model_id ?? null;

    // Build optimistic file shells so the user immediately sees
    // badges/thumbnails under their own message, before the server
    // reloads message history.
    //
    // PRIMARY source is `outgoingFiles` — the composer hands the
    // metadata over synchronously in submit(), immediately before
    // onSend. That matters because submit() also clears its chip
    // bucket synchronously, while the landing page's onSend awaits
    // ensureConv() before it ever reaches this function: reading the
    // chips here would find an already-emptied bucket and render the
    // user's message with no attachment (the classic "the AI clearly
    // read my PDF but my message shows no file" report — the file was
    // linked server-side the whole time, only the transcript was
    // missing it).
    //
    // FALLBACK is the old every-bucket pendingFiles scan, matched by
    // serverId. It still covers any caller that reaches sendMessage
    // without going through the composer (tests, future entry points).
    // Filtering by fileIds keeps unrelated still-uploading rows (from
    // a send the user abandoned) off this message.
    const fileIdSet = new Set(fileIds ?? []);
    let optimisticFiles: FileAttachment[] = [];
    if (fileIdSet.size > 0) {
      const staged = get().outgoingFiles;
      const fromPending = new Map<string, FileAttachment>();
      for (const p of Object.values(get().pendingFiles).flat()) {
        if (!p.serverId || !fileIdSet.has(p.serverId)) continue;
        fromPending.set(p.serverId, {
          id: p.serverId,
          filename: p.file.name,
          content_type: p.file.type || "application/octet-stream",
          file_size_bytes: p.file.size,
          extraction_status: p.extractionStatus ?? "completed",
          created_at: new Date().toISOString(),
        });
      }
      // Preserve the caller's order rather than bucket iteration order.
      optimisticFiles = (fileIds ?? [])
        .map((id) => staged[id] ?? fromPending.get(id))
        .filter((f): f is FileAttachment => Boolean(f));
      // Consume what we used so a later send can't resurrect stale
      // shells for ids it wasn't given.
      if (Object.keys(staged).length > 0) {
        set((s) => {
          const next = { ...s.outgoingFiles };
          for (const id of fileIdSet) delete next[id];
          return { outgoingFiles: next };
        });
      }
    }

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
      turnScrollRequests: { ...s.turnScrollRequests, [convId]: {
        id: tempAsstId, kind: "send", userKey: tempUserId, replyKey: tempAsstId,
      } },
      streamingAssistantId: { ...s.streamingAssistantId, [convId]: tempAsstId },
    }));

    const ctrl = new AbortController();
    let imageTimedOut = false;
    // Image AND video gen are both long-running tool calls that block the
    // stream; give them the same generous client-side timeout.
    const imageTimeout =
      imageMode === true || videoMode === true
        ? window.setTimeout(() => {
            imageTimedOut = true;
            void api.cancelGeneration(convId).catch(() => {
              /* best-effort */
            });
            ctrl.abort();
          }, IMAGE_GENERATION_TIMEOUT_MS)
        : null;
    set((s) => ({
      abortControllers: { ...s.abortControllers, [convId]: ctrl },
    }));

    try {
      const res = await api.openMessageStream(
        convId,
        trimmed,
        ctrl.signal,
        fileIds,
        imageMode,
        videoMode,
        convModelId, // FIX9 Part B: carry the selected model into the hosted turn
        requestId,
      );
      if (get().sendsOpening[convId]?.requestId === requestId) set((s) => ({ sendsOpening: { ...s.sendsOpening, [convId]: undefined } })); // the response is back: `streaming` gates the composer from here
      if (!res.ok) {
        // Refused: the server has no such message. Back to the composer —
        // except when the conversation itself is gone (nowhere to send it).
        if (res.status === 404 || res.status === 410) outboxAccepted(convId, requestId);
        else giveBackToComposer(set, convId, requestId);
        await handleStreamPreError(res, set, get, convId, tempAsstId, tempUserId, trimmed);
        return;
      }
      // The server took it. What happens to the turn from here is the
      // turn's story (its row, its error card), not the composer's.
      outboxAccepted(convId, requestId);
      await consumeStream(res, ctrl.signal, set, get, convId, tempAsstId, tempUserId);
      replyCompleted = lastReplyEndedCompleted(get, convId);
    } catch (err) {
      // consumeStream may have swapped the placeholder id for the
      // persisted row id (message_start). Finalize whichever id is
      // CURRENT — finalizing the stale temp id would leave the message
      // stuck in status "streaming" after a cancel/error.
      const streamingId = get().streamingAssistantId[convId] ?? tempAsstId;
      // FIX122 Part C — the request died with no answer (the entry is still
      // there; an accepted send has dropped it): a failed send, and the text
      // returns to the composer. A Stop is not a failure — the server
      // usually has the message by then and keeps the stopped turn's row —
      // so that entry is dropped.
      if ((err as Error).name === "AbortError") outboxAccepted(convId, requestId);
      else giveBackToComposer(set, convId, requestId);
      if ((err as Error).name === "AbortError") {
        if (imageTimedOut) {
          const localized = localizeErrorCode("image_timeout");
          set((s) => {
            const list = s.messages[convId] ?? [];
            return {
              messages: {
                ...s.messages,
                [convId]: list.map((m) =>
                  m.id === streamingId
                    ? {
                        ...m,
                        error_code: "image_timeout",
                        error_message: localized,
                      }
                    : m,
                ),
              },
            };
          });
          get().setError(localized);
          finalizeMessage(set, get, convId, streamingId, "error");
          return;
        }
        finalizeMessage(set, get, convId, streamingId, "cancelled");
        return;
      }
      // The conversation was deleted elsewhere (desktop propagation /
      // another tab): finalize the optimistic rows, then drop the
      // conversation + show the specific "deleted" banner instead of
      // the generic stream-error toast. Checked BEFORE the reattach
      // branch — a gone conversation must never try to resume.
      if (err instanceof ApiError && (err.status === 404 || err.status === 410)) {
        finalizeMessage(set, get, convId, streamingId, "error");
        get().handleConversationGone(convId);
        strandSend(set, trimmed);
        return;
      }
      // Phase 5 — a mid-stream network drop no longer kills the
      // generation server-side (the handler detaches instead of
      // cancelling), so if the assistant row already has its real id
      // (message_start arrived) try to reattach before declaring the
      // turn dead. Clear this send's streaming slot first — resume
      // manages its own single-flight state.
      if (!streamingId.startsWith("tmp-")) {
        set((s) => {
          const aborts = { ...s.abortControllers };
          delete aborts[convId];
          const sids = { ...s.streamingAssistantId };
          delete sids[convId];
          return { abortControllers: aborts, streamingAssistantId: sids };
        });
        await get().resumeStream(convId, streamingId);
        replyCompleted = lastReplyEndedCompleted(get, convId);
        return;
      }
      get().setError(tOutside("error.networkStream"));
      finalizeMessage(set, get, convId, streamingId, "error");
    } finally {
      // FIX122/FIX124 — the turn is over: ask for the name when this is the
      // first turn that was answered. A refused or failed send, or a Stop,
      // asks nothing — the chat keeps the first words the server wrote when
      // it took the first message — and the next answered turn asks.
      if (!answeredBefore && replyCompleted) void nameConversationOnce(set, get, convId);

      // FIX121 — a send that never got a response (network error, abort)
      // must not leave the conversation refusing the next one.
      if (get().sendsOpening[convId]?.requestId === requestId) set((s) => ({ sendsOpening: { ...s.sendsOpening, [convId]: undefined } }));
      if (imageTimeout != null) {
        window.clearTimeout(imageTimeout);
      }
      set((s) => {
        // FIX114 — only this send's own slot: a superseded turn's reader
        // exits after the next turn has taken the slot, and used to delete
        // the live turn's controller (its Stop) on the way out.
        if (s.abortControllers[convId] !== ctrl) return {};
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
      drainQueue(get, convId);
    }
  },

  async cancelGeneration(convId) {
    const ctrl = get().abortControllers[convId];
    const streamingId = get().streamingAssistantId[convId];
    // Optimistic cancel: reflect it in the UI on click, not ~1.5s later when
    // the host confirms and the pump's finally runs. Finalize the in-flight
    // message as cancelled (hides the "Working…" indicator) and clear the
    // streaming slots (flips the composer's Stop back to Send via
    // useIsStreaming). finalizeMessage is idempotent, so the pump's own
    // finalize on abort becomes a harmless no-op.
    if (streamingId) finalizeMessage(set, get, convId, streamingId, "cancelled");
    set((s) => {
      const aborts = { ...s.abortControllers };
      delete aborts[convId];
      const sids = { ...s.streamingAssistantId };
      delete sids[convId];
      return { abortControllers: aborts, streamingAssistantId: sids };
    });
    if (ctrl) ctrl.abort();
    try {
      await api.cancelGeneration(convId);
    } catch {
      /* idempotent; ignore */
    }
  },

  retryStalledStream(convId) {
    // T2 — recovery from a stalled stream. The server generation is
    // (presumed) still running: drop the local connection and go back
    // in through the EXISTING resume machinery, which replays the
    // prefix and reattaches to the live tail. If the stall happened
    // before message_start assigned a real id there is nothing to
    // resume against — aborting alone routes the send path into its
    // normal network-error handling (error card + regenerate).
    const messageId = get().streamingAssistantId[convId];
    const ctrl = get().abortControllers[convId];
    set((s) => ({
      stalledStreams: { ...s.stalledStreams, [convId]: false },
      providerStalledStreams: { ...s.providerStalledStreams, [convId]: false },
    }));
    ctrl?.abort();
    if (!messageId || messageId.startsWith("tmp-")) return;
    // Let the aborted pump release the streaming slot (its finally
    // runs on the microtask that rejects the read), then reattach.
    setTimeout(() => {
      if (!get().streamingAssistantId[convId]) {
        void get().resumeStream(convId, messageId);
      }
    }, 0);
  },

  async resumeStream(convId, messageId) {
    // Single-flight: an attached stream (or another resume) already
    // owns this conversation's streaming slot.
    if (get().streamingAssistantId[convId]) return;
    const ctrl = new AbortController();
    set((s) => ({
      abortControllers: { ...s.abortControllers, [convId]: ctrl },
      streamingAssistantId: { ...s.streamingAssistantId, [convId]: messageId },
    }));

    // Each attempt replays the FULL prefix as content_delta events, so
    // the bubble must be reset first or retries would duplicate text.
    const resetBubble = () =>
      set((s) => {
        const list = s.messages[convId] ?? [];
        return {
          messages: {
            ...s.messages,
            [convId]: list.map((m) =>
              m.id === messageId
                ? {
                    ...m,
                    content: "",
                    reasoning_content: undefined,
                    status: "streaming",
                    isStreaming: true,
                    error_code: undefined,
                    error_message: undefined,
                  }
                : m,
            ),
          },
        };
      });

    const messageState = () =>
      (get().messages[convId] ?? []).find((m) => m.id === messageId);

    // FIX124 R2 — a network drop is not an answer that failed. The server
    // keeps generating when this page goes away (and keeps the finished run
    // for the ledger's 600 s), so giving up after the quick retries — 7 s —
    // turned every drop of 10 s or more into a red "failed" card over an
    // answer that was sitting complete on the server. After the quick retries
    // the row now WAITS: it says the connection was lost, and tries again
    // when the browser says it is back online, when the tab comes back, and
    // every RESUME_WAIT.pollMs — until RESUME_WAIT.capMs, just inside the
    // ledger's retention. Past that the server's row is read once; only if
    // that cannot be reached either does the row stop waiting, and then it
    // offers "load the answer", not a failure. The desktop keeps the quick
    // retries only: there the run lives in the host, across a local bridge.
    const startedAt = Date.now();
    const setReconnecting = (on: boolean) =>
      set((s) => ({
        messages: {
          ...s.messages,
          [convId]: (s.messages[convId] ?? []).map((m) =>
            m.id === messageId && Boolean(m.reconnecting) !== on ? { ...m, reconnecting: on || undefined } : m,
          ),
        },
      }));

    try {
      for (let attempt = 0; ; attempt++) {
        if (attempt < RESUME_RETRY_DELAYS.length) {
          if (RESUME_RETRY_DELAYS[attempt] > 0) {
            await abortableSleep(RESUME_RETRY_DELAYS[attempt], ctrl.signal);
          }
        } else {
          if (HOSTED_MODE || Date.now() - startedAt >= RESUME_WAIT.capMs) break;
          setReconnecting(true);
          await waitForWake(RESUME_WAIT.pollMs, ctrl.signal);
          if (typeof navigator !== "undefined" && navigator.onLine === false) continue;
        }
        // FIX41 — with a cursor the server sends only what was missed, so
        // the bubble must be KEPT. Resetting it here (which is right when
        // the whole prefix is about to be replayed) would erase text the
        // server has no intention of sending again.
        const cursor = streamCursorFor(convId);
        const startingAfter = cursor?.seq ?? 0;
        let res: Response;
        try {
          res = await api.openResumeStream(convId, messageId, ctrl.signal, startingAfter);
        } catch (err) {
          if ((err as Error).name === "AbortError") throw err;
          continue; // network refused — back off and retry
        }
        if (res.status === 409) {
          // RESUME_TOO_OLD: the frames this page is missing have been
          // dropped by the ledger. There is no honest way to continue —
          // stitching the tail onto a stale prefix would show a hole as if
          // it were the answer. Reload the message from the API instead.
          if (await isResumeTooOld(res)) {
            clearStreamCursor(convId);
            await get().loadMessages(convId);
            return;
          }
          continue; // not attachable yet — retry
        }
        if (res.status === 404) {
          // Conversation/message gone (deleted elsewhere). Reload the
          // authoritative list instead of inventing a local state.
          await get().loadMessages(convId);
          return;
        }
        if (!res.ok) continue;
        // FIX124 — the bubble is emptied only once there is a stream to fill
        // it again. It used to be emptied before the request: every refused
        // attempt, and now a wait that can last minutes, showed an empty
        // bubble where the person had half an answer.
        if (startingAfter === 0) resetBubble();
        setReconnecting(false);
        try {
          await consumeStream(res, ctrl.signal, set, get, convId, messageId);
        } catch (err) {
          if ((err as Error).name === "AbortError") throw err;
          continue; // dropped mid-resume — reattach again
        }
        // The server cut this subscriber loose for lagging — the
        // generation itself is fine, so reattach fresh. The cursor is
        // dropped with it: a lagged subscriber cannot know which frames it
        // actually received, so the next attempt replays from the top.
        if (messageState()?.error_code === "RESUME_LAGGED") {
          clearStreamCursor(convId);
          continue;
        }
        return;
      }
      if (HOSTED_MODE) {
        // Every attempt failed — surface the P1 error card.
        get().setError(tOutside("error.resumeLost"));
        finalizeMessage(set, get, convId, messageId, "error");
        return;
      }
      // The wait is over. What the server has is the truth: a finished row
      // replaces this one, a failed row shows its own failure.
      if (await adoptServerRows(set, convId)) return;
      // The server cannot be reached either. Not a failure of the answer —
      // nobody knows yet how it ended.
      awaitReload(set, convId, messageId);
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        finalizeMessage(set, get, convId, messageId, "cancelled");
        return;
      }
      get().setError(tOutside("error.resumeLost"));
      finalizeMessage(set, get, convId, messageId, "error");
    } finally {
      set((s) => {
        const aborts = { ...s.abortControllers };
        if (aborts[convId] === ctrl) delete aborts[convId];
        const sids = { ...s.streamingAssistantId };
        if (sids[convId] === messageId) delete sids[convId];
        return { abortControllers: aborts, streamingAssistantId: sids };
      });
      // Refresh sidebar metadata (updated_at / auto-title) like the
      // primary send path does.
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

  addPendingConsent(convId, consent) {
    const id = get().streamingAssistantId[convId];
    set((s) => {
      const list = s.messages[convId] ?? [];
      // Attach to the streaming assistant row; if none is known yet, attach to
      // the last assistant row (a replayed consent after reload).
      const targetId =
        id ?? [...list].reverse().find((m) => m.role === "assistant")?.id;
      if (!targetId) return {};
      return {
        messages: {
          ...s.messages,
          [convId]: list.map((m) =>
            m.id === targetId ? { ...m, pending_consent: consent } : m,
          ),
        },
      };
    });
  },

  addPendingQuestion(convId, question) {
    const id = get().streamingAssistantId[convId];
    set((s) => {
      const list = s.messages[convId] ?? [];
      const targetId = id ?? [...list].reverse().find((m) => m.role === "assistant")?.id;
      if (!targetId) return {};
      return {
        messages: {
          ...s.messages,
          [convId]: list.map((m) => (m.id === targetId ? { ...m, pending_question: question } : m)),
        },
      };
    });
  },

  resolveQuestion(convId, answer) {
    const row = (get().messages[convId] ?? []).find((m) => m.pending_question?.promptId === answer.promptId);
    set((s) => {
      const list = s.messages[convId] ?? [];
      return {
        messages: {
          ...s.messages,
          [convId]: list.map((m) => (m.pending_question?.promptId === answer.promptId ? { ...m, pending_question: null } : m)),
        },
      };
    });
    if (usingBridgeTransport) {
      // Desktop: the host holds the turn open; the answer rides the bridge.
      bridge.respondQuestion(answer);
      return;
    }
    // FIX74 web: the turn is parked server-side; the answer resumes it as a
    // new stream on the same assistant row.
    if (row) void answerParkedQuestion(set, get, convId, row.id, answer);
  },

  resolveConsent(convId, promptId, decision, scope) {
    bridge.respondConsent(promptId, decision, scope);
    set((s) => {
      const list = s.messages[convId] ?? [];
      return {
        messages: {
          ...s.messages,
          [convId]: list.map((m) =>
            m.pending_consent?.promptId === promptId
              ? { ...m, pending_consent: null }
              : m,
          ),
        },
      };
    });
  },

  hostedResumeProbe(convId) {
    if (!HOSTED_MODE) return;
    // Single-flight: an attached stream already owns this slot.
    if (get().streamingAssistantId[convId]) return;
    void (async () => {
      const ctrl = new AbortController();
      const seqBefore = bridge.peekSeq(convId);
      // Opens the sink and posts ui.ready{conversationId,lastSeq}. Replayed
      // frames buffer in the stream until consumed, and advance the bridge's
      // seq cursor immediately — so we can tell "a turn replayed" from
      // "nothing did" WITHOUT first painting a speculative row.
      let res: Response;
      try {
        res = await api.openResumeStream(convId, "", ctrl.signal);
      } catch {
        return;
      }
      if (!res.ok) return;
      await new Promise((r) => setTimeout(r, HOSTED_RESUME_PROBE_MS));
      if (bridge.peekSeq(convId) <= seqBefore) {
        // Nothing replayed — no live turn. Close the sink, paint nothing.
        ctrl.abort();
        return;
      }
      // FIX114 — the frames that advanced the cursor may be a NEW turn the
      // user just sent (typing right after opening the chat). That turn owns
      // the slot; a probe row beside it was the "Working…" left under a
      // finished answer (measured on e48: one indicator after a normal turn
      // when the send landed inside this 700 ms window).
      if (get().streamingAssistantId[convId]) {
        ctrl.abort();
        return;
      }
      // A turn replayed. Materialize the streaming row and consume the
      // buffered stream from the top (the replayed message_start renames the
      // temp id to the host's assistant id).
      const tempAsstId = tmpId("asst");
      set((s) => ({
        messages: {
          ...s.messages,
          [convId]: [
            ...(s.messages[convId] ?? []),
            {
              id: tempAsstId,
              role: "assistant",
              content: "",
              status: "streaming",
              created_at: new Date().toISOString(),
              isStreaming: true,
            },
          ],
        },
        streamingAssistantId: { ...s.streamingAssistantId, [convId]: tempAsstId },
        abortControllers: { ...s.abortControllers, [convId]: ctrl },
      }));
      try {
        await consumeStream(res, ctrl.signal, set, get, convId, tempAsstId);
      } catch (err) {
        const sid = get().streamingAssistantId[convId] ?? tempAsstId;
        finalizeMessage(set, get, convId, sid, (err as Error).name === "AbortError" ? "cancelled" : "error");
      } finally {
        // FIX42 C4 — release the streaming slot too, exactly as
        // resumeStream's finally does. This probe is the ONLY path that
        // set it and did not clear it: on the desktop, a run replayed
        // from the host's disk ledger after a restart (FIX41 Part F) ends
        // with error/generation_interrupted, the bubble closed correctly,
        // and the composer stayed on "Streaming response…" with the Stop
        // button for good — seen on the installed e19 build.
        set((s) => {
          const aborts = { ...s.abortControllers };
          const mine = aborts[convId] === ctrl;
          if (mine) delete aborts[convId];
          const sids = { ...s.streamingAssistantId };
          // FIX114 — only the probe's own slot; a send that took over keeps its Stop.
          if (mine) delete sids[convId];
          // A probe row that never received a replayed turn (no message_start
          // swapped its temporary id, no content) was never a turn at all:
          // drop it rather than show an empty error or a spinner for it.
          const list = s.messages[convId] ?? [];
          const probeRow = list.find((m) => m.id === tempAsstId);
          const messages = probeRow && probeRow.content.length === 0 ? { ...s.messages, [convId]: list.filter((m) => m.id !== tempAsstId) } : s.messages;
          return { abortControllers: aborts, streamingAssistantId: sids, messages };
        });
      }
    })();
  },

  async regenerate(convId, messageId) {
    const existing = get().messages[convId] ?? [];
    const idx = existing.findIndex((m) => m.id === messageId);
    if (idx === -1) return;
    // FIX124 — Retry of a failed turn is this path too: when it is the first
    // turn to be answered, it is the one that names the chat.
    const answeredBefore = hasAnsweredTurn(get, convId);

    const convModelId =
      get().conversations.find((c) => c.id === convId)?.model_id ?? null;

    // Mark the old one regenerated, insert a fresh placeholder.
    const tempAsstId = tmpId("tmp-regen");
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
        turnScrollRequests: { ...s.turnScrollRequests, [convId]: {
          id: tempAsstId, kind: "regenerate", userKey: "", replyKey: tempAsstId,
        } },
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
      if (!answeredBefore && lastReplyEndedCompleted(get, convId)) void nameConversationOnce(set, get, convId);
    } catch (err) {
      // Same stale-id guard as sendMessage: message_start may have
      // swapped the temp id for the persisted one.
      const streamingId = get().streamingAssistantId[convId] ?? tempAsstId;
      if ((err as Error).name === "AbortError") {
        finalizeMessage(set, get, convId, streamingId, "cancelled");
        return;
      }
      get().setError(tOutside("error.networkRegenerate"));
      finalizeMessage(set, get, convId, streamingId, "error");
    } finally {
      set((s) => {
        const aborts = { ...s.abortControllers };
        delete aborts[convId];
        const sids = { ...s.streamingAssistantId };
        delete sids[convId];
        return { abortControllers: aborts, streamingAssistantId: sids };
      });
      drainQueue(get, convId);
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
    const tempAsstId = tmpId("tmp-edit-asst");

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
        turnScrollRequests: { ...s.turnScrollRequests, [convId]: {
          id: tempAsstId, kind: "send", userKey: list[idx].client_key ?? list[idx].id,
          replyKey: tempAsstId,
        } },
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
      // Same stale-id guard as sendMessage: message_start may have
      // swapped the temp id for the persisted one.
      const streamingId = get().streamingAssistantId[convId] ?? tempAsstId;
      if ((err as Error).name === "AbortError") {
        finalizeMessage(set, get, convId, streamingId, "cancelled");
        return;
      }
      get().setError(tOutside("error.networkEdit"));
      finalizeMessage(set, get, convId, streamingId, "error");
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
    // FIX47.6 Part D — optimistic flip, but honest: on failure the icon
    // reverts to what the server actually holds and a toast says so.
    // Before this, a failed save (e.g. the desktop's host-authored
    // message ids that the server can't resolve) left the thumb lit —
    // a success indicator for a write that never happened.
    const previous = (get().messages[convId] ?? []).find((m) => m.id === messageId)?.feedback ?? null;
    const apply = (value: "thumbs_up" | "thumbs_down" | null) =>
      set((s) => {
        const list = s.messages[convId] ?? [];
        return {
          messages: {
            ...s.messages,
            [convId]: list.map((m) =>
              m.id === messageId ? { ...m, feedback: value } : m,
            ),
          },
        };
      });
    apply(rating);
    try {
      await api.setFeedback(messageId, rating);
    } catch {
      apply(previous);
      toast(tOutside("msg.feedbackFailed"));
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

// Phase 5 — reattach backoff schedule (ms before each attempt). The
// first attempt is immediate; later ones back off lightly so a brief
// network blip reattaches fast without hammering the server.
// Exported ONLY so tests can zero the delays (mutate + restore).
export const RESUME_RETRY_DELAYS = [0, 1000, 2000, 4000];

/** FIX124 R2 — after the quick retries: how often a waiting row tries again
 *  on its own, and for how long. The cap sits just inside chat-edge's run
 *  ledger retention (STREAM_LEDGER_TTL_SECONDS = 600): inside it a resume
 *  replays what was missed; past it the row is simply read from the API.
 *  Mutable so tests can shorten it (the RESUME_RETRY_DELAYS pattern). */
export const RESUME_WAIT = { pollMs: 15_000, capMs: 540_000 };

/** Sleeps up to `ms`, or until the browser reports the network back, or the
 *  tab becomes visible again — whichever is first. */
function waitForWake(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const done = (fn: () => void) => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      if (typeof window !== "undefined") window.removeEventListener("online", onWake);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
      fn();
    };
    const onWake = () => done(resolve);
    const onVisible = () => {
      if (document.visibilityState === "visible") done(resolve);
    };
    const onAbort = () => done(() => reject(new DOMException("Aborted", "AbortError")));
    const timer = setTimeout(onWake, ms);
    signal.addEventListener("abort", onAbort);
    if (typeof window !== "undefined") window.addEventListener("online", onWake);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
  });
}

/** FIX127 C4 — the desktop's auto-resume. The host ended the turn because
 *  the machine went offline (terminal `error`, cause `disconnected`); the
 *  generation is gone with it, so "resume" here means: when the network is
 *  back, retry the turn the way the Retry button does (regenerate), without
 *  the person having to notice. Bounded like the web's wait (RESUME_WAIT:
 *  wake on `online`/visibility, poll every 15 s, give up after 9 min) and to
 *  HOSTED_RETRY_MAX attempts per row, so a network that keeps flapping does
 *  not retry forever. It stands down the moment the row is no longer the
 *  last, failed, disconnected reply, or a turn is already in flight. */
export const HOSTED_RETRY_MAX = 3;
const hostedRetries = new Map<string, number>();
export const hostedRetrySchedule = { waitFor: waitForWake };
async function retryWhenBackOnline(set: Set, get: Get, convId: string, messageId: string): Promise<void> {
  const tries = (hostedRetries.get(messageId) ?? 0) + 1;
  if (tries > HOSTED_RETRY_MAX) return;
  hostedRetries.set(messageId, tries);
  const ctrl = new AbortController();
  const startedAt = Date.now();
  const stillWaiting = () => {
    const list = get().messages[convId] ?? [];
    const last = list[list.length - 1];
    return !!last && last.id === messageId && last.status === "error" && last.terminal_cause === "disconnected" && !get().abortControllers[convId];
  };
  set((s) => ({ messages: { ...s.messages, [convId]: (s.messages[convId] ?? []).map((m) => (m.id === messageId ? { ...m, reconnecting: true } : m)) } }));
  try {
    while (Date.now() - startedAt < RESUME_WAIT.capMs) {
      await hostedRetrySchedule.waitFor(RESUME_WAIT.pollMs, ctrl.signal);
      if (!stillWaiting()) return;
      if (typeof navigator !== "undefined" && navigator.onLine === false) continue;
      set((s) => ({ messages: { ...s.messages, [convId]: (s.messages[convId] ?? []).map((m) => (m.id === messageId ? { ...m, reconnecting: undefined } : m)) } }));
      await get().regenerate(convId, messageId);
      return;
    }
  } finally {
    set((s) => ({ messages: { ...s.messages, [convId]: (s.messages[convId] ?? []).map((m) => (m.id === messageId && m.reconnecting ? { ...m, reconnecting: undefined } : m)) } }));
  }
}

/** Test seam. */
export function _resetHostedRetries(): void {
  hostedRetries.clear();
}

/** Replaces the conversation's rows with the server's. Returns false — and
 *  touches nothing — when the server cannot be reached: loadMessages would
 *  seed an empty thread on failure, which is not what a page that has just
 *  lost its network should show. */
async function adoptServerRows(set: Set, convId: string): Promise<boolean> {
  try {
    const res = await api.listMessages(convId, { limit: 50 });
    if (!res?.messages) return false;
    clearStreamCursor(convId);
    set((s) => ({
      messages: { ...s.messages, [convId]: res.messages },
      hasMoreMessages: { ...s.hasMoreMessages, [convId]: res.has_more ?? false },
    }));
    return true;
  } catch {
    return false;
  }
}

/** The row stops waiting without a verdict: not streaming, not failed —
 *  the bubble offers to load the answer (MessageBubble). */
function awaitReload(set: Set, convId: string, messageId: string): void {
  set((s) => ({
    messages: {
      ...s.messages,
      [convId]: (s.messages[convId] ?? []).map((m) =>
        m.id === messageId ? { ...m, isStreaming: false, is_thinking: false, reconnecting: undefined, awaiting_reload: true } : m,
      ),
    },
  }));
}

// T2 — stream idle watchdog thresholds, in ONE place. The server
// heartbeats every ~15s (chat-edge handler heartbeatInterval), so a
// stream with no BYTES for timeoutMs (≈6 missed beats) is genuinely
// stalled — a long tool run keeps the clock fresh via heartbeats.
// Mutable object so tests can shorten it (RESUME_RETRY_DELAYS pattern).
// FIX28 §2 (stale state): ``deadMs`` — a stream with NO bytes at all (not
// even the server's ": ping" heartbeats) for this long is dead, not slow. It
// is aborted so the turn finalises (the assistant bubble stops "streaming",
// Edit / Regenerate come back) instead of the page needing a reload.
export const STREAM_IDLE = { timeoutMs: 90_000, checkMs: 5_000, deadMs: 180_000 };

// FIX41 — the stream cursor, per conversation (docs/STREAM_CONTRACT.md §9).
//
// It is deliberately NOT in the zustand state: nothing renders it, and
// putting it there would re-render every subscriber on every delta. It
// records the last frame the page actually RENDERED — the pair
// (run_id, seq), because a seq from a previous generation says nothing
// about this one. A reattach sends it as `starting_after`, so the server
// replays only what was missed instead of the whole prefix.
const streamCursors = new Map<string, { runId: string; seq: number }>();

function noteFrame(convId: string, frame: { run_id?: string; seq?: number } | undefined) {
  if (!frame || typeof frame.seq !== "number" || frame.seq <= 0) return;
  const runId = typeof frame.run_id === "string" ? frame.run_id : "";
  const cur = streamCursors.get(convId);
  // A different run restarts the count. Comparing across runs is how a
  // client silently drops an entire answer.
  if (!cur || cur.runId !== runId) {
    streamCursors.set(convId, { runId, seq: frame.seq });
    return;
  }
  if (frame.seq > cur.seq) streamCursors.set(convId, { runId, seq: frame.seq });
}

/** Distinguish the two 409s the resume endpoint can return. RESUME_TOO_OLD
 *  means "reload"; RESUME_UNAVAILABLE means "retry". Treating them the same
 *  would either spin forever or quietly show a truncated answer. */
async function isResumeTooOld(res: Response): Promise<boolean> {
  try {
    const body = (await res.clone().json()) as { error?: { code?: string } };
    return body?.error?.code === "RESUME_TOO_OLD";
  } catch {
    return false;
  }
}

/** Test seam + the resume path's read of the cursor. */
export function streamCursorFor(convId: string): { runId: string; seq: number } | undefined {
  return streamCursors.get(convId);
}
export function clearStreamCursor(convId: string): void {
  streamCursors.delete(convId);
}

// Abortable sleep so a user Stop during the backoff window cancels the
// resume loop immediately instead of after the timer.
function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const t = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      window.clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** FIX74 — resume a parked web turn with the user's answer: POST the answer,
 *  consume the continuation into the SAME assistant row. 409 (another tab
 *  answered first) and 410 (expired) mean the server's row is the truth:
 *  reload it, and follow the continuation if it is streaming. */
async function answerParkedQuestion(set: Set, get: Get, convId: string, messageId: string, answer: QuestionAnswerPayload) {
  if (get().streamingAssistantId[convId]) return; // single-flight, like resumeStream
  const ctrl = new AbortController();
  set((s) => ({
    abortControllers: { ...s.abortControllers, [convId]: ctrl },
    streamingAssistantId: { ...s.streamingAssistantId, [convId]: messageId },
    messages: {
      ...s.messages,
      [convId]: (s.messages[convId] ?? []).map((m) =>
        m.id === messageId ? { ...m, status: "streaming", isStreaming: true, error_code: undefined, error_retryable: undefined, error_message: undefined } : m,
      ),
    },
  }));
  const releaseSlot = () =>
    set((s) => {
      const aborts = { ...s.abortControllers };
      if (aborts[convId] === ctrl) delete aborts[convId];
      const sids = { ...s.streamingAssistantId };
      if (sids[convId] === messageId) delete sids[convId];
      return { abortControllers: aborts, streamingAssistantId: sids };
    });
  try {
    const res = await api.openQuestionAnswerStream(convId, answer.promptId, answer, ctrl.signal);
    if (res.status === 409 || res.status === 410) {
      releaseSlot();
      await get().loadMessages(convId);
      return;
    }
    if (!res.ok) {
      await handleStreamPreError(res, set, get, convId, messageId);
      return;
    }
    await consumeStream(res, ctrl.signal, set, get, convId, messageId);
  } catch (err) {
    if ((err as Error).name === "AbortError") {
      finalizeMessage(set, get, convId, messageId, "cancelled");
      return;
    }
    get().setError(tOutside("error.resumeLost"));
    finalizeMessage(set, get, convId, messageId, "error");
  } finally {
    releaseSlot();
  }
}

/** FIX28 §2 (F3): replace an optimistic message id with the persisted row id
 *  (user row on message_start / message_complete; assistant row on the host's
 *  message_complete). Edit / regenerate / feedback then name a real message
 *  without a reload.
 *
 *  The row keeps rendering under the id it was born with (``client_key``):
 *  a React key that changed with the id unmounted the bubble and mounted
 *  a new one mid-send — the flicker the user saw on every message. */
function swapMessageId(set: Set, convId: string, fromId: string | undefined, toId: string | undefined) {
  if (!fromId || !toId || fromId === toId) return;
  set((s) => {
    const list = s.messages[convId] ?? [];
    if (!list.some((m) => m.id === fromId)) return {};
    // FIX127 C2 — the persisted id is already in the list (the row came
    // from the server transcript and a replay painted it again): one row,
    // the server's — it carries the files a frame-built row never has.
    if (list.some((m) => m.id === toId)) {
      return { messages: { ...s.messages, [convId]: list.filter((m) => m.id !== fromId) } };
    }
    return { messages: { ...s.messages, [convId]: list.map((m) => (m.id === fromId ? renamed(m, toId) : m)) } };
  });
}

/** The same row under its persisted id, still keyed by the id it was born with. */
function renamed(m: Message, toId: string): Message {
  return { ...m, id: toId, client_key: m.client_key ?? m.id };
}

async function consumeStream(
  res: Response,
  signal: AbortSignal,
  set: Set,
  get: Get,
  convId: string,
  tempAsstId: string,
  tempUserId?: string,
) {
  let realAssistantId: string | null = null;
  // FIX114 — whether message_complete / error / cancelled arrived. A stream
  // that closes without one (the host closed the sink, a proxy cut the
  // connection) used to leave the row streaming — a "Working…" nobody
  // would ever clear. It is now finished as an error that says so.
  let sawTerminal = false;
  // FIX33 A-W9 — the assistant row currently marked "organizing…"; cleared by the first content delta.
  let compactingAsst: string | null = null;
  // Wall-clock turn duration for the per-message metadata line
  // ("GPT-4o · 342 tokens · 1.2s"). Stream open → message_complete.
  const streamStartedAt = Date.now();
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

  // T2 — idle watchdog. Bytes (real frames AND ": ping" heartbeats,
  // which parseSSE never yields as events) reset the clock via
  // onActivity; past the threshold the conversation is flagged stalled
  // so the thread can offer "still working — retry?". The flag clears
  // itself the moment data flows again, and always on stream end.
  let lastActivityAt = Date.now();
  const setStalled = (stalled: boolean) =>
    set((s) =>
      Boolean(s.stalledStreams[convId]) === stalled
        ? s
        : { stalledStreams: { ...s.stalledStreams, [convId]: stalled } },
    );
  // Server-declared stall (provider_stall event). Deliberately NOT
  // touched by onActivity: heartbeats are bytes, and a stalled provider
  // still produces them.
  const setProviderStalled = (stalled: boolean) =>
    set((s) =>
      Boolean(s.providerStalledStreams[convId]) === stalled
        ? s
        : {
            providerStalledStreams: {
              ...s.providerStalledStreams,
              [convId]: stalled,
            },
          },
    );
  const onActivity = () => {
    lastActivityAt = Date.now();
    if (get().stalledStreams[convId]) setStalled(false);
  };
  const watchdog = window.setInterval(() => {
    // FIX30 §3.2: a turn waiting for the user's consent is not idle — the
    // host re-says so every heartbeat (bytes), and even without one the card
    // is the user's to answer; the deadman must never answer it for them
    // (it did: consent + 180 s → abort → turn.cancel → tool DENIED, with the
    // card still on the page).
    // FIX73: the same for a question card — only the user can answer it.
    if ((get().messages[convId] ?? []).some((m) => m.isStreaming && (m.pending_consent || m.pending_question))) {
      lastActivityAt = Date.now();
      if (get().stalledStreams[convId]) setStalled(false);
      return;
    }
    const idle = Date.now() - lastActivityAt;
    if (idle > STREAM_IDLE.timeoutMs) setStalled(true);
    if (idle > STREAM_IDLE.deadMs) {
      // FIX28 §2: a wedged turn ends here — abort → the send path finalises
      // the assistant row (cancelled) and the composer/bubbles unfreeze.
      window.clearInterval(watchdog);
      get().abortControllers[convId]?.abort();
    }
  }, STREAM_IDLE.checkMs);

  try {
    for await (const evt of parseSSE(res, signal, onActivity)) {
      // FIX41 — advance the cursor for every frame the page is about to
      // act on. Recorded here, one line above the dispatch, so a new event
      // type cannot be added without it.
      noteFrame(convId, evt.data as { run_id?: string; seq?: number });
      if (evt.event === "message_start") {
        const data = evt.data as SSEMessageStart;
        realAssistantId = data.message_id;
        // FIX28 §2 (F3): the persisted user row id, when the stream names it.
        swapMessageId(set, convId, tempUserId, (data as { user_message_id?: string }).user_message_id);
        // Swap the temp id with the real one so feedback / regenerate
        // can act on the persisted row.
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                // FIX119 — the run id lands on the row the moment the run is
                // announced, so a failure later has it to show.
                m.id === tempAsstId ? { ...renamed(m, data.message_id), ...(data.run_id ? { run_id: data.run_id } : {}) } : m,
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
        if (compactingAsst) {
          // FIX33 A-W9 — the answer is flowing: "organizing…" is over.
          const id = compactingAsst;
          compactingAsst = null;
          set((s) => {
            const list = s.messages[convId] ?? [];
            return { messages: { ...s.messages, [convId]: list.map((m) => (m.id === id ? { ...m, is_compacting: false } : m)) } };
          });
        }
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
      } else if (evt.event === "image_generating") {
        // Image-generation phase marker — swap the typing dots for the
        // image placeholder. Cleared when message_files delivers the
        // attachment, or by message_complete / error if it never does.
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === id ? { ...m, is_generating_image: true } : m,
              ),
            },
          };
        });
      } else if (evt.event === "video_generating") {
        // Video-generation phase marker — swap the typing dots for the
        // video placeholder. Cleared when message_files delivers the
        // attachment, or by message_complete / error if it never does.
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === id ? { ...m, is_generating_video: true } : m,
              ),
            },
          };
        });
      } else if (evt.event === "message_files") {
        const data = evt.data as SSEMessageFiles;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === data.message_id
                  ? {
                      ...m,
                      files: data.files,
                      is_generating_image: false,
                      is_generating_video: false,
                    }
                  : m,
              ),
            },
          };
        });
      } else if (evt.event === "compaction_status") {
        // FIX33 A-W9 — the server (or the desktop host) is summarising
        // older messages before it answers. Show "Organizing the
        // conversation…" in place of the working dots; the first
        // content_delta (or message_complete / error) clears it.
        const id = realAssistantId ?? tempAsstId;
        compactingAsst = id;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return { messages: { ...s.messages, [convId]: list.map((m) => (m.id === id ? { ...m, is_compacting: true } : m)) } };
        });
      } else if (evt.event === "context_budget") {
        // FIX33 A-D5 — the desktop host's live meter numbers (same shape as
        // the web's /context-info). Stored per conversation; the meter
        // renders from it and stays hidden below the show ratio.
        const data = evt.data as ContextInfo;
        if (data && typeof data.used_ratio === "number") get().setContextBudget(convId, data);
      } else if (evt.event === "search_status") {
        // Web-search phase marker — show the searching indicator. It
        // auto-hides once content streams in, and is_searching is
        // cleared by message_sources / message_complete / error.
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === id ? { ...m, is_searching: true } : m,
              ),
            },
          };
        });
      } else if (evt.event === "code_execution") {
        // Phase 8 — sandboxed run_python progress. Upsert by id so the
        // "running" frame and the terminal frame are ONE card. Not
        // rAF-batched: at most a couple of frames per run, and the
        // "running" frame must paint immediately (that is the whole
        // point — the sandbox job can take up to 60s).
        const data = evt.data as SSECodeExecution;
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => {
                if (m.id !== id) return m;
                const runs = m.code_runs ?? [];
                const at = runs.findIndex((r) => r.id === data.id);
                return {
                  ...m,
                  code_runs:
                    at === -1
                      ? [...runs, data]
                      : runs.map((r, i) => (i === at ? data : r)),
                };
              }),
            },
          };
        });
      } else if (evt.event === "question") {
        // FIX74 — the model asked the user (web path). The card lands on the
        // streaming row; any older card in the conversation is gone (one at
        // a time). The row keeps streaming until message_paused below.
        const data = evt.data as { promptId: string; toolCallId?: string; questions: PendingQuestionItem[] };
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === id
                  ? { ...m, pending_question: { promptId: data.promptId, toolCallId: data.toolCallId, questions: data.questions } }
                  : m.pending_question
                    ? { ...m, pending_question: null }
                    : m,
              ),
            },
          };
        });
      } else if (evt.event === "message_paused") {
        // FIX74 — the stream is over but the TURN is not: it waits in the
        // database for the card's answer. The row keeps its card and its
        // text, stops streaming, and is not "completed".
        if (rafHandle != null) {
          cancelRaf(rafHandle);
          rafHandle = null;
        }
        flush();
        const data = evt.data as { message_id?: string };
        const id = realAssistantId ?? tempAsstId;
        swapMessageId(set, convId, id, data?.message_id);
        const finalId = data?.message_id ?? id;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === finalId
                  ? { ...m, status: "waiting_user", isStreaming: false, is_generating_image: false, is_generating_video: false, is_searching: false, is_compacting: false, agent_status: null, agent_wait: null }
                  : m,
              ),
            },
          };
        });
        clearStreamCursor(convId);
      } else if (evt.event === "tool_activity") {
        // A2 — generic activity for tools WITHOUT a dedicated
        // indicator (memory/state/past-search/canvas + unknown/MCP
        // tools). Upsert by tool-call id mirroring code_execution;
        // not rAF-batched for the same reason (two frames per call,
        // the "started" frame must paint immediately). Ephemeral —
        // also cleared at message_complete so no stale "started" row
        // survives an error path that skipped the finished frame.
        const data = evt.data as SSEToolActivity;
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => {
                if (m.id !== id) return m;
                const acts = m.tool_activities ?? [];
                const at = acts.findIndex((a) => a.id === data.id);
                return {
                  ...m,
                  // FIX30 §3.2: a finished tool ends its "running for N s" notice.
                  agent_wait: data.phase === "finished" && m.agent_wait?.kind === "tool" ? null : m.agent_wait,
                  tool_activities:
                    at === -1
                      ? [...acts, data]
                      : // MERGE, not replace: the rich "started" frame carries
                        // argsSummary; the "finished" frame carries the result.
                        // Merging keeps both so the card has arg + result.
                        acts.map((a, i) => (i === at ? { ...a, ...data } : a)),
                };
              }),
            },
          };
        });
      } else if (evt.event === "tool_output") {
        // Desktop shell (Phase 4) — a live stdout/stderr chunk for a running
        // tool (run_command). Append to that activity's output buffer.
        const data = evt.data as { id: string; chunk: string };
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => {
                if (m.id !== id) return m;
                const acts = m.tool_activities ?? [];
                const at = acts.findIndex((a) => a.id === data.id);
                if (at === -1) return m;
                return {
                  ...m,
                  tool_activities: acts.map((a, i) =>
                    i === at ? { ...a, output: (a.output ?? "") + data.chunk } : a,
                  ),
                };
              }),
            },
          };
        });
      } else if (evt.event === "plan_update") {
        // Desktop shell (Phase 4) — the agent's live plan checklist.
        const data = evt.data as { steps: PlanStep[] };
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => (m.id === id ? { ...m, plan: data.steps } : m)),
            },
          };
        });
      } else if (evt.event === "paused") {
        // Desktop shell (Phase 4) — the turn paused at a budget checkpoint.
        const data = evt.data as { reason: string };
        const id = realAssistantId ?? tempAsstId;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => (m.id === id ? { ...m, paused: data } : m)),
            },
          };
        });
      } else if (evt.event === "status") {
        // Desktop shell (Phase 4) — the phase marker. FIX30 §3.2: the host's
        // payload is `{ status, waiting?, tool?, promptId?, elapsedMs? }`
        // (`text` was never what it sent — the marker was dead until now).
        // `waiting` names what the turn is blocked on: "consent" (the card)
        // or "tool" (a long local tool, re-sent every heartbeat); null or
        // absent clears it (the next model step, or a consent answered).
        const data = evt.data as { text?: string; status?: string; waiting?: string | null; tool?: string; promptId?: string; elapsedMs?: number };
        const id = realAssistantId ?? tempAsstId;
        const wait: Message["agent_wait"] =
          data.waiting === "consent" || data.waiting === "tool" || data.waiting === "question"
            ? { kind: data.waiting, tool: data.tool ?? "", promptId: data.promptId, elapsedMs: data.elapsedMs ?? 0 }
            : null;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => (m.id === id ? { ...m, agent_status: data.text ?? data.status ?? null, agent_wait: wait } : m)),
            },
          };
        });
      } else if (evt.event === "message_sources") {
        const data = evt.data as SSEMessageSources;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === data.message_id
                  ? { ...m, search_sources: data.sources, is_searching: false }
                  : m,
              ),
            },
          };
        });
      } else if (evt.event === "message_complete") {
        sawTerminal = true;
        // Flush any buffered tail BEFORE writing the terminal state
        // so the final content paint includes every token.
        if (rafHandle != null) {
          cancelRaf(rafHandle);
          rafHandle = null;
        }
        flush();
        const data = evt.data as SSEMessageComplete;
        const id = realAssistantId ?? tempAsstId;
        // FIX28 §2 (F3): the desktop host imports both rows AFTER the turn and
        // names them here; the browser path named the user row on message_start.
        swapMessageId(set, convId, tempUserId, (data as { user_message_id?: string }).user_message_id);
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === id
                  ? {
                      ...m,
                      status:
                        m.status === "cancelled" || m.status === "error"
                          ? m.status
                          : "completed",
                      isStreaming: false,
                      is_generating_image: false,
                      is_generating_video: false,
                      is_searching: false,
                      is_compacting: false,
                      // FIX50 P1-D — BOTH modes keep the tool cards after the
                      // turn (the desktop shell always did, like the WPF app;
                      // web used to clear them, so a correct tool-computed
                      // answer read as a bare number the moment the turn
                      // finished — FIX48 §2.4). Cards auto-fold to their
                      // header 1.2 s after finishing; still session-only, a
                      // reload drops them (persistence needs a schema change
                      // — recorded as an open decision).
                      tool_activities: m.tool_activities,
                      agent_status: null,
                      agent_wait: null,
                      pending_consent: null,
                      pending_question: null,
                      token_usage: data.token_usage ?? null,
                      // FIX35 Part B — the model that actually answered
                      // (an Auto turn names the router's pick).
                      model_id: data.model_id ?? m.model_id,
                      // FIX119 — why the model stopped; the bubble tells the person
                      // when it is max_tokens / refusal / content_filter.
                      ...(isStopReason(data.stop_reason) ? { stop_reason: data.stop_reason } : {}),
                      // FIX124 — the turn stopped at the tool-round cap unanswered.
                      ...(typeof data.tool_rounds === "number" && data.tool_rounds > 0 ? { tool_rounds: data.tool_rounds } : {}),
                      ...(typeof data.run_id === "string" && data.run_id ? { run_id: data.run_id } : {}),
                      // FIX120 — the requested model of a rescued turn, as the
                      // row will carry it; the bubble derives the notice from
                      // it after a reload exactly as from the live frame.
                      ...(typeof data.fallback_from === "string" && data.fallback_from
                        ? { fallback_from: data.fallback_from }
                        : {}),
                      response_time_ms: Date.now() - streamStartedAt,
                    }
                  : m,
              ),
            },
          };
        });
        // FIX28 §2: the host's persisted assistant row id (the page rendered the
        // turn under the host's client id) — regenerate / feedback need the row.
        swapMessageId(set, convId, id, (data as { assistant_message_id?: string }).assistant_message_id);
        // Refresh the credit badge — this turn just spent credits.
        bumpUsageVersion();
        // Phase 8 — the turn may have called update_canvas_artifact. If
        // the panel is open on a persisted artifact, re-read its lineage
        // so the model's new version replaces what's on screen (the
        // store keeps unsaved user edits; see loadVersions).
        useCanvasStore.getState().refreshIfOpen();
        // FIX41 — the run is over; its cursor cannot resume anything.
        clearStreamCursor(convId);
      } else if (evt.event === "fallback_info") {
        // B2 — a fallback model answered. Stash the requested/actual
        // model pair on the message so MessageBubble can show a small
        // notice under the reply ("answered by X — Y unavailable").
        const data = evt.data as SSEFallbackInfo;
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
                      fallback_info: {
                        requested_model: data.requested_model,
                        actual_model: data.actual_model,
                        reason: data.reason,
                      },
                    }
                  : m,
              ),
            },
          };
        });
      } else if (evt.event === "dlp_notice") {
        // FIX50 P1-C — the question was partially masked before the
        // model read it. Stash counts on the assistant message so
        // MessageBubble shows a quiet expandable notice — the user
        // learns the question was altered, never the hidden values.
        const data = evt.data as {
          findings?: number;
          by_detector?: Record<string, number>;
        };
        if (typeof data?.findings === "number" && data.findings > 0) {
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
                        dlp_notice: {
                          findings: data.findings as number,
                          by_detector: data.by_detector ?? {},
                        },
                      }
                    : m,
                ),
              },
            };
          });
        }
      } else if (evt.event === "model_resolved") {
        // FIX35 Part B — the gateway named the model that is answering
        // (before content on an Auto route). Attribute the reply to it now
        // so the meta line under the reply can say which model Auto got.
        const data = evt.data as { model?: string };
        const id = realAssistantId ?? tempAsstId;
        if (data.model) {
          const served = data.model;
          set((s) => {
            const list = s.messages[convId] ?? [];
            return {
              messages: {
                ...s.messages,
                [convId]: list.map((m) => (m.id === id ? { ...m, model_id: served } : m)),
              },
            };
          });
        }
      } else if (evt.event === "provider_stall") {
        // chat-edge holds the upstream connection, so it is the only
        // layer that can tell provider silence from server work. Trust
        // its verdict directly instead of guessing from byte timing.
        const data = evt.data as SSEProviderStall;
        setProviderStalled(Boolean(data?.stalled));
      } else if (evt.event === "reasoning_notice") {
        // The gateway answered without the requested reasoning (the
        // resolved model can't reason). Stash on the message so
        // MessageBubble shows a quiet informational line — same
        // surface as fallback_info, never an error.
        const data = evt.data as SSEReasoningNotice;
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
                      reasoning_notice: {
                        model: data.model,
                        reason: data.reason,
                      },
                    }
                  : m,
              ),
            },
          };
        });
      } else if (evt.event === "compaction_notice") {
        // FIX32 §1.2 — the desktop host trimmed the sent context for length.
        // Stash the numbers on the message so MessageBubble shows the same
        // kind of quiet marker web-chat's compacted_history source shows —
        // the user learns the assistant no longer sees the middle of the
        // conversation, instead of it silently getting worse.
        // FIX102 Part C — chat-edge sends the same event when this turn left
        // older messages out WITHOUT a summary; `summary` says which.
        const data = evt.data as { dropped?: number; sent?: number; total?: number; summary?: string };
        if (typeof data?.dropped === "number" && data.dropped > 0) {
          const id = realAssistantId ?? tempAsstId;
          set((s) => {
            const list = s.messages[convId] ?? [];
            return {
              messages: {
                ...s.messages,
                [convId]: list.map((m) =>
                  m.id === id ? { ...m, compaction_notice: { dropped: data.dropped!, sent: data.sent, total: data.total, summary: data.summary } } : m,
                ),
              },
            };
          });
        }
      } else if (evt.event === "memory_used") {
        // FIX130 A3a — saved memories went into this answer's prompt. Sent
        // once, before the first word (chat-edge, or the desktop host over
        // the bridge), and replayed from the ledger on a reattach, so the
        // "used N memories" chip is on the answer while it streams.
        const used = parseMemoryUsed(evt.data);
        if (used) {
          const id = realAssistantId ?? tempAsstId;
          set((s) => {
            const list = s.messages[convId] ?? [];
            return { messages: { ...s.messages, [convId]: list.map((m) => (m.id === id ? { ...m, memory_used: used } : m)) } };
          });
        }
      } else if (evt.event === "memory_saved") {
        // FIX130 A3b — a save_memory call in this turn stored a memory
        // (active) or queued a suggestion (pending_confirmation). A turn can
        // save more than once; a replayed frame updates in place.
        const id = realAssistantId ?? tempAsstId;
        let pending = false;
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) => {
                if (m.id !== id) return m;
                const merged = mergeMemorySaved(m.memory_saved, evt.data);
                if (!merged || merged === m.memory_saved) return m;
                pending = merged.some((x) => x.status === "pending_confirmation");
                return { ...m, memory_saved: merged };
              }),
            },
          };
        });
        // A new suggestion waits on the Memory page: the sidebar badge and
        // the header dot should say so now, not at the next 2-minute poll.
        if (pending) void useMemoryPendingStore.getState().refresh();
      } else if (evt.event === "image_notice") {
        // One or more attached images couldn't be read (unreachable,
        // resize failure, unsupported format). Stash the filenames on
        // the message so MessageBubble shows a quiet informational line
        // — the person learns their attachment didn't attach instead of
        // the turn silently degrading to text. Never an error.
        const data = evt.data as SSEImageNotice;
        const filenames = (data?.failures ?? [])
          .map((f) => f?.filename)
          .filter((n): n is string => Boolean(n));
        if (filenames.length > 0) {
          const id = realAssistantId ?? tempAsstId;
          set((s) => {
            const list = s.messages[convId] ?? [];
            return {
              messages: {
                ...s.messages,
                [convId]: list.map((m) =>
                  m.id === id ? { ...m, image_notice: { filenames } } : m,
                ),
              },
            };
          });
        }
      } else if (evt.event === "cancelled") {
        sawTerminal = true;
        // FIX41 — the run was stopped by a person (this tab, another tab,
        // or the desktop host). Everything produced so far stands; the
        // answer is simply partial. Before this event existed, a viewer
        // that had not pressed Stop saw an internal-error frame followed
        // by a success terminator and had no way to tell what happened.
        if (rafHandle != null) {
          cancelRaf(rafHandle);
          rafHandle = null;
        }
        flush();
        const data = evt.data as { message_id?: string };
        const id = realAssistantId ?? tempAsstId;
        swapMessageId(set, convId, id, data?.message_id);
        finalizeMessage(set, get, convId, data?.message_id ?? id, "cancelled");
        clearStreamCursor(convId);
      } else if (evt.event === "error") {
        sawTerminal = true;
        // B1 — terminal stream error. Attach the code + localized
        // message + per-model attempts to the message so the bubble
        // renders a styled error (red) instead of a blank reply.
        if (rafHandle != null) {
          cancelRaf(rafHandle);
          rafHandle = null;
        }
        flush();
        const data = evt.data as SSEError;
        const id = realAssistantId ?? tempAsstId;
        if (data?.code === "TOO_MANY_FILES") {
          // FIX126 F1 — the same refusal as the 422, arriving as a frame
          // (hosted turns): withdraw this send the same way. The user row
          // is the one just before this reply (its id may have been swapped
          // to the server's by message_start).
          const list = get().messages[convId] ?? [];
          const at = list.findIndex((m) => m.id === id);
          const userRow = at > 0 && list[at - 1].role === "user" ? list[at - 1] : undefined;
          withdrawRefusedSend(set, convId, userRow?.id ?? tempUserId, id, userRow?.content, true);
          clearStreamCursor(convId);
          get().setError(tooManyFilesNotice(data?.message));
          continue;
        }
        const localized = localizeErrorCode(data?.code, data?.message, data?.retryable);
        set((s) => {
          const list = s.messages[convId] ?? [];
          return {
            messages: {
              ...s.messages,
              [convId]: list.map((m) =>
                m.id === id
                  ? {
                      ...m,
                      error_code: data?.code,
                      error_retryable: data?.retryable,
                      error_message: localized,
                      error_attempts: data?.attempts,
                      terminal_cause: typeof data?.cause === "string" ? data.cause : "error",
                      ...(typeof data?.run_id === "string" && data.run_id ? { run_id: data.run_id } : {}),
                      is_generating_image: false,
                      is_generating_video: false,
                      is_searching: false,
                      is_compacting: false,
                    }
                  : m,
              ),
            },
          };
        });
        finalizeMessage(set, get, convId, id, "error");
        get().setError(localized);
        // FIX127 C4 — on the desktop a lost network ends the run in the host
        // (there is no server-side run to reconnect to, unlike the web's
        // resumeStream). The row waits for the network and retries itself.
        if (HOSTED_MODE && data?.cause === "disconnected") void retryWhenBackOnline(set, get, convId, id);
      }
    }
    if (!sawTerminal && !signal.aborted) {
      // FIX114 — the stream ended and nothing said how. If the row is still
      // streaming (a superseded turn was already retired by sendMessage and
      // is not), end it as an error rather than leave it spinning.
      flush();
      const id = realAssistantId ?? tempAsstId;
      const row = (get().messages[convId] ?? []).find((m) => m.id === id);
      if (row?.isStreaming) {
        // FIX118 — the same code chat-edge and the desktop host mint for
        // this ending (stream_interrupted / eof_without_terminal), so the
        // sentence is the catalogue's, not a page-private one, and the
        // Regenerate gate reads a code every mapper knows.
        const localized = localizeErrorCode(EOF_WITHOUT_TERMINAL_CODE, undefined, true);
        set((s) => {
          const list = s.messages[convId] ?? [];
          return { messages: { ...s.messages, [convId]: list.map((m) => (m.id === id ? { ...m, error_code: EOF_WITHOUT_TERMINAL_CODE, error_retryable: true, error_message: localized, terminal_cause: EOF_WITHOUT_TERMINAL_CAUSE } : m)) } };
        });
        finalizeMessage(set, get, convId, id, "error");
        get().setError(localized);
      }
    }
  } finally {
    // T2 — the watchdog dies with its stream; a finished/aborted turn
    // is never "stalled".
    window.clearInterval(watchdog);
    setStalled(false);
    setProviderStalled(false);
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

/** FIX127 D1 — a turn just ended: send the next queued message, one per
 *  finished turn. Nothing is sent while a turn is still in flight (M5), and
 *  a Stop by the person leaves the queue waiting — it shows above the
 *  composer with its own "send now"; only a finished or failed answer
 *  releases the next message on its own. */
function drainQueue(get: Get, convId: string): void {
  const s = get();
  if (s.abortControllers[convId] || s.streamingAssistantId[convId]) return;
  const head = (s.queuedMessages[convId] ?? [])[0];
  if (!head) return;
  const list = s.messages[convId] ?? [];
  const last = list[list.length - 1];
  if (last?.role === "assistant" && last.status === "cancelled") return;
  s.removeQueuedMessage(convId, head.id);
  void s.sendMessage(convId, head.text, head.fileIds);
}

/** FIX127 A3 — the text of a send refused because its conversation was
 *  deleted stays with the user: it becomes the new-chat draft (the page is
 *  about to land on /chat) and the banner offers to send it there. Until
 *  FIX127 the 404 path dropped the outbox entry and the composer was empty. */
function strandSend(set: Set, text: string): void {
  const t = text.trim();
  if (!t) return;
  saveDraft(NEW_CHAT_DRAFT_KEY, t);
  set({
    strandedSend: { text: t },
    errorBanner: tOutside("error.conversationDeletedKeptText"),
  });
}

async function handleStreamPreError(
  res: Response,
  set: Set,
  get: Get,
  convId: string,
  tempAsstId: string,
  /** The send path's optimistic user row — only a plain send passes it. */
  tempUserId?: string,
  /** The text of that send — only a plain send passes it. */
  sentText?: string,
) {
  const text = await res.text();
  let msg = res.statusText;
  let code: string | undefined;
  try {
    const env = JSON.parse(text) as {
      error?: { code?: string; message?: string };
    };
    msg = env.error?.message ?? msg;
    code = env.error?.code;
  } catch {
    /* ignore */
  }
  if (res.status === 404 || res.status === 410) {
    // The conversation was deleted elsewhere (desktop propagation /
    // another tab): finalize the optimistic rows, then drop the
    // conversation and surface the specific "deleted" banner instead
    // of echoing the server's raw 404 envelope message.
    finalizeMessage(set, get, convId, tempAsstId, "error");
    get().handleConversationGone(convId);
    if (sentText) strandSend(set, sentText);
    return;
  }
  if (code === "TOO_MANY_FILES" && tempUserId) {
    // FIX126 F1 — the server refused a message with more files than it
    // takes (422). Nothing was sent: same shape as CONVERSATION_BUSY below —
    // this send's rows go, the text is back in the composer, and a localized
    // notice says what to do.
    withdrawRefusedSend(set, convId, tempUserId, tempAsstId, sentText);
    get().setError(tooManyFilesNotice(msg));
    return;
  }
  if (res.status === 409 && code === "CONVERSATION_BUSY" && tempUserId) {
    // FIX125 M5 — another tab (or window) already has a turn in flight in
    // this conversation, and the server refused this send. Nothing was
    // sent: the text is already back in the composer (the outbox give-back
    // ran before this), so this send's optimistic user + assistant rows go
    // away — no error bubble, no duplicate question — and a notice says
    // why and what to do.
    set((s) => {
      const list = s.messages[convId] ?? [];
      const turnScroll = s.turnScrollRequests[convId];
      return {
        messages: { ...s.messages, [convId]: list.filter((m) => m.id !== tempUserId && m.id !== tempAsstId) },
        turnScrollRequests:
          turnScroll && turnScroll.id === tempAsstId
            ? { ...s.turnScrollRequests, [convId]: undefined }
            : s.turnScrollRequests,
      };
    });
    // The outbox holds one entry per conversation and every tab of it shares
    // that storage, so the other tab's send may already have replaced this
    // one's entry — the give-back above then finds nothing. Hand this send's
    // own text back directly. (No reload here: loadMessages' outbox recovery
    // would hand the OTHER tab's still-pending text to this composer.)
    if (sentText) set((s) => ({ returnedDrafts: { ...s.returnedDrafts, [convId]: { id: crypto.randomUUID(), text: sentText } } }));
    get().setError(tOutside("error.conversationBusy"));
    return;
  }
  if (res.status === 402 || code === "INSUFFICIENT_CREDITS") {
    // Credit balance exhausted — chat-edge already localized the
    // message; localizeErrorCode keeps a fallback if it didn't.
    get().setError(localizeErrorCode(code ?? "INSUFFICIENT_CREDITS", msg));
  } else if (res.status === 429) {
    get().setError(tOutside("error.tooManyMessages"));
  } else if (res.status === 502) {
    get().setError(tOutside("error.serviceUnavailable"));
  } else if (code === "CONVERSATION_BUSY") {
    get().setError(tOutside("error.conversationBusy"));
  } else if (res.status !== 401) {
    get().setError(msg);
  }
  finalizeMessage(set, get, convId, tempAsstId, "error");
}

/** FIX126 F1 — take back a send the server refused before answering: its
 *  optimistic user + assistant rows go (no error bubble, no duplicate
 *  question) and its text is handed back to the composer. `saveToDraft` is
 *  for the error-frame path, where the outbox entry was already dropped
 *  (the server had accepted the request) so nothing else saves it. */
function withdrawRefusedSend(
  set: Set,
  convId: string,
  userId: string | undefined,
  asstId: string,
  text: string | undefined,
  saveToDraft = false,
): void {
  set((s) => {
    const list = s.messages[convId] ?? [];
    const turnScroll = s.turnScrollRequests[convId];
    // The send's own finally releases its streaming slot.
    return {
      messages: { ...s.messages, [convId]: list.filter((m) => m.id !== userId && m.id !== asstId) },
      turnScrollRequests:
        turnScroll && turnScroll.id === asstId
          ? { ...s.turnScrollRequests, [convId]: undefined }
          : s.turnScrollRequests,
    };
  });
  if (!text) return;
  if (saveToDraft && loadDraft(convId) === null) saveDraft(convId, text);
  set((s) => ({ returnedDrafts: { ...s.returnedDrafts, [convId]: { id: crypto.randomUUID(), text } } }));
}

/** The localized TOO_MANY_FILES notice. The count comes from the server's
 *  sentence ("… (this message has N)") when it carries one. */
function tooManyFilesNotice(serverMessage?: string): string {
  const n = /has (\d+)/.exec(serverMessage ?? "")?.[1];
  return n
    ? tOutside("error.tooManyFiles", { max: "5", n })
    : tOutside("error.tooManyFilesNoCount", { max: "5" });
}

// ─── Pure helpers used by selectors ───────────────────────────

/** FIX122 Part C — a send that failed before the server took it: its text
 *  leaves the outbox for the conversation's draft, and the mounted composer
 *  is told. No-op when the entry is gone (the send was accepted) or belongs
 *  to another send. */
function giveBackToComposer(set: Set, convId: string, requestId?: string): void {
  const text = outboxGiveBack(convId, requestId);
  if (text === null) return;
  set((s) => ({ returnedDrafts: { ...s.returnedDrafts, [convId]: { id: crypto.randomUUID(), text } } }));
}

/** FIX122 — a chat is named once, by the server, after its first turn.
 *  The server decides everything (which model — the one that answered —
 *  whether at all, and the first words of the message when the model has
 *  nothing); this only asks and shows the answer. It used to rename the
 *  chat ITSELF when the request failed, over whatever the person had typed
 *  in the meantime. Now a failure changes nothing, and the answer — the
 *  title the conversation carries on the server, which is the person's own
 *  if they had already renamed it — is shown unless they renamed it HERE
 *  while the request was out (the server refuses that write too; this only
 *  keeps the screen from contradicting it for a moment).
 *  A temporary chat is not sent for a name at all. */
const userRenames = new Map<string, number>();
/** The turn's reply is the last assistant row — found by role, not id: the
 *  desktop host renames the row to the server's id at message_complete,
 *  after the stream slot was keyed. */
function lastReplyEndedCompleted(get: Get, convId: string): boolean {
  return (get().messages[convId] ?? []).filter((m) => m.role === "assistant").at(-1)?.status === "completed";
}
/** Whether any turn of this conversation has been answered yet (a completed
 *  assistant row with text). Read BEFORE a send adds its rows. */
function hasAnsweredTurn(get: Get, convId: string): boolean {
  return (get().messages[convId] ?? []).some((m) => m.role === "assistant" && m.status === "completed" && m.content.trim() !== "");
}
/** FIX124 — the sidebar never shows "New conversation" for a chat that has a
 *  message: the first words stand in at once, locally, the same cut the
 *  server makes (first 100 characters) when it takes the message. No request:
 *  the turn-end refresh brings the server's title. */
const DEFAULT_TITLE = "New conversation";
export function placeholderTitle(text: string): string {
  const chars = Array.from(text.trim());
  return chars.length <= 100 ? chars.join("") : `${chars.slice(0, 100).join("")}…`;
}
function showPlaceholderTitle(set: Set, get: Get, convId: string, text: string): void {
  const conv = get().conversations.find((c) => c.id === convId);
  const title = placeholderTitle(text);
  if (!conv || conv.title !== DEFAULT_TITLE || !title) return;
  set((s) => ({ conversations: s.conversations.map((c) => (c.id === convId && c.title === DEFAULT_TITLE ? { ...c, title } : c)) }));
}
async function nameConversationOnce(set: Set, get: Get, convId: string): Promise<void> {
  const conv = get().conversations.find((c) => c.id === convId);
  // FIX127 B1 — only a chat STARTED as temporary goes unnamed; memory off on
  // an ordinary chat still gets its title (R3 read memory-off as temporary).
  if (!conv || conv.is_temporary === true) return;
  // FIX124 — a title somebody chose is not sent for a name at all (the server
  // would refuse it; this saves the request). Recognised the way the server
  // does: anything but the creation placeholder or the first message's first
  // words. With older rows still unloaded the first message is unknown here,
  // and the server decides.
  const firstUser = (get().messages[convId] ?? []).find((m) => m.role === "user");
  const machineTitle = conv.title === DEFAULT_TITLE || (firstUser !== undefined && conv.title === placeholderTitle(firstUser.content));
  if (!machineTitle && !get().hasMoreMessages[convId]) return;
  const renamesWhenAsked = userRenames.get(convId) ?? 0;
  try {
    const res = await api.generateTitle(convId);
    if (!res?.title || (userRenames.get(convId) ?? 0) !== renamesWhenAsked) return;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === convId ? { ...c, title: res.title } : c,
      ),
    }));
  } catch {
    /* the title it has stands */
  }
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
          // FIX30 §3.2: a turn that ended (completed / cancelled / error) has
          // nothing left to wait on. A card left behind after Stop was being
          // answered into a prompt the host had already expired ("consent.respond
          // for unknown/expired prompt — ignored").
          // FIX114 — and no tool of it can still be running: a row the host
          // never finished is closed as failed, so the card stops spinning
          // and says the call did not complete.
          m.id === messageId
            ? {
                ...m,
                status,
                isStreaming: false,
                // FIX116 — a finished row is never still thinking. The
                // header reads "Thinking…" straight off this flag; a
                // turn cut short (Stop, error, superseded) before the
                // host's analysis_status: completed kept it on for good.
                // The reasoning text it received stays.
                is_thinking: false,
                pending_consent: null,
                pending_question: null,
                agent_wait: null,
                tool_activities:
                  status === "completed"
                    ? m.tool_activities
                    : m.tool_activities?.map((a) => (a.phase === "started" ? { ...a, phase: "finished" as const, success: false } : a)),
              }
            : m,
        ),
      },
    };
  });
}
