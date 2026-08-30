// FIX31 Part 1 — a sub-agent asks the user like everyone else; a scheduled
// task never asks a room with nobody in it.
//
// Same harness as turn-terminates.test.ts (real TurnCoordinator + HostTools +
// runNested, scripted gateway, stub sidecar in "Asks each action"), now with the
// sub-agent that CALLS run_command instead of declining it.
//
// Pins:
//   §1.1 • a fan-out sub-agent has run_command (no more auto-approvable-only) and
//          its call raises a consent.request on the PARENT conversation, with
//          requestedBy = { kind:"subagent", label, task } — never unattributed;
//        • run_subagent is still withheld inside a sub-agent (no second level);
//        • the user's answer is the user's: nothing runs on deny, once on allow;
//        • deny → THAT sub-agent ends FAILED ("declined permission"), asks no
//          second time, and the parent goes on and terminates;
//        • Stop while a sub-agent waits → its prompt is denied by the cancel,
//          nothing runs, the whole turn ends cancelled, no prompt is left;
//        • two sub-agents needing permission at once → one card at a time on
//          the page (the second is posted only after the first is answered), and
//          both end;
//        • the sub-agent's hard stop does not fire while its card is open.
//   §1.2 • a scheduled task ("Asks each action") whose run needs permission does
//          NOT post a card and is NOT answered for the user: the tool is refused
//          with the reason, the run ends, and the history line is
//          outcome=needs_permission with the tool named.
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

/** The scripted model. Sub-agents run the shell command their task names;
 *  the parent fans out (or, for a task run, calls run_command itself). */
function scriptedGateway(opts: { subagentCommands: boolean; parentDirectCommand?: boolean }) {
  const calls: GatewayMessage[][] = [];
  return {
    calls,
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[] }): AsyncGenerator<Ev> {
      calls.push(req.messages);
      const sys = String(req.messages[0]?.content ?? "");
      const last = req.messages[req.messages.length - 1];
      const toolNames = (req.tools ?? []).map((t) => t.function.name);
      if (sys.startsWith("You are a focused sub-agent")) {
        const task = String(req.messages[1]?.content ?? "");
        if (last.role === "user") {
          if (opts.subagentCommands && toolNames.includes("run_command")) {
            const cmd = /date/i.test(task) ? "Get-Date -Format o" : "(Get-ChildItem).Count";
            yield { kind: "tool_call_delta", index: 0, id: `call_${/date/i.test(task) ? "date" : "count"}`, name: "run_command", argumentsDelta: JSON.stringify({ command: cmd, timeout_seconds: 30 }) };
            yield { kind: "done", finish_reason: "tool_calls" };
            return;
          }
          yield { kind: "content_delta", delta: "FAILED: run_command is not attached to this run." };
          yield { kind: "done", finish_reason: "stop" };
          return;
        }
        // after its tool result: report it (a denied one never gets here — the run ends)
        yield { kind: "content_delta", delta: `Result: ${String(last.content).slice(0, 60)}` };
        yield { kind: "done", finish_reason: "stop" };
        return;
      }
      if (last.role === "user") {
        if (opts.parentDirectCommand) {
          yield { kind: "tool_call_delta", index: 0, id: "call_cmd", name: "run_command", argumentsDelta: JSON.stringify({ command: "Get-Date -Format o" }) };
          yield { kind: "done", finish_reason: "tool_calls" };
          return;
        }
        yield { kind: "tool_call_delta", index: 0, id: "call_sub", name: "run_subagent", argumentsDelta: JSON.stringify({ tasks: [{ label: "count", task: "count the files in the folder with a shell command" }, { label: "date", task: "get the current date with a shell command" }] }) };
        yield { kind: "done", finish_reason: "tool_calls" };
        return;
      }
      yield { kind: "content_delta", delta: `Done. (${String(last.content).replace(/\s+/g, " ").slice(0, 120)})` };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
}

