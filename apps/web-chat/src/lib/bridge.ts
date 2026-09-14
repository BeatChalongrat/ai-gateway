// Page → host bridge client (desktop shell, Phase 3).
//
// In hosted mode the chat transport is not an SSE fetch to chat-edge; it is
// a typed postMessage channel to the native host, which runs the agent loop
// and streams events back. This module is the ENTIRE page side of that
// channel — the renderer and the chat store are untouched, because the
// transport functions below hand back a synthetic `Response` whose body is
// an SSE `ReadableStream`. `parseSSE`/`consumeStream` cannot tell the
// difference, so no store or component code changes.
//
// The page never names a tool and never names a path (plan §2.1, §5): it
// posts `turn.submit` with opaque attachment handles and receives
// `turn.event` frames. Tool decisions live in the host.
//
// Security: every outbound message carries the per-navigation nonce the host
// injected into `window.__SRAN_BRIDGE__` (C-3). The host additionally
// verifies `e.Source` origin (C-2) and the closed message type set (C-4).

import { HOSTED_MODE } from "./auth";
import type { PendingQuestion, QuestionAnswerPayload } from "./types";

interface BridgeHandshake {
  nonce: string;
}

type HostToPage =
  | { type: "turn.event"; payload: { conversationId: string; runId?: string; seq: number; event: string; data: unknown } }
  | { type: "consent.request"; payload: ConsentRequest }
  // FIX73: the model's question card — its own message type on the same rail.
  | { type: "question.request"; payload: QuestionRequest }
  | { type: "session.changed"; payload: unknown }
  | { type: "host.error"; payload: { code: string; message: string } }
  // FIX22 §2: an informational host → page notice (today: a newer desktop
  // version exists). Dismissible, never modal; the page decides to show it.
  | { type: "host.notice"; payload: HostNotice }
  // Phase 5 control reply — correlated to a host.query by requestId.
  | { type: "host.result"; payload: { requestId: string; ok: boolean; data?: unknown; error?: string } };

/** FIX22 §2 — update notice. No URL and no file ride with it: notification
 *  only; the user installs by hand.
 *  FIX25 §1c.3 — approval-mode reset: the host wrote a stored `skip` back as
 *  `manual` at start and says so once. Informational; nothing to click. */
export type HostNotice =
  | { kind: "update-available"; latest: string; current: string }
  | { kind: "approval-mode-reset"; from: string; to: string };

/** The closed set of host.query operations (Phase 5 §2.2). Mirrors the host's
 *  HostQueryOp enum; the host drops anything outside it. */
export type HostQueryOp =
  // E3 §2: logout is an explicit host operation (the host revokes, clears its
  // store and jar, and shows its signed-out view). Replaces the E2 fallback of
  // the host inferring logout from a POST it observed.
  | "session.logout"
  | "skills.list" | "skills.setEnabled" | "skills.installPicker" | "skills.create" | "skills.delete"
  | "tasks.list" | "tasks.create" | "tasks.update" | "tasks.setEnabled" | "tasks.delete" | "tasks.runNow" | "tasks.runs"
  | "connectors.listCatalog" | "connectors.listInstalled" | "connectors.install" | "connectors.uninstall"
  | "connectors.oauthConnect" | "connectors.oauthDisconnect" | "connectors.addCustomDialog"
  | "connectors.addRemoteDialog" // FIX69: host-owned url+key form; the page names no url, sees no key
  // FIX31 Part 2: configured/enabled/connected per oauth2 connector (never a token).
  | "connectors.oauthStatus"
  | "settings.get" | "settings.set"
  | "folders.list" | "folders.pickAndGrant" | "folders.revoke"
  | "sites.list" | "sites.revoke"
  | "globalNotes.list" | "globalNotes.delete"
  | "diag.reportRendererError"
  // FIX1 §1.2: the one new op — browser-extension folder escape hatch (no path
  // from the page; the host opens a native picker).
  | "browser.pickExtensionFolder"
  | "browser.rotatePairingToken" // FIX70
  // FIX33 A-D5: context meter numbers + "summarise and continue" (page-only).
  | "context.info" | "context.compact"
  // FIX34 Part A: host-owned shell prefs (quick-ask hotkey, tray click) and the
  // quick-ask window's two hand-offs (close, open this chat in the main window).
  | "shell.prefs.get" | "shell.prefs.set" | "quick.close" | "quick.openInMain";

