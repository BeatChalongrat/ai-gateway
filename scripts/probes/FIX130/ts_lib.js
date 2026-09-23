// FIX130 — shared helpers for checks on the TEST stack (compose project sran-test).
// Talks only to 127.0.0.1:14000 (test chat-edge), 127.0.0.1:18080 (test gateway)
// and the sran-test-* containers; refuses to run if those belong to another project.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const API = "http://127.0.0.1:14000/api/v1";
const OUT = "C:/Users/Beat Beat/Desktop/ai-gateway/scripts/probes/FIX130/results";
const TENANT_A = "f1280000-0000-4000-8000-000000000001";
const TENANT_B = "f1300000-0000-4000-8000-000000000002";
// A = the seeded test owner; B = a second user of the same tenant; C = a user of another tenant.
const USERS = {
  A: { id: "f1280000-0000-4000-8000-000000000011", email: "ts-owner@fix128.test", tenant: TENANT_A, role: "owner" },
  B: { id: "f1300000-0000-4000-8000-000000000012", email: "ts-user2@fix130.test", tenant: TENANT_A, role: "member" },
  C: { id: "f1300000-0000-4000-8000-000000000013", email: "ts-user3@fix130.test", tenant: TENANT_B, role: "owner" },
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const dx = (c, args) => execFileSync("docker", ["exec", c, ...args], { encoding: "utf8", maxBuffer: 64 << 20 }).trim();
const tsql = (q) => dx("sran-test-postgres-1", ["psql", "-U", "postgres", "-d", "ai_gateway", "-At", "-F", "|", "-c", q]);
const csql = (q) => dx("sran-test-postgres-webchat-1", ["psql", "-U", "chatedge", "-d", "sran_ai", "-At", "-F", "|", "-c", q]);

function assertTestStack() {
  for (const c of ["sran-test-postgres-1", "sran-test-postgres-webchat-1", "sran-test-chat-edge-api-1", "sran-test-backend-1"]) {
    const p = execFileSync("docker", ["inspect", c, "--format", '{{index .Config.Labels "com.docker.compose.project"}}'], { encoding: "utf8" }).trim();
    if (p !== "sran-test") throw new Error(`REFUSED: ${c} is in project ${p}`);
  }
}

let secret = null;
function token(who = "A") {
  const u = USERS[who];
  if (!secret) secret = dx("sran-test-chat-edge-api-1", ["env"]).match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  // the gateway reads a stored 0 as version 1 (deps.py: `user.token_version or 1`)
  const tv = Number(tsql(`select token_version from portal_users where id='${u.id}'`)) || 1;
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: u.id, email: u.email, type: "portal", token_use: "access", tenant_id: u.tenant, tenant_role: u.role, token_version: tv, jti: crypto.randomUUID(), iat: now, exp: now + 7200, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}

/** A new chat. opts: {model, memory (default false), temporary (default false)} */
async function newChat(tok, { model = "deepseek-v4-flash", memory = false, temporary = false } = {}) {
  const body = { model_id: model };
  if (temporary) body.is_temporary = true;
  const c = await api(tok, "/conversations", { method: "POST", body: JSON.stringify(body) });
  if (c.status >= 300) throw new Error(`create chat ${c.status} ${JSON.stringify(c.body).slice(0, 200)}`);
  const want = temporary ? false : memory;
  if (c.body.memory_enabled !== want) {
    await api(tok, `/conversations/${c.body.id}`, { method: "PATCH", body: JSON.stringify({ memory_enabled: want }) });
  }
  return c.body.id;
}

/** One streamed turn. Returns {status, events, term, content, reasoning, frames}. */
async function turn(tok, id, content, { timeoutMs = 240000 } = {}) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), timeoutMs);
  let text = "";
  let status = 0;
  try {
    const r = await fetch(API + `/conversations/${id}/messages`, { method: "POST", signal: ac.signal, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify({ content, stream: true }) });
    status = r.status;
    text = await r.text();
  } catch (e) {
    text += `\n\nevent: probe_abort\ndata: ${JSON.stringify({ err: String(e) })}\n\n`;
  } finally { clearTimeout(t); }
  const frames = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const ev = /^event: (.+)$/m.exec(block)?.[1];
    const data = /^data: (.+)$/m.exec(block)?.[1];
    if (ev) { let d = null; try { d = JSON.parse(data); } catch { d = data; } frames.push({ ev, d }); }
  }
  const term = frames.filter((f) => ["message_complete", "error", "cancelled", "probe_abort"].includes(f.ev)).pop() || null;
  const contentText = frames.filter((f) => f.ev === "content_delta").map((f) => f.d?.delta || "").join("");
  const reasoning = frames.filter((f) => f.ev === "reasoning_delta").map((f) => f.d?.delta || "").join("");
  return { status, events: [...new Set(frames.map((f) => f.ev))], term, content: contentText, reasoning, frames };
}

const messages = async (tok, id) => (await api(tok, `/conversations/${id}/messages?limit=200`)).body;
const memoriesOf = (userId, tenant) => csql(`select status, memory_type, coalesce(category,''), source_type, coalesce(source_conversation_id::text,''), replace(content, E'\\n', ' ') from chat.user_memories where user_id='${userId}' and tenant_id='${tenant}' order by created_at`).split("\n").filter(Boolean);
const deleteChat = (tok, id) => api(tok, `/conversations/${id}`, { method: "DELETE" });

function save(name, obj) {
  fs.mkdirSync(path.dirname(path.join(OUT, name)), { recursive: true });
  fs.writeFileSync(path.join(OUT, name), typeof obj === "string" ? obj : JSON.stringify(obj, null, 2));
}

module.exports = { API, OUT, USERS, TENANT_A, TENANT_B, wait, dx, tsql, csql, assertTestStack, token, api, newChat, turn, messages, memoriesOf, deleteChat, save };
