// Fetch wrapper that:
//   • sends the HttpOnly Portal session cookies (credentials:"include")
//   • on 401, silently refreshes via the gateway then retries ONCE
//   • redirects to the Portal /login only when refresh itself fails
//
// HttpOnly cookie migration: there is no token in JS. chat-edge reads
// the ``portal_access_token`` cookie; the gateway rotates it. A single
// transient 401 no longer logs the user out — we refresh and retry.

import type {
  ContextInfo,
  Conversation,
  RoutingTier,
  ConversationListResponse,
  ErrorEnvelope,
  FeedbackResponse,
  FileAttachment,
  FileListResponse,
  KnowledgeBase,
  KnowledgeBaseDetailResponse,
  KnowledgeAvailability,
  KnowledgeBaseListResponse,
  KnowledgeDocument,
  KnowledgeSearchResponse,
  AudioGenInput,
  MediaGenSubmitResponse,
  MediaJobStatus,
  Memory,
  MemoryEventListResponse,
  MemoryListResponse,
  Message,
  MessageListResponse,
  ModelListResponse,
  CanvasArtifactRow,
  CloudTask,
  CloudTaskRun,
  Project,
  SearchResponse,
  UsageResponse,
  VideoGenInput,
  ShareExpiry,
  ShareLink,
  UserPreferences,
} from "./types";
import type { PreviewData } from "./previewData";
import { redirectToPortalLogin, refreshSession, type RefreshOutcome } from "./auth";
import { bridge, usingBridgeTransport } from "./bridge";

const API_BASE =
  typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL
    ? process.env.NEXT_PUBLIC_API_URL
    : "http://localhost:4000/api/v1";

/** FIX129 B2 — the chat-edge base for the session heartbeat (auth-refresh.ts). */
export const CHAT_API_BASE = API_BASE;

// Ceiling for a file upload. Shared by both upload paths, which now
// differ in what they wait for: a KB document is still extracted inside
// the request (OCR for a scanned PDF, up to the server's 240 s wall),
// while a conversation-file upload returns as soon as the bytes land and
// extracts in the background. Sized for the slower of the two, and
// finite either way so a stuck request eventually surfaces as an error
// instead of disabling the composer forever.
const UPLOAD_TIMEOUT_MS = 300_000;

