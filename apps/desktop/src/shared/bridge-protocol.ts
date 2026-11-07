// The bridge vocabulary (E3) — ported from SranDesktop.Shell/Bridge/
// BridgeProtocol.cs + HostQueryProtocol.cs. Four in, four out for turns, plus
// the one host.query/host.result control pair with a CLOSED op set. Nothing
// here imports 'electron': the pinning tests load it in plain Node.
//
// Every inbound message is an envelope { nonce, type, payload }:
//   * nonce  — the per-navigation value the preload obtained from the host;
//              missing or wrong → drop and log (C-3);
//   * type   — one of the closed sets below; unknown → drop and log, and there
//              is no default branch that does anything (C-4);
//   * payload— validated by shape per type; a "handle" that looks like a
//              path or a URI is refused (§1: the page never names a path).

export const TURN_INBOUND_TYPES = Object.freeze([
  "turn.submit",
  "turn.cancel",
  "consent.respond",
  "ui.ready",
  // FIX73: the user's answer to the model's question card (ask_user_question).
  // Its own type, not a consent.respond: that payload means "approve this tool
  // call" and would then mean two things.
  "question.respond",
] as const);
export type TurnInboundType = (typeof TURN_INBOUND_TYPES)[number];

/** The control pair (Phase 5): one request type in, one reply type out. */
export const CONTROL_INBOUND_TYPES = Object.freeze(["host.query"] as const);
export const CONTROL_OUTBOUND_TYPES = Object.freeze(["host.result"] as const);

export const TURN_OUTBOUND_TYPES = Object.freeze([
  "turn.event",
  "consent.request",
  "session.changed",
  "host.error",
  // FIX22 §2: a host → page NOTICE (today: "a newer desktop version exists").
  // Informational, dismissible, never modal; the page decides whether to show
  // it. The closed outbound set grows by exactly one, deliberately.
  "host.notice",
  // FIX73: the model's question card (ask_user_question). Rides the same rail
  // as consent.request — one card at a time, re-posted on reload, never
  // answered by a stored approval — as its own message type.
  "question.request",
] as const);
export type OutboundType = (typeof TURN_OUTBOUND_TYPES)[number] | (typeof CONTROL_OUTBOUND_TYPES)[number];

/** Closed host.query op set — mirrors web-chat's HostQueryOp union. E3 answers
 * `session.logout` and `diag.reportRendererError`; every other op here is a
 * known name that E3 answers with host.result {ok:false, error:"unsupported-in-e3"}
 * (the page's settings screens degrade honestly); names NOT here are dropped. */
