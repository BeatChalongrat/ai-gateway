// FIX31 Part 2 — an OAuth connector's tools are HOST tools that carry the
// host's Bearer; the sidecar never sees the tool, the args or the token.
// Against a DISPOSABLE in-test MCP server (test code only).
//
// Pins:
//   • the streamable-HTTP client: initialize → tools/list → tools/call, with
//     Authorization: Bearer <token> on every request, Mcp-Session-Id honoured,
//     a JSON reply and an SSE reply both understood;
//   • ConnectorTools lists nothing when no connector is enabled+connected,
//     names tools mcp__<id>__<tool>, and drops a connector whose list fails;
//   • through the real TurnCoordinator in "Asks each action": a connector tool
//     is offered only when enabled+connected, its call ASKS (Network/Ask —
//     and dangerous, so "Runs safe actions" asks too), runs host-side on allow
//     with the Bearer, never reaches the sidecar, and runs nothing on deny.
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectorOAuth, ConnectorTokenStore, type OAuthRegistration, type TokenCrypto } from "../src/main/connector-oauth";
import { ConnectorTools, McpHttpClient } from "../src/main/connector-mcp";
import type { GatewayMessage, GatewayToolDefinition } from "../src/main/gateway-client";
import { HostTools } from "../src/main/host-tools";
import type { Logger } from "../src/main/logger";
import { PermissionStore } from "../src/main/permissions";
import { GrantStore } from "../src/main/grants";
import { TurnCoordinator } from "../src/main/turn-coordinator";

const lines: string[] = [];
const log: Logger = { info: (m) => lines.push(m), warn: (m) => lines.push(m), error: (m) => lines.push(m), filePath: null, attachFile: () => {}, lines: [] };
const plainCrypto: TokenCrypto = { available: () => true, encrypt: (s) => Buffer.from(s, "utf8"), decrypt: (b) => b.toString("utf8") };

/** A throwaway MCP server: JSON-RPC over POST; requires the Bearer; answers
 *  tools/list as JSON and tools/call as an SSE event (both shapes covered). */
function mcpServer() {
  const state = { auths: [] as string[], calls: [] as { name: string; args: unknown }[], sessions: [] as (string | null)[], listFails: false };
  const server = http.createServer(async (req, res) => {
    let body = "";
    for await (const c of req) body += c;
    state.auths.push(req.headers["authorization"] ?? "");
    state.sessions.push((req.headers["mcp-session-id"] as string | undefined) ?? null);
    if (req.headers["authorization"] !== "Bearer tok-slack") return void res.writeHead(401).end();
    const msg = JSON.parse(body) as { id?: number; method: string; params?: { name?: string; arguments?: unknown } };
    if (msg.id === undefined) return void res.writeHead(202).end(); // notification
    const reply = (result: unknown) => JSON.stringify({ jsonrpc: "2.0", id: msg.id, result });
    if (msg.method === "initialize") return void res.writeHead(200, { "Content-Type": "application/json", "Mcp-Session-Id": "sess-1" }).end(reply({ protocolVersion: "2025-06-18", serverInfo: { name: "fake-slack", version: "0" }, capabilities: {} }));
    if (msg.method === "tools/list") {
      if (state.listFails) return void res.writeHead(500).end();
      return void res.writeHead(200, { "Content-Type": "application/json" }).end(reply({ tools: [{ name: "send_message", description: "Send a Slack message", inputSchema: { type: "object", properties: { channel: { type: "string" }, text: { type: "string" } }, required: ["channel", "text"] } }] }));
    }
    if (msg.method === "tools/call") {
      state.calls.push({ name: msg.params?.name ?? "", args: msg.params?.arguments });
      const ev = `event: message\ndata: ${reply({ content: [{ type: "text", text: `sent to ${(msg.params?.arguments as { channel?: string })?.channel}` }], isError: false })}\n\n`;
      return void res.writeHead(200, { "Content-Type": "text/event-stream" }).end(ev);
    }
    res.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: "no such method" } }));
  });
  return { state, start: () => new Promise<number>((r) => server.listen(0, "127.0.0.1", () => r((server.address() as { port: number }).port))), stop: () => new Promise<void>((r) => server.close(() => r())) };
}

let tmp: string;
let srv: ReturnType<typeof mcpServer>;
let port: number;
beforeEach(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "sran-mcp-"));
  srv = mcpServer();
  port = await srv.start();
  lines.length = 0;
});
afterEach(async () => {
  await srv.stop();
  fs.rmSync(tmp, { recursive: true, force: true });
});

