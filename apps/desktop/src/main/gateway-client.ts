// Gateway client (E3) — ported from SranDesktop.Infrastructure/Api/
// GatewayApiClient.cs + AgentKeyProvider.cs + GatewayChunkParser.
//
//   * The turn goes to the gateway's OpenAI-compatible /v1/chat/completions
//     with a per-user `gw_` agent key, provisioned once through the portal
//     routes (Bearer = the portal access token the host already owns) and
//     kept in safeStorage. The key is never logged (its server-issued prefix
//     is, once, at provisioning).
//   * SSE frames are parsed into the same event vocabulary the WebView2
//     shell emitted: content_delta, reasoning_delta, analysis_status,
//     fallback_info, reasoning_notice, error, done.
//   * An idle watchdog aborts a stream that sends nothing for 60 s; a stream
//     is never retried once it has begun (partial output must not repeat).

import type { Logger } from "./logger";
import { t } from "./strings";
import { classifyStreamFailure, eofWithoutTerminal, isStopReason, isTerminalCause, type StopReason, type TerminalCause } from "./terminal-cause";

/** FIX66 Part 4 — map a failed /v1/images/generations response into one
 *  truthful sentence for the model to relay. The desktop calls the image
 *  endpoint directly, so a failure here must never read as the chat model's
 *  fault, and an out-of-credits account must not be dressed up as a passing
 *  rate limit ("try again") when a top-up is what's actually needed.
 *  Pure + exported so it is unit-testable without a live gateway. */
export function classifyDesktopImageError(status: number, body: string): string {
  const lower = (body || "").toLowerCase();
  const outOfCredit =
    lower.includes("insufficient_quota") ||
    lower.includes("billing") ||
    lower.includes("exceeded your current quota") ||
    lower.includes("out of credit");
  if (outOfCredit)
    return "Image generation is unavailable because the image service's account is out of credits (this is separate from the chat model in use). It needs an administrator to top it up — retrying will not help.";
  if (status === 429 || lower.includes("rate_limit") || lower.includes("rate limit"))
    return "The image service is busy right now (this is separate from the chat model in use). Wait a moment and try again.";
  if (status === 401 || status === 403 || lower.includes("no api key") || lower.includes("permission"))
    return "The image service is not authorized — its API key or model permission needs an administrator's attention.";
  if (status === 503 || lower.includes("not configured") || lower.includes("no api key configured"))
    return "Image generation is not configured on this deployment. Please contact your administrator.";
  return "The image could not be generated right now. Please try again in a moment.";
}

/** OpenAI-compatible wire shapes (GatewayModels.cs). E4 adds the tool chain:
 * assistant messages with tool_calls, tool-role results, and multimodal
 * user content (a screenshot image block after a screenshot tool). */
export interface GatewayToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}
export type GatewayContentBlock = { type: "text"; text: string } | { type: "image_url"; image_url: { url: string } };
export interface GatewayMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | GatewayContentBlock[] | null;
  tool_calls?: GatewayToolCall[];
  tool_call_id?: string;
  name?: string;
}
export interface GatewayToolDefinition {
  type: "function";
  function: { name: string; description: string; parameters: unknown };
}
export interface CompletionRequest {
  model: string;
  messages: GatewayMessage[];
  max_tokens?: number;
  tools?: GatewayToolDefinition[];
  tool_choice?: "auto";
  /** Web parity: chat-edge attaches this on every turn; the gateway no-ops for models that cannot reason.
   *  FIX72: "off" is now expressible — the gateway honours it for a provider
   *  family whose box has a measured thinking off-switch (sranai qwen via the
   *  vLLM enable_thinking chat-template kwarg); for every other model it is a
   *  no-op, exactly as "auto" already was on non-reasoning models. */
  reasoning?: { mode: "auto" | "off"; show_summary: boolean };
  /** FIX36 Part D — caller-side DLP options ride in the request metadata.
   *  The state extractor sets {dlp_response_restore:"off"} so the gateway
   *  skips restoring masked values in THAT reply: the summary then carries
   *  placeholders (like the web extractor's ContentMasked source) instead
   *  of raw PII. Normal chat turns never set this. */
  metadata?: Record<string, unknown>;
  /** FIX104 — on `model="auto"`, what the compiled request needs and the window
   *  it was budgeted against, so the router cannot hand it to a model that
   *  would refuse it (chat-edge's applyContextHints, FIX102 Part A). Travels
   *  inside routing_hints only; never a field of its own on the wire. */
  routingContext?: { minContextTokens: number; preferredContextTokens: number };
}
/** FIX3 §1: provider-agnostic capability hints for `model="auto"`, mirroring the
 * gateway's RoutingHints (schemas/public.py) and what chat-edge sends. Partial is
 * fine — the router still routes; the point is to be on the key-aware path at all. */
