// FIX68 — the dev-only demo MCP server (scripts/probes/fix68/demo-mcp-server.mjs)
// speaks the stdio transport the sidecar's StdioMcpTransport expects: one
// JSON-RPC 2.0 message per '\n'-terminated line, no Content-Length framing,
// initialize → notifications/initialized → tools/list → tools/call. This test
// is the client side of that contract in plain Node, so a change to the server
// that would break the desktop's custom-connector proof fails here first, and
// the docs' "connect your own MCP server" example stays a working one.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { afterEach, describe, expect, it } from "vitest";

const SERVER = path.resolve(__dirname, "..", "..", "..", "scripts", "probes", "fix68", "demo-mcp-server.mjs");

type Reply = { id?: number; result?: Record<string, unknown>; error?: { code: number; message: string } };

class StdioClient {
  private nextId = 1;
  private readonly pending = new Map<number, (r: Reply) => void>();
  readonly child: ChildProcessWithoutNullStreams;
  constructor(env: Record<string, string>) {
    this.child = spawn(process.execPath, [SERVER], { env: { ...process.env, ...env }, stdio: ["pipe", "pipe", "pipe"] });
    readline.createInterface({ input: this.child.stdout }).on("line", (line) => {
      if (!line.trim()) return;
      const msg = JSON.parse(line) as Reply;
      if (typeof msg.id === "number") this.pending.get(msg.id)?.(msg);
    });
  }
  request(method: string, params: unknown = {}): Promise<Reply> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`timeout: ${method}`)), 10_000);
      this.pending.set(id, (r) => {
        clearTimeout(t);
        resolve(r);
      });
      this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  notify(method: string, params: unknown = {}): void {
    this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
  }
  async close(): Promise<number | null> {
    const exited = new Promise<number | null>((r) => this.child.once("exit", (code) => r(code)));
    this.child.stdin.end();
    return exited;
  }
}

const clients: StdioClient[] = [];
afterEach(async () => {
  for (const c of clients.splice(0)) {
    if (c.child.exitCode === null) c.child.kill();
  }
});

describe("FIX68 demo MCP server over stdio", () => {
  it("answers initialize, lists its tools, adds numbers, and exits when stdin closes", async () => {
    const logFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "fix68-")), "calls.log");
    const c = new StdioClient({ FIX68_NONCE: "abc123", FIX68_MCP_LOG: logFile });
    clients.push(c);

    const init = await c.request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "0" } });
    expect(init.error).toBeUndefined();
    expect((init.result?.["serverInfo"] as { name: string }).name).toBe("fix68-demo-mcp");
    c.notify("notifications/initialized");

    const list = await c.request("tools/list");
    const names = (list.result?.["tools"] as { name: string; inputSchema: unknown }[]).map((t) => t.name);
    expect(names).toEqual(["fix68_hello", "fix68_add_numbers", "fix68_nonce_abc123"]);

    const sum = await c.request("tools/call", { name: "fix68_add_numbers", arguments: { a: 17, b: 25 } });
    expect((sum.result?.["content"] as { text: string }[])[0]!.text).toBe("17 + 25 = 42");

    const bad = await c.request("tools/call", { name: "fix68_add_numbers", arguments: { a: "x", b: 1 } });
    expect(bad.result?.["isError"]).toBe(true);

    const unknown = await c.request("no/such/method");
    expect(unknown.error?.code).toBe(-32601);

    const code = await c.close();
    expect(code).toBe(0);

    // The evidence log the desktop probe relies on: start (with the tool
    // names), every request, the call with its arguments, and the exit.
    const entries = fs.readFileSync(logFile, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>);
    expect(entries[0]?.["kind"]).toBe("start");
    expect(entries[0]?.["tools"]).toEqual(names);
    expect(entries.some((e) => e["kind"] === "request" && e["tool"] === "fix68_add_numbers" && (e["args"] as { a: number }).a === 17)).toBe(true);
    expect(entries.at(-1)?.["kind"]).toBe("exit");
  });

  it("offers no nonce tool when FIX68_NONCE is unset", async () => {
    const c = new StdioClient({ FIX68_NONCE: "" });
    clients.push(c);
    await c.request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "0" } });
    const list = await c.request("tools/list");
    const names = (list.result?.["tools"] as { name: string }[]).map((t) => t.name);
    expect(names).toEqual(["fix68_hello", "fix68_add_numbers"]);
    expect(await c.close()).toBe(0);
  });
});
