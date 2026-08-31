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
  /** Web parity: chat-edge attaches this on every turn; the gateway no-ops for models that cannot reason. */
  reasoning?: { mode: "auto"; show_summary: true };
  /** FIX36 Part D — caller-side DLP options ride in the request metadata.
   *  The state extractor sets {dlp_response_restore:"off"} so the gateway
   *  skips restoring masked values in THAT reply: the summary then carries
   *  placeholders (like the web extractor's ContentMasked source) instead
   *  of raw PII. Normal chat turns never set this. */
  metadata?: Record<string, unknown>;
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
  return {
    needs_vision: hasVision,
    needs_reasoning: !!request.reasoning,
    has_tools: toolCount > 0,
    tool_count: toolCount,
    conv_turn_count: convTurns,
  };
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
  if (isCredits) {
    return {
      code: "insufficient_credits",
      message:
        `Out of gateway credits${accountLabel} — the agent could not send this message. ` +
        `This is a credit balance of zero, not a rate limit. Ask your administrator to add credits to the account, then try again.`,
    };
  }
  const wait = retryAfter && /^\d+$/.test(retryAfter) ? ` Try again in about ${retryAfter}s.` : " Wait a few seconds and try again.";
  return {
    code: err?.code ?? "rate_limited",
    message: `Rate limited — too many requests in a short time.${wait} This is a speed limit, not a credit problem, so no purchase is needed.`,
  };
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
  | { kind: "fallback_info"; requested_model: string; actual_model: string; reason: string | null }
  /** FIX35 Part B — the gateway named the model that is answering (before
   *  content on an auto route). */
  | { kind: "model_resolved"; model: string }
  | { kind: "reasoning_notice"; model: string | null; reason: string | null }
  | { kind: "error"; code: string; message: string }
  | { kind: "done"; finish_reason: string | null };

export interface KeyStore {
  load(): string | null;
  save(key: string): void;
  clear(): void;
}