export function buildRoutingHints(request: CompletionRequest): Record<string, unknown> {
  const toolCount = request.tools?.length ?? 0;
  const hasVision = request.messages.some(
    (m) => Array.isArray(m.content) && m.content.some((b) => b.type === "image_url"),
  );
  const convTurns = request.messages.filter((m) => m.role === "user" || m.role === "assistant").length;
  const hints: Record<string, unknown> = {
    needs_vision: hasVision,
    needs_reasoning: !!request.reasoning,
    has_tools: toolCount > 0,
    tool_count: toolCount,
    conv_turn_count: convTurns,
  };
  // chat-edge sends both with omitempty: min is HARD (no_capable_model when
  // nothing holds it), preferred is SOFT and 0 when the default was used.
  const ctx = request.routingContext;
  if (ctx && ctx.minContextTokens > 0) hints["min_context_tokens"] = Math.floor(ctx.minContextTokens);
  if (ctx && ctx.preferredContextTokens > 0) hints["preferred_context_tokens"] = Math.floor(ctx.preferredContextTokens);
  return hints;
}

/** FIX5 §2: classify a gateway HTTP 429 into the message a user should see. A
 * 429 is EITHER out-of-credits OR rate-limited, and the two need OPPOSITE advice
 * (add credits vs just wait) — the gateway distinguishes them by `error.code`
 * (backend/app/core/exceptions.py + middleware/error_handler.py, both → 429):
 * QuotaExceededError → "insufficient_credits" with details.credit_balance; the
 * per-key limiter → "rate_limit_exceeded". The rule: say CREDITS only on the
 * explicit credits signal; treat every other 429 as rate limiting — telling a
 * rate-limited user to buy credits is the wrong-advice failure. Pure so it can be
 * tested against the real captured body shapes without mocking the network. */
export function classify429(
  body: { error?: { code?: string; message?: string; details?: Record<string, unknown> } } | null,
  retryAfter: string | null,
  accountLabel: string,
): { code: string; message: string } {
  const err = body?.error;
  const isCredits = err?.code === "insufficient_credits" || (err?.details != null && "credit_balance" in err.details);
  // AM10 F — the sentences come from the strings table in the current
  // language (the page shows the host's sentence for a code its own table
  // does not know, so an English one would read as English in Thai mode).
  if (isCredits) {
    return { code: "insufficient_credits", message: t("error.outOfCredits", { account: accountLabel }) };
  }
  const wait = retryAfter && /^\d+$/.test(retryAfter) ? t("error.rateLimited.retryIn", { seconds: retryAfter }) : t("error.rateLimited.wait");
  return { code: err?.code ?? "rate_limited", message: t("error.rateLimited", { wait }) };
}

/** FIX21 §2: best-effort backend reason from an error response body. Prefers
 * the `{error:{message}}` envelope the gateway and Python backend both emit
 * (httpx/json.go, ErrorResponse); falls back to the trimmed raw body. Never
 * throws, and only ever reads the RESPONSE — it cannot surface the key. */
export async function readErrorReason(res: Response): Promise<string> {
  try {
    const text = (await res.text()).slice(0, 600);
    try {
      const parsed = JSON.parse(text) as { error?: { message?: unknown } };
      const msg = parsed?.error?.message;
      if (typeof msg === "string" && msg.trim()) return msg.trim();
    } catch {
      /* body was not JSON — fall through to the raw text */
    }
    return text.trim();
  } catch {
    return "";
  }
}

