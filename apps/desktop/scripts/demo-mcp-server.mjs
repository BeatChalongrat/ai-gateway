#!/usr/bin/env node
// FIX68 — a tiny MCP server for proving the desktop's custom-connector path.
//
// DEV ONLY. Nothing in the app, the installer or any image references this
// file; it exists so the "+ Add custom connector" flow can be exercised end to
// end without an account or a token anywhere: the user points the picker at
// this script, the sidecar spawns it over stdio, lists its tools, and the model
// calls them behind the normal consent card.
//
// It speaks the MCP stdio transport exactly as the sidecar's StdioMcpTransport
// expects — one JSON-RPC 2.0 message per line, no Content-Length framing — with
// no dependency at all, so it also doubles as the smallest working example for
// docs/CONNECTOR_SETUP.md ("connect your own MCP server").
//
// Tools:
//   fix68_hello                 → a fixed greeting (no arguments)
//   fix68_add_numbers {a, b}    → "a + b = sum"
//   fix68_nonce_<NONCE>         → only when FIX68_NONCE is set in the environment.
//                                 Its NAME carries the nonce, so a tool list
//                                 showing it can only have come from this live
//                                 process — nothing in the repo knows the value.
//
// Evidence hooks (both optional, both env-driven so the command line stays
// exactly what the desktop derives — `node <this file>`):
//   FIX68_MCP_LOG=<path>   append one JSON line per request/call here
//   FIX68_NONCE=<value>    add the nonce-named tool described above
//
// Binds no port. Talks only to whoever spawned it, over its own stdin/stdout.

import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";

const PROTOCOL_VERSION = "2025-06-18";
const NONCE = process.env.FIX68_NONCE || "";
const LOG = process.env.FIX68_MCP_LOG || "";

function logLine(entry) {
  if (!LOG) return;
  try {
    appendFileSync(LOG, JSON.stringify({ at: new Date().toISOString(), pid: process.pid, ...entry }) + "\n");
  } catch {
    /* evidence only — never fail the protocol over it */
  }
}

