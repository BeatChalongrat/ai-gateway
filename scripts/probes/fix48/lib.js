// FIX48 shared helpers — gateway (bare arm) + chat-edge (SRAN arm).
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

const REPO = path.resolve(__dirname, "..", "..", "..");
const GATEWAY = process.env.GATEWAY_URL || "http://localhost:8080";
const API = process.env.CHAT_API || "http://localhost:4000/api/v1";
const USER = process.env.PROBE_USER || "56726cc2-47d2-4e81-95bd-4aba41d4d677";
const TENANT = process.env.PROBE_TENANT || "169323ae-3aa2-4bc9-bb2a-e4467c716e51";

const gatewayKey = () =>
  (fs.readFileSync(path.join(REPO, ".env"), "utf8").match(/^GATEWAY_API_KEY=(.+)$/m) || [])[1].trim().replace(/^"|"$/g, "");

function token() {
  const env = execFileSync("docker", ["exec", "ai-gateway-chat-edge-api", "env"], { encoding: "utf8" });
  const secret = env.match(/^PORTAL_JWT_SECRET=(.+)$/m)[1].trim();
  const tv = Number(execFileSync("docker", ["exec", "ai-gateway-postgres-1", "psql", "-U", "postgres", "-d", "ai_gateway", "-tA", "-c",
    `select token_version from portal_users where id='${USER}'`], { encoding: "utf8" }).trim() || 0);
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const d = `${b64({ alg: "HS256", typ: "JWT" })}.${b64({ sub: USER, email: "owner@local", type: "portal", token_use: "access",
    tenant_id: TENANT, tenant_role: "owner", token_version: tv, iat: now, exp: now + 7200, iss: "sran-portal" })}`;
  return `${d}.${crypto.createHmac("sha256", secret).update(d).digest("base64url")}`;
}

async function api(tok, p, init = {}) {
  const r = await fetch(API + p, { ...init, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", ...(init.headers || {}) } });
  const t = await r.text();
  try { return { status: r.status, body: JSON.parse(t) }; } catch { return { status: r.status, body: t }; }
}

// ── ARM 1: bare model, straight at the gateway, no SRAN prompt/DLP/context ──
async function bare(model, messages, opts = {}) {
  const t0 = Date.now();
  const r = await fetch(`${GATEWAY}/v1/chat/completions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + gatewayKey() },
    body: JSON.stringify({ model, messages, max_tokens: opts.max_tokens || 1500, ...(opts.extra || {}) }),
  });
  const j = await r.json().catch(() => ({}));
  return {
    http: r.status, ms: Date.now() - t0,
    served: j.model || null,
    text: j?.choices?.[0]?.message?.content || "",
    usage: j.usage || null,
    error: j.error || null,
  };
}

// ── ARM 2: the product — a real conversation through chat-edge ──
async function sranConversation(tok, { title, model }) {
  const r = await api(tok, "/conversations", { method: "POST", body: JSON.stringify({ title, model_id: model }) });
  if (r.status >= 300) throw new Error(`conversation create ${r.status}: ${JSON.stringify(r.body).slice(0, 300)}`);
  return r.body.id || r.body.conversation?.id;
}

async function sranTurn(tok, convId, content, opts = {}) {
  const t0 = Date.now();
  const r = await fetch(`${API}/conversations/${convId}/messages`, {
    method: "POST",
    headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ content, stream: true, ...(opts.body || {}) }),
  });
  if (!r.ok) return { http: r.status, ms: Date.now() - t0, text: "", error: await r.text() };
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "", text = "", ttfbMs = null, meta = {}, events = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const line of raw.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const p = line.slice(5).trim();
        if (!p || p === "[DONE]") continue;
        let e; try { e = JSON.parse(p); } catch { continue; }
        events.push(e.type || e.event || Object.keys(e)[0]);
        const delta = e.delta ?? e.content ?? e.text ?? e?.choices?.[0]?.delta?.content;
        if (typeof delta === "string" && delta) { if (ttfbMs === null) ttfbMs = Date.now() - t0; text += delta; }
        if (e.type === "done" || e.type === "message_complete" || e.usage) meta = { ...meta, ...e };
      }
    }
  }
  return { http: r.status, ms: Date.now() - t0, ttfbMs, text, meta, events: [...new Set(events)] };
}

module.exports = { REPO, GATEWAY, API, USER, TENANT, gatewayKey, token, api, bare, sranConversation, sranTurn };