export type StreamEvent =
  | { kind: "content_delta"; delta: string }
  /** A streamed tool-call fragment; the caller accumulates by index. */
  | { kind: "tool_call_delta"; index: number; id: string | null; name: string | null; argumentsDelta: string }
  | { kind: "reasoning_delta"; delta: string }
  | { kind: "analysis_status"; status: string }
  // FIX133 A2 — the gateway's turn queue: where this turn stands while it
  // waits for a generation slot (position 0 = it started).
  | { kind: "queued"; position: number; ahead: number; active: number; waiting: number; max_active: number; estimate_s: number | null }
  | { kind: "fallback_info"; requested_model: string; actual_model: string; reason: string | null }
  /** FIX50 P1-C — input DLP masked part of the question before the model
   *  read it. Counts per detector only, never values. */
  | { kind: "dlp_notice"; findings: number; by_detector: Record<string, number> }
  /** FIX35 Part B — the gateway named the model that is answering (before
   *  content on an auto route). */
  | { kind: "model_resolved"; model: string }
  | { kind: "reasoning_notice"; model: string | null; reason: string | null }
  /** FIX118 — `retryable` is the gateway's verdict (forwarded, never
   *  invented); `cause` is the terminal cause when the host itself ended
   *  the stream (docs/contracts/terminal_cause.json); `details` carries
   *  the gateway's structured payload or the host's transport text. */
  | { kind: "error"; code: string; message: string; retryable?: boolean; cause?: TerminalCause; details?: Record<string, unknown>; attempts?: unknown[] }
  /** FIX119 — `stop_reason` is the gateway's closed-set reason from the
   *  final chunk (absent on [DONE] and on an older gateway);
   *  `provider_request_id` / `gateway_request_id` are the ids of the call
   *  that answered, when the chunk named them. */
  | { kind: "done"; finish_reason: string | null; stop_reason?: StopReason; provider_request_id?: string; gateway_request_id?: string };

export interface KeyStore {
  load(): string | null;
  save(key: string): void;
  clear(): void;
}

const IDLE_TIMEOUT_MS = 60_000;
// FIX124 — while a stream is open the host asks, on a tick, whether the
// machine still has a network. Offline AND nothing received for
// OFFLINE_QUIET_MS ends the stream as disconnected instead of waiting out the
// idle timeout. The silence is required: a link that blinks for a second
// under a stream that keeps delivering has lost nothing, and ending it would
// throw away (and bill) a generation that was fine.
const OFFLINE_CHECK_MS = 2_000;
const OFFLINE_QUIET_MS = 10_000;
const KEY_NAME = "SRAN Desktop Agent";
const FALLBACK_PROJECT_NAME = "SRAN Desktop";

export class GatewayClient {
  private cachedKey: string | null = null;
  private provisioning: Promise<string | null> | null = null;

  constructor(
    private readonly gatewayUrl: string,
    private readonly portalAccessToken: () => string | null,
    private readonly keyStore: KeyStore,
    private readonly log: Logger,
    /** FIX124 — `isOnline` is Electron's net.isOnline, injected (index.ts) so
     *  this module stays free of Electron; absent means online, which is
     *  every test and non-Electron context before this option existed. */
    private readonly options: { isOnline?: () => boolean } = {},
  ) {}

  /** FIX124 — true when the machine reports no network AND that says
   *  something about this gateway. A gateway on this machine (loopback, the
   *  dev stack) is reachable with every adapter down, so the network's state
   *  is no evidence about it. A probe that throws is read as online: it must
   *  never be the thing that ends a turn. */
  private machineOffline(): boolean {
    if (!this.options.isOnline || isLoopbackUrl(this.gatewayUrl)) return false;
    try {
      return this.options.isOnline() === false;
    } catch {
      return false;
    }
  }

  /** FIX5 §2: a legible " (account you@example.com)" for the out-of-credits
   * message, so the user knows WHICH account is dry — the desktop signs in as its
   * own account, not necessarily the one the person expects. Best-effort: the
   * portal JWT's email claim (payload segment, base64url), never verified here
   * (display only) and never thrown. Returns "" when the email is unavailable. */
  private accountLabel(): string {
    try {
      const token = this.portalAccessToken();
      if (!token) return "";
      const seg = token.split(".")[1];
      if (!seg) return "";
      const json = Buffer.from(seg.replace(/-/g, "+").replace(/_/g, "/"), "base64").toString("utf8");
      const email = (JSON.parse(json) as { email?: unknown }).email;
      return typeof email === "string" && email.includes("@") ? ` (account ${email})` : "";
    } catch {
      return "";
    }
  }

  /** The agent key: cached → safeStorage → provisioned via the portal. */
  async getOrProvisionKey(): Promise<string | null> {
    if (this.cachedKey) return this.cachedKey;
    if (this.provisioning) return this.provisioning;
    this.provisioning = (async () => {
      try {
        const stored = this.keyStore.load();
        if (stored) {
          this.cachedKey = stored;
          this.log.info("Agent gateway key loaded from safeStorage");
          return stored;
        }
        const key = await this.provision();
        if (key) {
          this.keyStore.save(key);
          this.cachedKey = key;
        }
        return key;
      } finally {
        this.provisioning = null;
      }
    })();
    return this.provisioning;
  }