export interface HostResult<T = unknown> {
  ok: boolean;
  data?: T;
  error?: string;
}

export interface ConsentRequest {
  promptId: string;
  conversationId: string;
  tool: string;
  description?: string;
  category?: string;
  argumentSummary: string;
  argumentDetail?: string;
  dangerClass: string;
  // FIX6: a browser-site prompt carries the origin and Anthropic's two grant
  // options; alwaysPromptReason explains a forced prompt on an approved site.
  site?: string;
  grantOptions?: ("once" | "session" | "always")[];
  alwaysPromptReason?: "download" | "sensitive-info" | "authorization" | null;
  /** FIX31 §1.1: set when the request comes from a sub-agent the assistant
   *  started on its own — the card says who asked and with what task. */
  requestedBy?: { kind: "subagent"; subagentId: string; label: string; task: string };
}

type ConsentDecision = "allow" | "deny";
type ConsentScope = "once" | "session" | "always";

/** FIX73: the host's question.request payload — a PendingQuestion plus the
 *  conversation it belongs to. */
export interface QuestionRequest extends PendingQuestion {
  conversationId: string;
}

// One open turn's SSE sink. The host's turn.event frames are re-encoded as
// SSE bytes and pushed here so the unchanged parseSSE consumes them.
interface TurnSink {
  controller: ReadableStreamDefaultController<Uint8Array>;
  closed: boolean;
}

const encoder = new TextEncoder();

function webviewApi(): {
  postMessage: (m: unknown) => void;
  addEventListener: (t: string, cb: (e: { data: unknown }) => void) => void;
} | null {
  type Transport = { postMessage: (m: unknown) => void; addEventListener: (t: string, cb: (e: { data: unknown }) => void) => void };
  const w = window as unknown as {
    chrome?: { webview?: Transport };
    // Electron shell (E3): the preload's contextBridge surface carries the
    // same two functions plus the per-navigation nonce. Same protocol, same
    // nonce rule, same closed vocabulary — only the pipe differs.
    sranHost?: Partial<Transport> & { nonce?: string | null };
  };
  if (w.chrome?.webview) return w.chrome.webview;
  const h = w.sranHost;
  if (h && typeof h.postMessage === "function" && typeof h.addEventListener === "function") return h as Transport;
  return null;
}

function nonce(): string | null {
  const b = (window as unknown as { __SRAN_BRIDGE__?: BridgeHandshake }).__SRAN_BRIDGE__;
  if (b?.nonce) return b.nonce;
  const h = (window as unknown as { sranHost?: { nonce?: string | null } }).sranHost;
  return h?.nonce ?? null;
}

class BridgeClient {
  private started = false;
  // FIX34 Part A: set by the /quick route while it is mounted. Every turn
  // submitted from that page carries `quick: true`, which the host answers
  // with NO tools (so no consent card can ever appear in the small window).
  private quickAsk = false;
  private sinks = new Map<string, TurnSink>();
  private consentHandlers = new Set<(r: ConsentRequest) => void>();
  private questionHandlers = new Set<(r: QuestionRequest) => void>();
  private errorHandlers = new Set<(code: string, message: string) => void>();
  private noticeHandlers = new Set<(n: HostNotice) => void>();
  // In-flight host.query calls, keyed by the requestId the host echoes back.
  private pendingQueries = new Map<string, { resolve: (r: HostResult) => void; timer: ReturnType<typeof setTimeout> }>();
  private queryCounter = 0;
  // Last turn.event seq the page has RENDERED per conversation — the resume
  // cursor sent in ui.ready so the host replays only what was missed.
  private lastSeq = new Map<string, number>();
  // FIX41 — which RUN that seq belongs to (docs/STREAM_CONTRACT.md §4/§9).
  // The cursor is the pair, not the number: a seq from a previous
  // generation says nothing about this one. Until FIX41 correctness rested
  // on two separate guards keeping seq monotonic across turns (the host
  // carrying it forward, and submitTurn clearing it here). Neither is true
  // any more once a run log can be reloaded from disk after a restart.
  private lastRunId = new Map<string, string>();

