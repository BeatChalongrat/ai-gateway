// FIX104 — the desktop learns a model's context window from the gateway.
//
// Before: a six-entry map in context-compiler.ts decided every budget, so a
// Typhoon conversation (real window 16,384) was budgeted as if it held
// ~100,000 tokens and died at the provider's refusal instead of trimming.
// This pins where the number comes from now and what happens when the
// gateway cannot say:
//   · the catalogue's `context_window` (GET /v1/models) is the window;
//   · a failed read keeps the last known windows;
//   · a restart reads the last known windows from disk;
//   · nothing known at all → the conservative default (14,500);
//   · `auto` budgets against the model that answered the last completed turn
//     (chat-edge's budgetModelFor) and tells the router what the request needs.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { workingLimitFor } from "../src/main/context-budget";
import { GatewayClient, buildRoutingHints, type GatewayMessage } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { ModelWindows } from "../src/main/model-windows";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };
const tmpDir = () => fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix104-"));

type Catalogue = Map<string, { tools: boolean; vision: boolean; contextWindow: number }>;
const CATALOGUE: Catalogue = new Map([
  ["Typhoon-S-ThaiLLM-8B-Instruct", { tools: false, vision: false, contextWindow: 16_384 }],
  ["qwen3.8-27b-fp8", { tools: true, vision: true, contextWindow: 131_072 }],
  ["gpt-image-1", { tools: false, vision: false, contextWindow: 0 }],
]);

