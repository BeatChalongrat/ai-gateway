#!/usr/bin/env node
// SRAN mail connector — "send as me" (AM9). An MCP server over stdio with one
// tool, send_message, that sends a message a person approved FROM THEIR OWN
// MAILBOX through the Gmail API, attachments included.
//
// Why our own and not a community server: the candidates (GongRzhe's
// server-gmail-autoauth-mcp and its forks, @pouyanafisi/gmail-mcp) send
// attachments only as file paths on the server's disk, keep the OAuth keys
// and tokens in ~/.gmail-mcp/*.json, and need read scopes (gmail.modify, or
// readonly + send). Here:
//   * the access token comes ONLY from this process's environment
//     (MAIL_ACCESS_TOKEN, set by the gateway per person) — never a file,
//     never a URL, never logged, never in an error;
//   * the only scope it needs is gmail.send (users.messages.send) — it
//     cannot read a mailbox;
//   * attachments are the file BYTES the caller sends (base64), built into
//     the message as real attachments — never a path, never a link.
//
// No model calls this tool: the gateway hides this server from the
// catalogue every model's tools are resolved from and refuses any call that
// does not carry chat-edge's internal token. chat-edge calls it only with a
// message a person saw and approved.
//
// MAIL_API_BASE overrides https://gmail.googleapis.com (tests and a local
// sink); set by the deployment, never by a caller.
"use strict";

const crypto = require("crypto");
const readline = require("readline");

const API = (process.env.MAIL_API_BASE || "https://gmail.googleapis.com").replace(/\/$/, "");
const MAX_BYTES = 25 * 1024 * 1024; // Gmail's limit for a message's attachments
const EMAIL = /^[^@\s<>",;]+@[^@\s<>",;]+\.[^@\s<>",;]+$/;

const TOOL = {
  name: "send_message",
  description: "Send one message the person approved, from their own mailbox, with its attachments. Called by the app only.",
  inputSchema: {
    type: "object",
    required: ["to", "subject", "text"],
    properties: {
      to: { type: "array", items: { type: "string" }, minItems: 1 },
      cc: { type: "array", items: { type: "string" } },
      subject: { type: "string" },
      text: { type: "string" },
      attachments: {
        type: "array",
        items: {
          type: "object",
          required: ["filename", "mime_type", "content_base64"],
          properties: { filename: { type: "string" }, mime_type: { type: "string" }, content_base64: { type: "string" } }
        }
      }
    }
  }
};

/** A header value with non-ASCII text as RFC 2047 encoded words (Thai subjects). */
function encodedWord(s) {
  const v = String(s).replace(/[\r\n]+/g, " ");
  return /^[\x20-\x7e]*$/.test(v) ? v : `=?UTF-8?B?${Buffer.from(v, "utf8").toString("base64")}?=`;
}

function wrap76(b64) {
  return b64.replace(/.{1,76}/g, "$&\r\n");
}

function filenameParams(name) {
  const safe = String(name).replace(/[\r\n"\\/]/g, "_");
  const ascii = safe.replace(/[^\x20-\x7e]/g, "_");
  return `filename="${ascii}"; filename*=UTF-8''${encodeURIComponent(safe)}`;
}

/** The message as RFC 5322 / MIME: multipart/mixed, the text, then each attachment. */
function buildMime(args) {
  const boundary = "sran-" + crypto.randomBytes(12).toString("hex");
  const head = [
    `To: ${args.to.join(", ")}`,
    ...(args.cc && args.cc.length ? [`Cc: ${args.cc.join(", ")}`] : []),
    `Subject: ${encodedWord(args.subject)}`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/mixed; boundary="${boundary}"`,
    "",
    `--${boundary}`,
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    wrap76(Buffer.from(args.text, "utf8").toString("base64"))
  ];
  for (const a of args.attachments || []) {
    head.push(
      `--${boundary}`,
      `Content-Type: ${String(a.mime_type).replace(/[\r\n]/g, "")}; name="${String(a.filename).replace(/[^\x20-\x7e]/g, "_").replace(/"/g, "_")}"`,
      `Content-Disposition: attachment; ${filenameParams(a.filename)}`,
      "Content-Transfer-Encoding: base64",
      "",
      wrap76(Buffer.from(a.content_base64, "base64").toString("base64"))
    );
  }
  head.push(`--${boundary}--`, "");
  return head.join("\r\n");
}

function check(args) {
  if (!args || !Array.isArray(args.to) || !args.to.length) return "to: at least one recipient";
  for (const x of [...args.to, ...(args.cc || [])]) if (!EMAIL.test(String(x))) return "a recipient is not an e-mail address";
  if (typeof args.subject !== "string" || typeof args.text !== "string") return "subject and text are text";
  let total = 0;
  for (const a of args.attachments || []) {
    if (!a || !a.filename || !a.mime_type || typeof a.content_base64 !== "string") return "an attachment needs filename, mime_type and content";
    total += Buffer.byteLength(a.content_base64, "base64");
  }
  if (total > MAX_BYTES) return "the attachments are larger than 25 MB";
  return "";
}

async function send(args) {
  const token = process.env.MAIL_ACCESS_TOKEN || "";
  if (!token) return { isError: true, text: "not_connected" };
  const problem = check(args);
  if (problem) return { isError: true, text: `bad_message: ${problem}` };
  const raw = buildMime(args);
  let resp;
  try {
    resp = await fetch(`${API}/upload/gmail/v1/users/me/messages/send?uploadType=media`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "message/rfc822" },
      body: raw
    });
  } catch (err) {
    return { isError: true, text: "unreachable" };
  }
  if (!resp.ok) {
    // the provider's status only: its body may echo request details
    return { isError: true, text: `refused: ${resp.status}` };
  }
  let id = "";
  try {
    id = String((await resp.json()).id || "");
  } catch (_) {
    /* sent; no id read */
  }
  return { isError: false, text: JSON.stringify({ sent: true, id, attachments: (args.attachments || []).length }) };
}

function reply(id, result, error) {
  if (id === undefined || id === null) return;
  const msg = error ? { jsonrpc: "2.0", id, error } : { jsonrpc: "2.0", id, result };
  process.stdout.write(JSON.stringify(msg) + "\n");
}

async function handle(m) {
  switch (m.method) {
    case "initialize":
      return reply(m.id, {
        protocolVersion: (m.params && m.params.protocolVersion) || "2025-03-26",
        capabilities: { tools: {} },
        serverInfo: { name: "sran-mail-send", version: "1.0.0" }
      });
    case "notifications/initialized":
      return;
    case "ping":
      return reply(m.id, {});
    case "tools/list":
      return reply(m.id, { tools: [TOOL] });
    case "tools/call": {
      if (!m.params || m.params.name !== TOOL.name) return reply(m.id, null, { code: -32602, message: "unknown tool" });
      const r = await send(m.params.arguments || {});
      return reply(m.id, { content: [{ type: "text", text: r.text }], isError: r.isError });
    }
    default:
      return reply(m.id, null, { code: -32601, message: "method not found" });
  }
}

if (require.main === module) {
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", (line) => {
    let m;
    try {
      m = JSON.parse(line);
    } catch (_) {
      return;
    }
    handle(m).catch(() => reply(m.id, null, { code: -32603, message: "internal error" }));
  });
}

module.exports = { buildMime, check, send, TOOL };
