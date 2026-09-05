// FIX64 — the stale-catalog repair on the desktop path.
//
// Measured on the web path before the fix: an admin turns a model's tools
// switch off; the gateway refuses tools for it at once
// (model_no_tool_support, not retryable) while the client's model list is
// cached for five minutes, so every turn on that model died for the rest
// of the window. The desktop caches the same list (MODELS_TTL_MS). This
// pins the repair: on that error, with tools sent and nothing shown yet,
// the host re-fetches the list and, when it now says tools-blind, resends
// the same turn without tools — once — and the turn completes.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { GatewayMessage, GatewayToolDefinition } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };

type Ev = { kind: string; [k: string]: unknown };

/** A gateway whose catalogue flips to tools-blind between the two calls,
 *  exactly like an admin flipping the switch under a running client. */
function gateway(opts: { blindAfterRefresh: boolean; alwaysRefuse?: boolean }) {
  const requests: { tools: number }[] = [];
  let listCalls = 0;
  return {
    requests,
    listCalls: () => listCalls,
    async listModels() {
      listCalls++;
      // First fetch (before the turn) says tools; the refresh after the
      // refusal says what the admin set.
      const tools = listCalls === 1 ? true : !opts.blindAfterRefresh;
      return new Map([["qwen3.8-27b-fp8", { tools }]]);
    },
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[] }): AsyncGenerator<Ev> {
      requests.push({ tools: req.tools?.length ?? 0 });
      if ((req.tools?.length ?? 0) > 0 || opts.alwaysRefuse) {
        yield { kind: "error", code: "model_no_tool_support", message: "The selected model cannot use tools." };
        return;
      }
      yield { kind: "content_delta", delta: "OK" };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
}

async function run(gw: ReturnType<typeof gateway>) {
  lines.length = 0;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix64-"));
  const events: { event: string; data: Record<string, unknown> }[] = [];
  const bridge = {
    postTurnEvent: (_conv: string, _seq: number, event: string, data: unknown) => events.push({ event, data: data as Record<string, unknown> }),
    post: () => {},
    postHostResult: () => {},
    postHostError: () => {},
  };
  const sidecar = { listTools: async () => [], callTool: async () => ({ ok: true, text: "", imageDataUri: null }), hostQuery: async () => ({ ok: true, data: {} }) };
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(
    bridge as never, gw as never, {} as never, {} as never, sidecar as never, hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "qwen3.8-27b-fp8", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20 },
    log,
  );
  // Warm the model list the way a running client has it: "tools".
  await (co as unknown as { refreshAcceptedModels: (s: AbortSignal) => Promise<void> }).refreshAcceptedModels(new AbortController().signal);
  const tools: GatewayToolDefinition[] = [{ type: "function", function: { name: "get_time", description: "time", parameters: { type: "object", properties: {} } } }];
  let text = "";
  const outcome = await (co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({
    conversationId: "conv-1",
    model: "qwen3.8-27b-fp8",
    messages: [{ role: "system", content: "sys" }, { role: "user", content: "hi" }] as GatewayMessage[],
    signal: new AbortController().signal,
    maxIterations: 4,
    maxMs: 10_000,
    tools,
    parentToolCallId: null,
    onText: (t: string) => { text += t; },
  });
  return { outcome, text, events, gw };
}

describe("FIX64 — desktop resends without tools when the gateway refuses tools the stale list said the model had", () => {
  it("re-fetches the list, drops the tools, resends once, and the turn completes", async () => {
    const r = await run(gateway({ blindAfterRefresh: true }));
    expect(r.outcome).toBe("done");
    expect(r.text).toBe("OK");
    expect(r.gw.requests).toEqual([{ tools: 1 }, { tools: 0 }]);
    expect(r.gw.listCalls()).toBe(2);
    // The refusal never reached the page; the reply did.
    expect(r.events.some((e) => e.event === "error")).toBe(false);
    expect(r.events.some((e) => e.event === "content_delta")).toBe(true);
    expect(lines.some((l) => /resending conv=conv-1 without tools/.test(l))).toBe(true);
  });

  it("surfaces the error when the fresh list still says the model takes tools", async () => {
    const r = await run(gateway({ blindAfterRefresh: false }));
    expect(r.outcome).toBe("error");
    expect(r.gw.requests).toEqual([{ tools: 1 }]);
    expect(r.gw.listCalls()).toBe(2);
    expect(r.events.some((e) => e.event === "error" && e.data.code === "model_no_tool_support")).toBe(true);
  });

  it("retries at most once — a second refusal is a real error", async () => {
    const r = await run(gateway({ blindAfterRefresh: true, alwaysRefuse: true }));
    expect(r.outcome).toBe("error");
    expect(r.gw.requests).toEqual([{ tools: 1 }, { tools: 0 }]);
    expect(r.events.filter((e) => e.event === "error")).toHaveLength(1);
  });
});
