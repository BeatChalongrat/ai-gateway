// FIX72 — the desktop loop's answers to the long-run measurements:
//   Part C  a round that dies of output_limit_reached is retried ONCE with
//           thinking off (measured: qwen calls the tool 5/5 with thinking off
//           vs 4/5 with it on, on the ambiguous prompt that killed FIX71).
//   Part D  a batch stops at the first failure and answers the rest with the
//           exact browser-batch contract text (latent today — neither qwen nor
//           deepseek emits >1 tool block per round, measured 0/148 rounds —
//           so this is forward-correctness).
//   Part F  a chat is NEVER left silently untitled: deriveTitle falls back to
//           the user's own first words when the model returns nothing.
import { describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { GatewayMessage, GatewayToolDefinition } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator, deriveTitle, toolResultErrored, NOT_EXECUTED_AFTER_FAILURE } from "../src/main/turn-coordinator";

const lines: string[] = [];
const log: Logger = { info: (m: string) => { lines.push(m); }, warn: (m: string) => { lines.push(m); }, error: (m: string) => { lines.push(m); }, filePath: null, attachFile: () => {}, lines: [] };

type Ev = { kind: string; [k: string]: unknown };
type Req = { tools: number; reasoning: string; toolCalls?: unknown[] };

// A gateway scripted per call: each entry is the events for one streamCompletion.
function scriptedGateway(scripts: ((req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[]; reasoning?: { mode: string } }) => Ev[])[]) {
  const requests: Req[] = [];
  let call = 0;
  return {
    requests,
    async listModels() { return new Map([["qwen3.8-27b-fp8", { tools: true }]]); },
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[]; reasoning?: { mode: string } }): AsyncGenerator<Ev> {
      requests.push({ tools: req.tools?.length ?? 0, reasoning: req.reasoning?.mode ?? "none" });
      const script = scripts[Math.min(call, scripts.length - 1)];
      call++;
      for (const ev of script(req)) yield ev;
    },
  };
}