export const HOST_QUERY_OPS = Object.freeze([
  "session.logout",
  "diag.reportRendererError",
  "skills.list", "skills.setEnabled", "skills.installPicker", "skills.delete",
  "skills.create", // FIX28 §3 (G3): write a skill from a form, no archive
  "tasks.list", "tasks.create", "tasks.update", "tasks.setEnabled", "tasks.delete", "tasks.runNow",
  "tasks.runs", // FIX8 A1.4: a task's run history (reads only; control-plane like the rest)
  "connectors.listCatalog", "connectors.listInstalled", "connectors.install", "connectors.uninstall",
  "connectors.oauthConnect", "connectors.oauthDisconnect",
  // FIX31 Part 2: configured/enabled/connected per oauth2 connector — never a token.
  "connectors.oauthStatus", "connectors.addCustomDialog",
  // FIX69: a remote (https) MCP server, entered in a HOST-owned form — the
  // page opens the form and learns the outcome; the URL and the key never
  // pass through the page.
  "connectors.addRemoteDialog",
  "settings.get", "settings.set",
  "folders.list", "folders.pickAndGrant", "folders.revoke",
  "globalNotes.list", "globalNotes.delete",
  // FIX6: the approved-site list is control-plane. The page may LIST and REVOKE
  // approved sites; there is deliberately NO "sites.add" op — a site is approved
  // ONLY through the consent prompt, so the page (and the model) can never name a
  // site to approve. Adding these to the page vocabulary is the whole surface.
  "sites.list", "sites.revoke",
  // FIX1 §1.2: the ONE new page-facing op — the browser-extension folder escape
  // hatch. No args (the page names no path); the host opens a native dialog and
  // forwards the chosen folder to the sidecar. This is the single op §1.2 budgets;
  // the closed set grows by exactly one, deliberately.
  "browser.pickExtensionFolder",
  "browser.rotatePairingToken", // FIX70: mint a fresh pairing token; the page learns only that it happened — never a token, never a tool
  // FIX33 A-D5: the context meter's numbers (read) and "summarise and continue"
  // (the page asks; the host summarises with the user's instructions and sets
  // the boundary). Page-only ops, never tools — the model cannot compact.
  "context.info", "context.compact",
  // FIX34 Part A: the quick-ask window. shell.prefs.* read/write the HOST-owned
  // shell preferences (quick-ask hotkey, tray-click behaviour — never a tool);
  // quick.close hides the quick-ask window (the page's Esc); quick.openInMain
  // hands a quick-ask conversation to the main window. Page-only, all four.
  "shell.prefs.get", "shell.prefs.set", "quick.close", "quick.openInMain",
  // FIX127 C3: the new-chat page abandons a conversation it created for an
  // attachment and never sent into. On the web a keepalive DELETE does it on
  // pagehide; Electron refuses that cross-origin request while the renderer
  // is torn down, so the page asks the host, which deletes it ONLY if the
  // server still says it has no messages. Page-only, never a tool.
  "conversation.discardEmpty",
  // FIX130 A4b: the Memory page changed the saved memories (edit, delete,
  // accept, reject, add, clear). The host drops its cached copy so the next
  // turn reads them again. No arguments, nothing returned; never a tool.
  "memory.changed",
] as const);
export type HostQueryOp = (typeof HOST_QUERY_OPS)[number];
export const E3_SUPPORTED_OPS: readonly HostQueryOp[] = Object.freeze(["session.logout", "diag.reportRendererError"]);

/** FIX1 §3.3: the 8 tools that ride the browser bridge (open_url is NOT here — it
 * launches the OS default browser and needs no pipe). Withheld from the model's
 * tool list for a turn when the bridge is down. Lives here (not in turn-coordinator)
 * so it is testable without pulling in Electron. */
export const BROWSER_BRIDGE_TOOLS: ReadonlySet<string> = Object.freeze(
  new Set([
    "browser_list_tabs", "browser_tabs", "browser_navigate", "browser_read_page",
    "browser_find", "browser_click", "browser_type", "browser_screenshot",
  ]),
) as ReadonlySet<string>;

/** The tools that WRITE durable memory. Never offered to a chat whose memory
 *  switch is off — under any setting, which is what makes "nothing said here
 *  will be remembered" a fact about the build rather than a hope about the
 *  model. `remember` writes the sidecar's local note store; the web client
 *  omits its save_memory the same way. */
export const MEMORY_WRITE_TOOLS: ReadonlySet<string> = Object.freeze(
  new Set(["remember"]),
) as ReadonlySet<string>;

/** The tools that READ across conversations (other chats and saved
 *  memories). Withheld from a chat whose memory is not active exactly like the
 *  write half: recalling past chats IS a memory read. FIX56.1 had kept them in
 *  memory-off and temporary chats as "read-only"; FIX130 A2 reverses that — a
 *  temporary chat neither reads nor writes, and "memory off" means off. The
 *  same rule as chat-edge (tools.go ToolDefs / execSearchPastConversations);
 *  host-tools.ts also refuses a call that arrives anyway. The sets stay split
 *  so the union is still pinned (memory-tool-gating.test.ts). */
export const MEMORY_READ_TOOLS: ReadonlySet<string> = Object.freeze(
  new Set(["search_past_conversations"]),
) as ReadonlySet<string>;

/** FIX130 A2 — the tool list a chat gets: every memory tool (write AND read)
 *  only when memory is active (switched on and not a temporary chat). */