const IDLE_TIMEOUT_MS = 60_000;
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
  ) {}

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
   * validation of the page-supplied model. Returns null on failure (caller keeps
   * its last-known set / fails safe). Never throws. */
  async listModels(signal: AbortSignal): Promise<Set<string> | null> {
    const key = await this.getOrProvisionKey();
    if (!key) return null;
    try {
      const res = await fetch(`${this.gatewayUrl}/v1/models`, {
        method: "GET",
        headers: { Authorization: `Bearer ${key}`, Accept: "application/json" },
        signal: AbortSignal.any([signal, AbortSignal.timeout(8_000)]),
      });
      if (!res.ok) return null;
      const body = (await res.json()) as { data?: { id?: unknown }[] };
      const ids = (body.data ?? []).map((m) => m.id).filter((id): id is string => typeof id === "string" && id.length > 0);
      return ids.length > 0 ? new Set(ids) : null;
    } catch {
      return null;
    }
  }

  /** FIX9 Part B: generate an image (POST /v1/images/generations) with the agent
   * key — host-side, the run_python/web_search precedent, so the sidecar never
   * holds the JWT. Returns a data: URI on success. */
  async generateImage(prompt: string, signal: AbortSignal): Promise<{ ok: true; dataUri: string; revised?: string } | { ok: false; error: string }> {
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
        const detail = (await res.text().catch(() => "")).slice(0, 200);
        return { ok: false, error: `Image generation failed: HTTP ${res.status} ${detail}` };
      }
      const body = (await res.json()) as { data?: { b64_json?: string; url?: string; revised_prompt?: string }[] };
      const first = body.data?.[0];
      if (first?.b64_json) return { ok: true, dataUri: `data:image/png;base64,${first.b64_json}`, revised: first.revised_prompt };
      if (first?.url) return { ok: true, dataUri: first.url, revised: first.revised_prompt };
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

  /** Stream a completion. Yields parsed events; ends with `done` or `error`. */
  async *streamCompletion(request: CompletionRequest, signal: AbortSignal): AsyncGenerator<StreamEvent> {
    const key = await this.getOrProvisionKey();
    if (!key) {
      yield { kind: "error", code: "no_agent_key", message: "The app could not obtain a gateway key for this account." };
      return;
    }
    const watchdog = new AbortController();
    const onAbort = () => watchdog.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    let idleTimer: NodeJS.Timeout | null = null;
    const kick = () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => watchdog.abort(new Error("stream_stalled")), IDLE_TIMEOUT_MS);
    };
    try {
      kick();
      // FIX3 §1: on `auto`, send routing_hints so the gateway takes the
      // KEY-AWARE smart-routing path (orchestrator._smart_select) instead of the
      // legacy semantic→keyword chain, which does not filter by key availability
      // and can pick a Google model with no configured key → http_503 for the
      // whole turn. chat-edge already sends hints for web-chat; the Electron host
      // talks to the gateway directly and did not. The presence of hints alone
      // switches the path; the fields refine the pick.
      const body: Record<string, unknown> = { ...request, stream: true };
      if (request.model === "auto") body["routing_hints"] = buildRoutingHints(request);
      if (request.metadata) body["metadata"] = request.metadata;
      const res = await fetch(`${this.gatewayUrl}/v1/chat/completions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal: watchdog.signal,
      });
      // FIX21 §2: 401 and 403 are DIFFERENT failures and must not share a
      // story. 401 = the agent key really is invalid / the session expired →
      // drop the key and re-provision. 403 = a policy or guardrail REFUSAL of
      // this request, whose reason rides the response body — the key is fine, so
      // do NOT invalidate it (invalidating hides the real cause and forces a
      // needless re-provision every refused turn). Never log or echo the key.
      if (res.status === 401) {
        this.invalidateKey();
        yield {
          kind: "error",
          code: "gateway_unauthorized",
          message: "The gateway rejected the agent key (HTTP 401) — your session may have expired. Signing in again should fix it.",
        };
        return;
      }
      if (res.status === 403) {
        const reason = await readErrorReason(res);
        yield {
          kind: "error",
          code: "gateway_forbidden",
          message: `The gateway refused this request (HTTP 403)${reason ? `: ${reason}` : "."}`,
        };
        return;
      }
      if (!res.ok) {
        let detail = "";
        let parsed: { error?: { code?: string; message?: string; details?: Record<string, unknown> } } | null = null;
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
        yield { kind: "error", code: err?.code ?? `http_${res.status}`, message: err?.message || detail || `Gateway returned HTTP ${res.status}.` };
        return;
      }
      if (!res.body) {
        yield { kind: "error", code: "no_body", message: "Gateway returned no stream." };
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
      if (!finished) yield { kind: "done", finish_reason: null };
    } catch (err) {
      if (signal.aborted) return; // caller cancelled — the coordinator emits the terminal event
      const e = err as Error;
      const stalled = watchdog.signal.aborted && (watchdog.signal.reason as Error | undefined)?.message === "stream_stalled";
      yield {
        kind: "error",
        code: stalled ? "stream_stalled" : "network",
        message: stalled ? `The gateway connection stalled (no data for ${IDLE_TIMEOUT_MS / 1000}s).` : e.message,
      };
    } finally {
      if (idleTimer) clearTimeout(idleTimer);
      signal.removeEventListener("abort", onAbort);
    }
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
    out.push({ kind: "error", code: typeof e["code"] === "string" ? e["code"] : "gateway_error", message: typeof e["message"] === "string" ? e["message"] : "Gateway error" });
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
    } else if (t === "reasoning_summary_delta" && typeof root["text"] === "string" && root["text"]) {
      out.push({ kind: "reasoning_delta", delta: root["text"] });
    } else if (t === "analysis_status" && typeof root["status"] === "string" && root["status"]) {
      out.push({ kind: "analysis_status", status: root["status"] });
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
  if (typeof finish === "string" && finish) out.push({ kind: "done", finish_reason: finish });
  return out;
}