function coordinator(opts: { list: () => Promise<Catalogue | null>; rows?: unknown[]; windows?: ModelWindows }) {
  const tmp = tmpDir();
  const requests: unknown[] = [];
  const gw = {
    listModels: opts.list,
    async *streamCompletion(req: unknown) {
      requests.push(req);
      yield { kind: "content_delta", delta: "OK" };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
  const chatEdge = { listMessages: async () => opts.rows ?? [] };
  const bridge = { postTurnEvent: () => {}, post: () => {}, postHostResult: () => {}, postHostError: () => {} };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(
    bridge as never, gw as never, chatEdge as never, {} as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20, ...(opts.windows ? { modelWindows: opts.windows } : {}) },
    log,
  );
  const priv = co as unknown as {
    budgetBasis: (c: string, m: string, s: AbortSignal) => Promise<{ model: string; window: number; outputTokens: number }>;
    acceptedModelsAt: number;
    runLoop: (r: unknown) => Promise<string>;
  };
  return { co, priv, requests };
}
const signal = () => new AbortController().signal;

describe("FIX104 — the window is the gateway catalogue's", () => {
  it("a pinned model budgets against its catalogue window, with chat-edge's headroom", async () => {
    const { priv } = coordinator({ list: async () => CATALOGUE });
    const typhoon = await priv.budgetBasis("c", "Typhoon-S-ThaiLLM-8B-Instruct", signal());
    expect(typhoon).toEqual({ model: "Typhoon-S-ThaiLLM-8B-Instruct", window: 16_384, outputTokens: 8192 });
    expect(workingLimitFor(typhoon)).toBe(14_500);
    const qwen = await priv.budgetBasis("c", "qwen3.8-27b-fp8", signal());
    expect(workingLimitFor(qwen)).toBe(117_500);
  });

  it("a model the catalogue gives no window for budgets at the conservative default", async () => {
    const { priv } = coordinator({ list: async () => CATALOGUE });
    const basis = await priv.budgetBasis("c", "a-model-nobody-catalogued", signal());
    expect(basis.window).toBe(0);
    expect(workingLimitFor(basis)).toBe(14_500);
  });

  it("the gateway failing after a successful read keeps the last known window", async () => {
    let calls = 0;
    const { priv } = coordinator({ list: async () => (calls++ === 0 ? CATALOGUE : null) });
    expect((await priv.budgetBasis("c", "Typhoon-S-ThaiLLM-8B-Instruct", signal())).window).toBe(16_384);
    priv.acceptedModelsAt = 0; // the five-minute list has expired; the next read fails
    expect((await priv.budgetBasis("c", "Typhoon-S-ThaiLLM-8B-Instruct", signal())).window).toBe(16_384);
    expect(calls).toBe(2);
  });

  it("cold start: a restart with the gateway down budgets against the windows it cached last time", async () => {
    const file = path.join(tmpDir(), "model-windows.json");
    const first = coordinator({ list: async () => CATALOGUE, windows: new ModelWindows(file, log) });
    await first.priv.budgetBasis("c", "qwen3.8-27b-fp8", signal());
    expect(JSON.parse(fs.readFileSync(file, "utf8")).windows).toEqual({ "Typhoon-S-ThaiLLM-8B-Instruct": 16_384, "qwen3.8-27b-fp8": 131_072 });

    lines.length = 0;
    const restarted = coordinator({ list: async () => null, windows: new ModelWindows(file, log) });
    expect(lines.some((l) => /CONTEXT WINDOWS: 2 last-known from/.test(l))).toBe(true);
    expect((await restarted.priv.budgetBasis("c", "Typhoon-S-ThaiLLM-8B-Instruct", signal())).window).toBe(16_384);
  });

  it("cold start with nothing cached and no gateway: every model at the default, and the log says so", async () => {
    lines.length = 0;
    const windows = new ModelWindows(path.join(tmpDir(), "model-windows.json"), log);
    expect(lines.some((l) => /none cached yet/.test(l))).toBe(true);
    const { priv } = coordinator({ list: async () => null, windows });
    const basis = await priv.budgetBasis("c", "Typhoon-S-ThaiLLM-8B-Instruct", signal());
    expect(workingLimitFor(basis)).toBe(14_500);
  });

  it("a changed catalogue number (an admin edit) replaces the cached one", () => {
    const file = path.join(tmpDir(), "model-windows.json");
    const w = new ModelWindows(file, log);
    w.learn([["m", 32_768]]);
    w.learn([["m", 16_384]]);
    expect(new ModelWindows(file, log).advertised("m")).toBe(16_384);
  });
});

describe("FIX104 — auto budgets against the model that answered, and tells the router", () => {
  const rows = (model: string | null, status = "completed") => [
    { id: "1", role: "user", content: "q", status: "completed", model_id: null },
    { id: "2", role: "assistant", content: "a", status, model_id: model },
  ];

  it("the last completed turn's model, while the catalogue lists it", async () => {
    const { priv } = coordinator({ list: async () => CATALOGUE, rows: rows("Typhoon-S-ThaiLLM-8B-Instruct") });
    expect(await priv.budgetBasis("c", "auto", signal())).toEqual({ model: "Typhoon-S-ThaiLLM-8B-Instruct", window: 16_384, outputTokens: 8192 });
  });

  it("no evidence, an errored row, or a model the catalogue no longer lists → the default", async () => {
    for (const r of [[], rows("Typhoon-S-ThaiLLM-8B-Instruct", "error"), rows("gone-model"), rows("auto")]) {
      const { priv } = coordinator({ list: async () => CATALOGUE, rows: r });
      expect(await priv.budgetBasis("c", "auto", signal())).toEqual({ model: "", window: 0, outputTokens: 8192 });
    }
  });

  it("routing hints carry min (hard) and preferred (soft) context, as chat-edge sends them", () => {
    const base = { model: "auto", messages: [{ role: "user", content: "hi" }] as GatewayMessage[] };
    expect(buildRoutingHints({ ...base, routingContext: { minContextTokens: 12_345, preferredContextTokens: 131_072 } })).toMatchObject({ min_context_tokens: 12_345, preferred_context_tokens: 131_072 });
    const noWindow = buildRoutingHints({ ...base, routingContext: { minContextTokens: 9_000, preferredContextTokens: 0 } });
    expect(noWindow["min_context_tokens"]).toBe(9_000);
    expect("preferred_context_tokens" in noWindow).toBe(false);
    expect("min_context_tokens" in buildRoutingHints(base)).toBe(false);
  });

  it("the run passes the context to the request, and the wire body carries it only inside routing_hints", async () => {
    const { priv, requests } = coordinator({ list: async () => CATALOGUE });
    await priv.runLoop({
      conversationId: "c", model: "auto", messages: [{ role: "system", content: "s" }, { role: "user", content: "hi" }], signal: signal(),
      maxIterations: 2, maxMs: 5_000, tools: [], parentToolCallId: null, onText: () => {},
      routingContext: { minContextTokens: 11_000, preferredContextTokens: 16_384 },
    });
    expect((requests[0] as { routingContext: unknown }).routingContext).toEqual({ minContextTokens: 11_000, preferredContextTokens: 16_384 });

    const realFetch = globalThis.fetch;
    let body: Record<string, unknown> = {};
    globalThis.fetch = (async (_url: unknown, init: { body: string }) => {
      body = JSON.parse(init.body);
      return new Response("data: [DONE]\n\n", { status: 200, headers: { "content-type": "text/event-stream" } });
    }) as never;
    try {
      const client = new GatewayClient("http://gateway.invalid", () => null, { load: () => "k", save: () => {}, clear: () => {} }, log);
      for await (const _ of client.streamCompletion({ model: "auto", messages: [{ role: "user", content: "hi" }], routingContext: { minContextTokens: 11_000, preferredContextTokens: 16_384 } }, signal())) {
        /* drain */
      }
    } finally {
      globalThis.fetch = realFetch;
    }
    expect("routingContext" in body).toBe(false);
    expect(body["routing_hints"]).toMatchObject({ min_context_tokens: 11_000, preferred_context_tokens: 16_384 });
  });
});