async function build(gwOpts: { subagentCommands: boolean; parentDirectCommand?: boolean } = { subagentCommands: true }, keepaliveMs = 20) {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-fix31-"));
  const posts: { type: string; payload: Record<string, unknown> }[] = [];
  const events: { conv: string; event: string; data: Record<string, unknown> }[] = [];
  const hostQueries: { op: string; args: Record<string, unknown> }[] = [];
  const bridge = {
    postTurnEvent: (conv: string, _seq: number, event: string, data: unknown) => events.push({ conv, event, data: data as Record<string, unknown> }),
    post: (type: string, payload: unknown) => posts.push({ type, payload: payload as Record<string, unknown> }),
    postHostResult: () => {},
    postHostError: () => {},
  };
  const sidecar = {
    listTools: async () => [{ name: "run_command", description: "run a shell command", inputSchema: { type: "object" } }],
    callTool: vi.fn(async (_name: string, args: Record<string, unknown>) => ({ ok: true, text: `${String(args["command"])} → ok [exit code 0]`, imageDataUri: null })),
    hostQuery: async (op: string, args: Record<string, unknown> = {}) => {
      hostQueries.push({ op, args });
      if (op === "settings.get") return { ok: true, data: { approvalMode: "manual" } };
      return { ok: true, data: {} };
    },
  };
  const gw = scriptedGateway(gwOpts);
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(
    bridge as never,
    gw as never,
    { createConversation: async () => "task-conv", renameConversation: async () => {}, getConversation: async () => null, importTranscript: async () => ({ ok: true }) } as never,
    { build: async () => ({ messages: [] }) } as never,
    sidecar as never,
    hostTools,
    new PermissionStore(path.join(tmp, "permissions.json")),
    new GrantStore(path.join(tmp, "grants.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: "manual", signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: keepaliveMs },
    log,
  );
  return { co, posts, events, hostQueries, sidecar, gw, tmp };
}

async function startTurn(h: Awaited<ReturnType<typeof build>>, userText = "count the files and get the date, as two sub-agents") {
  const messages: GatewayMessage[] = [
    { role: "system", content: "sys" },
    { role: "user", content: userText },
  ];
  const ctrl = new AbortController();
  let text = "";
  const tools = await (h.co as unknown as { toolDefinitions: (a: null, b: boolean, c: boolean) => Promise<GatewayToolDefinition[]> }).toolDefinitions(null, false, false);
  const outcome = (h.co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({
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
  return { ctrl, messages, outcome, text: () => text };
}

async function until(pred: () => boolean, ms = 5000): Promise<void> {
  const t0 = Date.now();
  while (!pred()) {
    if (Date.now() - t0 > ms) throw new Error("condition not met in time");
    await new Promise((r) => setTimeout(r, 5));
  }
}
const withTimeout = <T,>(p: Promise<T>, ms: number) =>
  Promise.race([p, new Promise<never>((_, rej) => setTimeout(() => rej(new Error(`did not terminate within ${ms} ms`)), ms))]);
const consents = (h: { posts: { type: string; payload: Record<string, unknown> }[] }) => h.posts.filter((p) => p.type === "consent.request").map((p) => p.payload);

describe("FIX31 §1.1 — a sub-agent's tool call asks the user, attributed", () => {
  it("posts ONE attributed consent card at a time on the parent conversation; run_subagent stays withheld inside", async () => {
    const h = await build();
    const t = await startTurn(h);
    await until(() => consents(h).length >= 1);
    // Both sub-agents asked at once (fan-out runs them concurrently); only one card is up.
    await new Promise((r) => setTimeout(r, 60));
    expect(consents(h)).toHaveLength(1);
    const first = consents(h)[0];
    expect(first.conversationId).toBe("conv-1");
    expect(first.tool).toBe("run_command");
    const by = first.requestedBy as { kind: string; label: string; task: string; subagentId: string };
    expect(by.kind).toBe("subagent");
    expect(["count", "date"]).toContain(by.label);
    expect(by.task).toMatch(/shell command/);
    expect(by.subagentId).toMatch(/^subagent-/);
    // The page is told what it waits on, with the same attribution.
    const w = h.events.find((e) => e.conv === "conv-1" && e.event === "status" && e.data.waiting === "consent")!;
    expect((w.data.requestedBy as { label: string }).label).toBe(by.label);
    // No second level: the sub-agent's tool list had no run_subagent.
    const subCalls = h.gw.calls.filter((c) => String(c[0]?.content).startsWith("You are a focused sub-agent"));
    expect(subCalls.length).toBeGreaterThanOrEqual(1);
    const nestedTools = (h.co as unknown as { toolDefinitions: (a: null, b: boolean, c: boolean) => Promise<GatewayToolDefinition[]> }).toolDefinitions(null, false, true);
    expect((await nestedTools).map((d) => d.function.name)).not.toContain("run_subagent");

    // Allow the first → it runs once; the SECOND card appears only now.
    h.co.onConsentRespond({ promptId: String(first.promptId), decision: "allow", scope: "once" });
    await until(() => consents(h).length === 2);
    const second = consents(h)[1];
    expect((second.requestedBy as { label: string }).label).not.toBe(by.label);
    expect(h.sidecar.callTool).toHaveBeenCalledTimes(1);
    h.co.onConsentRespond({ promptId: String(second.promptId), decision: "allow", scope: "once" });
    expect(await withTimeout(t.outcome, 5000)).toBe("done");
    expect(h.sidecar.callTool).toHaveBeenCalledTimes(2);
    const subs = h.events.filter((e) => e.event === "tool_activity" && e.data.tool === "subagent" && e.data.phase === "finished");
    expect(subs.map((s) => s.data.success)).toEqual([true, true]);
    expect(h.co.pendingConsentCount).toBe(0);
  });

  it("deny → that sub-agent ends FAILED without asking again; the other proceeds; the parent terminates", async () => {
    const h = await build();
    const t = await startTurn(h);
    await until(() => consents(h).length >= 1);
    const first = consents(h)[0];
    const deniedLabel = (first.requestedBy as { label: string }).label;
    h.co.onConsentRespond({ promptId: String(first.promptId), decision: "deny", scope: "once" });
    await until(() => consents(h).length === 2);
    const second = consents(h)[1];
    h.co.onConsentRespond({ promptId: String(second.promptId), decision: "allow", scope: "once" });
    expect(await withTimeout(t.outcome, 5000)).toBe("done");
    // Exactly one command ran (the allowed one); the denied sub-agent never re-asked.
    expect(h.sidecar.callTool).toHaveBeenCalledTimes(1);
    expect(consents(h)).toHaveLength(2);
    const subs = h.events.filter((e) => e.event === "tool_activity" && e.data.tool === "subagent" && e.data.phase === "finished");
    const denied = subs.find((s) => s.data.success === false)!;
    expect(String(denied.data.resultPreview)).toMatch(/^FAILED: the user declined permission for run_command/);
    expect(String(denied.data.resultPreview)).toContain(deniedLabel);
    // The parent's tool result carries the failure in FIX30's words.
    const subResult = String(t.messages.find((m) => m.role === "tool" && m.name === "run_subagent")?.content);
    expect(subResult).toContain(`## ${deniedLabel}\n(failed)\nFAILED: the user declined permission for run_command`);
    expect(t.text()).toContain("Done.");
    expect(h.co.pendingConsentCount).toBe(0);
  });

  it("Stop while a sub-agent waits → denied by the cancel, nothing runs, turn cancelled, no prompt left", async () => {
    const h = await build();
    const t = await startTurn(h);
    await until(() => consents(h).length >= 1);
    const first = consents(h)[0];
    t.ctrl.abort();
    expect(await withTimeout(t.outcome, 5000)).toBe("cancelled");
    expect(h.sidecar.callTool).not.toHaveBeenCalled();
    expect(h.co.pendingConsentCount).toBe(0);
    // The queued second request was never posted, and a late click is ignored.
    await new Promise((r) => setTimeout(r, 60));
    expect(consents(h)).toHaveLength(1);
    h.co.onConsentRespond({ promptId: String(first.promptId), decision: "allow", scope: "session" });
    expect(h.sidecar.callTool).not.toHaveBeenCalled();
  });
});

describe("FIX31 §1.2 — a scheduled task never asks a room with nobody in it", () => {
  it("'Asks each action' task needing permission: no card, not answered for the user, history says needs_permission", async () => {
    const h = await build({ subagentCommands: false, parentDirectCommand: true });
    // buildMessages is stubbed: the assembler returns no messages, so give the
    // coordinator a chain directly through the same seam fireTask uses.
    (h.co as unknown as { buildMessages: unknown }).buildMessages = async (_c: string, _conv: unknown, prompt: string) => ({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: prompt }] as GatewayMessage[],
      parts: [],
    });
    (h.co as unknown as { importVisible: unknown }).importVisible = async () => {};
    (h.co as unknown as { saveLocalHistory: unknown }).saveLocalHistory = () => {};
    await withTimeout(h.co.fireTask({ id: "task-1", name: "Nightly", prompt: "get the date with a shell command", conversationId: "task-conv", permissionMode: "manual" }), 5000);
    // No consent card was ever posted, nothing ran.
    expect(consents(h)).toHaveLength(0);
    expect(h.sidecar.callTool).not.toHaveBeenCalled();
    expect(h.co.pendingConsentCount).toBe(0);
    expect(h.co.toolCallsSeen.at(-1)).toMatchObject({ tool: "run_command", decision: "needs-permission-unattended", ok: false });
    // The history line says why.
    const rec = h.hostQueries.find((q) => q.op === "tasks._recordRun")!;
    expect(rec.args).toMatchObject({ taskId: "task-1", outcome: "needs_permission", reason: "run_command" });
    expect(h.events.some((e) => e.event === "tool_activity" && e.data.phase === "finished" && String(e.data.resultPreview).startsWith("Needs permission"))).toBe(true);
  });

  it("'Never asks' task runs the same command without a prompt (the owner's explicit choice)", async () => {
    const h = await build({ subagentCommands: false, parentDirectCommand: true });
    (h.co as unknown as { buildMessages: unknown }).buildMessages = async (_c: string, _conv: unknown, prompt: string) => ({
      messages: [{ role: "system", content: "sys" }, { role: "user", content: prompt }] as GatewayMessage[],
      parts: [],
    });
    (h.co as unknown as { importVisible: unknown }).importVisible = async () => {};
    (h.co as unknown as { saveLocalHistory: unknown }).saveLocalHistory = () => {};
    await withTimeout(h.co.fireTask({ id: "task-2", name: "Nightly", prompt: "get the date with a shell command", conversationId: "task-conv", permissionMode: "skip" }), 5000);
    expect(consents(h)).toHaveLength(0);
    expect(h.sidecar.callTool).toHaveBeenCalledTimes(1);
    const rec = h.hostQueries.find((q) => q.op === "tasks._recordRun")!;
    expect(rec.args).toMatchObject({ taskId: "task-2", outcome: "ok" });
  });
});
