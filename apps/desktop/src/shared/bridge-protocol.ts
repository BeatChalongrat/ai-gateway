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
  "tasks.list", "tasks.create", "tasks.update", "tasks.setEnabled", "tasks.delete", "tasks.runNow",
  "tasks.runs", // FIX8 A1.4: a task's run history (reads only; control-plane like the rest)
  "connectors.listCatalog", "connectors.listInstalled", "connectors.install", "connectors.uninstall",
  "connectors.oauthConnect", "connectors.oauthDisconnect", "connectors.addCustomDialog",
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
export interface UiReadyPayload {
  readonly conversationId: string | null;
  readonly lastSeq: number;
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
  return { ok: true, value: { conversationId, text, attachmentHandles: handles, modelId: typeof modelId === "string" && modelId ? modelId : null, imageMode, editMessageId: typeof editMessageId === "string" && editMessageId ? editMessageId : null } };
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

export function validateUiReady(payload: unknown): Validated<UiReadyPayload> {
  if (payload === undefined || payload === null) return { ok: true, value: { conversationId: null, lastSeq: 0 } };
  if (typeof payload !== "object") return { ok: false, reason: "payload-not-object" };
  const p = payload as Record<string, unknown>;
  const conv = p["conversationId"];
  if (conv !== undefined && conv !== null && typeof conv !== "string") return { ok: false, reason: "conversationId-not-string" };
  const seq = p["lastSeq"];
  if (seq !== undefined && (typeof seq !== "number" || !Number.isInteger(seq) || seq < 0)) return { ok: false, reason: "lastSeq-invalid" };
  return { ok: true, value: { conversationId: typeof conv === "string" && conv ? conv : null, lastSeq: typeof seq === "number" ? seq : 0 } };
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