export function withholdMemoryTools<T extends { function: { name: string } }>(defs: T[], memoryActive: boolean): T[] {
  if (memoryActive) return defs;
  return defs.filter((d) => !MEMORY_WRITE_TOOLS.has(d.function.name) && !MEMORY_READ_TOOLS.has(d.function.name));
}

export const MAX_MESSAGE_BYTES = 1_048_576;
export const MAX_TEXT_CHARS = 200_000;
export const MAX_ATTACHMENTS = 32;

export interface TurnSubmitPayload {
  readonly conversationId: string;
  readonly text: string;
  readonly attachmentHandles: readonly string[];
  readonly modelId: string | null;
  // FIX9 Part B: image mode — biases the model to call generate_image and offers
  // that host tool this turn. A plain boolean on the EXISTING message (not a new
  // message type), validated below. (videoMode is deliberately NOT carried — the
  // 🎬 button is disabled in hosted mode; video gen is an async job, not this round.)
  readonly imageMode: boolean;
  // FIX11 §3: when set, this turn is an EDIT of that user message — the host
  // forks the conversation at it (chat-edge /edit, server-authoritative
  // branching) instead of appending a linear turn, so the ◀ n/m ▶ navigator
  // works. A field on the EXISTING message (like modelId), NOT a new message type.
  readonly editMessageId: string | null;
  // FIX34 Part A: a turn from the quick-ask window — answered with NO tools (no
  // tool list, so no consent prompt can ever appear in the small window). A
  // boolean on the EXISTING message, validated below, like imageMode.
  readonly quick: boolean;
}
export interface TurnCancelPayload {
  readonly conversationId: string;
}
export interface ConsentRespondPayload {
  readonly promptId: string;
  readonly decision: "allow" | "deny";
  // FIX6: "always" is the Claude-in-Chrome "Always allow actions on this site"
  // grant — it PERSISTS (allow_always), unlike "session" (memory only). Browser-
  // site prompts offer "once" + "always"; other tools keep "once" + "session".
  readonly scope: "once" | "session" | "always";
}
/** FIX73: the page's answer to a question.request. `answers` is keyed by the
 *  question text (the reference shape) — each value the chosen label(s), or the
 *  user's own text for the "Something else" row; `response` is a free reply
 *  typed INSTEAD of answering; outcome "skipped" is the decline (not a
 *  cancel: the turn goes on and the model is told nobody chose). */
export interface QuestionRespondPayload {
  readonly promptId: string;
  readonly outcome: "answered" | "skipped";
  readonly answers: Readonly<Record<string, readonly string[]>>;
  readonly response: string | null;
}
export const MAX_QUESTION_FREE_TEXT = 2000;
export interface UiReadyPayload {
  readonly conversationId: string | null;
  readonly lastSeq: number;
  /** FIX41 — which run `lastSeq` counts. The page's cursor is
   *  (runId, seq): a number from a previous generation says nothing about
   *  this one, and the host replays from the start rather than trusting
   *  it. Optional so a page that predates FIX41 still resumes exactly as
   *  it did (the host treats a missing runId as "same run"). */
  readonly runId: string | null;
}
export interface HostQueryPayload {
  readonly requestId: string;
  readonly op: HostQueryOp;
  readonly args: Record<string, unknown>;
}

export type Envelope =
  | { readonly ok: true; readonly type: TurnInboundType | "host.query"; readonly payload: unknown }
  | { readonly ok: false; readonly reason: string };

/** The shared gate for both vocabularies: object + nonce + closed type. */
export function parseEnvelope(raw: unknown, expectedNonce: string | null): Envelope {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { ok: false, reason: "not-an-object" };
  const obj = raw as Record<string, unknown>;
  if (expectedNonce === null) return { ok: false, reason: "no-nonce-for-this-page" };
  if (typeof obj["nonce"] !== "string" || obj["nonce"] !== expectedNonce) return { ok: false, reason: "nonce-mismatch" };
  const type = obj["type"];
  if (typeof type !== "string") return { ok: false, reason: "type-missing" };
  if ((TURN_INBOUND_TYPES as readonly string[]).includes(type) || (CONTROL_INBOUND_TYPES as readonly string[]).includes(type)) {
    return { ok: true, type: type as TurnInboundType | "host.query", payload: obj["payload"] };
  }
  return { ok: false, reason: `unknown-type:${type.slice(0, 40)}` };
}