// Single-flight refresh shared across all concurrent 401s (parallel
// page-mount fetches + an in-flight stream must not stampede the gateway;
// the backend has a 30s rotation grace window too, but coalescing keeps
// it clean and avoids redundant rotations).
let refreshInFlight: Promise<RefreshOutcome> | null = null;
export function sharedRefresh(): Promise<RefreshOutcome> {
  if (!refreshInFlight) {
    refreshInFlight = refreshSession().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

// FIX121 C3 — may the page send X-Request-Id?
//
// A custom request header on a cross-origin call is preflighted, and a
// header the server does not list in Access-Control-Allow-Headers does not
// degrade the request: the browser refuses to send it AT ALL. A chat-edge
// older than FIX121 does not list X-Request-Id, so sending it blindly would
// turn every send into a network error the moment this page is deployed
// ahead of the server. The page therefore asks once: a GET of the server's
// health endpoint carrying the header. It succeeds ⇒ the header is allowed
// (or the call is same-origin, where no preflight exists). It fails ⇒ the
// page sends exactly what it sent before FIX121, no key, no protection, no
// outage. One request per page load, its answer shared by every send.
let requestIdProbe: Promise<boolean> | null = null;
export function requestIdAllowed(): Promise<boolean> {
  if (requestIdProbe) return requestIdProbe;
  requestIdProbe = (async () => {
    try {
      const health = new URL("/health", new URL(API_BASE, typeof window !== "undefined" ? window.location.href : "http://localhost")).toString();
      const res = await fetch(health, { method: "GET", headers: { "X-Request-Id": "capability-probe-0001" }, signal: AbortSignal.timeout(2000) });
      return res.ok;
    } catch {
      return false;
    }
  })();
  return requestIdProbe;
}
/** Test seam: forget the probe's answer. */
export function resetRequestIdProbeForTest(): void {
  requestIdProbe = null;
}

class ApiClient {
  // -----------------------------------------------------------
  // Low-level fetch. Cookie-authenticated, with 401 → refresh → retry.
  // -----------------------------------------------------------
  async raw(path: string, init: RequestInit = {}, _retried = false): Promise<Response> {
    const headers = new Headers(init.headers);
    if (!headers.has("Content-Type") && init.body) {
      headers.set("Content-Type", "application/json");
    }
    const res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      // Send the HttpOnly portal_access_token cookie cross-origin to
      // chat-edge. Host-only on localhost (shared across ports) /
      // shared parent domain in prod; SameSite=Lax permits same-site
      // sends. This also covers SSE streams (which open via raw()) —
      // EventSource can't set headers, so cookie auth is the only way.
      credentials: "include",
    });
    if (res.status === 401 && !_retried) {
      // Try a silent refresh before giving up — a single transient 401
      // (access token just lapsed) must NOT log the user out.
      const outcome = await sharedRefresh();
      if (outcome === "ok") {
        return this.raw(path, init, true);
      }
      // FIX125 M6 — only a refresh the gateway REJECTED means the session
      // is over. One that could not reach it (network error, 5xx during a
      // redeploy) keeps the page: the caller sees this 401 as an error and
      // the next request or the silent-refresh tick tries again.
      if (outcome === "rejected") redirectToPortalLogin();
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

  // ----- Direct-dispatch media generation (Sprint 2A.5) -----
  // These ride the chat-edge media proxy (router.go MediaProxy block):
  // POST /videos|audio/generations, GET /jobs/{id}, POST /jobs/{id}/cancel,
  // GET /jobs/{id}/output. chat-edge forwards to the gateway's public media
  // routes as a trusted internal caller; the gateway enforces auth, DLP,
  // balance, and the feature gate. The route contract test
  // (services/chat-edge-api/internal/router/contract_test.go) pins both sides.
  submitVideoGen(input: VideoGenInput) {
    return this.json<MediaGenSubmitResponse>("/videos/generations", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }
  submitAudioGen(input: AudioGenInput) {
    return this.json<MediaGenSubmitResponse>("/audio/generations", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }
  getMediaJob(jobId: string) {
    return this.json<MediaJobStatus>(`/jobs/${jobId}`);
  }
  // Sprint 3 — abort a reserved/in-flight job (premium-confirm Cancel and the
  // in-progress Cancel button). Backend marks it cancelled + refunds the L1
  // session reservation; the chat-edge proxy forwards POST /jobs/{id}/cancel.
  cancelMediaJob(jobId: string) {
    return this.json<MediaJobStatus>(`/jobs/${jobId}/cancel`, {
      method: "POST",
    });
  }
  /** Fetch a finished media job's artifact bytes through the authed
   *  cookie-carrying client (raw() → credentials:"include" + the
   *  401→refresh→retry contract) and return an object URL for
   *  <video>/<audio> playback. A bare src pointing at the gateway
   *  URL can't authenticate (and the job status' output_url is a
   *  gateway-relative path that doesn't even resolve from this
   *  origin). Caller owns the URL and must URL.revokeObjectURL()
   *  it when done — mirror AuthedImage/VideoPlayer. */
  async mediaJobOutputObjectUrl(jobId: string): Promise<string> {
    const res = await this.raw(`/jobs/${jobId}/output`);
    if (!res.ok) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText, res.status);
    }
    return URL.createObjectURL(await res.blob());
  }

  // ----- Usage & credits ------------------------------------
  getUsage() {
    return this.json<UsageResponse>("/usage/me");
  }

  // ----- Cloud scheduled tasks ------------------------------
  // Server-side scheduled prompts (chat-edge /cloud-tasks). They fire
  // even with the computer off, so — unlike the desktop-only LOCAL
  // tasks over the host bridge — they are managed over plain
  // cookie-authenticated HTTP and available in the browser too. Read-
  // only tool set server-side (no local files). raw()/json() carry the
  // HttpOnly session cookie + the 401→refresh→retry contract as usual.
  listCloudTasks() {
    return this.json<{ tasks: CloudTask[]; total: number }>("/cloud-tasks");
  }
  createCloudTask(input: {
    conversationId: string;
    name: string;
    prompt: string;
    intervalSeconds: number;
    maxRuns?: number | null;
  }) {
    return this.json<CloudTask>("/cloud-tasks", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: input.conversationId,
        name: input.name,
        prompt: input.prompt,
        interval_seconds: input.intervalSeconds,
        max_runs: input.maxRuns ?? null,
      }),
    });
  }
  async setCloudTaskEnabled(id: string, enabled: boolean): Promise<void> {
    const res = await this.raw(`/cloud-tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText, res.status);
    }
  }
  async deleteCloudTask(id: string): Promise<void> {
    const res = await this.raw(`/cloud-tasks/${id}`, { method: "DELETE" });
    if (!res.ok) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText, res.status);
    }
  }
  listCloudTaskRuns(id: string) {
    return this.json<{ runs: CloudTaskRun[]; total: number }>(
      `/cloud-tasks/${id}/runs`,
    );
  }

  // ----- Conversations --------------------------------------
  listConversations(cursor?: string) {
    const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return this.json<ConversationListResponse>(`/conversations${q}`);
  }
  createConversation(model_id: string, opts?: { temporary?: boolean }) {
    // FIX127 B1 — a chat started as temporary says so at creation
    // (is_temporary); the server starts it with memory off and never names
    // it. Memory off alone no longer means temporary.
    return this.json<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify(opts?.temporary ? { model_id, is_temporary: true } : { model_id }),
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
  /** FIX35 Part C — the Auto router's cost ceiling for one conversation.
   *  "" resets to the tenant / deployment default. */
  setConversationRoutingTier(id: string, tier: RoutingTier | "") {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ routing_tier: tier }),
    });
  }

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
  /** Pin / unpin a conversation (migration 021). Pinned conversations
   *  render in the sidebar's "PINNED" section. Returns the updated
   *  conversation so the caller can reconcile state. */
  setConversationPinned(id: string, pinned: boolean) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_pinned: pinned }),
    });
  }
  /** Archive / un-archive a conversation (migration 032). NOT a delete:
   *  the chat stays readable, it just leaves the sidebar list. */
  setConversationArchived(id: string, archived: boolean) {
    return this.json<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_archived: archived }),
    });
  }
  /** The archive view — the same paginated list endpoint with the
   *  archived filter flipped. */
  listArchivedConversations() {
    return this.json<ConversationListResponse>("/conversations?archived=1&limit=100");
  }
  // -----------------------------------------------------------
  // Folders were merged into Projects in Phase 9 (chat-edge migration
  // 031). The /folders routes are gone from the server, so there is no
  // folder client here — projects below are the only organiser.
  // -----------------------------------------------------------

  // -----------------------------------------------------------
  // Projects (Phase 4).
  // -----------------------------------------------------------
  listProjects() {
    return this.json<{ projects: Project[] }>("/projects");
  }
  createProject(name: string, instructions: string) {
    return this.json<{ project: Project }>("/projects", {
      method: "POST",
      body: JSON.stringify({ name, instructions }),
    });
  }
  getProject(id: string) {
    return this.json<{ project: Project }>(`/projects/${id}`);
  }
  updateProject(id: string, patch: { name?: string; instructions?: string }) {
    return this.json<{ project: Project }>(`/projects/${id}`, {
      method: "PUT",
      body: JSON.stringify(patch),
    });
  }
  /** Attach a knowledge base to a project, or detach with `null`. */
  setProjectKnowledgeBase(id: string, knowledgeBaseId: string | null) {
    return this.json<{ project: Project }>(`/projects/${id}/knowledge-base`, {
      method: "PUT",
      body: JSON.stringify({ knowledge_base_id: knowledgeBaseId }),
    });
  }
  deleteProject(id: string) {
    return this.raw(`/projects/${id}`, { method: "DELETE" });
  }
  /** Assign a conversation to a project, or `null` to un-assign. */
  moveConversationToProject(id: string, projectId: string | null) {
    return this.raw(`/conversations/${id}/project`, {
      method: "PUT",
      body: JSON.stringify({ project_id: projectId }),
    });
  }

  deleteConversation(id: string) {
    return this.raw(`/conversations/${id}`, { method: "DELETE" });
  }
  /** FIX126 F2 — best-effort delete while the page is going away (tab
   *  closed or reloaded on the new-chat page with files attached but
   *  nothing sent). `keepalive` lets the request outlive the document; no
   *  refresh-and-retry is possible here, so a lapsed session just leaves
   *  the row (the sidebar hides a conversation with no messages). */
  deleteConversationOnExit(id: string): void {
    try {
      void fetch(`${API_BASE}/conversations/${id}`, {
        method: "DELETE",
        credentials: "include",
        keepalive: true,
      }).catch(() => undefined);
    } catch {
      /* the page is unloading; nothing to report to */
    }
  }
  cancelGeneration(convId: string) {
    // Hosted mode: the host owns the turn — tell it to cancel over the bridge
    // and do NOT POST /cancel (which would 404 the bridge-only turn and,
    // worse, could cancel an unrelated server row). Returns the same shape.
    if (usingBridgeTransport) {
      bridge.cancelTurn(convId);
      return Promise.resolve({ cancelled: true });
    }
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
  /** Full-text search over MESSAGE BODIES via the S6 engine
   *  (GET /search, ts_rank). Complements listConversationsSearch,
   *  which only substring-matches conversation titles. */
  searchMessages(q: string, limit = 20) {
    const params = new URLSearchParams();
    params.set("q", q);
    params.set("scope", "messages");
    params.set("limit", String(limit));
    return this.json<SearchResponse>(`/search?${params.toString()}`);
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
    imageMode?: boolean,
    videoMode?: boolean,
    modelId?: string | null,
    requestId?: string,
  ): Promise<Response> {
    // Hosted mode: submit the turn to the native host over the bridge and
    // render the events it streams back. The page never POSTs /messages here
    // (the host, not the page, drives the turn), so there is no double-persist.
    // fileIds become opaque attachment handles. FIX9 Part B: modelId and imageMode
    // are now CARRIED (fields on the existing turn), not dropped — the host
    // validates the model and offers generate_image on imageMode. videoMode is NOT
    // carried: the 🎬 button is disabled in hosted mode (video is an async job).
    if (usingBridgeTransport) {
      void videoMode; // deliberately unused in hosted mode
      return bridge.submitTurn(convId, content, signal, fileIds ?? [], modelId ?? null, imageMode ?? false);
    }
    const body: Record<string, unknown> = { content, stream: true };
    if (fileIds && fileIds.length > 0) body.file_ids = fileIds;
    // Phase C — 🎨 image mode: biases a tool-capable model to call
    // generate_image (chat-edge appends a system hint).
    if (imageMode) body.image_mode = true;
    // Sprint 2A — 🎬 video mode: biases a tool-capable model to call
    // generate_video (chat-edge appends a system hint).
    if (videoMode) body.video_mode = true;
    // FIX121 C3 — the send names itself. The id was minted BEFORE this
    // request left, once for the user's action; chat-edge uses it as the run
    // id and as the idempotency key, so a repeat of this request (raw()'s
    // own retry after a 401 refresh is one) is answered with the first turn
    // instead of running a second one. Only sent when the server is known to
    // allow the header cross-origin — see requestIdAllowed.
    const headers: Record<string, string> = {};
    if (requestId && (await requestIdAllowed())) headers["X-Request-Id"] = requestId;
    return this.raw(`/conversations/${convId}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
      headers,
      signal,
    });
  }
  async openRegenerateStream(
    convId: string,
    messageId: string,
    signal: AbortSignal,
  ): Promise<Response> {
    // Hosted mode: regenerate is a fresh host turn for the conversation. The
    // richer semantics (fork from a specific message) are Phase 4+; Phase 3
    // keeps the transport swap minimal and routes it through the bridge so no
    // /regenerate POST forks a server row behind the host's back.
    if (usingBridgeTransport) {
      return bridge.submitTurn(convId, "", signal, [], null);
    }
    return this.raw(
      `/conversations/${convId}/messages/${messageId}/regenerate`,
      { method: "POST", signal },
    );
  }
  /** Phase 5 — reattach to an in-flight generation (SSE, same event
   *  vocabulary as openMessageStream).
   *
   *  FIX41: `startingAfter` is the client's cursor (the last seq it
   *  RENDERED). With it the server replays only the frames that were
   *  missed, each under its original seq — so a reattach neither loses
   *  the tool cards that happened during the gap nor repeats what is
   *  already on screen. Omitted (0) keeps the pre-FIX41 behaviour of
   *  replaying the whole run.
   *
   *  409 RESUME_UNAVAILABLE = not attachable yet, retry.
   *  409 RESUME_TOO_OLD = the missed frames are gone; reload the message.
   */
  /** FIX74 — answer (or skip) a parked question on the web path. The
   *  response is the continuation stream on the same assistant message. */
  async openQuestionAnswerStream(
    convId: string,
    promptId: string,
    answer: { outcome: "answered" | "skipped"; answers: Record<string, string[]>; response: string | null },
    signal: AbortSignal,
  ): Promise<Response> {
    return this.raw(`/conversations/${convId}/questions/${promptId}`, {
      method: "POST",
      body: JSON.stringify(answer),
      signal,
    });
  }
  async openResumeStream(
    convId: string,
    messageId: string,
    signal: AbortSignal,
    startingAfter = 0,
  ): Promise<Response> {
    // Hosted mode: re-open the host's event sink for this conversation's
    // in-flight turn (the host is the authority on whether one exists).
    if (usingBridgeTransport) {
      return bridge.resumeTurn(convId, signal);
    }
    const q = startingAfter > 0 ? `?starting_after=${startingAfter}` : "";
    return this.raw(`/conversations/${convId}/messages/${messageId}/stream${q}`, {
      method: "GET",
      signal,
    });
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
  /** FIX127 A1 — delete an upload the page knows only by the client_id it
   *  sent with the body (the chip was removed before the server answered).
   *  The server removes unattached rows only; 404 when there is none. */
  deleteFileByClientId(convId: string, clientId: string) {
    return this.raw(
      `/conversations/${convId}/files/by-client/${encodeURIComponent(clientId)}`,
      { method: "DELETE" },
    );
  }
  /** Absolute URL for the download endpoint. Auth rides on the
   *  ``portal_access_token`` cookie — cookies aren't scoped by port
   *  on localhost, so the cookie set by the Portal flows to the
   *  chat-edge origin automatically. */
  downloadUrl(fileId: string): string {
    return `${API_BASE}/files/${fileId}/download`;
  }
  /** Fetch a file's bytes through the authed client and return an
   *  object URL. There is NO Bearer token in this app — auth is the
   *  HttpOnly ``portal_access_token`` cookie, sent cross-origin via
   *  raw()'s ``credentials: "include"``. Unlike ``downloadUrl`` (a
   *  bare <img>/<a download> URL), this path benefits from raw()'s
   *  401 → silent-refresh → retry, so it survives access-token expiry
   *  instead of rendering a broken image. The caller owns the returned
   *  URL and must URL.revokeObjectURL() it when done. */
  async fileObjectUrl(fileId: string): Promise<string> {
    const res = await this.raw(`/files/${fileId}/download`);
    if (!res.ok) {
      throw new ApiError(`HTTP_${res.status}`, res.statusText, res.status);
    }
    return URL.createObjectURL(await res.blob());
  }
  /** The text the extraction pipeline pulled out of a document
   *  (GET /files/{id}/text). Used by the in-app preview for office /
   *  text files: it is exactly what the model was given, so the user
   *  can see what was actually read. A non-"completed" status means
   *  extraction is still running (or produced nothing). */
  getFileText(fileId: string) {
    return this.json<{
      extraction_status: string;
      text: string;
      truncated: boolean;
      ocr: boolean;
    }>(`/files/${fileId}/text`);
  }
  /** Structured preview data (Phase 16) — a window of one sheet, or a
   *  document's blocks, or a deck's slides. Throws ApiError 415 when the
   *  type has no structured view and 503 when the server cannot produce
   *  one; both mean "fall back to getFileText", which is why this is a
   *  separate call rather than an extra field on it. */
  getFilePreviewData(
    fileId: string,
    opts: { sheet?: number; offset?: number; limit?: number } = {},
  ) {
    const query = new URLSearchParams();
    if (opts.sheet) query.set("sheet", String(opts.sheet));
    if (opts.offset) query.set("offset", String(opts.offset));
    if (opts.limit) query.set("limit", String(opts.limit));
    const suffix = query.toString() ? `?${query}` : "";
    return this.json<PreviewData>(`/files/${fileId}/preview-data${suffix}`);
  }
  /** Authenticated download: fetch the blob through the cookie-authed
   *  client (with its 401 → refresh → retry), then trigger a browser
   *  save via a transient object URL. Replaces the bare <a href
   *  download> which broke the moment the access cookie lapsed. */
  async downloadFile(fileId: string, filename: string): Promise<void> {
    const url = await this.fileObjectUrl(fileId);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Revoke after the download has had time to start.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  /** FIX81 — persist assistant-produced bytes as a generated
   *  ConversationFile (the one delivery pipeline). Used by the media
   *  forms so a finished job survives reset/reload instead of dying
   *  with its object URL. */
  saveGeneratedConversationFile(
    convId: string,
    body: { filename: string; content_type: string; data_base64: string; generated_by?: string },
  ) {
    return this.json<import("./types").FileAttachment>(
      `/conversations/${convId}/files/generated`,
      { method: "POST", body: JSON.stringify(body) },
    );
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
   *  validator report so the result card can render both.
   *
   *  source_file_id is OPTIONAL (Phase 8): omit it to generate a
   *  workbook from scratch, the same contract executeDocumentPlan has. */
  executeExcelPlan(
    convId: string,
    body: {
      source_file_id?: string;
      output_filename?: string;
      plan: unknown;
      /** FIX126 F4 — the assistant message that wrote the plan; the
       *  server links the generated file to it. */
      message_id?: string;
    },
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
      /** FIX126 F4 — see executeExcelPlan. */
      message_id?: string;
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
  // ── FIX32 Part 4.3: skills (instruction text on chat-edge) ────────────────
  listSkills(): Promise<{ skills: WebSkill[]; total: number }> {
    return this.json("/skills");
  }
  createSkill(body: { name: string; description?: string; triggers?: string[]; instructions: string }) {
    return this.json<WebSkill>("/skills", { method: "POST", body: JSON.stringify(body) });
  }
  setSkillEnabled(id: string, enabled: boolean) {
    return this.json<WebSkill>(`/skills/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) });
  }
  deleteSkill(id: string) {
    return this.json<void>(`/skills/${id}`, { method: "DELETE" });
  }

  listMemories(params?: {
    scope_type?: string;
    /** Which instance of the scope — the project id when
     *  `scope_type` is "project" (FIX58/59). The server REJECTS
     *  `scope_type=project` without it (400) rather than answering
     *  with every project's memories mixed together, so forgetting it
     *  is a visible error and never a silent cross-project leak. */
    scope_id?: string;
    memory_type?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }) {
    const search = new URLSearchParams();
    if (params?.scope_type) search.set("scope_type", params.scope_type);
    if (params?.scope_id) search.set("scope_id", params.scope_id);
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
   *  /account/exports/{id}/download, authenticated by the HttpOnly
   *  session cookie like every other request. */
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

  // ----- Canvas artifacts (Phase 6 / C3) --------------------
  /** Newest version of every artifact lineage in a conversation. */
  listCanvasArtifacts(convId: string) {
    return this.json<{ artifacts: CanvasArtifactRow[] }>(
      `/conversations/${convId}/artifacts`,
    );
  }
  /** Appends a new version to a lineage (creates it on first save). */
  saveCanvasArtifact(
    convId: string,
    body: {
      artifact_key: string;
      type: string;
      title?: string;
      content: string;
      message_id?: string;
    },
  ) {
    return this.json<{ artifact: CanvasArtifactRow }>(
      `/conversations/${convId}/artifacts`,
      { method: "POST", body: JSON.stringify(body) },
    );
  }
  /** Full history for one lineage, newest version first. */
  listCanvasVersions(convId: string, key: string) {
    return this.json<{ versions: CanvasArtifactRow[] }>(
      `/conversations/${convId}/artifacts/${encodeURIComponent(key)}/versions`,
    );
  }
  /** Re-applies an older version AS A NEW VERSION (history preserved). */
  revertCanvasArtifact(convId: string, key: string, version: number) {
    return this.json<{ artifact: CanvasArtifactRow }>(
      `/conversations/${convId}/artifacts/${encodeURIComponent(key)}/revert`,
      { method: "POST", body: JSON.stringify({ version }) },
    );
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

  // ----- FIX33 — context meter + manual compaction -------------
  /** Dry-run of the context compiler: the meter's numbers (A-W6). */
  getContextInfo(convId: string) {
    return this.json<ContextInfo>(`/conversations/${convId}/context-info`);
  }
  /** "Summarize and continue" with optional instructions (A-W7). */
  compactConversation(convId: string, instructions: string) {
    return this.json<{ coverage_reached: boolean; messages_total: number }>(`/conversations/${convId}/compact`, {
      method: "POST",
      body: JSON.stringify({ instructions }),
    });
  }

  // ----- Branching ------------------------------------------
  async openEditStream(
    convId: string,
    messageId: string,
    content: string,
    signal: AbortSignal,
  ): Promise<Response> {
    // Hosted mode (FIX11 §3): carry the edited message's id through the turn so
    // the HOST forks the conversation at it (chat-edge /edit, server-authoritative
    // branching) instead of appending a linear turn — the ◀ n/m ▶ navigator then
    // works exactly as in the browser. A field on the existing turn, not a new
    // message type.
    if (usingBridgeTransport) {
      return bridge.submitTurn(convId, content, signal, [], null, false, messageId);
    }
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
  /** FIX130 C2 — whether a knowledge base can work right now. */
  getKnowledgeAvailability() {
    return this.json<KnowledgeAvailability>("/knowledge-bases/availability");
  }
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
    _retried = false,
  ): Promise<KnowledgeDocument> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/knowledge-bases/${kbId}/documents`);
      // Send the HttpOnly session cookie; no Bearer header.
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && opts?.onProgress) {
          opts.onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          // Silent refresh → retry once before bouncing to login.
          if (!_retried) {
            sharedRefresh().then((outcome) => {
              if (outcome === "ok") {
                this.uploadKBDocument(kbId, file, opts, true).then(
                  resolve,
                  reject,
                );
              } else {
                // FIX125 M6 — only a rejected refresh leaves the page.
                if (outcome === "rejected") redirectToPortalLogin();
                reject(new ApiError("UNAUTHORIZED", "Session expired", 401));
              }
            });
            return;
          }
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
      // The server extracts synchronously — a scanned PDF is OCR'd
      // before the response comes back — so this request is long by
      // design. It must still END: with no timeout at all, a hung
      // extraction left the upload pending forever, which in the
      // composer meant a permanently disabled send button and no way
      // for the user to tell a slow read from a dead one. The ceiling
      // sits above the server's own extraction budget so a genuinely
      // slow scan still finishes instead of being cut off here.
      xhr.timeout = UPLOAD_TIMEOUT_MS;
      xhr.ontimeout = () => {
        reject(
          new ApiError(
            "UPLOAD_TIMEOUT",
            "The server took too long to read this file",
            0,
          ),
        );
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
      /** FIX127 A1 — the chip's id, sent as the multipart `client_id` so a
       *  removed chip can be deleted on the server before its id is known. */
      clientId?: string;
    },
    _retried = false,
  ): Promise<FileAttachment> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API_BASE}/conversations/${convId}/files`);
      // Send the HttpOnly session cookie; no Bearer header.
      xhr.withCredentials = true;
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && opts?.onProgress) {
          opts.onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          // Same refresh-then-retry contract as the JSON path.
          if (!_retried) {
            sharedRefresh().then((outcome) => {
              if (outcome === "ok") {
                this.uploadFile(convId, file, opts, true).then(
                  resolve,
                  reject,
                );
              } else {
                // FIX125 M6 — only a rejected refresh leaves the page.
                if (outcome === "rejected") redirectToPortalLogin();
                reject(new ApiError("UNAUTHORIZED", "Session expired", 401));
              }
            });
            return;
          }
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
      // Conversation-file uploads no longer wait on text extraction:
      // the endpoint returns as soon as the bytes are stored, with
      // extraction_status "processing", and the reader runs server-side
      // in the background (tracked client-side by lib/fileExtraction).
      // So this request is short again. The generous ceiling stays as a
      // pure transfer-side backstop — it has to exist at all because
      // with no timeout a wedged request stays pending forever and the
      // chip never leaves "uploading", which DOES still block sending.
      xhr.timeout = UPLOAD_TIMEOUT_MS;
      xhr.ontimeout = () => {
        reject(
          new ApiError(
            "UPLOAD_TIMEOUT",
            "The upload took too long. Please try again.",
            0,
          ),
        );
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
      if (opts?.clientId) form.append("client_id", opts.clientId);
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

// ── FIX32 Part 4.3 — user skills (chat-edge /api/v1/skills; FIX12 Item 3).
// Skills are INSTRUCTION TEXT stored per user and folded into the system
// prompt by chat-edge (prompt_compiler layer 10b) — no host involvement, so
// the WEB surface can list/create/toggle/delete them like the desktop does.
export interface WebSkill {
  id: string;
  name: string;
  description: string;
  triggers: string[];
  instructions: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
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
