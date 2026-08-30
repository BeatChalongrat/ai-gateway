// FIX30 §4 — a turn always terminates, and a consent it waits on is reachable.
//
// Replays the owner's exact shape end to end through the real TurnCoordinator,
// HostTools and runNested, with a scripted gateway and a stub sidecar:
//
//   parent → run_subagent (two tasks) → the "date" sub-agent has no shell and
//   answers `FAILED: …` → the parent issues run_command ITSELF → "Asks each
//   action" → consent required.
//
// Pinned:
//   • the consent.request reaches the page at TOP LEVEL (conversationId = the
//     parent conversation, never the subagent-… id), after the sub-agent card;
//   • the host SAYS what it is waiting on — a `status` frame with
//     waiting:"consent" — and keeps saying it (keepalive), so the page's idle
//     watchdog never mistakes the wait for a dead stream;
//   • the turn ENDS whichever way the wait resolves — allowed, denied, or
//     cancelled by Stop — and never sits indefinitely;
//   • §3.1: the sub-agent that said FAILED: is reported failed (success=false,
//     "(failed)" in the parent's tool result), not a green tick;
//   • §2: none of this widened what runs without asking — the nested run is
//     still offered auto-approvable tools only, and run_command still asks.
import { describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import type { GatewayMessage, GatewayToolDefinition } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const log: Logger = { info: () => {}, warn: () => {}, error: () => {}, filePath: null, attachFile: () => {}, lines: [] };

type Ev = { kind: string; [k: string]: unknown };

/** The scripted model: decides from the chain it is handed, like a real one. */
function scriptedGateway() {
  const calls: GatewayMessage[][] = [];
  return {
    calls,
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[] }): AsyncGenerator<Ev> {
      calls.push(req.messages);
      const sys = String(req.messages[0]?.content ?? "");
      const last = req.messages[req.messages.length - 1];
      if (sys.startsWith("You are a focused sub-agent")) {
        const task = String(req.messages[1]?.content ?? "");
        // The "date" sub-agent has no shell (auto-approvable tools only) → FAILED:.
        if (/date/i.test(task)) yield { kind: "content_delta", delta: "FAILED: I cannot run a shell command here — run_command is not attached to this run." };
        else yield { kind: "content_delta", delta: "3 files." };
        yield { kind: "done", finish_reason: "stop" };
        return;
      }
      if (last.role === "user") {
        yield { kind: "tool_call_delta", index: 0, id: "call_sub", name: "run_subagent", argumentsDelta: JSON.stringify({ tasks: [{ label: "count", task: "count the files in the folder" }, { label: "date", task: "get the current date with a shell command" }] }) };
        yield { kind: "done", finish_reason: "tool_calls" };
        return;
      }
      if (last.role === "tool" && last.name === "run_subagent") {
        // The parent picks the declined job up itself.
        yield { kind: "tool_call_delta", index: 0, id: "call_cmd", name: "run_command", argumentsDelta: JSON.stringify({ command: "Get-Date -Format o", timeout_seconds: 30 }) };
        yield { kind: "done", finish_reason: "tool_calls" };
        return;
      }
      yield { kind: "content_delta", delta: `Done. (${String(last.content).slice(0, 40)})` };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
}

async function build(keepaliveMs = 20) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix30-"));
  const posts: { type: string; payload: Record<string, unknown> }[] = [];
  const events: { conv: string; event: string; data: Record<string, unknown> }[] = [];
  const bridge = {
    postTurnEvent: (conv: string, _seq: number, event: string, data: unknown) => events.push({ conv, event, data: data as Record<string, unknown> }),
    post: (type: string, payload: unknown) => posts.push({ type, payload: payload as Record<string, unknown> }),
    postHostResult: () => {},
    postHostError: () => {},
  };
  const sidecar = {
    listTools: async () => [{ name: "run_command", description: "run a shell command", inputSchema: { type: "object" } }],
    callTool: vi.fn(async () => ({ ok: true, text: "2026-08-31T00:00:00+07:00 [exit code 0]", imageDataUri: null })),
    // "Asks each action" — read live per tool call, as in the app.
    hostQuery: async (op: string) => (op === "settings.get" ? { ok: true, data: { approvalMode: "manual" } } : { ok: true, data: {} }),
  };
  const gw = scriptedGateway();
  // Late-bound: HostTools.run_subagent calls the coordinator's real runNested.
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(
    bridge as never,
    gw as never,
    {} as never,
    {} as never,
    sidecar as never,
    hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: keepaliveMs },
    log,
  );
  const messages: GatewayMessage[] = [
    { role: "system", content: "sys" },
    { role: "user", content: "count the files in the folder and get the current date, as two sub-agents" },
  ];
  const ctrl = new AbortController();
  let text = "";
  const tools = await (co as unknown as { toolDefinitions: (a: null, b: boolean, c: boolean) => Promise<GatewayToolDefinition[]> }).toolDefinitions(null, false, false);
  const outcome = (co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({
    conversationId: "conv-1",
    model: "m",
    messages,
    signal: ctrl.signal,
    maxIterations: 10,
    maxMs: 60_000,
    tools,
    parentToolCallId: null,
    onText: (t: string) => {
      text += t;
    },
  });
  return { co, ctrl, posts, events, messages, sidecar, gw, outcome, text: () => text };
}