type Validated<T> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly reason: string };

/** An opaque attachment handle: short, single-token, free of every character
 * a path or URI needs. Deny-by-shape, not allow-by-luck (BridgeProtocol.cs). */
export function isOpaqueHandle(value: string): boolean {
  if (value.length === 0 || value.length > 128) return false;
  return /^[A-Za-z0-9_-]+$/.test(value);
}

export function validateTurnSubmit(payload: unknown): Validated<TurnSubmitPayload> {
  if (typeof payload !== "object" || payload === null) return { ok: false, reason: "payload-not-object" };
  const p = payload as Record<string, unknown>;
  const conversationId = p["conversationId"];
  if (typeof conversationId !== "string" || conversationId.length === 0 || conversationId.length > 128) {
    return { ok: false, reason: "conversationId-missing" };
  }
  const text = p["text"];
  if (typeof text !== "string") return { ok: false, reason: "text-missing" };
  if (text.length > MAX_TEXT_CHARS) return { ok: false, reason: "text-too-long" };
  const handles: string[] = [];
  const raw = p["attachmentHandles"];
  if (raw !== undefined) {
    if (!Array.isArray(raw)) return { ok: false, reason: "attachmentHandles-not-array" };
    if (raw.length > MAX_ATTACHMENTS) return { ok: false, reason: "attachmentHandles-too-many" };
    for (const h of raw) {
      if (typeof h !== "string") return { ok: false, reason: "attachmentHandle-not-string" };
      if (!isOpaqueHandle(h)) return { ok: false, reason: "attachmentHandle-path-shaped" };
      handles.push(h);
    }
  }
  const modelId = p["modelId"];
  if (modelId !== undefined && modelId !== null && typeof modelId !== "string") return { ok: false, reason: "modelId-not-string" };
  if (typeof modelId === "string" && modelId.length > 128) return { ok: false, reason: "modelId-too-long" };
  const imageMode = p["imageMode"] === true;
  const editMessageId = p["editMessageId"];
  if (editMessageId !== undefined && editMessageId !== null && typeof editMessageId !== "string") return { ok: false, reason: "editMessageId-not-string" };
  if (typeof editMessageId === "string" && editMessageId.length > 128) return { ok: false, reason: "editMessageId-too-long" };
  const quick = p["quick"] === true;
  return { ok: true, value: { conversationId, text, attachmentHandles: handles, modelId: typeof modelId === "string" && modelId ? modelId : null, imageMode, editMessageId: typeof editMessageId === "string" && editMessageId ? editMessageId : null, quick } };
}

export function validateTurnCancel(payload: unknown): Validated<TurnCancelPayload> {
  if (typeof payload !== "object" || payload === null) return { ok: false, reason: "payload-not-object" };
  const id = (payload as Record<string, unknown>)["conversationId"];
  if (typeof id !== "string" || id.length === 0) return { ok: false, reason: "conversationId-missing" };
  return { ok: true, value: { conversationId: id } };
}

export function validateConsentRespond(payload: unknown): Validated<ConsentRespondPayload> {
  if (typeof payload !== "object" || payload === null) return { ok: false, reason: "payload-not-object" };
  const p = payload as Record<string, unknown>;
  if (typeof p["promptId"] !== "string" || p["promptId"].length === 0) return { ok: false, reason: "promptId-missing" };
  if (p["decision"] !== "allow" && p["decision"] !== "deny") return { ok: false, reason: "decision-invalid" };
  if (p["scope"] !== "once" && p["scope"] !== "session" && p["scope"] !== "always") return { ok: false, reason: "scope-invalid" };
  return { ok: true, value: { promptId: p["promptId"], decision: p["decision"], scope: p["scope"] } };
}