async function runLoop(gw: ReturnType<typeof scriptedGateway>, tools: GatewayToolDefinition[]) {
  lines.length = 0;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix72-"));
  const events: { event: string; data: Record<string, unknown> }[] = [];
  const bridge = { postTurnEvent: (_c: string, _s: number, event: string, data: unknown) => events.push({ event, data: data as Record<string, unknown> }), post: () => {}, postHostResult: () => {}, postHostError: () => {} };
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
  await (co as unknown as { refreshAcceptedModels: (s: AbortSignal) => Promise<void> }).refreshAcceptedModels(new AbortController().signal);
  let text = "";
  const outcome = await (co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({
    conversationId: "conv-1", model: "qwen3.8-27b-fp8",
    messages: [{ role: "system", content: "sys" }, { role: "user", content: "hi" }] as GatewayMessage[],
    signal: new AbortController().signal, maxIterations: 6, maxMs: 10_000, tools, parentToolCallId: null,
    onText: (t: string) => { text += t; },
  });
  const messages = (co as unknown as { lastReply: Map<string, string> });
  void messages;
  return { outcome, text, events, gw };
}

const noTools: GatewayToolDefinition[] = [];

describe("FIX72 Part C — output_limit_reached retries once with thinking off", () => {
  it("first round asks with thinking on; after output_limit it re-asks with thinking OFF and completes", async () => {
    const gw = scriptedGateway([
      () => [{ kind: "error", code: "output_limit_reached", message: "budget" }],
      () => [{ kind: "content_delta", delta: "done" }, { kind: "done", finish_reason: "stop" }],
    ]);
    const r = await runLoop(gw, noTools);
    expect(r.outcome).toBe("done");
    expect(r.text).toBe("done");
    expect(r.gw.requests.map((x) => x.reasoning)).toEqual(["auto", "off"]);
    expect(r.events.some((e) => e.event === "error")).toBe(false);
    expect(lines.some((l) => /retrying conv=conv-1 with thinking off/.test(l))).toBe(true);
  });

  it("retries at most once — a second output_limit is a real error", async () => {
    const gw = scriptedGateway([
      () => [{ kind: "error", code: "output_limit_reached", message: "budget" }],
      () => [{ kind: "error", code: "output_limit_reached", message: "budget again" }],
    ]);
    const r = await runLoop(gw, noTools);
    expect(r.outcome).toBe("error");
    expect(r.gw.requests.map((x) => x.reasoning)).toEqual(["auto", "off"]);
    expect(r.events.filter((e) => e.event === "error")).toHaveLength(1);
    expect(r.events.some((e) => e.event === "error" && e.data.code === "output_limit_reached")).toBe(true);
  });

  it("empty_completion (finish_reason=stop) is NOT retried with thinking off — only output_limit is", async () => {
    const gw = scriptedGateway([
      () => [{ kind: "error", code: "empty_completion", message: "blank" }],
    ]);
    const r = await runLoop(gw, noTools);
    expect(r.outcome).toBe("error");
    expect(r.gw.requests.map((x) => x.reasoning)).toEqual(["auto"]);
  });
});

describe("FIX72 Part D — a batch stops at the first failure", () => {
  it("runs the first tool_use block, then answers each later block with the contract text (no execution)", async () => {
    // One round with TWO tool_use blocks; both name a tool that is not in the
    // offered set, so the first errors ("unknown tool"). The second must come
    // back as NOT_EXECUTED_AFTER_FAILURE — proof it was skipped, not run.
    const gw = scriptedGateway([
      () => [
        { kind: "tool_call_delta", index: 0, id: "c1", name: "does_not_exist", argumentsDelta: "{}" },
        { kind: "tool_call_delta", index: 1, id: "c2", name: "also_missing", argumentsDelta: "{}" },
        { kind: "done", finish_reason: "tool_calls" },
      ],
      () => [{ kind: "content_delta", delta: "ok" }, { kind: "done", finish_reason: "stop" }],
    ]);
    const captured: GatewayMessage[] = [];
    // Intercept the messages the SECOND round is built from by reading them off
    // the run: simplest is to assert via a tool tool_call_id lookup helper on
    // the messages the gateway received in round 2.
    const gw2 = {
      requests: gw.requests,
      async listModels() { return new Map([["qwen3.8-27b-fp8", { tools: true }]]); },
      calls: 0,
      async *streamCompletion(req: { messages: GatewayMessage[] }): AsyncGenerator<Ev> {
        if (this.calls === 1) captured.push(...req.messages);
        this.calls++;
        yield* (gw.streamCompletion as never as (r: unknown) => AsyncGenerator<Ev>)(req);
      },
    };
    const r = await runLoop(gw2 as never, noTools);
    expect(r.outcome).toBe("done");
    const c2 = captured.find((m) => m.role === "tool" && m.tool_call_id === "c2");
    const c1 = captured.find((m) => m.role === "tool" && m.tool_call_id === "c1");
    expect(c1 && typeof c1.content === "string" && c1.content.startsWith("Error")).toBe(true);
    expect(c2?.content).toBe(NOT_EXECUTED_AFTER_FAILURE);
  });
});

describe("FIX72 Part D/F helpers", () => {
  it("toolResultErrored matches the tool result by id, not the last message", () => {
    const msgs: GatewayMessage[] = [
      { role: "tool", tool_call_id: "a", name: "x", content: "Error: boom" },
      { role: "tool", tool_call_id: "b", name: "y", content: "fine" },
    ];
    expect(toolResultErrored(msgs, "a")).toBe(true);
    expect(toolResultErrored(msgs, "b")).toBe(false);
    expect(toolResultErrored(msgs, "missing")).toBe(false);
  });

  it("deriveTitle never returns empty when the user wrote something", () => {
    expect(deriveTitle("A Nice Title", "hello there")).toBe("A Nice Title");
    expect(deriveTitle('"Quoted Title."', "x")).toBe("Quoted Title");
    // model empty → fall back to the user's own words, in their language
    expect(deriveTitle("", "Please summarize the quarterly report for me")).toBe("Please summarize the quarterly report for me");
    expect(deriveTitle("   ", "เข้า youtube ที หาข่าว")).toBe("เข้า youtube ที หาข่าว");
    // nothing at all → null (only case that leaves a chat untitled)
    expect(deriveTitle("", "   ")).toBeNull();
    // over-long model output is rejected in favour of the user words
    expect(deriveTitle("x".repeat(200), "short prompt")).toBe("short prompt");
  });
});