  invalidateKey(): void {
    this.cachedKey = null;
    this.keyStore.clear();
    this.log.warn("Agent gateway key invalidated (will re-provision on next use)");
  }

  private async provision(): Promise<string | null> {
    const token = this.portalAccessToken();
    if (!token) {
      this.log.warn("Agent key provisioning failed: no portal session");
      return null;
    }
    const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" };
    try {
      let projectId: string | null = null;
      const list = await fetch(`${this.gatewayUrl}/portal/projects`, { headers, signal: AbortSignal.timeout(15_000) });
      if (list.ok) {
        const body = (await list.json()) as { projects?: { id?: string }[] };
        projectId = body.projects?.[0]?.id ?? null;
      }
      if (!projectId) {
        const created = await fetch(`${this.gatewayUrl}/portal/projects`, {
          method: "POST",
          headers,
          body: JSON.stringify({ name: FALLBACK_PROJECT_NAME }),
          signal: AbortSignal.timeout(15_000),
        });
        if (!created.ok) {
          this.log.warn(`Agent key provisioning failed: project create HTTP ${created.status}`);
          return null;
        }
        const body = (await created.json()) as { id?: string; project?: { id?: string } };
        projectId = body.id ?? body.project?.id ?? null;
      }
      if (!projectId) {
        this.log.warn("Agent key provisioning failed: no usable portal project");
        return null;
      }
      const res = await fetch(`${this.gatewayUrl}/portal/projects/${encodeURIComponent(projectId)}/keys`, {
        method: "POST",
        headers,
        body: JSON.stringify({ name: KEY_NAME }),
        signal: AbortSignal.timeout(15_000),
      });
      if (!res.ok) {
        this.log.warn(`Agent key creation failed: HTTP ${res.status}`);
        return null;
      }
      const body = (await res.json()) as { api_key?: string; key_prefix?: string };
      if (!body.api_key) {
        this.log.warn("Agent key creation returned no api_key");
        return null;
      }
      this.log.info(`Provisioned agent gateway key ${body.key_prefix ?? "(prefix unknown)"}… in project ${projectId}`);
      return body.api_key;
    } catch (err) {
      this.log.warn(`Agent key provisioning failed: ${(err as Error).message}`);
      return null;
    }
  }

