// The sidecar client (E4): spawns SranDesktop.ToolsSidecar.exe as a child
// process and speaks MCP over its stdio — newline-delimited JSON-RPC 2.0, the
// framing E0 proved with the .NET StdioMcpTransport. This is the ONLY code in
// the host that touches the sidecar, and the renderer never can (the bridge's
// closed enum has no tool message; tools are a host decision).
//
//   * every tools/call carries _meta { conversationId, grants, progressToken }
//     — the sidecar refuses a call without grants (E4 §1 #4);
//   * notifications/progress with our token → the caller's onProgress
//     (run_command's live output);
//   * cancel() sends notifications/cancelled and fails the pending call;
//   * the process is supervised: a crash fails every in-flight call with a
//     legible error and the next call respawns it (bounded backoff).

import { spawn, type ChildProcessByStdio } from "node:child_process";
import { randomUUID } from "node:crypto";
import readline from "node:readline";
import type { Readable, Writable } from "node:stream";
import type { Logger } from "./logger";

export interface SidecarToolDefinition {
  name: string;
  description: string;
  inputSchema: unknown;
}
export interface SidecarCallResult {
  ok: boolean;
  text: string;
  imageDataUri: string | null;
  isError: boolean;
  /** JSON-RPC error (refusal, unknown tool, cancelled) — distinct from a tool's own failure. */
  rpcError: { code: number; message: string } | null;
}
export interface SidecarCallOptions {
  conversationId: string;
  grants: readonly string[];
  onProgress?: (chunk: string) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** FIX6: the origin the host authorized for this browser action. The sidecar
   * re-checks the target tab's LIVE origin against it and refuses on a mismatch
   * (closing the redirect / TOCTOU window between the host's gate and the act). */
  authorizedSite?: string;
}

interface Pending {
  resolve: (r: { result?: unknown; error?: { code: number; message: string } }) => void;
  progressToken?: string;
  onProgress?: (chunk: string) => void;
}

export class SidecarClient {
  private child: ChildProcessByStdio<Writable, Readable, Readable> | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();
  private readonly byProgressToken = new Map<string, Pending>();
  private starting: Promise<void> | null = null;
  private spawnFailures = 0;
  private toolsCache: SidecarToolDefinition[] | null = null;
  pid: number | null = null;
  // FIX3 §3: an HONEST startup indicator — the phase is derived from the
  // sidecar's real stderr, not a timer. `ready` flips only when the child has
  // answered `initialize` (all startup work — native extraction, connector
  // refresh, browser provisioning — happens before that).
  private _ready = false;
  private _phase = "Starting tools…";
  get ready(): boolean {
    return this._ready;
  }
  get phase(): string {
    return this._phase;
  }

  constructor(
    private readonly exePath: string,
    private readonly dataDir: string,
    private readonly log: Logger,
    private readonly env: Record<string, string> = {},
  ) {}

  get isRunning(): boolean {
    return this.child !== null && this.child.exitCode === null;
  }

  async ensureStarted(): Promise<void> {
    if (this.isRunning) return;
    if (this.starting) return this.starting;
    this.starting = this.spawnOnce().finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  private async spawnOnce(): Promise<void> {
    if (this.spawnFailures >= 3) {
      throw new Error("sidecar failed to start 3 times; not retrying until the app restarts");
    }
    const t0 = Date.now();
    // No windowsHide: with CREATE_NO_WINDOW the sidecar's GDI screen capture
    // failed ("The handle is invalid") — found live in E4 S2. The sidecar is
    // a console app whose window is hidden by the OS anyway (no console
    // attached to Electron).
    const child = spawn(this.exePath, [], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, SRAN_SIDECAR_DATA_DIR: this.dataDir, ...this.env },
    });
    this.child = child;
    this.pid = child.pid ?? null;
    this.toolsCache = null;
    this._ready = false;
    this._phase = "Starting tools…";
    this.log.info(`SIDECAR spawned pid=${this.pid} exe=${this.exePath}`);