function reg(enabled: boolean): OAuthRegistration {
  return { connectorId: "slack", clientId: "c", clientSecret: null, authorizationEndpoint: "https://p/a", tokenEndpoint: "https://p/t", revocationEndpoint: null, serverUrl: "https://mcp.test/slack", scopes: [], extraAuthParams: {}, enabled };
}
const viaLocal = (input: string, init?: RequestInit) => fetch(input.replace("https://mcp.test/slack", `http://127.0.0.1:${port}/mcp`), init);

let stackN = 0;
function connectorStack(enabled: boolean, connected: boolean) {
  const tokens = new ConnectorTokenStore(path.join(tmp, `tokens-${++stackN}`), plainCrypto); // one store per stack
  if (connected) tokens.set("slack", { accessToken: "tok-slack", refreshToken: null, tokenType: "Bearer", expiresAt: null, scope: null, obtainedAt: Date.now() });
  const oauth = new ConnectorOAuth({ registrations: () => new Map([["slack", reg(enabled)]]), tokens, openExternal: async () => {}, log });
  const tools = new ConnectorTools(oauth, async (id) => oauth.registration(id)?.serverUrl ?? null, log, viaLocal);
  return { oauth, tokens, tools };
}

describe("McpHttpClient", () => {
  it("speaks streamable HTTP with the Bearer on every request, keeps the session id, reads JSON and SSE replies", async () => {
    const c = new McpHttpClient(`http://127.0.0.1:${port}/mcp`, async () => "tok-slack");
    expect(await c.initialize()).toEqual({ name: "fake-slack", version: "0" });
    const tools = await c.listTools();
    expect(tools.map((t) => t.name)).toEqual(["send_message"]);
    const r = await c.callTool("send_message", { channel: "#general", text: "hi" });
    expect(r).toEqual({ ok: true, text: "sent to #general", imageDataUri: null });
    expect(srv.state.auths.every((a) => a === "Bearer tok-slack")).toBe(true);
    expect(srv.state.sessions.slice(1).every((s) => s === "sess-1")).toBe(true); // after initialize
  });

  it("no token → no request is made", async () => {
    const c = new McpHttpClient(`http://127.0.0.1:${port}/mcp`, async () => null);
    await expect(c.listTools()).rejects.toThrow(/not connected/);
    expect(srv.state.auths).toHaveLength(0);
  });
});

describe("ConnectorTools", () => {
  it("lists nothing unless enabled AND connected; names tools mcp__<id>__<tool>", async () => {
    expect(await connectorStack(false, true).tools.definitions()).toEqual([]);
    expect(await connectorStack(true, false).tools.definitions()).toEqual([]);
    const { tools } = connectorStack(true, true);
    const defs = await tools.definitions();
    expect(defs.map((d) => d.name)).toEqual(["mcp__slack__send_message"]);
    expect(tools.has("mcp__slack__send_message")).toBe(true);
    expect(tools.has("mcp__gmail__search")).toBe(false);
    expect(ConnectorTools.parse("mcp__slack__send_message")).toEqual({ connectorId: "slack", tool: "send_message" });
    expect(ConnectorTools.parse("mcp__slack")).toBeNull();
  });

  it("a connector whose tools/list fails is withheld this turn, and said so", async () => {
    srv.state.listFails = true;
    const { tools } = connectorStack(true, true);
    expect(await tools.definitions()).toEqual([]);
    expect(lines.some((l) => /CONNECTOR slack: tools\/list failed — withholding/.test(l))).toBe(true);
  });
});

// ── through the real coordinator ────────────────────────────────────────────

type Ev = { kind: string; [k: string]: unknown };
function gateway() {
  return {
    async *streamCompletion(req: { messages: GatewayMessage[]; tools?: GatewayToolDefinition[] }): AsyncGenerator<Ev> {
      const last = req.messages[req.messages.length - 1];
      const names = (req.tools ?? []).map((t) => t.function.name);
      if (last.role === "user") {
        if (names.includes("mcp__slack__send_message")) {
          yield { kind: "tool_call_delta", index: 0, id: "call_slack", name: "mcp__slack__send_message", argumentsDelta: JSON.stringify({ channel: "#general", text: "deploy done" }) };
          yield { kind: "done", finish_reason: "tool_calls" };
          return;
        }
        yield { kind: "content_delta", delta: `No connector tool offered (${names.join(",")}).` };
        yield { kind: "done", finish_reason: "stop" };
        return;
      }
      yield { kind: "content_delta", delta: `Done: ${String(last.content)}` };
      yield { kind: "done", finish_reason: "stop" };
    },
  };
}

