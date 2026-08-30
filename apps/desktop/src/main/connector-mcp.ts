// FIX31 Part 2 — the host-side MCP client for OAuth connectors.
//
// The sidecar's remote MCP client dials a connector with whatever token its
// install record holds; for oauth2 that was the PORTAL JWT (Phase 7 C4), which
// is exactly the design the brief closes ("the token belongs to the host that
// owns privileged capability, never to the process that runs tools"). So an
// OAuth connector's tools are HOST tools: this client speaks MCP over
// streamable HTTP (JSON-RPC 2.0 POSTs; a JSON or a single-event SSE reply;
// Mcp-Session-Id honoured) and puts the connector's Bearer on the wire itself.
// The token is read from ConnectorOAuth.accessToken() per call and never
// leaves this process.
//
// Offered to the model as `mcp__<connectorId>__<tool>` — the prefix the
// gateway's tool-argument DLP policy already classifies as external egress
// (FIX12); unknown to toolMeta, so category Network / default Ask: every call
// asks in "Asks each action" and in "Runs safe actions" (a connector call is
// dangerous by the classifier), runs unprompted only under "Never asks".
// Nothing is offered unless ConnectorOAuth says a connector is BOTH enabled in
// the owner's file AND connected — with no file, this module lists nothing.
//
// No 'electron' import: unit-tested in plain Node against an in-test MCP
// server (test code, not a runnable default).

import type { ConnectorOAuth } from "./connector-oauth";
import type { Logger } from "./logger";

export const CONNECTOR_TOOL_PREFIX = "mcp__";
const PROTOCOL_VERSION = "2025-06-18";
const TOOLS_TTL_MS = 5 * 60_000;

export interface McpToolDef {
  name: string;
  description: string;
  inputSchema: unknown;
}
export interface McpCallResult {
  ok: boolean;
  text: string;
  imageDataUri: string | null;
}

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;
type RpcReply = { result?: unknown; error?: { code: number; message: string } };

/** Minimal streamable-HTTP MCP client. One instance per (connector, session). */
export class McpHttpClient {
  private nextId = 1;
  private sessionId: string | null = null;
  private initialized = false;

  constructor(
    private readonly baseUrl: string,
    private readonly bearer: () => Promise<string | null>,
    private readonly fetchImpl: FetchLike = (i, init) => fetch(i, init),
    private readonly timeoutMs = 30_000,
  ) {}

  async initialize(): Promise<{ name: string; version: string }> {
    const r = (await this.request("initialize", { protocolVersion: PROTOCOL_VERSION, capabilities: {}, clientInfo: { name: "SranDesktop.Electron", version: "fix31" } })) as { serverInfo?: { name?: string; version?: string } };
    this.initialized = true;
    await this.notify("notifications/initialized", {});
    return { name: r.serverInfo?.name ?? "?", version: r.serverInfo?.version ?? "?" };
  }

  async listTools(): Promise<McpToolDef[]> {
    if (!this.initialized) await this.initialize();
    const r = (await this.request("tools/list", {})) as { tools?: { name: string; description?: string; inputSchema?: unknown }[] };
    return (r.tools ?? []).map((t) => ({ name: t.name, description: t.description ?? "", inputSchema: t.inputSchema ?? { type: "object" } }));
  }

  async callTool(name: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<McpCallResult> {
    if (!this.initialized) await this.initialize();
    const r = (await this.request("tools/call", { name, arguments: args }, signal)) as { content?: { type: string; text?: string; data?: string; mimeType?: string }[]; isError?: boolean };
    let text = "";
    let image: string | null = null;
    for (const block of r.content ?? []) {
      if (block.type === "text" && typeof block.text === "string") text += (text ? "\n" : "") + block.text;
      else if (block.type === "image" && block.data) image = `data:${block.mimeType ?? "image/png"};base64,${block.data}`;
    }
    return { ok: !r.isError, text, imageDataUri: image };
  }

  private async headers(): Promise<Record<string, string>> {
    const token = await this.bearer();
    if (!token) throw new Error("not connected (no token)");
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      Authorization: `Bearer ${token}`,
      "MCP-Protocol-Version": PROTOCOL_VERSION,
    };
    if (this.sessionId) h["Mcp-Session-Id"] = this.sessionId;
    return h;
  }

  private async notify(method: string, params: unknown): Promise<void> {
    await this.fetchImpl(this.baseUrl, { method: "POST", headers: await this.headers(), body: JSON.stringify({ jsonrpc: "2.0", method, params }), signal: AbortSignal.timeout(this.timeoutMs) });
  }