export function validateQuestionRespond(payload: unknown): Validated<QuestionRespondPayload> {
  if (typeof payload !== "object" || payload === null) return { ok: false, reason: "payload-not-object" };
  const p = payload as Record<string, unknown>;
  if (typeof p["promptId"] !== "string" || p["promptId"].length === 0 || p["promptId"].length > 64) return { ok: false, reason: "promptId-missing" };
  if (p["outcome"] !== "answered" && p["outcome"] !== "skipped") return { ok: false, reason: "outcome-invalid" };
  const answers: Record<string, string[]> = {};
  const rawA = p["answers"];
  if (rawA !== undefined && rawA !== null) {
    if (typeof rawA !== "object" || Array.isArray(rawA)) return { ok: false, reason: "answers-not-object" };
    const entries = Object.entries(rawA as Record<string, unknown>);
    if (entries.length > 4) return { ok: false, reason: "answers-too-many" };
    for (const [q, v] of entries) {
      if (q.length === 0 || q.length > 500) return { ok: false, reason: "answer-key-invalid" };
      if (!Array.isArray(v) || v.length > 4) return { ok: false, reason: "answer-not-array" };
      const out: string[] = [];
      for (const item of v) {
        if (typeof item !== "string") return { ok: false, reason: "answer-not-string" };
        if (item.length > MAX_QUESTION_FREE_TEXT) return { ok: false, reason: "answer-too-long" };
        out.push(item);
      }
      answers[q] = out;
    }
  }
  const response = p["response"];
  if (response !== undefined && response !== null && typeof response !== "string") return { ok: false, reason: "response-not-string" };
  if (typeof response === "string" && response.length > MAX_QUESTION_FREE_TEXT) return { ok: false, reason: "response-too-long" };
  return { ok: true, value: { promptId: p["promptId"], outcome: p["outcome"], answers, response: typeof response === "string" && response.trim() ? response : null } };
}

export function validateUiReady(payload: unknown): Validated<UiReadyPayload> {
  if (payload === undefined || payload === null) return { ok: true, value: { conversationId: null, lastSeq: 0, runId: null } };
  if (typeof payload !== "object") return { ok: false, reason: "payload-not-object" };
  const p = payload as Record<string, unknown>;
  const conv = p["conversationId"];
  if (conv !== undefined && conv !== null && typeof conv !== "string") return { ok: false, reason: "conversationId-not-string" };
  const seq = p["lastSeq"];
  if (seq !== undefined && (typeof seq !== "number" || !Number.isInteger(seq) || seq < 0)) return { ok: false, reason: "lastSeq-invalid" };
  const runId = p["runId"];
  if (runId !== undefined && runId !== null && typeof runId !== "string") return { ok: false, reason: "runId-not-string" };
  return {
    ok: true,
    value: {
      conversationId: typeof conv === "string" && conv ? conv : null,
      lastSeq: typeof seq === "number" ? seq : 0,
      runId: typeof runId === "string" && runId ? runId : null,
    },
  };
}

export function validateHostQuery(payload: unknown): Validated<HostQueryPayload> {
  if (typeof payload !== "object" || payload === null) return { ok: false, reason: "payload-not-object" };
  const p = payload as Record<string, unknown>;
  const requestId = p["requestId"];
  if (typeof requestId !== "string" || requestId.length === 0 || requestId.length > 64) return { ok: false, reason: "requestId-missing" };
  const op = p["op"];
  if (typeof op !== "string" || !(HOST_QUERY_OPS as readonly string[]).includes(op)) return { ok: false, reason: `unknown-op:${String(op).slice(0, 40)}` };
  const args = p["args"];
  if (args !== undefined && (typeof args !== "object" || args === null || Array.isArray(args))) return { ok: false, reason: "args-not-object" };
  return { ok: true, value: { requestId, op: op as HostQueryOp, args: (args as Record<string, unknown>) ?? {} } };
}