const tools = [
  {
    name: "fix68_hello",
    description: "Returns a fixed greeting from the FIX68 demo MCP server.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "fix68_add_numbers",
    description: "Adds two numbers and returns the sum.",
    inputSchema: {
      type: "object",
      properties: { a: { type: "number" }, b: { type: "number" } },
      required: ["a", "b"],
      additionalProperties: false,
    },
  },
];
if (NONCE) {
  tools.push({
    name: `fix68_nonce_${NONCE}`,
    description: "Proof tool: its name is only known to the process that listed it.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  });
}

function text(s) {
  return { content: [{ type: "text", text: s }] };
}

function callTool(name, args) {
  if (name === "fix68_hello") return text(`Hello from the FIX68 demo MCP server (pid ${process.pid}).`);
  if (name === "fix68_add_numbers") {
    const a = Number(args?.a);
    const b = Number(args?.b);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return { ...text("a and b must be numbers"), isError: true };
    return text(`${a} + ${b} = ${a + b}`);
  }
  if (NONCE && name === `fix68_nonce_${NONCE}`) return text(`nonce ${NONCE}`);
  return { ...text(`Unknown tool '${name}'`), isError: true };
}

function handle(msg) {
  const { id, method, params } = msg;
  const isNotification = id === undefined || id === null;
  logLine({ kind: isNotification ? "notification" : "request", method, ...(method === "tools/call" ? { tool: params?.name, args: params?.arguments ?? {} } : {}) });
  if (isNotification) return null; // notifications/initialized etc. — no reply
  switch (method) {
    case "initialize":
      return { jsonrpc: "2.0", id, result: { protocolVersion: PROTOCOL_VERSION, capabilities: { tools: {} }, serverInfo: { name: "fix68-demo-mcp", version: "1.0.0" } } };
    case "ping":
      return { jsonrpc: "2.0", id, result: {} };
    case "tools/list":
      return { jsonrpc: "2.0", id, result: { tools } };
    case "tools/call":
      return { jsonrpc: "2.0", id, result: callTool(params?.name, params?.arguments) };
    default:
      return { jsonrpc: "2.0", id, error: { code: -32601, message: `Method not found: ${method}` } };
  }
}

// ── FIX69: the same server over HTTP(S), for the REMOTE-connector proof ─────
//   node demo-mcp-server.mjs --http <port>
//     binds 127.0.0.1 ONLY (never a public interface); POST /mcp per the
//     streamable-HTTP transport (JSON reply), DELETE /mcp closes a session.
//   FIX69_TLS_CERT / FIX69_TLS_KEY   PEM paths → https (the desktop accepts
//                                    https only; a locally trusted dev cert
//                                    for "localhost" is enough)
//   FIX69_KEY                        when set, every request must carry it —
//                                    `Authorization: Bearer <key>` (or the raw
//                                    value under FIX69_AUTH_HEADER); otherwise
//                                    401. The log records ok|missing|wrong,
//                                    NEVER the value presented.
const httpArg = process.argv.indexOf("--http");
if (httpArg >= 0) {
  const port = Number(process.argv[httpArg + 1]);
  const KEY = process.env.FIX69_KEY || "";
  const HEADER = (process.env.FIX69_AUTH_HEADER || "authorization").toLowerCase();
  const certPath = process.env.FIX69_TLS_CERT || "";
  const keyPath = process.env.FIX69_TLS_KEY || "";
  const { readFileSync } = await import("node:fs");
  const authState = (req) => {
    if (!KEY) return "ok";
    const v = req.headers[HEADER];
    if (typeof v !== "string" || !v) return "missing";
    const presented = HEADER === "authorization" ? v.replace(/^Bearer\s+/i, "") : v;
    return presented === KEY ? "ok" : "wrong";
  };
  const handler = (req, res) => {
    const url = new URL(req.url || "/", "http://x");
    if (url.pathname !== "/mcp") {
      res.writeHead(404).end();
      return;
    }
    const auth = authState(req);
    if (auth !== "ok") {
      logLine({ kind: "http", method: req.method, auth, status: 401 });
      res.writeHead(401, { "content-type": "application/json" }).end(JSON.stringify({ error: "unauthorized" }));
      return;
    }
    if (req.method === "DELETE") {
      logLine({ kind: "http", method: "DELETE", auth, status: 200 });
      res.writeHead(200).end();
      return;
    }
    if (req.method !== "POST") {
      res.writeHead(405).end();
      return;
    }
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      let msg;
      try {
        msg = JSON.parse(body);
      } catch {
        res.writeHead(400).end();
        return;
      }
      const reply = handle(msg);
      if (!reply) {
        res.writeHead(202).end();
        return;
      }
      res.writeHead(200, { "content-type": "application/json", "mcp-session-id": "fix69-session" }).end(JSON.stringify(reply));
    });
  };
  let server;
  if (certPath && keyPath) {
    const { createServer } = await import("node:https");
    server = createServer({ cert: readFileSync(certPath), key: readFileSync(keyPath) }, handler);
  } else {
    const { createServer } = await import("node:http");
    server = createServer(handler);
  }
  server.listen(port, "127.0.0.1", () => {
    logLine({ kind: "start", mode: certPath ? "https" : "http", port, keyRequired: Boolean(KEY), tools: tools.map((t) => t.name) });
    process.stdout.write(`listening ${certPath ? "https" : "http"}://127.0.0.1:${port}/mcp\n`);
  });
  process.on("SIGINT", () => process.exit(0));
  process.on("SIGTERM", () => process.exit(0));
} else {
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }) + "\n");
    return;
  }
  const reply = handle(msg);
  if (reply) process.stdout.write(JSON.stringify(reply) + "\n");
});
rl.on("close", () => {
  logLine({ kind: "exit", reason: "stdin closed" });
  process.exit(0);
});
logLine({ kind: "start", tools: tools.map((t) => t.name) });
}