  private async request(method: string, params: unknown, signal?: AbortSignal): Promise<unknown> {
    const id = this.nextId++;
    const res = await this.fetchImpl(this.baseUrl, {
      method: "POST",
      headers: await this.headers(),
      body: JSON.stringify({ jsonrpc: "2.0", id, method, params }),
      signal: signal ?? AbortSignal.timeout(this.timeoutMs),
    });
    const sid = res.headers.get("mcp-session-id");
    if (sid) this.sessionId = sid;
    if (!res.ok) throw new Error(`MCP ${method}: HTTP ${res.status}`);
    const ctype = res.headers.get("content-type") ?? "";
    const raw = await res.text();
    let msg: RpcReply | null = null;
    if (ctype.includes("text/event-stream")) {
      // The reply is one (or more) SSE events; the one whose id matches ours wins.
      for (const block of raw.split(/\n\n+/)) {
        const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
        if (!data) continue;
        try {
          const j = JSON.parse(data) as RpcReply & { id?: number };
          if (j.id === id) {
            msg = j;
            break;
          }
        } catch {
          /* keep looking */
        }
      }
    } else {
      msg = JSON.parse(raw) as RpcReply;
    }
    if (!msg) throw new Error(`MCP ${method}: no response for id ${id}`);
    if (msg.error) throw new Error(`MCP ${method}: ${msg.error.code} ${msg.error.message}`);
    return msg.result;
  }
}

/** The connector tools a turn may offer, and their dispatch. Names are
 *  `mcp__<connectorId>__<tool>`; the connector id is taken from the OWNER'S
 *  registration file, never from the model. */
export class ConnectorTools {
  private readonly clients = new Map<string, McpHttpClient>();
  private readonly toolsCache = new Map<string, { at: number; tools: McpToolDef[] }>();

  constructor(
    private readonly oauth: ConnectorOAuth,
    /** connector id → MCP server url. The catalogue's serverUrl, resolved by the
     *  host from the sidecar's catalogue (the sidecar keeps the catalogue; the
     *  host keeps the token). */
    private readonly serverUrlOf: (connectorId: string) => Promise<string | null>,
    private readonly log: Logger,
    private readonly fetchImpl?: FetchLike,
  ) {}

  static parse(name: string): { connectorId: string; tool: string } | null {
    if (!name.startsWith(CONNECTOR_TOOL_PREFIX)) return null;
    const rest = name.slice(CONNECTOR_TOOL_PREFIX.length);
    const i = rest.indexOf("__");
    if (i <= 0 || i === rest.length - 2) return null;
    return { connectorId: rest.slice(0, i), tool: rest.slice(i + 2) };
  }

  has(name: string): boolean {
    const p = ConnectorTools.parse(name);
    return p !== null && this.oauth.activeConnectorIds().includes(p.connectorId);
  }

  /** Definitions for every enabled+connected connector. A connector that
   *  cannot be listed (down, token refused) contributes nothing this turn and
   *  is logged — a tool that cannot work is not offered. */
  async definitions(): Promise<{ name: string; description: string; inputSchema: unknown }[]> {
    const out: { name: string; description: string; inputSchema: unknown }[] = [];
    for (const id of this.oauth.activeConnectorIds()) {
      const cached = this.toolsCache.get(id);
      let tools: McpToolDef[];
      if (cached && Date.now() - cached.at < TOOLS_TTL_MS) tools = cached.tools;
      else {
        try {
          const client = await this.client(id);
          if (!client) continue;
          tools = await client.listTools();
          this.toolsCache.set(id, { at: Date.now(), tools });
          this.log.info(`CONNECTOR ${id}: ${tools.length} tool(s) listed`);
        } catch (err) {
          this.log.warn(`CONNECTOR ${id}: tools/list failed — withholding this turn (${(err as Error).message})`);
          this.clients.delete(id);
          continue;
        }
      }
      for (const t of tools) out.push({ name: `${CONNECTOR_TOOL_PREFIX}${id}__${t.name}`, description: `[${id}] ${t.description}`, inputSchema: t.inputSchema });
    }
    return out;
  }

  async execute(name: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<McpCallResult> {
    const p = ConnectorTools.parse(name);
    if (!p) return { ok: false, text: `Unknown connector tool '${name}'.`, imageDataUri: null };
    if (!this.oauth.activeConnectorIds().includes(p.connectorId)) return { ok: false, text: `Connector '${p.connectorId}' is not connected.`, imageDataUri: null };
    const client = await this.client(p.connectorId);
    if (!client) return { ok: false, text: `Connector '${p.connectorId}' has no server url.`, imageDataUri: null };
    try {
      return await client.callTool(p.tool, args, signal);
    } catch (err) {
      this.clients.delete(p.connectorId);
      return { ok: false, text: `Connector '${p.connectorId}' call failed: ${(err as Error).message}`, imageDataUri: null };
    }
  }

  forget(connectorId: string): void {
    this.clients.delete(connectorId);
    this.toolsCache.delete(connectorId);
  }

  private async client(id: string): Promise<McpHttpClient | null> {
    const existing = this.clients.get(id);
    if (existing) return existing;
    const url = await this.serverUrlOf(id);
    if (!url) {
      this.log.warn(`CONNECTOR ${id}: no server url in the catalogue`);
      return null;
    }
    const c = new McpHttpClient(url, () => this.oauth.accessToken(id), this.fetchImpl);
    this.clients.set(id, c);
    return c;
  }
}