  /** FIX34 Part A: mark this page as the quick-ask window (tool-less turns). */
  setQuickAsk(on: boolean): void {
    this.quickAsk = on;
  }
  get isQuickAsk(): boolean {
    return this.quickAsk;
  }

  /** Test-only: drop all state so a fresh fake webview can be wired. Never
   *  called in production (start() is idempotent there). */
  resetForTest(): void {
    this.started = false;
    this.quickAsk = false;
    this.sinks.forEach((_, id) => this.closeSink(id));
    this.sinks.clear();
    this.lastSeq.clear();
    this.lastRunId.clear();
    this.consentHandlers.clear();
    this.questionHandlers.clear();
    this.errorHandlers.clear();
    this.noticeHandlers.clear();
    this.pendingQueries.forEach((p) => clearTimeout(p.timer));
    this.pendingQueries.clear();
  }

  /** Idempotent. Wires the host→page listener and completes the handshake. */
  start(): void {
    if (this.started) return;
    const api = webviewApi();
    if (!api) return;
    this.started = true;
    api.addEventListener("message", (e) => this.onHostMessage(e.data as HostToPage));
    // Handshake: echo the nonce so the host knows the page is live (ui.ready).
    this.post("ui.ready", {});
  }

  private onHostMessage(msg: HostToPage): void {
    if (!msg || typeof msg !== "object") return;
    switch (msg.type) {
      case "turn.event": {
        const { conversationId, runId, seq, event, data } = msg.payload;
        const sink = this.sinks.get(conversationId);
        // No sink → this frame is NOT rendered, so it must NOT advance the
        // resume cursor. (Earlier this advanced lastSeq unconditionally, which
        // meant a reload mid-turn — whose frames arrive before the resume sink
        // opens — pushed the cursor past events the page never rendered, so the
        // resume replayed nothing and the answer's prefix was lost. The 4.11
        // test caught it.) The cursor is "highest seq actually delivered".
        if (!sink || sink.closed) return;
        // Idempotent, ordered delivery keyed by seq: deliver only if it advances
        // the cursor. Dedups the one race where the host both replays a buffered
        // event and posts it live, and drops any out-of-order arrival.
        if (typeof seq === "number") {
          // A frame from a DIFFERENT run restarts the cursor: its numbers
          // are its own, and comparing them against the previous run's
          // would silently drop the whole answer.
          const known = this.lastRunId.get(conversationId);
          if (runId && known && runId !== known) {
            this.lastSeq.delete(conversationId);
          }
          if (seq <= (this.lastSeq.get(conversationId) ?? 0)) return; // already delivered
          this.lastSeq.set(conversationId, seq);
          if (runId) this.lastRunId.set(conversationId, runId);
        }
        // Re-encode as one SSE frame; parseSSE splits on the blank line. seq
        // stays on the envelope — the renderer never sees it.
        const frame = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
        try {
          sink.controller.enqueue(encoder.encode(frame));
        } catch {
          /* controller already closed */
        }
        // Terminal events end the stream, mirroring chat-edge closing the SSE.
        // FIX41 adds `cancelled`: without it a stopped turn left the sink
        // open and the page waiting on a run that had already ended.
        if (event === "message_complete" || event === "error" || event === "cancelled") {
          this.closeSink(conversationId);
        }
        return;
      }
      case "consent.request":
        this.consentHandlers.forEach((h) => h(msg.payload));
        return;
      case "question.request": {
        // Shape-check before fan-out: a card with no questions must not render.
        const q = msg.payload as QuestionRequest | undefined;
        if (!q || typeof q.promptId !== "string" || !q.promptId || typeof q.conversationId !== "string" || !Array.isArray(q.questions) || q.questions.length === 0) return;
        this.questionHandlers.forEach((h) => h(q));
        return;
      }
      case "host.error":
        this.errorHandlers.forEach((h) => h(msg.payload.code, msg.payload.message));
        return;
      case "host.notice": {
        // Shape-check before fan-out: the host is trusted, but a notice with
        // no version must not render an empty banner.
        const p = msg.payload as HostNotice | undefined;
        if (!p) return;
        if (p.kind === "update-available") {
          if (typeof p.latest !== "string" || !p.latest || typeof p.current !== "string") return;
        } else if (p.kind === "approval-mode-reset") {
          if (typeof p.from !== "string" || !p.from || typeof p.to !== "string" || !p.to) return;
        } else return;
        this.noticeHandlers.forEach((h) => h(p));
        return;
      }
      case "host.result": {
        const { requestId, ok, data, error } = msg.payload;
        const pending = this.pendingQueries.get(requestId);
        if (!pending) return; // unknown/late reply — drop
        clearTimeout(pending.timer);
        this.pendingQueries.delete(requestId);
        pending.resolve({ ok, data, error });
        return;
      }
      case "session.changed":
        // Defined by the vocabulary; the host does not send it (Phase 2 found
        // the page needs no telling about cookie rotation). Ignore if it ever
        // arrives rather than assume a shape.
        return;
    }
  }