    const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
    rl.on("line", (line) => this.onLine(line));
    const err = readline.createInterface({ input: child.stderr, crlfDelay: Infinity });
    err.on("line", (line) => {
      if (!line.trim()) return;
      this.updatePhase(line);
      this.log.info(`sidecar: ${line.slice(0, 400)}`);
    });
    child.on("exit", (code, signal) => {
      this.log.error(`SIDECAR EXITED pid=${this.pid} code=${code} signal=${signal ?? "-"}; failing ${this.pending.size} in-flight call(s)`);
      for (const [id, p] of this.pending) {
        this.pending.delete(id);
        p.resolve({ error: { code: -32000, message: "The tools sidecar exited while this tool was running." } });
      }
      this.byProgressToken.clear();
      this.child = null;
      this.toolsCache = null;
      this._ready = false;
      this._phase = "Starting tools…";
    });
    child.on("error", (e) => this.log.error(`SIDECAR spawn error: ${e.message}`));

    const init = await this.request("initialize", {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "SranDesktop.Electron", version: "e4" },
    }, 30_000);
    if (init.error) {
      this.spawnFailures += 1;
      throw new Error(`sidecar initialize failed: ${init.error.message}`);
    }
    await this.notify("notifications/initialized", {});
    this.spawnFailures = 0;
    this._ready = true;
    this._phase = "ready";
    const info = init.result as { serverInfo?: { name?: string; version?: string }; instructions?: string };
    this.log.info(`SIDECAR ready in ${Date.now() - t0} ms: ${info.serverInfo?.name} ${info.serverInfo?.version} ${info.instructions ?? ""}`);
  }

  async listTools(): Promise<SidecarToolDefinition[]> {
    await this.ensureStarted();
    if (this.toolsCache) return this.toolsCache;
    const r = await this.request("tools/list", {}, 30_000);
    if (r.error) throw new Error(`tools/list failed: ${r.error.message}`);
    const tools = ((r.result as { tools?: SidecarToolDefinition[] }).tools ?? []).map((t) => ({
      name: t.name,
      description: t.description ?? "",
      inputSchema: t.inputSchema ?? { type: "object" },
    }));
    this.toolsCache = tools;
    return tools;
  }

  async status(): Promise<Record<string, unknown>> {
    await this.ensureStarted();
    const r = await this.request("sran/status", {}, 10_000);
    return (r.result as Record<string, unknown>) ?? { error: r.error?.message };
  }

  /** FIX1 §1.3: the host-only control surface (settings.*, browser picker). A
   * DIFFERENT JSON-RPC method from tools/call — never enumerated by tools/list,
   * so the model has no vocabulary for it. The host forwards the page's
   * host.query onto this; the model's turn loop only ever calls callTool(). */
  async hostQuery(op: string, args: Record<string, unknown> = {}): Promise<{ ok: boolean; data?: unknown; error?: string }> {
    await this.ensureStarted();
    const r = await this.request("host/query", { op, args }, 30_000);
    if (r.error) return { ok: false, error: r.error.message };
    const res = r.result as { ok?: boolean; data?: unknown; error?: string };
    return { ok: !!res.ok, data: res.data, error: res.error };
  }

  /** tools/call with the E4 _meta. Never throws for tool failures; rpcError
   * carries refusals so the caller can show them legibly. */
  async callTool(name: string, args: Record<string, unknown>, opts: SidecarCallOptions): Promise<SidecarCallResult> {
    await this.ensureStarted();
    const progressToken = opts.onProgress ? `p-${randomUUID().slice(0, 8)}` : undefined;
    const meta: Record<string, unknown> = { conversationId: opts.conversationId, grants: [...opts.grants] };
    if (progressToken) meta["progressToken"] = progressToken;
    if (opts.authorizedSite) meta["authorizedSite"] = opts.authorizedSite;
    const r = await this.request("tools/call", { name, arguments: args, _meta: meta }, opts.timeoutMs ?? 3_900_000, {
      progressToken,
      onProgress: opts.onProgress,
      signal: opts.signal,
    });
    if (r.error) return { ok: false, text: r.error.message, imageDataUri: null, isError: true, rpcError: r.error };
    const result = r.result as { content?: { type: string; text?: string; data?: string; mimeType?: string }[]; isError?: boolean };
    let text = "";
    let image: string | null = null;
    for (const block of result.content ?? []) {
      if (block.type === "text" && typeof block.text === "string") text += (text ? "\n" : "") + block.text;
      else if (block.type === "image" && block.data) image = `data:${block.mimeType ?? "image/png"};base64,${block.data}`;
    }
    return { ok: !result.isError, text, imageDataUri: image, isError: !!result.isError, rpcError: null };
  }

  /** Dev/probe seam: a raw request with an arbitrary params object (used to
   * prove the refusal of a call without _meta). */
  raw(method: string, params: unknown, timeoutMs = 30_000): Promise<{ result?: unknown; error?: { code: number; message: string } }> {
    return this.ensureStarted().then(() => this.request(method, params, timeoutMs));
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    this.log.info(`SIDECAR stopping pid=${this.pid}`);
    try {
      child.stdin.end();
    } catch {
      /* ignore */
    }
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => {
        try {
          child.kill();
        } catch {
          /* ignore */
        }
        resolve();
      }, 3000);
      child.once("exit", () => {
        clearTimeout(t);
        resolve();
      });
    });
  }

  // ── wire ────────────────────────────────────────────────────────────────
  private request(
    method: string,
    params: unknown,
    timeoutMs: number,
    stream?: { progressToken?: string; onProgress?: (chunk: string) => void; signal?: AbortSignal },
  ): Promise<{ result?: unknown; error?: { code: number; message: string } }> {
    const child = this.child;
    if (!child) return Promise.resolve({ error: { code: -32000, message: "sidecar not running" } });
    const id = this.nextId++;
    return new Promise((resolve) => {
      let settled = false;
      const finish = (r: { result?: unknown; error?: { code: number; message: string } }) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.pending.delete(id);
        if (stream?.progressToken) this.byProgressToken.delete(stream.progressToken);
        stream?.signal?.removeEventListener("abort", onAbort);
        resolve(r);
      };
      const timer = setTimeout(() => {
        void this.notify("notifications/cancelled", { requestId: id, reason: "timeout" });
        finish({ error: { code: -32001, message: `sidecar request ${method} timed out after ${timeoutMs / 1000}s` } });
      }, timeoutMs);
      const onAbort = () => {
        void this.notify("notifications/cancelled", { requestId: id, reason: "cancelled by the host" });
        // Keep waiting briefly for the sidecar's own -32800 so the tool really
        // stopped; fail locally if it does not answer.
        setTimeout(() => finish({ error: { code: -32800, message: "Request cancelled" } }), 2000);
      };
      stream?.signal?.addEventListener("abort", onAbort, { once: true });
      const pending: Pending = { resolve: finish, progressToken: stream?.progressToken, onProgress: stream?.onProgress };
      this.pending.set(id, pending);
      if (stream?.progressToken) this.byProgressToken.set(stream.progressToken, pending);
      child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }

  private notify(method: string, params: unknown): Promise<void> {
    const child = this.child;
    if (!child) return Promise.resolve();
    return new Promise((resolve) => child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n", () => resolve()));
  }

  /** FIX3 §3: map a real sidecar stderr line to a human phase label. Order in
   * Program.cs: start → browser provisioning → connector refresh → registry →
   * initialize (ready). Reflects actual work, never a timer. */
  private updatePhase(line: string): void {
    if (this._ready) return;
    if (/Remote MCP tools cached/i.test(line)) this._phase = "Loading connectors…";
    else if (/^.*Browser: host=|Browser extension deployed/i.test(line)) this._phase = "Setting up the browser bridge…";
    else if (/Registered \d+ local tool/i.test(line)) this._phase = "Almost ready…";
    else if (/starting, pid/i.test(line)) this._phase = "Starting tools…";
  }

  private onLine(line: string): void {
    if (!line.trim()) return;
    let msg: { id?: number; result?: unknown; error?: { code: number; message: string }; method?: string; params?: Record<string, unknown> };
    try {
      msg = JSON.parse(line);
    } catch {
      this.log.warn(`sidecar: non-JSON stdout line ignored (${line.length} chars)`);
      return;
    }
    if (msg.method === "notifications/progress" && msg.params) {
      const token = String(msg.params["progressToken"]);
      const p = this.byProgressToken.get(token);
      const message = msg.params["message"];
      if (p?.onProgress && typeof message === "string") p.onProgress(message);
      return;
    }
    if (typeof msg.id === "number") {
      const p = this.pending.get(msg.id);
      if (p) p.resolve(msg.error ? { error: msg.error } : { result: msg.result });
    }
  }
}