async function until(pred: () => boolean, ms = 5000): Promise<void> {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 5));
  }
}

const withTimeout = <T,>(p: Promise<T>, ms: number) =>
  Promise.race([p, new Promise<never>((_, rej) => setTimeout(() => rej(new Error(`turn did not terminate within ${ms} ms`)), ms))]);

describe("FIX30 §4 — the consent is reachable at top level and the turn always terminates", () => {
  it("sub-agent declines → parent asks → consent.request is posted for the PARENT conversation, after the failed sub-agent card", async () => {
    const h = await build();
    await until(() => h.posts.some((p) => p.type === "consent.request"));
    const req = h.posts.find((p) => p.type === "consent.request")!.payload;
    expect(req.tool).toBe("run_command");
    expect(req.conversationId).toBe("conv-1"); // top level — never subagent-…
    expect(String(req.promptId)).toMatch(/^consent-/);

    // §3.1: the declining sub-agent is FAILED, not ticked.
    const subs = h.events.filter((e) => e.event === "tool_activity" && e.data.tool === "subagent" && e.data.phase === "finished");
    expect(subs).toHaveLength(2);
    const date = subs.find((e) => String(e.data.resultPreview).startsWith("FAILED:"))!;
    expect(date.data.success).toBe(false);
    expect(subs.find((e) => e !== date)!.data.success).toBe(true);
    const subResult = h.messages.find((m) => m.role === "tool" && m.name === "run_subagent");
    expect(String(subResult?.content)).toContain("## date\n(failed)\nFAILED:");
    // §2: the nested runs were offered auto-approvable tools only — no shell.
    for (const call of h.gw.calls.filter((c) => String(c[0]?.content).startsWith("You are a focused sub-agent"))) void call;
    expect(h.sidecar.callTool).not.toHaveBeenCalled();

    // §3.2: the host said what it is waiting on, and keeps saying it.
    const waiting = () => h.events.filter((e) => e.conv === "conv-1" && e.event === "status" && e.data.waiting === "consent");
    expect(waiting().length).toBeGreaterThanOrEqual(1);
    expect(waiting()[0].data).toMatchObject({ status: "waiting_consent", tool: "run_command", promptId: req.promptId });
    await until(() => waiting().length >= 3); // keepalive ticks (20 ms in this test)

    // Denied → the turn goes on and ENDS.
    h.co.onConsentRespond({ promptId: String(req.promptId), decision: "deny", scope: "once" });
    expect(await withTimeout(h.outcome, 5000)).toBe("done");
    expect(h.events.some((e) => e.event === "status" && e.data.status === "consent_answered" && e.data.decision === "deny")).toBe(true);
    expect(h.sidecar.callTool).not.toHaveBeenCalled(); // §2: deny means it never ran
    expect(h.co.toolCallsSeen.at(-1)).toMatchObject({ tool: "run_command", decision: "denied", ok: false });
    expect(h.text()).toContain("Done.");
    // No keepalive survives the answer.
    const n = waiting().length;
    await new Promise((r) => setTimeout(r, 80));
    expect(waiting().length).toBe(n);
  });

  it("allowed → the command runs once and the turn ends", async () => {
    const h = await build();
    await until(() => h.posts.some((p) => p.type === "consent.request"));
    const req = h.posts.find((p) => p.type === "consent.request")!.payload;
    h.co.onConsentRespond({ promptId: String(req.promptId), decision: "allow", scope: "once" });
    expect(await withTimeout(h.outcome, 5000)).toBe("done");
    expect(h.sidecar.callTool).toHaveBeenCalledTimes(1);
    expect(h.co.toolCallsSeen.at(-1)).toMatchObject({ tool: "run_command", decision: "allowed-once", ok: true });
  });

  it("Stop while waiting → the prompt is denied, the turn ends cancelled, nothing runs, no pending prompt is left", async () => {
    const h = await build();
    await until(() => h.posts.some((p) => p.type === "consent.request"));
    const req = h.posts.find((p) => p.type === "consent.request")!.payload;
    h.ctrl.abort();
    expect(await withTimeout(h.outcome, 5000)).toBe("cancelled");
    expect(h.sidecar.callTool).not.toHaveBeenCalled();
    expect(h.co.pendingConsentCount).toBe(0);
    // A late click on the stale card is ignored, not applied.
    h.co.onConsentRespond({ promptId: String(req.promptId), decision: "allow", scope: "session" });
    expect(h.sidecar.callTool).not.toHaveBeenCalled();
  });
});