  private post(type: string, payload: unknown): boolean {
    const api = webviewApi();
    const n = nonce();
    if (!api || !n) return false;
    api.postMessage({ nonce: n, type, payload });
    return true;
  }

  /**
   * Submit a turn and return a Response streaming its events as SSE. The
   * host resolves attachment handles against folder grants; the page never
   * sends a path.
   */
  submitTurn(
    conversationId: string,
    text: string,
    signal: AbortSignal | undefined,
    attachmentHandles: string[],
    modelId: string | null,
    imageMode = false,
    editMessageId: string | null = null,
  ): Response {
    // A submit resets the resume cursor: this turn's events start a fresh
    // run. (Kept alongside the run-id check above — belt and braces, since
    // between them they are what stops a second turn's frames from being
    // dropped by a cursor left over from the first.)
    this.lastSeq.delete(conversationId);
    this.lastRunId.delete(conversationId);
    const body = this.openSink(conversationId, signal, true);
    const posted = this.post("turn.submit", { conversationId, text, attachmentHandles, modelId, imageMode, editMessageId, quick: this.quickAsk });
    if (!posted) {
      // No bridge/nonce — fail like an unreachable transport so the store's
      // pre-error path runs instead of hanging on an open stream.
      this.closeSink(conversationId);
      return new Response(null, { status: 503, statusText: "bridge unavailable" });
    }
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  /**
   * Reload recovery (plan addendum). Re-open the sink and send ui.ready with
   * the conversation and the last seq the page rendered; the host replays from
   * seq+1 if a turn is live (or just completed), else sends nothing. This uses
   * ui.ready, NOT a fifth message type — the vocabulary stays four-in/four-out.
   */
  resumeTurn(conversationId: string, signal: AbortSignal | undefined): Response {
    // postCancelOnAbort=false: a resume probe that finds no live turn aborts
    // its own sink; it must NOT post turn.cancel (there is nothing to cancel).
    const body = this.openSink(conversationId, signal, false);
    const posted = this.post("ui.ready", {
      conversationId,
      lastSeq: this.lastSeq.get(conversationId) ?? 0,
      runId: this.lastRunId.get(conversationId) ?? null,
    });
    if (!posted) {
      this.closeSink(conversationId);
      return new Response(null, { status: 503 });
    }
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  cancelTurn(conversationId: string): void {
    this.post("turn.cancel", { conversationId });
  }

  /** The highest turn.event seq received for a conversation — the resume
   *  cursor. Used by the store to tell "a turn replayed" from "nothing did"
   *  without materializing a speculative message row. */
  peekSeq(conversationId: string): number {
    return this.lastSeq.get(conversationId) ?? 0;
  }

  respondConsent(promptId: string, decision: ConsentDecision, scope: ConsentScope): void {
    this.post("consent.respond", { promptId, decision, scope });
  }

  /** FIX73: answer (or skip) the model's question card. */
  respondQuestion(answer: QuestionAnswerPayload): void {
    this.post("question.respond", answer);
  }

  /**
   * Phase 5: call one host operation and await its result. Mints a requestId,
   * posts `host.query`, and resolves when the correlated `host.result` arrives
   * (or rejects on timeout / unavailable bridge). The op is a closed string the
   * host validates; the page never names a path, a command, or a secret — those
   * are obtained by the host through native OS dialogs (§2.3).
   */
  hostQuery<T = unknown>(op: HostQueryOp, args?: Record<string, unknown>, timeoutMs = 30_000): Promise<HostResult<T>> {
    const api = webviewApi();
    const n = nonce();
    if (!api || !n) return Promise.resolve({ ok: false, error: "bridge-unavailable" });
    const requestId = `q${++this.queryCounter}-${n.slice(0, 6)}`;
    return new Promise<HostResult<T>>((resolve) => {
      const timer = setTimeout(() => {
        this.pendingQueries.delete(requestId);
        resolve({ ok: false, error: "timeout" });
      }, timeoutMs);
      this.pendingQueries.set(requestId, { resolve: resolve as (r: HostResult) => void, timer });
      api.postMessage({ nonce: n, type: "host.query", payload: { requestId, op, args: args ?? {} } });
    });
  }

  /** Fire-and-forget renderer error report (§4.2). Never throws. */
  reportRendererError(kind: string, message: string, stack?: string): void {
    // Best effort: a failed report must not itself surface an error.
    void this.hostQuery("diag.reportRendererError", { kind, message, stack }, 5_000).catch(() => {});
  }

  onConsentRequest(handler: (r: ConsentRequest) => void): () => void {
    this.consentHandlers.add(handler);
    return () => this.consentHandlers.delete(handler);
  }

  onQuestionRequest(handler: (r: QuestionRequest) => void): () => void {
    this.questionHandlers.add(handler);
    return () => this.questionHandlers.delete(handler);
  }

  onHostError(handler: (code: string, message: string) => void): () => void {
    this.errorHandlers.add(handler);
    return () => this.errorHandlers.delete(handler);
  }

  /** FIX22 §2: subscribe to host notices (update available). */
  onHostNotice(handler: (n: HostNotice) => void): () => void {
    this.noticeHandlers.add(handler);
    return () => this.noticeHandlers.delete(handler);
  }

  private openSink(
    conversationId: string,
    signal: AbortSignal | undefined,
    postCancelOnAbort: boolean,
  ): ReadableStream<Uint8Array> {
    this.closeSink(conversationId); // one turn per conversation at a time
    // FIX116 — every path below ends THIS sink, never whichever sink the
    // conversation holds at the time. A newer openSink replaces this one
    // (that is the rule above); an abort that arrives afterwards — the
    // resume probe standing down because a send took the slot (FIX114),
    // an image-generation timeout of a superseded turn — used to look the
    // sink up by id and close the newer turn's stream, and the submit
    // path would also have posted turn.cancel for a run that was no
    // longer its own. Measured on e48: a send within ~700 ms of opening a
    // conversation ended in "Network error during streaming." with the
    // answer never rendered until a reload.
    let mine: TurnSink | null = null;
    const endMine = (cancelHost: boolean) => {
      if (mine === null || this.sinks.get(conversationId) !== mine) return; // replaced already: not ours to end
      if (cancelHost) this.cancelTurn(conversationId);
      this.closeSink(conversationId);
    };
    return new ReadableStream<Uint8Array>({
      start: (controller) => {
        mine = { controller, closed: false };
        this.sinks.set(conversationId, mine);
        if (signal) {
          if (signal.aborted) endMine(false);
          else signal.addEventListener("abort", () => endMine(postCancelOnAbort), { once: true });
        }
      },
      cancel: () => {
        endMine(false);
      },
    });
  }

  private closeSink(conversationId: string): void {
    const sink = this.sinks.get(conversationId);
    if (!sink || sink.closed) return;
    sink.closed = true;
    this.sinks.delete(conversationId);
    try {
      sink.controller.close();
    } catch {
      /* already closed */
    }
  }
}

/** The one bridge instance. Inert (all methods no-op) outside hosted mode. */
export const bridge = new BridgeClient();

/** Call once at app boot; safe to call always (no-op unless hosted). */
export function startBridge(): void {
  if (!HOSTED_MODE) return;
  bridge.start();
  // §4.2 — JS exceptions and unhandled rejections in the page reach the same
  // CrashReportService the .NET path uses, so Phase 6 does not ship blind.
  if (typeof window !== "undefined") {
    window.addEventListener("error", (e) => {
      bridge.reportRendererError("error", e.message ?? String(e.error), e.error?.stack);
    });
    window.addEventListener("unhandledrejection", (e) => {
      const reason = e.reason;
      bridge.reportRendererError(
        "unhandledrejection",
        reason?.message ?? String(reason),
        reason?.stack,
      );
    });
  }
}

/** For any code that must branch on transport (kept to api.ts). */
export const usingBridgeTransport = HOSTED_MODE;