  /** FIX5 §3: GET /v1/search/config — the tenant's web-search capability, the
   * CHEAP authority (no provider call, nothing billed, no DLP egress). The
   * backend returns { enabled, search_enabled, provider_count } where `enabled`
   * already means "tenant flag on AND at least one provider configured" — the
   * only thing that makes a real search succeed. Used to withhold web_search when
   * the tenant has it off, instead of offering a tool that 400s. Returns null on
   * any failure so the caller can fall back to its last-known value. */
  async searchConfig(signal: AbortSignal): Promise<{ enabled: boolean } | null> {
    const key = await this.getOrProvisionKey();
    if (!key) return null;
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/search/config`, {
        method: "GET",
        headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
        signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { enabled?: unknown };
      return { enabled: body.enabled === true };
    } catch {
      return null;
    }
  }

  /** FIX22 §1a: would storing `text` trip the gateway's content guardrail?
   * POST /v1/guardrails/check — the chat path's OWN checker, so the host keeps
   * no rules of its own. Returns null when the gateway gives no answer (route
   * missing on an older gateway, network, timeout) so the caller can decide
   * how to fall back. Never throws, never logs the text. */
  async guardrailCheck(text: string, signal: AbortSignal): Promise<{ passed: boolean; ruleName: string | null; matched: string | null } | null> {
    const key = await this.getOrProvisionKey();
    if (!key) return null;
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/guardrails/check`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ text }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { passed?: unknown; rule_name?: unknown; matched?: unknown };
      if (typeof body.passed !== "boolean") return null;
      return {
        passed: body.passed,
        ruleName: typeof body.rule_name === "string" ? body.rule_name : null,
        matched: typeof body.matched === "string" ? body.matched : null,
      };
    } catch {
      return null;
    }
  }

  /** FIX22 §2: the desktop version the gateway says is current (GET
   * /v1/desktop/version → a bare string). null on ANY non-200 / error /
   * empty — the caller shows nothing. No URL, no file: notification only. */
  async latestDesktopVersion(signal: AbortSignal): Promise<string | null> {
    const key = await this.getOrProvisionKey();
    if (!key) return null;
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/desktop/version`, {
        method: "GET",
        headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
        signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { version?: unknown };
      return typeof body.version === "string" && body.version.trim() ? body.version.trim() : null;
    } catch {
      return null;
    }
  }

  /** POST /v1/search with the agent key (web_search, host-side in E4). */
  /** FIX9 §1.1: the model ids the gateway accepts (GET /v1/models), for host-side
   * validation of the page-supplied model. FIX47.6 Part C — each id now carries
   * whether the catalog says it can use tools, so the coordinator can withhold
   * tool definitions from a tools-blind model instead of erroring every turn
   * (chat-edge's attachTools has done the same since FIX28). Returns null on
   * failure (caller keeps its last-known map / fails safe). Never throws.
   * FIX104 — and the catalogue's context window (0 when it gives none), the
   * number the desktop now budgets a conversation against. */
  async listModels(signal: AbortSignal): Promise<Map<string, { tools: boolean; vision: boolean; contextWindow: number }> | null> {
    const key = await this.getOrProvisionKey();
    if (!key) return null;
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/models`, {
        method: "GET",
        headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
        signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { data?: { id?: unknown; context_window?: unknown; capabilities?: { tools?: unknown; vision?: unknown } }[] };
      const map = new Map<string, { tools: boolean; vision: boolean; contextWindow: number }>();
      for (const m of body.data ?? []) {
        if (typeof m.id === "string" && m.id.length > 0) {
          // FIX65 — vision rides along: a model the catalogue says cannot
          // read images gets no image blocks from the host (see
          // TurnCoordinator.modelSupportsVision).
          const contextWindow = typeof m.context_window === "number" && m.context_window > 0 ? Math.floor(m.context_window) : 0;
          map.set(m.id, { tools: m.capabilities?.tools === true, vision: m.capabilities?.vision === true, contextWindow });
        }
      }
      return map.size > 0 ? map : null;
    } catch {
      return null;
    }
  }

  /** FIX9 Part B: generate an image (POST /v1/images/generations) with the agent
   * key — host-side, the run_python/web_search precedent, so the sidecar never
   * holds the JWT. Returns a data: URI on success. */
  async generateImage(prompt: string, signal: AbortSignal): Promise<{ ok: true; dataUri: string; revised?: string; producedBy?: string } | { ok: false; error: string }> {
    const key = await this.getOrProvisionKey();
    if (!key) return { ok: false, error: "The app could not obtain a gateway key for this account." };
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/images/generations`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ prompt, response_format: "b64_json", n: 1 }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(120_000)]),
      });
      if (!res.ok) {
        const detail = (await res.text().catch(() => "")).slice(0, 400);
        // FIX66 Part 4 — hand the model a truthful sentence, not raw HTTP/JSON.
        // In particular an out-of-credits image account must not read as the
        // chat model's fault (the desktop path calls the gateway directly).
        return { ok: false, error: classifyDesktopImageError(res.status, detail) };
      }
      const body = (await res.json()) as { data?: { b64_json?: string; url?: string; revised_prompt?: string }[]; model?: string; provider?: string };
      // FIX67 — which destination produced it (after a fallback this differs
      // from the primary), for "made by …" attribution.
      const producedBy = body.model || body.provider || undefined;
      const first = body.data?.[0];
      if (first?.b64_json) return { ok: true, dataUri: `data:image/png;base64,${first.b64_json}`, revised: first.revised_prompt, producedBy };
      if (first?.url) return { ok: true, dataUri: first.url, revised: first.revised_prompt, producedBy };
      return { ok: false, error: "Image generation returned no image." };
    } catch (err) {
      return { ok: false, error: `Image generation failed: ${(err as Error).message}` };
    }
  }

  async search(query: string, maxResults: number, signal: AbortSignal): Promise<{ ok: true; results: { title: string; url: string; snippet: string | null }[] } | { ok: false; error: string }> {
    const key = await this.getOrProvisionKey();
    if (!key) return { ok: false, error: "The app could not obtain a gateway key for this account." };
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/search`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ query, max_results: maxResults }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]),
      });
      if (!res.ok) return { ok: false, error: `Search failed: HTTP ${res.status} ${(await res.text()).slice(0, 200)}` };
      const body = (await res.json()) as { results?: { title?: string; url?: string; snippet?: string | null }[] };
      return { ok: true, results: (body.results ?? []).map((r) => ({ title: r.title ?? "", url: r.url ?? "", snippet: r.snippet ?? null })) };
    } catch (err) {
      return { ok: false, error: `Search failed: ${(err as Error).message}` };
    }
  }

  /** Stream a completion. Yields parsed events; ends with `done` or `error`.
   *  FIX119: `opts.requestId` (the turn's run id) is sent as X-Request-Id so
   *  the gateway's and the backend's logs carry the same id the page shows;
   *  the backend's own request id comes back on the response header and is
   *  logged beside it. */
  async *streamCompletion(request: CompletionRequest, signal: AbortSignal, opts: { requestId?: string } = {}): AsyncGenerator<StreamEvent> {
    const key = await this.getOrProvisionKey();
    if (!key) {
      yield { kind: "error", code: "no_agent_key", message: t("error.noAgentKey") };
      return;
    }
    const watchdog = new AbortController();
    const onAbort = () => watchdog.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    let idleTimer: NodeJS.Timeout | null = null;
    let lastDataAt = Date.now();
    const kick = () => {
      lastDataAt = Date.now();
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => watchdog.abort(new Error("stream_stalled")), IDLE_TIMEOUT_MS);
    };
    // FIX124 — the second watchdog: see OFFLINE_QUIET_MS.
    const offlineTick = setInterval(() => {
      if (Date.now() - lastDataAt >= OFFLINE_QUIET_MS && this.machineOffline()) watchdog.abort(new Error("offline"));
    }, OFFLINE_CHECK_MS);
    offlineTick.unref?.();
    try {
      kick();
      // FIX3 §1: on `auto`, send routing_hints so the gateway takes the
      // KEY-AWARE smart-routing path (orchestrator._smart_select) instead of the
      // legacy semantic→keyword chain, which does not filter by key availability
      // and can pick a Google model with no configured key → http_503 for the
      // whole turn. chat-edge already sends hints for web-chat; the Electron host
      // talks to the gateway directly and did not. The presence of hints alone
      // switches the path; the fields refine the pick.
      const { routingContext: _routingContext, ...wire } = request;
      const body: Record<string, unknown> = { ...wire, stream: true };
      if (request.model === "auto") body["routing_hints"] = buildRoutingHints(request);
      if (request.metadata) body["metadata"] = request.metadata;
      const res = await fetch(`${this.gatewayUrl}/v1/chat/completions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", Accept: "text/event-stream", ...(opts.requestId ? { "X-Request-Id": opts.requestId } : {}) },
        body: JSON.stringify(body),
        signal: watchdog.signal,
      });
      // FIX119 — the one line that joins the run id to the backend's own
      // request id (the backend may echo ours too; its own starts "req_").
      const gatewayRequestId = (res.headers.get("x-request-id") ?? "").split(",").map((s) => s.trim()).find((s) => s.startsWith("req_")) ?? null;
      this.log.info(`GATEWAY stream opened run=${opts.requestId ?? "-"} status=${res.status} gateway_request_id=${gatewayRequestId ?? "-"} provider=${res.headers.get("x-gateway-provider") ?? "-"} model=${res.headers.get("x-gateway-model") ?? "-"}`);
      // FIX21 §2: 401 and 403 are DIFFERENT failures and must not share a
      // story. 401 = the agent key really is invalid / the session expired →
      // drop the key and re-provision. 403 = a policy or guardrail REFUSAL of
      // this request, whose reason rides the response body — the key is fine, so
      // do NOT invalidate it (invalidating hides the real cause and forces a
      // needless re-provision every refused turn). Never log or echo the key.
      if (res.status === 401) {
        this.invalidateKey();
        yield { kind: "error", code: "gateway_unauthorized", message: t("error.unauthorized") };
        return;
      }
      if (res.status === 403) {
        const reason = await readErrorReason(res);
        yield {
          kind: "error",
          code: "gateway_forbidden",
          message: reason ? t("error.forbiddenBecause", { reason }) : t("error.forbidden"),
        };
        return;
      }
      if (!res.ok) {
        let detail = "";
        let parsed: { error?: { code?: string; message?: string; details?: Record<string, unknown>; attempts?: unknown } } | null = null;
        try {
          detail = (await res.text()).slice(0, 600);
          parsed = JSON.parse(detail);
        } catch {
          /* body may not be JSON — fall back to the raw text below */
        }
        const err = parsed?.error;
        // FIX5 §2: a 429 needs credit-vs-rate-limit classification (see classify429).
        if (res.status === 429) {
          const { code, message } = classify429(parsed, res.headers.get("retry-after"), this.accountLabel());
          yield { kind: "error", code, message };
          return;
        }
        yield { kind: "error", code: err?.code ?? `http_${res.status}`, message: err?.message || detail || t("error.httpStatus", { status: res.status }), ...(Array.isArray(err?.attempts) && err.attempts.length > 0 ? { attempts: err.attempts as unknown[] } : {}) };
        return;
      }
      if (!res.body) {
        yield { kind: "error", code: "no_body", message: t("error.noStream") };
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finished = false;
      while (!finished) {
        const { value, done } = await reader.read();
        if (done) break;
        kick();
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const data = frame
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trim())
            .join("\n");
          if (!data) continue;
          for (const ev of parseChunk(data)) {
            yield ev;
            if (ev.kind === "done" || ev.kind === "error") finished = true;
          }
          if (finished) break;
        }
      }
      // FIX118 Part B — the socket closed and nothing said the answer was
      // done: no [DONE], no finish_reason, no error frame. Before this line
      // read `yield { kind: "done" }`, so a cut stream became a completed
      // turn with half an answer (or none). It is an interrupted stream.
      if (!finished) yield { kind: "error", ...eofWithoutTerminal() };
    } catch (err) {
      if (signal.aborted) return; // caller cancelled — the coordinator emits the terminal event
      const stalled = watchdog.signal.aborted && (watchdog.signal.reason as Error | undefined)?.message === "stream_stalled";
      // FIX118 Part A — a throw out of the read is classified into a
      // catalogue code (stream_stalled keeps FIX80's declared code); the
      // raw error text rides details.transport, never the message. Before,
      // code "network" carried e.message and the page showed "fetch failed".
      // FIX124 — and whichever way it ended (the offline tick, the idle
      // timeout, a read the OS tore), a machine with no network lost its
      // CONNECTION: never stream_stalled's "the model stalled", never
      // unclassified. Nothing is re-sent — the gateway cancelled (and billed)
      // the generation when the stream died; the person retries.
      const offline = this.machineOffline();
      if (offline) this.log.warn(`GATEWAY stream lost run=${opts.requestId ?? "-"}: the machine is offline (${stalled ? "idle timeout" : (err as Error)?.message ?? "read failed"})`);
      yield { kind: "error", ...classifyStreamFailure(err, { stalled, offline, idleSeconds: IDLE_TIMEOUT_MS / 1000 }) };
    } finally {
      clearInterval(offlineTick);
      if (idleTimer) clearTimeout(idleTimer);
      signal.removeEventListener("abort", onAbort);
    }
  }
}

/** FIX124 — a gateway URL that names this machine (see machineOffline). */
function isLoopbackUrl(url: string): boolean {
  try {
    const host = new URL(url).hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return host === "localhost" || host.endsWith(".localhost") || host === "::1" || /^127(\.\d{1,3}){3}$/.test(host);
  } catch {
    return false;
  }
}

/** One SSE `data:` payload → zero or more events (GatewayChunkParser port). */
export function parseChunk(data: string): StreamEvent[] {
  if (data.trim() === "[DONE]") return [{ kind: "done", finish_reason: null }];
  let root: Record<string, unknown>;
  try {
    root = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return [];
  }
  const out: StreamEvent[] = [];
  const error = root["error"];
  if (error && typeof error === "object") {
    const e = error as Record<string, unknown>;
    const frame: Extract<StreamEvent, { kind: "error" }> = { kind: "error", code: typeof e["code"] === "string" ? e["code"] : "gateway_error", message: typeof e["message"] === "string" ? e["message"] : "Gateway error" };
    // FIX118 — the gateway's own verdict and payload were dropped here
    // before; the page's Regenerate gate reads `retryable`.
    if (typeof e["retryable"] === "boolean") frame.retryable = e["retryable"];
    if (isTerminalCause(e["cause"])) frame.cause = e["cause"];
    if (e["details"] && typeof e["details"] === "object") frame.details = e["details"] as Record<string, unknown>;
    // FIX119 — the backend's request id for the failed call rides details
    // so the page's card and the logs name the same thing.
    if (typeof e["request_id"] === "string" && e["request_id"]) frame.details = { ...(frame.details ?? {}), gateway_request_id: e["request_id"] };
    // FIX121 — the per-model attempt log of an exhausted fallback walk. It is
    // a SIBLING of code/message on the gateway's frame, and it was dropped
    // here: the desktop never showed "Connection attempts" and never kept it.
    if (Array.isArray(e["attempts"]) && e["attempts"].length > 0) frame.attempts = e["attempts"] as unknown[];
    out.push(frame);
    return out;
  }
  const choices = root["choices"];
  if (!Array.isArray(choices) || choices.length === 0) {
    // Typed metadata frames ride the same stream without choices.
    const t = root["type"];
    if (t === "fallback_info" && typeof root["fallback_info"] === "object" && root["fallback_info"]) {
      const f = root["fallback_info"] as Record<string, unknown>;
      if (typeof f["requested_model"] === "string" && typeof f["actual_model"] === "string") {
        out.push({ kind: "fallback_info", requested_model: f["requested_model"], actual_model: f["actual_model"], reason: typeof f["reason"] === "string" ? f["reason"] : null });
      }
    } else if (t === "dlp_notice" && typeof root["dlp_notice"] === "object" && root["dlp_notice"]) {
      // FIX50 P1-C — the question was partially masked before the model
      // read it; the page shows a quiet expandable notice.
      const n = root["dlp_notice"] as Record<string, unknown>;
      if (typeof n["findings"] === "number" && n["findings"] > 0) {
        const by: Record<string, number> = {};
        const raw = n["by_detector"];
        if (raw && typeof raw === "object") {
          for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
            if (typeof v === "number") by[k] = v;
          }
        }
        out.push({ kind: "dlp_notice", findings: n["findings"], by_detector: by });
      }
    } else if (t === "reasoning_summary_delta" && typeof root["text"] === "string" && root["text"]) {
      out.push({ kind: "reasoning_delta", delta: root["text"] });
    } else if (t === "analysis_status" && typeof root["status"] === "string" && root["status"]) {
      out.push({ kind: "analysis_status", status: root["status"] });
    } else if (t === "queued" && typeof root["position"] === "number") {
      const num = (k: string) => (typeof root[k] === "number" ? (root[k] as number) : 0);
      out.push({
        kind: "queued",
        position: root["position"] as number,
        ahead: num("ahead"),
        active: num("active"),
        waiting: num("waiting"),
        max_active: num("max_active"),
        estimate_s: typeof root["estimate_s"] === "number" ? (root["estimate_s"] as number) : null,
      });
    } else if (t === "reasoning_notice") {
      out.push({ kind: "reasoning_notice", model: typeof root["model"] === "string" ? root["model"] : null, reason: typeof root["reason"] === "string" ? root["reason"] : null });
    } else if (t === "model_resolved" && typeof root["model"] === "string" && root["model"]) {
      // FIX35 Part B — an auto route resolved; the turn is attributed to it.
      out.push({ kind: "model_resolved", model: root["model"] });
    }
    return out;
  }
  const choice = choices[0] as Record<string, unknown>;
  const delta = choice["delta"];
  if (delta && typeof delta === "object") {
    const d = delta as Record<string, unknown>;
    const content = d["content"];
    if (typeof content === "string" && content.length > 0) out.push({ kind: "content_delta", delta: content });
    const toolCalls = d["tool_calls"];
    if (Array.isArray(toolCalls)) {
      for (const tc of toolCalls as Record<string, unknown>[]) {
        const fn = (tc["function"] ?? {}) as Record<string, unknown>;
        out.push({
          kind: "tool_call_delta",
          index: typeof tc["index"] === "number" ? tc["index"] : 0,
          id: typeof tc["id"] === "string" ? tc["id"] : null,
          name: typeof fn["name"] === "string" ? fn["name"] : null,
          argumentsDelta: typeof fn["arguments"] === "string" ? fn["arguments"] : "",
        });
      }
    }
  }
  const finish = choice["finish_reason"];
  if (typeof finish === "string" && finish) {
    // FIX119 — the closed-set reason and the ids ride the final chunk.
    const done: Extract<StreamEvent, { kind: "done" }> = { kind: "done", finish_reason: finish };
    if (isStopReason(choice["stop_reason"])) done.stop_reason = choice["stop_reason"];
    if (typeof root["provider_request_id"] === "string" && root["provider_request_id"]) done.provider_request_id = root["provider_request_id"];
    if (typeof root["id"] === "string" && root["id"].startsWith("req_")) done.gateway_request_id = root["id"];
    out.push(done);
  }
  return out;
}