async function coordinator(stack: ReturnType<typeof connectorStack> | null, mode: "manual" | "auto" | "skip" = "manual") {
  const posts: { type: string; payload: Record<string, unknown> }[] = [];
  const bridge = { postTurnEvent: () => {}, post: (type: string, payload: unknown) => posts.push({ type, payload: payload as Record<string, unknown> }), postHostResult: () => {}, postHostError: () => {} };
  const sidecar = {
    listTools: async () => [{ name: "run_command", description: "x", inputSchema: { type: "object" } }],
    callTool: vi.fn(async () => ({ ok: true, text: "", imageDataUri: null })),
    hostQuery: async (op: string) => (op === "settings.get" ? { ok: true, data: { approvalMode: mode } } : { ok: true, data: {} }),
  };
  const gw = gateway();
  let co: TurnCoordinator;
  const hostTools = new HostTools(gw as never, {} as never, (req, sig) => co.runNested(req, sig), log, () => false);
  co = new TurnCoordinator(bridge as never, gw as never, {} as never, {} as never, sidecar as never, hostTools, new PermissionStore(path.join(tmp, "p.json")), new GrantStore(path.join(tmp, "g.json"), { forbiddenRoots: [] }),
    { homeDirectory: tmp, dataDir: tmp, defaultModel: "m", approvalMode: mode, signOut: async () => {}, pickFolder: async () => null, waitKeepaliveMs: 20, ...(stack ? { connectorOAuth: stack.oauth, connectorTools: stack.tools } : {}) }, log);
  const messages: GatewayMessage[] = [{ role: "system", content: "sys" }, { role: "user", content: "tell #general the deploy is done" }];
  const ctrl = new AbortController();
  let text = "";
  const tools = await (co as unknown as { toolDefinitions: (a: null, b: boolean, c: boolean) => Promise<GatewayToolDefinition[]> }).toolDefinitions(null, false, false);
  const outcome = (co as unknown as { runLoop: (r: unknown) => Promise<string> }).runLoop({ conversationId: "conv-1", model: "m", messages, signal: ctrl.signal, maxIterations: 6, maxMs: 30_000, tools, parentToolCallId: null, onText: (t: string) => { text += t; } });
  return { co, posts, sidecar, tools, outcome, text: () => text, messages };
}
const until = async (pred: () => boolean) => { const t0 = Date.now(); while (!pred()) { if (Date.now() - t0 > 4000) throw new Error("timeout"); await new Promise((r) => setTimeout(r, 5)); } };

describe("connector tools through the coordinator", () => {
  it("not enabled → not offered; enabled+connected → offered, and the call ASKS in 'Asks each action'", async () => {
    const off = await coordinator(connectorStack(false, true));
    expect(off.tools.map((t) => t.function.name)).not.toContain("mcp__slack__send_message");
    expect(await off.outcome).toBe("done");
    expect(off.text()).toContain("No connector tool offered");

    const on = await coordinator(connectorStack(true, true));
    expect(on.tools.map((t) => t.function.name)).toContain("mcp__slack__send_message");
    await until(() => on.posts.some((p) => p.type === "consent.request"));
    const req = on.posts.find((p) => p.type === "consent.request")!.payload;
    expect(req).toMatchObject({ tool: "mcp__slack__send_message", category: "Network", dangerClass: "danger.connector" });
    expect(String(req.argumentDetail)).toContain("#general");
    // deny → nothing reaches the connector, nothing reaches the sidecar
    on.co.onConsentRespond({ promptId: String(req.promptId), decision: "deny", scope: "once" });
    expect(await on.outcome).toBe("done");
    expect(srv.state.calls).toHaveLength(0);
    expect(on.sidecar.callTool).not.toHaveBeenCalled();
  });

  it("allow → runs HOST-side with the Bearer; the sidecar never sees it; 'Runs safe actions' still asks", async () => {
    const h = await coordinator(connectorStack(true, true), "auto");
    await until(() => h.posts.some((p) => p.type === "consent.request")); // dangerous → asks even in auto
    const req = h.posts.find((p) => p.type === "consent.request")!.payload;
    h.co.onConsentRespond({ promptId: String(req.promptId), decision: "allow", scope: "once" });
    expect(await h.outcome).toBe("done");
    expect(srv.state.calls).toEqual([{ name: "send_message", args: { channel: "#general", text: "deploy done" } }]);
    expect(srv.state.auths.every((a) => a === "Bearer tok-slack")).toBe(true);
    expect(h.sidecar.callTool).not.toHaveBeenCalled();
    expect(h.co.toolCallsSeen.at(-1)).toMatchObject({ tool: "mcp__slack__send_message", where: "host", ok: true, decision: "allowed-once" });
    expect(h.text()).toContain("sent to #general");
    expect(lines.join("\n")).not.toContain("tok-slack"); // the token is never logged
  });

  it("no module wired (this build's default for probes/tests) → no connector tools at all", async () => {
    const h = await coordinator(null);
    expect(h.tools.map((t) => t.function.name).some((n) => n.startsWith("mcp__"))).toBe(false);
    expect(await h.outcome).toBe("done");
  });
});
